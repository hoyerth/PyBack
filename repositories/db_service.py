# db_service.py
"""
db_service.py - FASSADE (Re-Export-Wrapper), seit 18.01.02 (E2/E3).

Diese Datei enthaelt KEINE Logik mehr – die Implementierung wurde im Rahmen
von 18.01.02 (Refactoring & Modularisierung) auf folgende Module aufgeteilt:

  * db/db_pool.py                    – DATA_DIR, DB_*-Pfad-Konstanten, DbPool,
                                       _LockedConnection, db_connect, with_db_lock
  * db/db_utils.py                   – _parse_json_field, _ensure_epoch
  * db/schema_initializer.py         – check_and_init_databases
  * data_sync/mt5_sync_service.py    – SYMBOLS, _TIMEFRAMES_CACHE, get_timeframes,
                                       TF_SECONDS_MAP, MT5_LOCK, check_mt5_connection,
                                       get_latest_timestamp, sync_market_data
  * repositories/market_data_repository.py – MarketDataRepository, get_symbol_precision

Die Bestands-Caller (20 Dateien, u. a. chart_win, state_manager, symbol_repository,
analytics_profile_repository, service_selector_model) importieren weiterhin
unveraendert aus `db_service` (E2). Der CLI-Einstieg `python db_service.py`
(fuehrt den MT5-Sync aus) bleibt erhalten.
"""

from db.db_pool import (
    DATA_DIR,
    DB_ANALYTICS,
    DB_APP_DATA,
    DB_MARKET_DATA,
    DbPool,
    _LockedConnection,
    db_connect,
    with_db_lock,
)
from db.db_utils import _ensure_epoch, _parse_json_field
from db.schema_initializer import check_and_init_databases
from data_sync.mt5_sync_service import (
    SYMBOLS,
    TF_SECONDS_MAP,
    MT5_LOCK,
    check_mt5_connection,
    get_latest_timestamp,
    get_timeframes,
    sync_market_data,
)
from repositories.market_data_repository import (
    MarketDataRepository,
    get_symbol_precision,
)


def main() -> None:
    """CLI-Einstieg (Kompatibilitaet): fuehrt den MT5-Sync aus."""
    sync_market_data()


if __name__ == "__main__":
    main()
