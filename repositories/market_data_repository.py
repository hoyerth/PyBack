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
                WHERE LOWER(symbol) = LOWER(?) AND LOWER(timeframe) = LOWER(?)
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

                precision_query = """
                    SELECT COALESCE(MAX(
                        CASE
                            WHEN POSITION('.' IN CAST(ROUND(close, 5) AS VARCHAR)) > 0
                            THEN LENGTH(RTRIM(CAST(ROUND(close, 5) AS VARCHAR), '0')) - POSITION('.' IN CAST(ROUND(close, 5) AS VARCHAR))
                            ELSE 0
                        END
                    ), 2) AS precision
                    FROM (
                        SELECT close
                        FROM ohlcv_bars
                        WHERE LOWER(symbol) = LOWER(?) AND LOWER(timeframe) = LOWER(?)
                          AND close IS NOT NULL
                        LIMIT 1000
                    );
                """
                p_row = con.execute(precision_query, [symbol, timeframe]).fetchone()
                if p_row and p_row[0] is not None:
                    precision = int(p_row[0])

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
                        WHERE LOWER(symbol) = LOWER(?) AND LOWER(timeframe) = LOWER(?)
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
