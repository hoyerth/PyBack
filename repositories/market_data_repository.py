# repositories/market_data_repository.py
"""
repositories/market_data_repository.py - Exklusiver Lesezugriff auf Marktdaten.

Ausgelagert aus db_service.py im Rahmen von 18.01.02 (E3): `MarketDataRepository`
(kapselt den exklusiven Lesezugriff auf die Marktdatenbank) und
`get_symbol_precision` (identische Precision-Query, DRY – Grundlage der
Custom-Level-Eingabefelder). Importiert nur db/db_pool (E4).
"""

import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from db.db_pool import DB_MARKET_DATA, DbPool, db_connect


# ==============================================================================
# HELPER: Symbol-Preision (fixer Wert je Symbol, identisch zur Preisskala)
# ==============================================================================
def get_symbol_precision(symbol: str, timeframe: str,
                         db_path: str = DB_MARKET_DATA) -> int:
    """Liefert die Preisskala-Praezision (Nachkommastellen) eines Symbols.

    Identische Query wie MarketDataRepository.fetch_historical_candles()
    (die Preisskala im Chart nutzt exakt diesen Wert) – jedoch OHNE die
    Candles zu laden. Wird fuer die Custom-Level-Eingabefelder (prox_level1..6)
    verwendet, damit die Eingabe dieselbe Dezimalanzahl wie die Preisskala hat.

    Fallback: 2 bei fehlender DB / leerer Tabelle / Fehler.
    """
    default = 2
    if not os.path.exists(db_path):
        return default
    try:
        con = DbPool.get(db_path)
        p_row = con.execute("""
            SELECT COALESCE(MAX(
                CASE
                    WHEN POSITION('.' IN CAST(ROUND(close, 5) AS VARCHAR)) > 0
                    THEN LENGTH(RTRIM(CAST(ROUND(close, 5) AS VARCHAR), '0'))
                         - POSITION('.' IN CAST(ROUND(close, 5) AS VARCHAR))
                    ELSE 0
                END
            ), 2) AS precision
            FROM (
                SELECT close
                FROM ohlcv_bars
                WHERE symbol = UPPER(?) AND timeframe = UPPER(?)
                  AND close IS NOT NULL
                LIMIT 1000
            );
        """, [symbol, timeframe]).fetchone()
        if p_row and p_row[0] is not None:
            return int(p_row[0])
    except Exception:
        pass
    return default


