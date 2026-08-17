# db/__init__.py
"""
db-Paket (18.01.02, E3): Datenbank-Basisschicht.

  * db_pool.py            – DbPool, db_connect, _LockedConnection, with_db_lock, DB-Pfade
  * db_utils.py           – _parse_json_field, _ensure_epoch
  * schema_initializer.py – check_and_init_databases

Kein Modul dieses Pakets importiert main.py oder db_service.py (E4).
"""
