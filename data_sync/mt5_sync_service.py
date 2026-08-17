# data_sync/mt5_sync_service.py
"""
data_sync/mt5_sync_service.py - MT5-Sync-Service (Zeitrahmen, Verbindung, Import).

Ausgelagert aus db_service.py im Rahmen von 18.01.02 (E3): SYMBOLS,
get_timeframes/TF_SECONDS_MAP/MT5_LOCK (MT5-Konfiguration),
check_mt5_connection (Verbindungspruefung), get_latest_timestamp und
sync_market_data (Voll-/Update-Import der MT5-Historie).

Lazy-Import-Prinzip (E3): `MetaTrader5` wird weiterhin erst beim Aufruf
importiert – kein MT5-DLL-Load beim Modul-Import.

Abhaengigkeiten (E4): db/db_pool (DB_MARKET_DATA, db_connect),
db/schema_initializer (check_and_init_databases).
"""

import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import duckdb
import pandas as pd

from db.db_pool import DB_MARKET_DATA, db_connect
from db.schema_initializer import check_and_init_databases

SYMBOLS = ["SILVER", "GOLD", "BTCUSD"]

# TIMEFRAMES als Lazy-Initialisierung (vermeidet MT5-DLL-Load beim Import)
_TIMEFRAMES_CACHE: Optional[Dict[str, int]] = None


def get_timeframes() -> Dict[str, int]:
    """Gibt das Timeframe-Mapping zurueck (lazy, importiert mt5 nur bei Bedarf)."""
    global _TIMEFRAMES_CACHE
    if _TIMEFRAMES_CACHE is None:
        import MetaTrader5 as _mt5
        _TIMEFRAMES_CACHE = {
            "MN1": _mt5.TIMEFRAME_MN1,
            "W1": _mt5.TIMEFRAME_W1,
            "D1": _mt5.TIMEFRAME_D1,
            "H4": _mt5.TIMEFRAME_H4,
            "H1": _mt5.TIMEFRAME_H1,
            "M30": _mt5.TIMEFRAME_M30,
            "M15": _mt5.TIMEFRAME_M15,
            "M10": _mt5.TIMEFRAME_M10,
            "M5": _mt5.TIMEFRAME_M5,
            "M2": _mt5.TIMEFRAME_M2,
            "M1": _mt5.TIMEFRAME_M1,
        }
    return _TIMEFRAMES_CACHE

TF_SECONDS_MAP: Dict[str, int] = {
    "M1": 60,
    "M2": 120,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}

MT5_LOCK = threading.Lock()


# ==============================================================================
# 2) MT5 VERBINDUNG PRÜFEN
# ==============================================================================
def check_mt5_connection() -> None:
    """Prüft die MT5-Verbindung mit abgesicherter Fehlerbehandlung (lazy mt5 import)."""
    import MetaTrader5 as _mt5

    print("\n🔗 [2/3] Prüfe MT5-Verbindung...")

    with MT5_LOCK:
        if not _mt5.initialize():
            error_code = _mt5.last_error()
            try:
                _mt5.shutdown()
            except Exception:
                pass
            raise SystemExit(
                f"❌ KRITISCHER FEHLER: MT5-Verbindung fehlgeschlagen!\n"
                f"   Fehlercode: {error_code}\n"
                f"   Bitte stelle sicher, dass das MT5 Terminal geöffnet und eingeloggt ist."
            )

        account = _mt5.account_info()
        if account is None:
            try:
                _mt5.shutdown()
            except Exception:
                pass
            raise SystemExit("❌ KRITISCHER FEHLER: Im MT5-Terminal ist kein Konto eingeloggt!")

        for symbol in SYMBOLS:
            if not _mt5.symbol_select(symbol, True):
                try:
                    _mt5.shutdown()
                except Exception:
                    pass
                raise SystemExit(f"❌ KRITISCHER FEHLER: Symbol '{symbol}' konnte im MT5 nicht aktiviert werden.")

    print(f"   ✅ Verbunden mit Broker: {account.company} (Server: {account.server}, Login: {account.login})")


# ==============================================================================
# 3 & 4) DATEN HILFSFUNKTIONEN & IMPORT-SCHLEIFE
# ==============================================================================
def get_latest_timestamp(con: duckdb.DuckDBPyConnection, symbol: str, timeframe_str: str) -> Optional[datetime]:
    """Liefert den neuesten Zeitstempel eines Symbol/Timeframe in ohlcv_bars."""
    res = con.execute("""
        SELECT MAX(time)
        FROM ohlcv_bars
        WHERE symbol = ? AND timeframe = ?
    """, [symbol, timeframe_str]).fetchone()

    return res[0] if res and res[0] is not None else None


