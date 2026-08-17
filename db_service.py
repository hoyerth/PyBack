# db_service.py
"""
db_service.py - KOMPATIBILITAETS-SHIM (seit 23.02).

Diese Root-Datei ist seit Schritt 23.02 (Root-Orphans sortieren) ein reiner
Re-Export-Shim. Die Fassade selbst (Logik-freier Re-Export-Wrapper, seit
18.01.02/E2/E3) liegt jetzt unter `repositories/db_service.py`.

  * repositories/db_service.py  – Fassade (Re-Export aus db/, data_sync/,
                                   repositories/market_data_repository.py)
  * db_service.py (Root)        – Shim: importiert aus repositories.db_service,
                                   damit alle Bestands-Caller (20+ Dateien,
                                   u. a. chart_win, state_manager,
                                   symbol_repository, analytics_view_model,
                                   test/test.py Teil 30) unveraendert
                                   weiterarbeiten (E2).
  * CLI-Einstieg `python db_service.py` (MT5-Sync) bleibt erhalten.

Es ist KEINE Logik in diesem Shim – nur Re-Export + CLI-Durchreichung.
"""

from repositories.db_service import (  # noqa: F401
    DATA_DIR,
    DB_ANALYTICS,
    DB_APP_DATA,
    DB_MARKET_DATA,
    DbPool,
    _LockedConnection,
    db_connect,
    with_db_lock,
    _ensure_epoch,
    _parse_json_field,
    check_and_init_databases,
    SYMBOLS,
    TF_SECONDS_MAP,
    MT5_LOCK,
    check_mt5_connection,
    get_latest_timestamp,
    get_timeframes,
    sync_market_data,
    MarketDataRepository,
    get_symbol_precision,
)
from repositories.db_service import main


if __name__ == "__main__":
    main()
