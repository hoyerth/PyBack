# db/db_pool.py
"""
db/db_pool.py - Thread-lokaler DuckDB-Verbindungspool & DB-Pfad-Konstanten.

Ausgelagert aus db_service.py im Rahmen von 18.01.02 (Refactoring &
Modularisierung, Pruefprotokoll-Entscheidung E3). Enthaelt:

  * `DATA_DIR` / `DB_MARKET_DATA` / `DB_ANALYTICS` / `DB_APP_DATA` (Pfad-Konstanten)
  * `DbPool` (Thread-local Singleton: eine Connection pro Thread & DB-Datei)
  * `_LockedConnection` (LEGACY-Wrapper) und `db_connect` (LEGACY)
  * `with_db_lock` (No-op Decorator, API-Kompatibilitaet)

Basis-Schicht (E4): kein Import anderer Projekt-Module.
"""

import datetime
import os
import shutil
import threading
from typing import Dict

import duckdb

# ==============================================================================
# CONFIGURATION & THREAD SAFETY
# ==============================================================================
DATA_DIR = "data"
DB_MARKET_DATA = os.path.join(DATA_DIR, "market_data.duckdb")
DB_ANALYTICS = os.path.join(DATA_DIR, "analytics.duckdb")
DB_APP_DATA = os.path.join(DATA_DIR, "app_data.duckdb")

# ==============================================================================
# DB LOCK-FREIER CONNECTION-HELPER
# ==============================================================================
# DuckDB unterstützt Multiple Connections innerhalb eines Prozesses nativ.
# Threading-Locks sind hier kontraproduktiv, da sie z. B. eine dauerhaft
# offene Haupt-Connection (self.db in MainWindow) blockieren.
# Cross-Prozess-Konflikte (IO Error: file is already open) werden durch
# sauberes Beenden vorheriger Prozesse gelöst, nicht durch threading.Lock.


