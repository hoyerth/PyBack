# window_state_repository.py
"""
window_state_repository.py - Zentraler Repository-Zugriff auf die
instanz-/fensterbezogenen Tabellen der app_data.duckdb (Phase 15.04).

Kapselt die SQL-Zugriffe auf `window_instances`, `instance_states` UND
`symbol_tf_states` (alle instanz-/fensterbezogenen Tabellen – nicht nur die
zwei im Ursprungsentwurf genannten). Das Repository ist REIN lesend/
schreibend: Die Schema-Anlage und -Migration (DDL in `StateManager._init_db()`)
verbleibt im StateManager (keine Verantwortungs-Verdopplung).

Nutzt zwingend `DbPool.get(db_path)` (Thread-local, lock-frei, wie
StateManager) – KEINE eigene Connection-Verwaltung. Der DB-Pfad wird vom
StateManager (bzw. Test-Aufrufer) aufgelöst und an das Repository
durchgereicht, damit Test-Isolation (Temp-DBs unter test/) und die laufende
App strikt getrennt bleiben.

Phase 15.04: `state_manager.py` ist seitdem eine additive Fassade – alle
Bestands-Methoden delegieren intern an dieses Repository (identische
Signaturen, keine Aufrufer-Aenderung in main.py, persistent_win.py,
chart_win.py, service_win.py, Analytics, Tests).
"""

import json
import os
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from db.db_pool import DbPool
from db.db_utils import _parse_json_field

# Seit 23.02 (Root-Orphans sortieren) liegt dieses Repository unter
# repositories/ – der Projekt-Root ist daher eine Ebene hoeher.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DB_PATH = os.path.join(BASE_DIR, "data", "app_data.duckdb")


