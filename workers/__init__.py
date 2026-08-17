# workers/__init__.py
"""
workers-Paket (18.01.02, E7): Qt-Hintergrund-Threads.

  * data_sync_worker.py – DataSyncWorker (MT5-Historie-Sync)
  * live_tick_worker.py – LiveTickWorker (Tick-Polling + Bar-Close-Persistenz)

Kein Import von main.py (E4); UI-Logik ist verboten (SRP).
"""