def with_db_lock(db_path: str):
    """No-op decorator (Lock-frei). Beibehalten für API-Kompatibilität."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ==============================================================================
# DB CONNECTION POOL (Thread-local Singleton) – eine Connection pro Thread & DB
# ==============================================================================
# Loest drei Kernprobleme unter Windows:
#   1. "Can't open a connection with a different configuration" – immer gleiche Config
#   2. "Cannot open file – file used by another process" – keine Open/Close-Zyklen
#   3. DuckDB-Connections sind nicht thread-safe – eigenes Connection pro Thread
#
# Nutzung: DbPool.get(db_path) statt db_connect(db_path)
# Connections werden automatisch via atexit geschlossen.

_db_pool_lock = threading.Lock()
_db_pool_global: Dict[str, int] = {}  # abs_path -> Referenzzähler (fuer atexit)


class DbPool:
    """Thread-sicherer Connection-Pool: eine persistente Connection pro Thread & DB-Datei."""

    _local = threading.local()

    @staticmethod
    def _open_with_wal_recovery(abs_path: str) -> duckdb.DuckDBPyConnection:
        """Oeffnet eine DuckDB-Connection mit defensivem WAL-Recovery.

        Bugfix 09.08.2026 (wiederkehrender Start-Abbruch unter Windows):
        `duckdb.connect()` schlug mit "INTERNAL Error: Failure while replaying
        WAL file .../analytics.duckdb.wal: Calling DatabaseManager::
        GetDefaultDatabase with no default database set" fehl, wenn die WAL
        (z. B. durch hartes Beenden der App) korrupt war. Statt die gesamte
        App am Start scheitern zu lassen, wird die korrupte WAL-Datei unter
        `<db>.wal.corrupt_<YYYYMMDD_HHMMSS>` wegsichert und der Connect
        erneut versucht. Verloren gehen dabei nur un-checkpointete
        Transaktionen – die Haupt-DB (letzter Checkpoint) bleibt intakt.

        Raises:
            Exception: Wenn auch der zweite Versuch fehlschlaegt (kein
                WAL-Problem oder die DB selbst ist beschädigt).
        """
        try:
            return duckdb.connect(abs_path)
        except duckdb.InternalException as exc:
            if "Failure while replaying WAL" not in str(exc):
                raise
            wal_path = abs_path + ".wal"
            if os.path.exists(wal_path):
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                corrupt_path = f"{wal_path}.corrupt_{stamp}"
                try:
                    shutil.move(wal_path, corrupt_path)
                except OSError:
                    # Wegsichern fehlgeschlagen (z. B. Datei gesperrt) –
                    # dann wenigstens umbenennen, sonst Retry schlaegt erneut fehl.
                    try:
                        os.replace(wal_path, corrupt_path)
                    except OSError:
                        pass
            # Zweiter Versuch nach Entfernen der korrupten WAL.
            return duckdb.connect(abs_path)

    @staticmethod
    def get(db_path: str) -> duckdb.DuckDBPyConnection:
        """Gibt eine persistente Connection zur DB-Datei zurueck (eine pro Thread).
        Die Connection lebt bis Prozess-Ende und wird nie geschlossen."""
        abs_path = os.path.abspath(db_path)
        # Thread-local Storage: Jeder Thread hat seine eigenen Connections
        if not hasattr(DbPool._local, 'conns'):
            DbPool._local.conns = {}
        if abs_path not in DbPool._local.conns:
            DbPool._local.conns[abs_path] = DbPool._open_with_wal_recovery(abs_path)
            # Globalen Referenzzähler erhöhen (für atexit)
            with _db_pool_lock:
                if _db_pool_global.get(abs_path, 0) == 0:
                    import atexit
                    atexit.register(lambda p=abs_path: DbPool._close_all_for(p))
                _db_pool_global[abs_path] = _db_pool_global.get(abs_path, 0) + 1
        return DbPool._local.conns[abs_path]

    @staticmethod
    def _close_all_for(abs_path: str) -> None:
        """Schliesst ALLE Connections zu einer DB-Datei (fuer atexit)."""
        # Kann nur die Connections des aktuellen Threads schliessen
        if hasattr(DbPool._local, 'conns'):
            con = DbPool._local.conns.pop(abs_path, None)
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    @staticmethod
    def close_all() -> None:
        """Schliesst ALLE Connections des aktuellen Threads.

        Dekrementiert dabei den globalen Referenzzaehler, damit der
        atexit-Bookkeeping-Dict (Fix 15.03, Worker-Connection-Leak) nicht
        unbegrenzt waechst. Wird u. a. von AnalyticsAsyncWorker nach jeder
        Abfrage aufgerufen (Worker-Thread gibt seine Connection frei; ein
        neuer Worker-Thread erhaelt automatisch eine frische Connection).
        """
        if hasattr(DbPool._local, 'conns'):
            for abs_path in list(DbPool._local.conns.keys()):
                try:
                    DbPool._local.conns[abs_path].close()
                except Exception:
                    pass
                with _db_pool_lock:
                    _db_pool_global[abs_path] = max(
                        0, _db_pool_global.get(abs_path, 0) - 1)
            DbPool._local.conns = {}

    @staticmethod
    def release(abs_path: str) -> None:
        """Schliesst die Connection des aktuellen Threads zu EINER DB-Datei.

        Phase 21.02 (12.08.2026): Benoetigt fuer den Datei-Ersatz bei der
        Kompaktierung (Windows File-Locking). Anders als `close_all()`
        bleiben Connections zu anderen DB-Dateien (z. B. app_data.duckdb)
        unangetastet. Die Connection wird beim naechsten `DbPool.get()`
        lazy wieder geoeffnet (Referenzzaehler wird dekrementiert).
        """
        abs_path = os.path.abspath(abs_path)
        if hasattr(DbPool._local, 'conns'):
            con = DbPool._local.conns.pop(abs_path, None)
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
                with _db_pool_lock:
                    _db_pool_global[abs_path] = max(
                        0, _db_pool_global.get(abs_path, 0) - 1)


class _LockedConnection:
    """Wrapper um DuckDBPyConnection (LEGACY – nur noch fuer sync_market_data & MarketDataRepository).
    Oeffnet/schliesst die Connection bei jedem Aufruf.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self._con = con

    def __getattr__(self, name):
        return getattr(self._con, name)

    def close(self):
        try:
            self._con.close()
        finally:
            pass


def db_connect(db_path: str, read_only: bool = False) -> _LockedConnection:
    """LEGACY: Oeffnet eine neue Connection (wird geschlossen nach Gebrauch).

    Warnung: Nicht fuer haeufige Zugriffe verwenden!
    Nutze stattdessen: DbPool.get(db_path)
    """
    con = duckdb.connect(db_path, read_only=read_only)
    return _LockedConnection(con)
