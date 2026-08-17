# db/db_utils.py
"""
db/db_utils.py - Gemeinsame DB-Hilfsfunktionen.

Ausgelagert aus db_service.py im Rahmen von 18.01.02 (E3): `_parse_json_field`
(DuckDB liefert JSON teils als str, teils als dict) und `_ensure_epoch`
(DEPRECATED, backward-compat). Basis-Schicht (E4) – kein Projekt-Import.

Phase 21.02 (12.08.2026): DB-Bloat-Analyse & Maintenance (Kap. 21.02
AKTUELLE_UMSETZUNG):
  * get_db_fragmentation_info() – PRAGMA database_size (F1: res[2]/res[4])
  * execute_db_vacuum()         – CHECKPOINT gefolgt von VACUUM (App-Exit)
  * copy_database()             – COPY FROM DATABASE (echte Kompaktierung)
  * compact_database()          – Kompaktierung inkl. Datei-Ersatz
                                 (Windows File-Locking-sicher, F2)
"""

import calendar
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from db.db_pool import DbPool


def _ensure_epoch(val: Any) -> int:
    """DEPRECATED: Nutze stattdessen EXTRACT('epoch' FROM time)::BIGINT in SQL.
    Diese Funktion zerstört die Zeitzone bei TIMESTAMPTZ (timetuple() verliert offset).
    Nur noch für backward-compat in Test-Dateien."""
    if isinstance(val, datetime):
        return calendar.timegm(val.timetuple())
    return int(val)


def _parse_json_field(val: Any) -> Any:
    """Wandelt JSON aus DuckDB in Python-Objekt um (str->dict, dict bleibt)."""
    if isinstance(val, str):
        return json.loads(val) if val else None
    return val


# ---------------------------------------------------------------------------
# Phase 21.02 (12.08.2026): DB-Bloat-Analyse & Maintenance
# ---------------------------------------------------------------------------
def get_db_fragmentation_info(db_path: str) -> dict:
    """Liefert Fragmentierung/Bloat einer DuckDB-Datei (21.02, Schritt 1).

    Basis: `PRAGMA database_size` (DuckDB 1.5.5). Spaltenreihenfolge:
        0 database_name, 1 database_size (VARCHAR), 2 block_size,
        3 total_blocks, 4 used_blocks, 5 free_blocks, ...
    Bloat = Dateigröße - (used_blocks * block_size). Bei nicht vorhandener
    Datei oder Fehler werden Null-Werte geliefert (defensiv).

    Returns:
        {"pct": float, "bloat_mb": float, "size_mb": float}
    """
    if not os.path.exists(db_path):
        return {"pct": 0, "bloat_mb": 0, "size_mb": 0}
    file_bytes = os.path.getsize(db_path)
    try:
        con = DbPool.get(db_path)
        res = con.execute("PRAGMA database_size;").fetchone()
        # F1 (12.08.2026): korrekte Indizes res[2]/res[4] statt res[1]/res[3]
        block_size, used_blocks = (res[2], res[4]) if res else (262144, 0)
        netto_bytes = used_blocks * block_size
        bloat_bytes = max(0, file_bytes - netto_bytes)
        return {
            "pct": round((bloat_bytes / file_bytes * 100), 1) if file_bytes else 0,
            "bloat_mb": round(bloat_bytes / (1024 * 1024), 1),
            "size_mb": round(file_bytes / (1024 * 1024), 1)
        }
    except Exception:
        return {"pct": 0, "bloat_mb": 0, "size_mb": round(file_bytes / (1024 * 1024), 1)}


def execute_db_vacuum(db_path: str) -> None:
    """DB-Pflege beim App-Exit (21.02, Stufe 1): CHECKPOINT gefolgt von VACUUM.

    CHECKPOINT flusht die WAL in die Hauptdatei (konsistenter Zustand, kein
    WAL-Replay beim nächsten Start); VACUUM ist in DuckDB ohne
    Dateigrößen-Effekt (echte Kompaktierung siehe compact_database).
    """
    con = DbPool.get(db_path)
    con.execute("CHECKPOINT;")
    con.execute("VACUUM;")


def copy_database(src_db_path: str, dst_db_path: str) -> None:
    """Kompaktierung (21.02, Stufe 2): COPY FROM DATABASE in frische Datei.

    Erzeugt eine 100 % lückenlose Kopie inkl. Schema/Constraints/Indizes.
    Katalogname der Quelle = Datei-Basename ohne .duckdb (ggf. gequotet).
    dst_db_path sollte ein absoluter Pfad sein (BASE_DIR-basiert).
    """
    src_db_path = os.path.abspath(src_db_path)
    dst_db_path = os.path.abspath(dst_db_path)
    if not os.path.exists(src_db_path):
        raise FileNotFoundError(f"Quell-DB nicht gefunden: {src_db_path}")
    # Alte Ziel-Datei entfernen, falls vorhanden (sonst ATTACH auf bestehende Datei)
    if os.path.exists(dst_db_path):
        os.remove(dst_db_path)
    con = DbPool.get(src_db_path)
    src_catalog = Path(src_db_path).stem  # z. B. 'analytics'
    dst_sql = dst_db_path.replace("\\", "/")
    con.execute(f"ATTACH '{dst_sql}' AS new_db")
    con.execute(f'COPY FROM DATABASE "{src_catalog}" TO new_db')
    con.execute("DETACH new_db")


def compact_database(db_path: str) -> dict:
    """Kompaktiert eine DuckDB-Datei inkl. Datei-Ersatz (21.02, Stufe 2).

    Windows File-Locking: Vor dem Löschen/Umbenennen wird die DbPool-
    Verbindung des aktuellen Threads zur DB geschlossen (DbPool.release).
    Andere Threads (Scans/Worker) müssen beendet sein – der Aufrufer stellt
    das über den Concurrency-Guard sicher (_sync_pause_count == 0).

    Returns:
        dict von get_db_fragmentation_info() NACH der Kompaktierung.
    """
    db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        return {"pct": 0, "bloat_mb": 0, "size_mb": 0}
    tmp_path = f"{db_path}.compacted.duckdb"
    # 1) Kopieren (COPY FROM DATABASE)
    copy_database(db_path, tmp_path)
    # 2) Verbindung(en) des aktuellen Threads zur DB schliessen (File-Lock)
    DbPool.release(db_path)
    # 3) Alte Datei ersetzen
    os.remove(db_path)
    os.replace(tmp_path, db_path)
    return get_db_fragmentation_info(db_path)