def sync_market_data(target_pairs: Optional[Set[Tuple[str, str]]] = None) -> Set[Tuple[str, str]]:
    """Synchronisiert die MT5-Historie (VOLLIMPORT oder UPDATE) je Symbol/Timeframe.

    Phase 21.03.22 (Full Market-Data Sync Button): Optionaler `target_pairs`-
    Filter. Ist das Set uebergeben (nicht None), werden exakt diese
    (symbol, timeframe)-Paare synchronisiert (statt des Standard-Rasters);
    bei None/leer greift der Fallback auf das bisherige Standard-Raster
    (SYMBOLS x get_timeframes()). Der Delta-Sync-Abgleich via
    get_latest_timestamp() bleibt pro Paar voll erhalten.
    """
    import MetaTrader5 as _mt5
    timeframes = get_timeframes()

    check_and_init_databases()
    check_mt5_connection()

    # 21.03.22: Paar-Filter (UPPER-normalisiert, unbekannte Timeframes
    # werden ignoriert); None/leer -> Standard-Raster (Abwaertskompatibilitaet).
    filtered_pairs: Optional[Set[Tuple[str, str]]] = None
    symbols_to_sync: List[str] = list(SYMBOLS)
    if target_pairs:
        filtered_pairs = {
            (str(s).upper(), str(tf).upper())
            for s, tf in target_pairs
            if str(tf).upper() in timeframes
        }
        symbols_to_sync = sorted({s for s, _ in filtered_pairs})

    print(f"\n📥 [3/3] Starte Synchronisation für {', '.join(symbols_to_sync)} über {len(timeframes)} Timeframes...")

    start_time_total = time.perf_counter()
    total_bars_downloaded = 0
    updated_pairs: Set[Tuple[str, str]] = set()

    for symbol in symbols_to_sync:
        print(f"\n--- Synchronisiere {symbol} ---")
        # Connection pro Symbol öffnen/schließen, damit andere Threads (LiveTickWorker)
        # zwischendurch ebenfalls auf die DB zugreifen können
        con = db_connect(DB_MARKET_DATA)
        try:
            for tf_str, tf_mt5 in timeframes.items():
                # 21.03.22: Bei target_pairs-Filter exakt diese Paare abgleichen.
                if filtered_pairs is not None and (symbol, tf_str) not in filtered_pairs:
                    continue
                tf_start = time.perf_counter()

                last_time = get_latest_timestamp(con, symbol, tf_str)

                with MT5_LOCK:
                    if last_time is not None:
                        rates = _mt5.copy_rates_from_pos(symbol, tf_mt5, 0, 5_000)
                        update_type = "UPDATE"
                    else:
                        rates = _mt5.copy_rates_from_pos(symbol, tf_mt5, 0, 10_000_000)
                        update_type = "VOLLIMPORT"

                    if rates is None:
                        err = _mt5.last_error()
                        print(f"   [--] {tf_str:<4} | MT5 Fehler beim Abrufen der Kerzen: {err}")
                        continue

                if len(rates) == 0:
                    print(f"   [--] {tf_str:<4} | Keine Kerzen von MT5 empfangen.")
                    continue

                df = pd.DataFrame(rates)
                df["symbol"] = symbol
                df["timeframe"] = tf_str
                df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                df = df.drop_duplicates(subset=["time"], keep="last")

                df_to_insert = df[
                    [
                        "symbol",
                        "timeframe",
                        "time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "tick_volume",
                        "spread",
                        "real_volume",
                    ]
                ]

                con.register("df_temp", df_to_insert)

                # NATIVE UPSERT VIA PRIMÄRSCHLÜSSEL (symbol, timeframe, time)
                con.execute("""
                    INSERT OR REPLACE INTO ohlcv_bars (
                        symbol, timeframe, time, open, high, low, close, tick_volume, spread, real_volume
                    )
                    SELECT
                        symbol, timeframe, "time", open, high, low, close, tick_volume, spread, real_volume
                    FROM df_temp
                """)

                con.unregister("df_temp")

                tf_elapsed = time.perf_counter() - tf_start
                bars_count = len(df_to_insert)
                total_bars_downloaded += bars_count

                if bars_count > 0:
                    updated_pairs.add((symbol, tf_str))

                print(f"   [✅] {tf_str:<4} | {update_type:<10} | {bars_count:>8,} Kerzen verarbeitet in {tf_elapsed:.2f}s")
        finally:
            con.close()

    total_elapsed = time.perf_counter() - start_time_total

    print("\n" + "=" * 60)
    print("⏱️  ERGEBNIS & ZEITMESSUNG")
    print("=" * 60)
    print(f"Gesamtdauer Process:    {total_elapsed:.2f} Sekunden")
    print(f"Gesamtanzahl Kerzen:    {total_bars_downloaded:,}")
    print(f"Durchschnittliche Rate: {total_bars_downloaded / max(total_elapsed, 0.001):,.0f} Kerzen/Sekunde")
    print("=" * 60)
    return updated_pairs