# ==============================================================================
# REPOSITORY MIT ROBUSTER STATISTISCHER PRECISION-ERMITTLUNG
# ==============================================================================
class MarketDataRepository:
    """Kapselt den exklusiven Lesezugriff auf die Marktdatenbank."""

    def __init__(self, db_path: str = DB_MARKET_DATA) -> None:
        self.db_path = db_path
        # P1#6 (Optimierung): Precision je (symbol, timeframe) nur 1x pro
        # Repository-Instanz ermitteln (die Query scannt sonst bei jedem
        # Laden 1000 Zeilen mit LOWER()-Funktionen).
        self._precision_cache: Dict[Tuple[str, str], int] = {}

    # ------------------------------------------------------------------
    # P1#6: gecachte Precision-Ermittlung (DRY-Grundlage fuer die Queries)
    # ------------------------------------------------------------------
    def _get_precision_cached(self, con, symbol: str,
                              timeframe: str) -> int:
        """Ermittelt die Preisskala-Praezision eines Paares (1x gecacht).

        Die identische statistische Query wie get_symbol_precision(), aber
        pro Repository-Instanz gecacht (P1#6). Daten sind UPPER-normalisiert
        (Verifikation 17.08.2026: 0 Abweichungen) -> Exakt-Vergleich mit
        UPPER(?)-Parameter statt LOWER()-Funktionen auf der Spalte (nutzt
        den PK-/ARTEMIS-Index, kein Funktions-Scan).
        """
        key = (symbol.upper(), timeframe.upper())
        cached = self._precision_cache.get(key)
        if cached is not None:
            return cached
        precision: int = 2
        try:
            p_row = con.execute("""
                SELECT COALESCE(MAX(
                    CASE
                        WHEN POSITION('.' IN CAST(ROUND(close, 5) AS VARCHAR)) > 0
                        THEN LENGTH(RTRIM(CAST(ROUND(close, 5) AS VARCHAR), '0'))
                             - POSITION('.' IN CAST(ROUND(close, 5) AS VARCHAR))
                        ELSE 0
                    END
                ), 2) AS precision
                FROM (
                    SELECT close
                    FROM ohlcv_bars
                    WHERE symbol = UPPER(?) AND timeframe = UPPER(?)
                      AND close IS NOT NULL
                    LIMIT 1000
                );
            """, [symbol, timeframe]).fetchone()
            if p_row and p_row[0] is not None:
                precision = int(p_row[0])
        except Exception:
            pass
        self._precision_cache[key] = precision
        return precision

    @staticmethod
    def _rows_to_candles(rows) -> List[Dict[str, Any]]:
        """Wandelt DuckDB-Zeilen in Candle-Dicts um (DRY fuer beide Queries)."""
        candles: List[Dict[str, Any]] = []
        for r in rows:
            t_epoch = int(r[0])  # Bereits epoch-Integer aus DuckDB
            # P16.05 VWMA-Fix (P-D4): tick_volume wird mitgeliefert.
            # Entscheidung F3: NaN/None -> 0, Candle bleibt gueltig
            # (kein WHERE-Filter auf tick_volume, damit Candles mit
            # NULL-Volumen nicht wegfallen).
            vol_raw = r[5]
            candles.append({
                "time": t_epoch,
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "tick_volume": float(vol_raw) if vol_raw is not None else 0.0
            })
        return candles

    def fetch_candles_in_range(self, symbol: str, timeframe: str,
                               from_epoch: int, to_epoch: int,
                               limit: int = 50000) -> Tuple[List[Dict[str, Any]], int]:
        """Liest OHLCV-Kerzen im Zeitraum [from_epoch, to_epoch] (aufsteigend).

        P1#5 (Optimierung): Zeitraum-basiertes Laden statt 'immer letzte N'
        Kerzen – der Playground laedt damit exakt den gewaehlten Bereich
        (+ Margin im Controller), behebt das 'fast leere 30-Tage-Chart'
        (A4) und haelt den RAM-Cache anwendungsbezogen.

        Bei mehr Kerzen im Zeitraum als `limit` werden die NEUESTEN `limit`
        Kerzen geladen (ORDER BY time DESC LIMIT ? -> aufsteigend sortiert),
        damit der rechte (aktuellste) Rand immer sichtbar ist.

        Args:
            symbol/timeframe: Paar (Case-insensitiv, Daten sind UPPER).
            from_epoch/to_epoch: Wanduhr-Epoch-Grenzen (inkl.).
            limit: Obergrenze der geladenen Kerzen.

        Returns:
            (candles, precision) – Candles aufsteigend nach time sortiert.
        """
        candles: List[Dict[str, Any]] = []
        precision: int = 2

        if not os.path.exists(self.db_path):
            return candles, precision

        for attempt in range(3):
            try:
                con = db_connect(self.db_path)
                precision = self._get_precision_cached(con, symbol, timeframe)

                query = """
                    SELECT EXTRACT('epoch' FROM "time")::BIGINT AS time_epoch,
                           open, high, low, close, tick_volume
                    FROM (
                        SELECT "time", open, high, low, close, tick_volume
                        FROM ohlcv_bars
                        WHERE symbol = UPPER(?) AND timeframe = UPPER(?)
                          AND "time" >= to_timestamp(?)
                          AND "time" <= to_timestamp(?)
                          AND "time" IS NOT NULL
                          AND open IS NOT NULL
                          AND high IS NOT NULL
                          AND low IS NOT NULL
                          AND close IS NOT NULL
                        ORDER BY "time" DESC
                        LIMIT ?
                    )
                    ORDER BY "time" ASC;
                """
                rows = con.execute(
                    query, [symbol, timeframe, int(from_epoch),
                            int(to_epoch), int(limit)]).fetchall()
                con.close()
                candles = self._rows_to_candles(rows)
                break

            except Exception as e:
                if attempt == 2:
                    print(f"❌ [Repository Error] Fehler beim Laden von "
                          f"{symbol} {timeframe}: {e}")
                else:
                    time.sleep(0.1)

        return candles, precision

    def fetch_historical_candles(self, symbol: str, timeframe: str, limit: int = 3000,
                                 before_epoch: Optional[int] = None) -> Tuple[List[Dict[str, Any]], int]:
        """Liest OHLCV-Kerzen aus der Marktdatenbank (aufsteigend sortiert).

        Phase 16.07 (Two-Tier Caching, D4): Additiver Parameter `before_epoch`.
        Ist er gesetzt, werden ausschliesslich KERZEN GELADEN, DIE ÄLTER ALS
        diese Wanduhr-Epoch sind (WHERE "time" < to_timestamp(?)) – das
        Chunk-Nachladen des `ChartDataBuffer` (Tier 2 -> DuckDB) nutzt genau
        diesen Pfad, um den naechsten Block alter Geschichte vorzuladen.
        Ohne `before_epoch` ist das Verhalten unveraendert (letzte `limit`
        Kerzen, Abwaertskompatibilitaet).
        """
        candles: List[Dict[str, Any]] = []
        precision: int = 2

        if not os.path.exists(self.db_path):
            return candles, precision

        for attempt in range(3):
            try:
                con = db_connect(self.db_path)
                # P1#6: Precision 1x pro Paar (statt eigener Query pro Load).
                precision = self._get_precision_cached(con, symbol, timeframe)

                # Phase 16.07: before_epoch filtert additiv auf ältere Kerzen
                # (Wanduhr-Epoch; "time" ist TIMESTAMPTZ, daher to_timestamp-
                # Vergleich). Die WHERE-Bedingung wird nur bei gesetztem
                # before_epoch ergänzt (Abwaertskompatibilität).
                older_filter = ""
                params: List[Any] = [symbol, timeframe]
                if before_epoch is not None:
                    older_filter = ' AND "time" < to_timestamp(?)'
                    params.append(int(before_epoch))
                params.append(limit)

                query = """
                    SELECT EXTRACT('epoch' FROM "time")::BIGINT AS time_epoch,
                           open, high, low, close, tick_volume
                    FROM (
                        SELECT "time", open, high, low, close, tick_volume
                        FROM ohlcv_bars
                        WHERE symbol = UPPER(?) AND timeframe = UPPER(?)
                          AND "time" IS NOT NULL
                          AND open IS NOT NULL
                          AND high IS NOT NULL
                          AND low IS NOT NULL
                          AND close IS NOT NULL
                        """ + older_filter + """
                        ORDER BY "time" DESC
                        LIMIT ?
                    )
                    ORDER BY "time" ASC;
                """
                rows = con.execute(query, params).fetchall()
                con.close()
                candles = self._rows_to_candles(rows)
                break

            except Exception as e:
                if attempt == 2:
                    print(f"❌ [Repository Error] Fehler beim Laden von {symbol} {timeframe}: {e}")
                else:
                    time.sleep(0.1)

        return candles, precision

    def get_all_stored_symbol_tf_pairs(self) -> Set[Tuple[str, str]]:
        """Liefert alle (symbol, timeframe)-Paare, für die bereits Daten in ohlcv_bars existieren.

        Phase 21.03.22 (Full Market-Data Sync Button): Grundlage fuer den
        manuellen Sync aller lokal gespeicherten Paare im ServiceWindow.
        UPPER-normalisiert (DISTINCT); Muster: DbPool.get (Thread-local,
        kein manuelles close(), konsistent mit get_symbol_precision).
        """
        try:
            con = DbPool.get(self.db_path)
            rows = con.execute("""
                SELECT DISTINCT UPPER(symbol), UPPER(timeframe)
                FROM ohlcv_bars
                WHERE symbol IS NOT NULL AND timeframe IS NOT NULL
            """).fetchall()
            return {(str(r[0]), str(r[1])) for r in rows if r[0] and r[1]}
        except Exception as e:
            print(f"WARN [MarketDataRepository] Pair-Abfrage fehlgeschlagen: {e}")
            return set()
