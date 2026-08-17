# repositories/__init__.py
"""
repositories-Paket (18.01.02, E3 / 23.02): Exklusive Lese-Repositories.

  * market_data_repository.py        – MarketDataRepository, get_symbol_precision
  * db_service.py                    – DbService-Fassade (Root-Shim re-exportiert)
  * symbol_repository.py             – SymbolRepository, get_symbol_repository
  * window_state_repository.py       – WindowStateRepository (Fenster-Persistenz)
  * analytics_profile_repository.py  – AnalyticsProfileRepository

Importiert nur die db-Basisschicht (E4); kein Import von main.py/db_service.py.
"""