class WindowStateRepository:
    """Kapselt die SQL-Zugriffe auf die instanz-/fensterbezogenen Tabellen.

    Methoden (Phase 15.04, Bestandsverhalten EXAKT reproduziert):
        save_window_geometry / get_window_geometry  (window_instances)
        save_instance_state / load_all_instances    (instance_states, join)
        delete_instance                             (beide Tabellen)
        get_next_instance_id                        (window_instances)
        save_symbol_tf_state / get_symbol_tf_state /
        delete_symbol_tf_state                      (symbol_tf_states)

    Das Repository fuehrt KEINE Schema-Anlage/-Migration durch – die
    Tabellenstruktur wird ausschliesslich vom StateManager (`_init_db()`)
    verwaltet (Invariante: keine Verantwortungs-Verdopplung).
    """

    def __init__(self, db_path: str = APP_DB_PATH) -> None:
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Connection (Thread-local DbPool, lock-frei)
    # ------------------------------------------------------------------
    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        return DbPool.get(self.db_path)

    # ------------------------------------------------------------------
    # window_instances: Geometrie
    # ------------------------------------------------------------------
    def save_window_geometry(
        self,
        instance_id: str,
        x: int,
        y: int,
        width: int,
        height: int,
        is_maximized: bool,
        preset_id: Optional[str] = None
    ) -> None:
        con = self._get_connection()
        con.execute("""
            INSERT INTO window_instances (
                instance_id, preset_id, window_title, pos_x, pos_y, width, height, is_maximized
            ) VALUES (?, ?, 'PyTrader Window', ?, ?, ?, ?, ?)
            ON CONFLICT (instance_id) DO UPDATE SET
                pos_x = EXCLUDED.pos_x,
                pos_y = EXCLUDED.pos_y,
                width = EXCLUDED.width,
                height = EXCLUDED.height,
                is_maximized = EXCLUDED.is_maximized,
                preset_id = EXCLUDED.preset_id;
        """, [instance_id, preset_id, x, y, width, height, is_maximized])

    def get_window_geometry(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Liest die gespeicherte Fenstergeometrie einer spezifischen Instanz aus."""
        con = self._get_connection()
        res = con.execute("""
            SELECT pos_x, pos_y, width, height, is_maximized
            FROM window_instances
            WHERE instance_id = ?
        """, [instance_id]).fetchone()
        if res and res[0] is not None:
            return {
                "pos_x": res[0],
                "pos_y": res[1],
                "width": res[2],
                "height": res[3],
                "is_maximized": bool(res[4])
            }
        return None

    def get_next_instance_id(self) -> str:
        con = self._get_connection()
        res = con.execute("SELECT instance_id FROM window_instances").fetchall()
        existing_ids = [r[0] for r in res]
        count = 1
        while f"win_{count}" in existing_ids:
            count += 1
        return f"win_{count}"

    # ------------------------------------------------------------------
    # instance_states: Instanz-Zustand (Symbol/Timeframe/Viewport/Indikatoren)
    # ------------------------------------------------------------------
    def save_instance_state(
        self,
        instance_id: str,
        symbol: str,
        timeframe: str,
        visible_range_from: Optional[int] = None,
        visible_range_to: Optional[int] = None,
        visible_price_from: Optional[float] = None,
        visible_price_to: Optional[float] = None,
        indicators_state: Optional[Dict[str, Any]] = None,
        measurement_state: Optional[Dict[str, Any]] = None
    ) -> None:
        con = self._get_connection()
        ind_json = json.dumps(indicators_state) if indicators_state is not None else None
        meas_json = json.dumps(measurement_state) if measurement_state is not None else None
        con.execute("""
            INSERT INTO instance_states (
                instance_id, symbol, timeframe, visible_range_from, visible_range_to,
                visible_price_from, visible_price_to, indicators_state, measurement_state, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (instance_id) DO UPDATE SET
                symbol = EXCLUDED.symbol,
                timeframe = EXCLUDED.timeframe,
                visible_range_from = EXCLUDED.visible_range_from,
                visible_range_to = EXCLUDED.visible_range_to,
                visible_price_from = EXCLUDED.visible_price_from,
                visible_price_to = EXCLUDED.visible_price_to,
                indicators_state = EXCLUDED.indicators_state,
                measurement_state = EXCLUDED.measurement_state,
                updated_at = EXCLUDED.updated_at;
        """, [
            instance_id, symbol, timeframe, visible_range_from, visible_range_to,
            visible_price_from, visible_price_to, ind_json, meas_json
        ])

    def load_all_instances(self) -> List[Dict[str, Any]]:
        """Reproduziert das Bestandsverhalten EXAKT (pandas-`.df()`-Leseart +
        String-Normalisierung von symbol/timeframe)."""
        con = self._get_connection()
        query = """
            SELECT
                w.instance_id, w.preset_id, w.pos_x, w.pos_y, w.width, w.height, w.is_maximized,
                CAST(s.symbol AS VARCHAR) AS symbol,
                CAST(s.timeframe AS VARCHAR) AS timeframe,
                s.visible_range_from, s.visible_range_to,
                s.visible_price_from, s.visible_price_to, s.indicators_state, s.measurement_state,
                s.updated_at
            FROM window_instances w
            LEFT JOIN instance_states s ON w.instance_id = s.instance_id
            ORDER BY s.updated_at ASC;
        """
        df = con.execute(query).df()
        records = df.to_dict(orient="records")
        for rec in records:
            if "symbol" in rec and rec["symbol"] is not None and not isinstance(rec["symbol"], str):
                rec["symbol"] = str(rec["symbol"]) if not pd.isna(rec["symbol"]) else None
            if "timeframe" in rec and rec["timeframe"] is not None and not isinstance(rec["timeframe"], str):
                rec["timeframe"] = str(rec["timeframe"]) if not pd.isna(rec["timeframe"]) else None
        return records

    def delete_instance(self, instance_id: str) -> None:
        con = self._get_connection()
        con.execute("DELETE FROM instance_states WHERE instance_id = ?", [instance_id])
        con.execute("DELETE FROM window_instances WHERE instance_id = ?", [instance_id])

    # ------------------------------------------------------------------
    # symbol_tf_states: Symbol-/Timeframe-Zustand (Viewport/Indikatoren)
    # ------------------------------------------------------------------
    def save_symbol_tf_state(
        self,
        symbol: str,
        timeframe: str,
        visible_range_from: Optional[int] = None,
        visible_range_to: Optional[int] = None,
        visible_price_from: Optional[float] = None,
        visible_price_to: Optional[float] = None,
        indicators_state: Optional[Dict[str, Any]] = None,
        measurement_state: Optional[Dict[str, Any]] = None
    ) -> None:
        con = self._get_connection()
        ind_json = json.dumps(indicators_state) if indicators_state is not None else None
        meas_json = json.dumps(measurement_state) if measurement_state is not None else None
        con.execute("""
            INSERT INTO symbol_tf_states (
                symbol, timeframe, visible_range_from, visible_range_to,
                visible_price_from, visible_price_to, indicators_state, measurement_state, updated_at
            ) VALUES (CAST(? AS VARCHAR), CAST(? AS VARCHAR), ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, timeframe) DO UPDATE SET
                visible_range_from = EXCLUDED.visible_range_from,
                visible_range_to = EXCLUDED.visible_range_to,
                visible_price_from = EXCLUDED.visible_price_from,
                visible_price_to = EXCLUDED.visible_price_to,
                indicators_state = EXCLUDED.indicators_state,
                measurement_state = EXCLUDED.measurement_state,
                updated_at = EXCLUDED.updated_at;
        """, [
            symbol, timeframe, visible_range_from, visible_range_to,
            visible_price_from, visible_price_to, ind_json, meas_json
        ])

    def get_symbol_tf_state(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        con = self._get_connection()
        res = con.execute("""
            SELECT visible_range_from, visible_range_to, visible_price_from, visible_price_to, indicators_state, measurement_state
            FROM symbol_tf_states
            WHERE symbol = CAST(? AS VARCHAR) AND timeframe = CAST(? AS VARCHAR)
        """, [symbol, timeframe]).fetchone()

        if res:
            v_from, v_to, p_from, p_to, ind_json, meas_json = res
            ind_state = _parse_json_field(ind_json)
            meas_state = _parse_json_field(meas_json)
            return {
                "visible_range_from": v_from,
                "visible_range_to": v_to,
                "visible_price_from": p_from,
                "visible_price_to": p_to,
                "indicators_state": ind_state,
                "measurement_state": meas_state
            }
        return None

    def delete_symbol_tf_state(self, symbol: str, timeframe: str) -> None:
        con = self._get_connection()
        con.execute(
            "DELETE FROM symbol_tf_states WHERE symbol = CAST(? AS VARCHAR) AND timeframe = CAST(? AS VARCHAR)",
            [symbol, timeframe]
        )

    # ------------------------------------------------------------------
    # workspace_state: Fenster-Workspace (Phase 20.01, win_analytics)
    # ------------------------------------------------------------------
    def save_workspace_state(
        self, instance_id: str, state: Dict[str, Any]
    ) -> None:
        """Upsertet `workspace_state` (JSON) auf die instance_states-Zeile.

        Phase 20.01 (E6, NOT-NULL-konform): `instance_states.symbol`/
        `timeframe` sind NOT NULL. Fuer eine noch nicht existierende Row
        werden die bestehenden Werte uebernommen (Fallback ''/'M1'), damit
        der reine Workspace-Upsert keinen NOT-NULL-Constraint verletzt.
        Vorhandene symbol/timeframe/indicator-Zustaende bleiben unberuehrt
        (ON CONFLICT aktualisiert nur workspace_state + updated_at).
        """
        con = self._get_connection()
        existing = con.execute(
            "SELECT symbol, timeframe FROM instance_states "
            "WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
        symbol = str(existing[0]) if existing and existing[0] is not None else ""
        timeframe = (
            str(existing[1]) if existing and existing[1] is not None else "M1"
        )
        con.execute("""
            INSERT INTO instance_states (
                instance_id, symbol, timeframe, workspace_state, updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (instance_id) DO UPDATE SET
                workspace_state = EXCLUDED.workspace_state,
                updated_at = EXCLUDED.updated_at;
        """, [instance_id, symbol, timeframe, json.dumps(state)])

    def get_workspace_state(
        self, instance_id: str
    ) -> Optional[Dict[str, Any]]:
        """Liest den gespeicherten Fenster-Workspace einer Instanz (oder None).

        Workspace-Payload (Phase 20.01, E7):
            {"params": {...}, "layout": {"page_index": n}}
        """
        con = self._get_connection()
        row = con.execute(
            "SELECT workspace_state FROM instance_states "
            "WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
        if row and row[0] is not None:
            parsed = _parse_json_field(row[0])
            return parsed if isinstance(parsed, dict) else None
        return None
