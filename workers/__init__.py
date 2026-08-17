# workers/__init__.py
"""
workers-Paket (PyBack): Qt-Hintergrund-Threads.

  * data_sync_worker.py    – DataSyncWorker (MT5-Historie-Sync, uebernommen)
  * playground_worker.py   – PlaygroundWorker (Phase 6: Algo-Berechnung im
                             QThread, damit das UI nicht einfriert)

Kein UI-Import (SRP); Kommunikation ueber Qt-Signale.
"""
