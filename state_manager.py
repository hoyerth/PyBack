# state_manager.py

"""
state_manager.py - Persistence Manager with Symbol/TF Reset Support, Robust Schema Migration & Type Validation
"""

import json
import os
from typing import Any, Dict, List, Optional
import duckdb

from db_service import _parse_json_field, DbPool
from config.base_state_model import AbstractStateModel
from config.app_settings import AppSettings
# Phase 15.04: Instanz-/Fenster-SQL-Zugriffe sind in das
# WindowStateRepository ausgelagert (repositories/window_state_repository.py,
# seit 23.02 unter repositories/). Der StateManager ist seitdem eine additive
# Fassade – alle Bestands-Methoden bleiben mit identischen Signaturen erhalten
# und delegieren intern.
from repositories.window_state_repository import WindowStateRepository

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DB_PATH = os.path.join(BASE_DIR, "data", "app_data.duckdb")

KEY_APP_SETTINGS = "app_settings"


class StateManager:

    def __init__(self, db_path: str = APP_DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()
        # Phase 15.04: Fassaden-Delegation an das WindowStateRepository.
        # Die DB-Pfad-Aufloesung verbleibt beim StateManager und wird an das
        # Repository durchgereicht (Test-Isolation: Temp-DBs bleiben getrennt).
        self._window_repo = WindowStateRepository(db_path)

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        return DbPool.get(self.db_path)

    def _init_db(self) -> None:
        """Initialisiert die Tabellenstrukturen und f\u00fchrt eine saubere Schema-Migration durch."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        con = self._get_connection()
        con.execute("""
            CREATE TABLE IF NOT EXISTS window_instances (
                instance_id VARCHAR PRIMARY KEY,
                preset_id VARCHAR,
                window_title VARCHAR,
                pos_x INTEGER,
                pos_y INTEGER,
                width INTEGER,
                height INTEGER,
                is_maximized BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS instance_states (
                instance_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                visible_range_from BIGINT,
                visible_range_to BIGINT,
                visible_price_from DOUBLE,
                visible_price_to DOUBLE,
                indicators_state JSON,
                measurement_state JSON,
                workspace_state JSON,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS symbol_tf_states (
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                visible_range_from BIGINT,
                visible_range_to BIGINT,
                visible_price_from DOUBLE,
                visible_price_to DOUBLE,
                indicators_state JSON,
                measurement_state JSON,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, timeframe)
            );

            CREATE TABLE IF NOT EXISTS indicator_presets (
                indicator_id VARCHAR NOT NULL,
                preset_name VARCHAR NOT NULL,
                params JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (indicator_id, preset_name)
            );

            CREATE TABLE IF NOT EXISTS global_settings (
                key VARCHAR PRIMARY KEY,
                value JSON NOT NULL
            );
        """)

        # Phase 12 (Hybrid-Schema): Additive Erweiterung der indicator_presets
        # um die Plugin-Verknuepfung. plugin_id verknuepft ein Preset mit einem
        # Plugin (z.B. 'srv_grid_lines'), version fuehrt die Plugin-Version und
        # is_active_batch markiert Presets, die von den Batch-Services
        # (HistoricalScanner/LiveAnalyzer) ueber den PluginExecutor aktiv
        # verarbeitet werden. Bestehende Presets und Daten bleiben unangetastet.
        con.execute("ALTER TABLE indicator_presets ADD COLUMN IF NOT EXISTS plugin_id VARCHAR;")
        con.execute("ALTER TABLE indicator_presets ADD COLUMN IF NOT EXISTS version VARCHAR DEFAULT '1.0.0';")
        con.execute("ALTER TABLE indicator_presets ADD COLUMN IF NOT EXISTS is_active_batch BOOLEAN DEFAULT FALSE;")
        # 20.04 (Q7, 09.08.2026): Doc-Log-Spalte fuer Presets/Clones –
        # Freitextfeld (Negativ-Wissen) analog ServiceInstanceConfig.doc_log.
        # Additiv/idempotent – bestehende Presets bleiben unangetastet.
        con.execute("ALTER TABLE indicator_presets ADD COLUMN IF NOT EXISTS doc_log VARCHAR;")
        
        # Phase 20.01 (09.08.2026): Analytics-Workspace-Persistenz – additive
        # JSON-Spalte `workspace_state` in instance_states (win_analytics:
        # vm.params + UI-Layout). Idempotent – bestehende Zeilen/Spalten
        # bleiben unangetastet.
        con.execute("ALTER TABLE instance_states ADD COLUMN IF NOT EXISTS workspace_state JSON;")

        # USER-REQ (17.08.2026): PyTrader-Legacy-Migrationen entfernt (Vorgabe 6):
        #   * Alt-Indikator 'grid' / 'grid_liquidity' (grid_liquidity ->
        #     ind_fixed_grid_proximity) – nicht mehr Teil von PyBack.
        #   * Phase-16.08.01-Plugin-ID-Rename auf service_sets/service_sets_trash/
        #     service_set_history/feature_store – Tabellen wurden entfernt
        #     (app_data = nur System-/App-Daten, analytics = nur Algos).

        # Explicit Column Check via information_schema
        tables_to_migrate = ["instance_states", "symbol_tf_states"]
        columns_to_check = ["indicators_state", "measurement_state"]

        for table in tables_to_migrate:
            existing_cols = con.execute(f"""
                SELECT LOWER(column_name)
                FROM information_schema.columns
                WHERE LOWER(table_name) = '{table.lower()}'
            """).fetchall()
            existing_col_names = [col[0] for col in existing_cols]

            for col_name in columns_to_check:
                if col_name.lower() not in existing_col_names:
                    try:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} JSON")
                        print(f"[MIGRATION] Spalte '{col_name}' (JSON) zur Tabelle '{table}' hinzugefuegt.")
                    except Exception as e:
                        print(f"[MIGRATION WARNUNG] Spalte '{col_name}' konnte nicht hinzugefuegt werden: {e}")

    def get_next_instance_id(self) -> str:
        # Phase 15.04: Delegation an das WindowStateRepository.
        return self._window_repo.get_next_instance_id()

    def delete_instance(self, instance_id: str) -> None:
        # Phase 15.04: Delegation an das WindowStateRepository.
        self._window_repo.delete_instance(instance_id)

    def delete_symbol_tf_state(self, symbol: str, timeframe: str) -> None:
        # Phase 15.04: Delegation an das WindowStateRepository.
        self._window_repo.delete_symbol_tf_state(symbol, timeframe)

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
        # Phase 15.04: Delegation an das WindowStateRepository.
        self._window_repo.save_instance_state(
            instance_id, symbol, timeframe,
            visible_range_from, visible_range_to,
            visible_price_from, visible_price_to,
            indicators_state, measurement_state,
        )

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
        # Phase 15.04: Delegation an das WindowStateRepository.
        self._window_repo.save_symbol_tf_state(
            symbol, timeframe,
            visible_range_from, visible_range_to,
            visible_price_from, visible_price_to,
            indicators_state, measurement_state,
        )

    def get_symbol_tf_state(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        # Phase 15.04: Delegation an das WindowStateRepository.
        return self._window_repo.get_symbol_tf_state(symbol, timeframe)

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
        # Phase 15.04: Delegation an das WindowStateRepository.
        self._window_repo.save_window_geometry(
            instance_id, x, y, width, height, is_maximized, preset_id,
        )

    def get_window_geometry(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Liest die gespeicherte Fenstergeometrie einer spezifischen Instanz aus.

        Phase 15.04: Delegation an das WindowStateRepository (Bestandsverhalten
        exakt reproduziert).
        """
        return self._window_repo.get_window_geometry(instance_id)

    # Phase 20.01: Workspace-Persistenz (win_analytics) – Fassaden-Delegation
    # an das WindowStateRepository (Muster 15.04, identische Signaturen).
    def save_workspace_state(
        self, instance_id: str, state: Dict[str, Any]
    ) -> None:
        """Persistiert einen Fenster-Workspace (E6, NOT-NULL-konform).

        `workspace_state` (JSON) wird auf die instance_states-Zeile der
        Instanz ge-upsertet; symbol/timeframe der Zeile bleiben erhalten
        (Fallback ''/'M1' bei noch nicht existierender Row).
        """
        self._window_repo.save_workspace_state(instance_id, state)

    def get_workspace_state(
        self, instance_id: str
    ) -> Optional[Dict[str, Any]]:
        """Liest den gespeicherten Fenster-Workspace (oder None)."""
        return self._window_repo.get_workspace_state(instance_id)

    def load_all_instances(self) -> List[Dict[str, Any]]:
        # Phase 15.04: Delegation an das WindowStateRepository (pandas-.df()-
        # Leseart + String-Normalisierung exakt wie im Bestand).
        return self._window_repo.load_all_instances()

    def get_indicator_preset(self, indicator_id: str, preset_name: str) -> Optional[Dict[str, Any]]:
        """Liest die Parametervalue eines Indikator-Presets (RÜCKWÄRTSKOMPATIBEL:
        gibt direkt das params-Dict zurück, wie vom bestehenden indicator_dialog erwartet)."""
        con = self._get_connection()
        res = con.execute(
            "SELECT params FROM indicator_presets WHERE indicator_id = ? AND preset_name = ?",
            [indicator_id, preset_name]
        ).fetchone()
        if res and res[0]:
            return _parse_json_field(res[0])
        return None

    def get_indicator_preset_meta(self, indicator_id: str, preset_name: str) -> Optional[Dict[str, Any]]:
        """Liest ein Indikator-Preset INKL. Plugin-Verknüpfung (Phase 12 Hybrid-Schema).
        Rückgabe: {"params": ..., "plugin_id": ..., "version": ..., "is_active_batch": ...}."""
        con = self._get_connection()
        res = con.execute(
            "SELECT params, plugin_id, version, is_active_batch FROM indicator_presets WHERE indicator_id = ? AND preset_name = ?",
            [indicator_id, preset_name]
        ).fetchone()
        if res and res[0]:
            params = _parse_json_field(res[0])
            data = {"params": params}
            # Neue Hybrid-Schema-Spalten (können NULL sein bei Alt-Presets)
            if len(res) > 1 and res[1] is not None:
                data["plugin_id"] = str(res[1])
            if len(res) > 2 and res[2] is not None:
                data["version"] = str(res[2])
            if len(res) > 3 and res[3] is not None:
                data["is_active_batch"] = bool(res[3])
            return data
        return None

    def save_indicator_preset(
        self,
        indicator_id: str,
        preset_name: str,
        params: Dict[str, Any],
        plugin_id: Optional[str] = None,
        version: Optional[str] = None,
        is_active_batch: bool = False,
        doc_log: Optional[str] = None,
    ) -> None:
        """Speichert ein Indikator-Preset. Unterstützt zusätzlich plugin_id,
        version und is_active_batch (Phase 12 Hybrid-Schema) sowie das
        Doc-Log (20.04, Q7)."""
        con = self._get_connection()
        con.execute("""
            INSERT INTO indicator_presets (indicator_id, preset_name, params, plugin_id, version, is_active_batch, doc_log)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (indicator_id, preset_name) DO UPDATE SET
                params = EXCLUDED.params,
                plugin_id = EXCLUDED.plugin_id,
                version = EXCLUDED.version,
                is_active_batch = EXCLUDED.is_active_batch,
                doc_log = EXCLUDED.doc_log;
        """, [
            indicator_id, preset_name, json.dumps(params),
            plugin_id, version, bool(is_active_batch), doc_log,
        ])

    def delete_indicator_preset(self, indicator_id: str, preset_name: str) -> None:
        con = self._get_connection()
        con.execute(
            "DELETE FROM indicator_presets WHERE indicator_id = ? AND preset_name = ?",
            [indicator_id, preset_name]
        )

    def rename_indicator_preset(self, indicator_id: str, old_name: str,
                                new_name: str) -> None:
        """Benennt ein Indikator-/Plugin-Preset um (10.08.2026, Bugfix).

        'Variante umbenennen' im MasterTree-Kontextmenue (Clone/Preset):
        Der Primaerschluessel von indicator_presets ist (indicator_id,
        preset_name) – der Rename ist ein UPDATE des preset_name. Alle
        weiteren Spalten (params, plugin_id, version, is_active_batch,
        doc_log) bleiben unangetastet. Der Aufrufer muss zuvor auf
        Namenskollisionen pruefen (sonst Unique-Constraint-Fehler).
        """
        con = self._get_connection()
        con.execute(
            "UPDATE indicator_presets SET preset_name = ? "
            "WHERE indicator_id = ? AND preset_name = ?",
            [new_name, indicator_id, old_name]
        )

    def list_plugin_presets(self, plugin_id: str) -> List[Dict[str, Any]]:
        """Liefert alle Presets eines Plugins (20.04, Q7).

        Quelle: indicator_presets (Spalte plugin_id, Phase-12-Hybrid-Schema).
        Rueckgabe pro Eintrag: {"indicator_id", "preset_name", "params",
        "plugin_id", "version", "is_active_batch", "doc_log"} –
        deterministisch (preset_name ASC). Grundlage der Parent-Child-Clone-
        Ansicht im MasterTree (Services-Gruppe: Plugin -> Presets/Clones)
        und der Archiv-Logik (Q7: Archivierung eines Presets =>
        is_active_batch = False; die Scans isolieren is_active_batch=False-
        Presets).
        """
        con = self._get_connection()
        res = con.execute("""
            SELECT indicator_id, preset_name, params, plugin_id, version,
                   is_active_batch, doc_log
            FROM indicator_presets
            WHERE plugin_id = ?
            ORDER BY preset_name ASC
        """, [plugin_id]).fetchall()
        presets: List[Dict[str, Any]] = []
        for indicator_id, preset_name, params_json, pid, version, is_active, doc_log in res:
            presets.append({
                "indicator_id": indicator_id,
                "preset_name": preset_name,
                "params": _parse_json_field(params_json) if params_json else {},
                "plugin_id": pid,
                "version": version,
                "is_active_batch": bool(is_active),
                "doc_log": str(doc_log) if doc_log else "",
            })
        return presets

    def set_plugin_preset_doc_log(self, indicator_id: str, preset_name: str,
                                  doc_log: str) -> None:
        """Persistiert das Doc-Log (Negativ-Wissen) eines Plugin-Presets.

        20.04 (Q7): analog ServiceInstanceConfig.doc_log – Freitextfeld,
        das im MasterTree-Clone-Tooltip angezeigt wird. Additiv: bestehende
        Presets ohne Eintrag bleiben unangetastet (doc_log = NULL).
        """
        con = self._get_connection()
        con.execute(
            "UPDATE indicator_presets SET doc_log = ? "
            "WHERE indicator_id = ? AND preset_name = ?",
            [(doc_log or "").strip() or None, indicator_id, preset_name])

    def list_indicator_presets(self, indicator_id: str) -> List[str]:
        con = self._get_connection()
        res = con.execute(
            "SELECT preset_name FROM indicator_presets WHERE indicator_id = ? ORDER BY preset_name ASC",
            [indicator_id]
        ).fetchall()
        presets = [r[0] for r in res]
        if "Default" not in presets:
            presets.insert(0, "Default")
        return presets

    def list_active_batch_presets(self) -> List[Dict[str, Any]]:
        """Liefert alle Batch-aktiven Plugin-Presets (Phase 12 Hybrid-Schema).

        Selektiert aus indicator_presets nur Presets mit is_active_batch = TRUE
        und gesetzter plugin_id. Diese steuern den Plugin-Modus der
        Batch-Services (HistoricalScanner / LiveAnalyzer) über den
        PluginExecutor – der Alt-Pfad bleibt davon unberührt.

        Rückgabe: Liste von {"indicator_id", "preset_name", "plugin_id",
        "version", "params"}.
        """
        con = self._get_connection()
        res = con.execute("""
            SELECT indicator_id, preset_name, params, plugin_id, version, is_active_batch
            FROM indicator_presets
            WHERE is_active_batch = TRUE AND plugin_id IS NOT NULL
            ORDER BY preset_name ASC
        """).fetchall()
        presets: List[Dict[str, Any]] = []
        for indicator_id, preset_name, params_json, plugin_id, version, is_active in res:
            presets.append({
                "indicator_id": indicator_id,
                "preset_name": preset_name,
                "plugin_id": plugin_id,
                "version": version,
                "params": _parse_json_field(params_json) if params_json else {},
            })
        return presets

    def get_app_settings(self) -> AppSettings:
        """L\u00e4dt AppSettings aus der DB oder gibt Defaults zur\u00fcck."""
        con = self._get_connection()
        row = con.execute(
            "SELECT value FROM global_settings WHERE key = ?",
            [KEY_APP_SETTINGS]
        ).fetchone()
        if row and row[0]:
            raw = row[0]
            data = _parse_json_field(raw)
            return AppSettings.from_dict(data)
        return AppSettings()

    def save_app_settings(self, settings: AppSettings) -> None:
        """Speichert AppSettings in der DB."""
        con = self._get_connection()
        con.execute("""
            INSERT INTO global_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, [KEY_APP_SETTINGS, json.dumps(settings.to_dict())])

    # =========================================================================
    # Generische global_settings-Zugriffe (17.01.04, Plugin-Parameter-Presets)
    # -------------------------------------------------------------------------
    # Speichert/liest beliebige JSON-Werte unter einem Key in global_settings.
    # Verwendet fuer die Standalone-Plugin-Parameter des ServiceWindows
    # (Key 'plugin_params_<plugin_id>'): Parameter + lookback + Beschreibung
    # eines Plugin ohne Set werden hier persistiert, damit die Parameter-Spalte
    # beim Klick auf eine Plugin-Zeile unter 'Services' die gespeicherten
    # Werte anzeigt und die Ausfuehrung sie nutzt.
    # =========================================================================
    def save_global_value(self, key: str, value: Any) -> None:
        """Speichert einen beliebigen JSON-faehigen Wert unter `key`."""
        con = self._get_connection()
        con.execute("""
            INSERT INTO global_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, [key, json.dumps(value)])

    def get_global_value(self, key: str, default: Any = None) -> Any:
        """Liest den unter `key` gespeicherten Wert (oder `default`)."""
        con = self._get_connection()
        row = con.execute(
            "SELECT value FROM global_settings WHERE key = ?", [key]
        ).fetchone()
        if row and row[0]:
            return _parse_json_field(row[0])
        return default

    # =========================================================================
    # Dialog-Geometrie (nicht-modale Dialoge, z. B. IndicatorSettingsDialog)
    # -------------------------------------------------------------------------
    # Speichert Position/Groesse eines nicht-modalen Dialogs in global_settings,
    # damit er beim erneuten Oeffnen an der letzten Position erscheint.
    # dialog_key: z. B. "indicator_settings" (gilt generisch fuer alle Indikatoren)
    # =========================================================================
    def save_dialog_geometry(self, dialog_key: str, x: int, y: int, width: int, height: int) -> None:
        con = self._get_connection()
        key = f"dialog_geometry_{dialog_key}"
        con.execute("""
            INSERT INTO global_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, [key, json.dumps({"pos_x": x, "pos_y": y, "width": width, "height": height})])

    def get_dialog_geometry(self, dialog_key: str) -> Optional[Dict[str, Any]]:
        """Liest die gespeicherte Dialog-Geometrie eines dialog_key aus (oder None)."""
        con = self._get_connection()
        key = f"dialog_geometry_{dialog_key}"
        row = con.execute("SELECT value FROM global_settings WHERE key = ?", [key]).fetchone()
        if row and row[0]:
            data = _parse_json_field(row[0])
            if isinstance(data, dict):
                return data
        return None

    # =========================================================================
    # Splitter-Persistenz (13.08.2026, Runde 3d): Grafikteiler Tree|Parameter
    # -------------------------------------------------------------------------
    # Speichert die zuletzt vom Anwender eingestellte Splitter-Position
    # (z. B. MasterTree | Parameter-Panel) unter global_settings
    # (Key 'splitter_<dialog_key>'). Sie wird beim erneuten Oeffnen des
    # Fensters/Dialogs wiederhergestellt (Gesamt-Historie) und zusaetzlich
    # ueber den Analytics-Workspace + Profil-Payload (service_picker_splitter)
    # persistiert. dialog_key: z. B. "service_selector" / "win_service".
    # =========================================================================
    def save_splitter_state(self, dialog_key: str, sizes) -> None:
        """Persistiert die Splitter-Position (Liste von Pixel-Breiten)."""
        con = self._get_connection()
        key = f"splitter_{dialog_key}"
        try:
            sizes = [int(x) for x in (sizes or [])]
        except (TypeError, ValueError):
            sizes = []
        con.execute("""
            INSERT INTO global_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, [key, json.dumps(sizes)])

    def get_splitter_state(self, dialog_key: str) -> Optional[List[int]]:
        """Liest die gespeicherte Splitter-Position eines dialog_key (oder None)."""
        con = self._get_connection()
        key = f"splitter_{dialog_key}"
        row = con.execute(
            "SELECT value FROM global_settings WHERE key = ?", [key]).fetchone()
        if row and row[0]:
            data = _parse_json_field(row[0])
            if isinstance(data, list):
                try:
                    return [int(x) for x in data]
                except (TypeError, ValueError):
                    return None
        return None
