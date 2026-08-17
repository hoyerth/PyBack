# controllers/algo_playground_controller.py
"""
controllers/algo_playground_controller.py - Presenter fuer den Algo-Playground.

Phase 2: Koppelt die UI-Events der Algo-Liste an den AlgoPickerDialog und
haelt den Zustand (aktive Algos). Phase 3: Parameter-Form – Auswahl eines
Listeneintrags befuellt die ParamFormWidget aus dessen parameter_schema;
Aenderungen werden pro Instanz (instance_key) gespeichert. Phase 4: Canvas-
Basics – Daten-Cache (Konzept 2.4: einmalig pro Symbol/TF aus dem
MarketDataRepository laden), Zeitraum-Slice (client-seitig) und Candlestick-
Render in den QWebEngineView. Phase 6 erweitert: Overlays, Debounce, Worker.

Verantwortlich (SRP/IoC):
  * Registry einmalig laden (AlgoRegistry.discover_algos)
  * "+"-Event -> AlgoPickerDialog oeffnen, gewaehlte Algos zur Liste hinzufuegen
  * Selektion -> ParamFormWidget mit parameter_schema + gespeicherten Params
  * params_changed -> pro Instanz speichern (Phase 6: Debounce + Neuberechnung)
  * Kontextmenue "Entfernen" -> aktiven Zustand nachfuehren
  * Checkbox -> Signal (Phase 6 konsumiert: Overlay ein/aus)
  * Symbol/TF-Wechsel + Zeitraum -> Daten laden (Cache) + Canvas rendern

Der Controller kennt main_win NICHT als Modul – er erhaelt das View-Objekt
(duck-typed) ueber den Konstruktor.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from PySide6.QtCore import QObject, QUrl
from PySide6.QtWidgets import QDialog

from algos.algo_registry import AlgoRegistry
from repositories.market_data_repository import MarketDataRepository
from ui.algo_picker_dialog import AlgoPickerDialog
from ui.playground_chart_service import PlaygroundChartService

# Persistenz-Key (Anforderung 0, Phase 3): kompletter Playground-Zustand
# wird in global_settings abgelegt (save_global_value/get_global_value).
PLAYGROUND_STATE_KEY = "playground_state"


class AlgoPlaygroundController(QObject):
    """Presenter des Algo-Playgrounds (Phase 2-4: Liste, Dialog, Params, Canvas)."""

    # Maximale Kerzenzahl beim einmaligen Laden aus DuckDB (Konzept 2.4).
    CANDLE_LIMIT = 5000

    def __init__(self, view) -> None:
        super().__init__()
        self.ui = view
        self.registry: Dict[str, Dict[str, Any]] = AlgoRegistry.discover_algos()
        # Spiegel des aktiven Algo-Zustands (algo_ids, mit Duplikaten).
        self.active_algos: List[str] = []
        # Parameter je Instanz (instance_key -> params-dict, Phase 3).
        self.instance_params: Dict[str, Dict[str, Any]] = {}
        # Aktuell in der ParamFormWidget angezeigte Instanz (Phase 3).
        self.selected_instance: Optional[str] = None

        # Phase 4: Daten-Cache (Konzept 2.4) + Chart-Service + Repository.
        self._candles_cache: Optional[pd.DataFrame] = None
        self._last_pair: Optional[Tuple[str, str]] = None
        self._chart_service = PlaygroundChartService()
        self._data_repo = MarketDataRepository()

        self._init_bindings()

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------
    def _init_bindings(self) -> None:
        """Verbindet die Signale des Views mit diesem Controller.

        Defensiv (getattr): FakeViews in Tests duerfen Teilbereiche weglassen
        (z. B. keine symbol_combo/canvas) – die restlichen Bindings greifen.
        """
        self.ui.algo_panel.algo_add_requested.connect(self._on_add_requested)
        self.ui.algo_panel.algo_removed.connect(self._on_algo_removed)
        # Checkbox: in Phase 6 konsumiert (Overlay ein/aus) – hier nur Status.
        self.ui.algo_panel.algo_visibility_changed.connect(self._on_visibility_changed)
        self.ui.algo_panel.algo_selected.connect(self._on_algo_selected)
        # Parameter-Aenderungen (Phase 3): pro Instanz speichern.
        self.ui.param_form.params_changed.connect(self._on_params_changed)

        # Phase 4: Symbol/TF-Wechsel -> Cache neu laden + rendern.
        for attr in ("symbol_combo", "tf_combo"):
            combo = getattr(self.ui, attr, None)
            if combo is not None:
                combo.currentTextChanged.connect(self._on_symbol_tf_changed)
        # Phase 4: Zeitraum-Aenderung -> nur Slicen + rendern (kein Neuladen).
        tr = getattr(self.ui, "time_range", None)
        if tr is not None:
            tr.range_changed.connect(self._on_range_changed)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _on_add_requested(self) -> None:
        """'+' geklickt: AlgoPickerDialog oeffnen und Auswahl uebernehmen."""
        if not self.registry:
            self.ui.status_label.setText(
                "Status: keine Algos verfuegbar (algos/ ist leer)")
            return
        dlg = AlgoPickerDialog(self.registry, self.ui)
        if dlg.exec() == QDialog.Accepted:
            chosen = dlg.selected_algo_ids()
            new_instances = []
            for algo_id in chosen:
                meta = self.registry.get(algo_id, {})
                key = self.ui.algo_panel.add_algo(algo_id, meta.get("name"))
                new_instances.append((algo_id, key))
            self.active_algos = list(self.ui.algo_panel.algo_ids())
            # Neue Instanzen mit Schema-Defaults initialisieren.
            for algo_id, key in new_instances:
                self.instance_params[key] = self._schema_defaults(algo_id)
            self.ui.status_label.setText(
                f"Status: {len(chosen)} Algo(s) hinzugefuegt "
                f"(gesamt {len(self.active_algos)})")

    def _on_algo_removed(self, algo_id: str, instance_key: str) -> None:
        """Kontextmenue 'Entfernen': Zustand nachfuehren."""
        # Parameter der entfernten Instanz verwerfen.
        self.instance_params.pop(instance_key, None)
        if self.selected_instance == instance_key:
            self.selected_instance = None
            self.ui.param_form.set_schema({})
        self.active_algos = list(self.ui.algo_panel.algo_ids())
        self.ui.status_label.setText(
            f"Status: {algo_id} entfernt (aktiv: {len(self.active_algos)})")

    def _on_visibility_changed(self, algo_id: str, instance_key: str,
                               checked: bool) -> None:
        """Checkbox geaendert (Darstellung ein/aus) – Phase 6 rendert neu."""
        state = "ein" if checked else "aus"
        self.ui.status_label.setText(
            f"Status: {algo_id} Darstellung {state}")

    def _on_algo_selected(self, algo_id: str, instance_key: str) -> None:
        """Eintrag angeklickt: Parameter-Form mit Schema + Werten befuellen."""
        self.selected_instance = instance_key
        schema = self.registry.get(algo_id, {}).get("schema", {})
        self.ui.param_form.set_schema(schema)
        params = self.instance_params.get(instance_key)
        self.ui.param_form.set_params(params if params is not None else {})
        self.ui.status_label.setText(
            f"Status: Parameter fuer {algo_id} geladen")

    def _on_params_changed(self, params: Dict[str, Any]) -> None:
        """Parameter geaendert: pro Instanz speichern (Phase 6: Debounce)."""
        if self.selected_instance is None:
            return
        self.instance_params[self.selected_instance] = dict(params)
        self.ui.status_label.setText(
            "Status: Parameter geaendert (Phase 6: Neuberechnung)")

    # ------------------------------------------------------------------
    # Persistenz (Anforderung 0: alle aktuellen Werte im Fenster)
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        """Persistiert den kompletten Playground-Zustand (Anforderung 0).

        Gespeichert werden: Algo-Liste (algo_id, instance_key, Checkbox),
        Parameter je Instanz, selektierte Instanz, Zeitraum (Von/Bis) und
        beide Splitter-Positionen (horizontal + vertikal im linken Panel).
        Abgelegt unter global_settings/playground_state (StateManager).
        """
        panel = self.ui.algo_panel
        checked = panel.checked_states()
        algos = [
            {
                "algo_id": panel.algo_ids()[i],
                "instance_key": key,
                "checked": checked.get(key, True),
            }
            for i, key in enumerate(panel.instance_keys())
        ]
        from_epoch, to_epoch = self.ui.time_range.get_range()
        state = {
            "version": 1,
            "algos": algos,
            "params": {key: dict(params)
                       for key, params in self.instance_params.items()},
            "selected_instance": self.selected_instance,
            "time_range": [int(from_epoch), int(to_epoch)],
            "splitter_main": [int(x) for x in self.ui.splitter.sizes()],
            "splitter_left": [int(x) for x in self.ui.left_splitter.sizes()],
        }
        self.ui.state_manager.save_global_value(PLAYGROUND_STATE_KEY, state)

    def restore_state(self) -> None:
        """Stellt den persistierten Playground-Zustand wieder her.

        Defensiv (Anforderung 0): Algos, die nicht mehr in der Registry
        existieren, werden uebersprungen. Fehlende/ungueltige Felder werden
        ignoriert. Splitter-Positionen erst nach der Fenster-Geometrie
        anwenden (main_win ruft restore_state() nach restore_geometry auf).
        """
        sm = self.ui.state_manager
        data = sm.get_global_value(PLAYGROUND_STATE_KEY)
        if not isinstance(data, dict):
            return

        # -- Zeitraum ----------------------------------------------------
        tr = data.get("time_range")
        if isinstance(tr, list) and len(tr) == 2:
            try:
                from datetime import datetime
                # fromtimestamp (lokale Wanduhr) statt utcfromtimestamp:
                # liefert den exakten Epoch-Roundtrip (Wanduhr-Konvention,
                # _to_qdt setzt die Komponenten naiv als lokale Zeit).
                self.ui.time_range.set_range(
                    datetime.fromtimestamp(int(tr[0])),
                    datetime.fromtimestamp(int(tr[1])))
            except (ValueError, OSError, OverflowError, TypeError):
                pass  # ungueltige Epochs -> Standardbereich behalten

        # -- Algo-Liste + Parameter + Checkboxen -------------------------
        panel = self.ui.algo_panel
        params = data.get("params") or {}
        restored: List[tuple] = []  # (instance_key, algo_id)
        for entry in data.get("algos") or []:
            if not isinstance(entry, dict):
                continue
            algo_id = entry.get("algo_id")
            if algo_id not in self.registry:
                continue  # Algo existiert nicht mehr (defensiv)
            key = panel.add_algo(
                algo_id, self.registry[algo_id].get("name"),
                instance_key=entry.get("instance_key"))
            if entry.get("checked") is False:
                panel.set_checked_instance(key, False)
            stored = params.get(key)
            self.instance_params[key] = (
                dict(stored) if isinstance(stored, dict)
                else self._schema_defaults(algo_id))
            restored.append((key, algo_id))
        self.active_algos = list(panel.algo_ids())

        # -- Selektion ---------------------------------------------------
        sel = data.get("selected_instance")
        for key, algo_id in restored:
            if key == sel:
                self.selected_instance = key
                self.ui.param_form.set_schema(
                    self.registry.get(algo_id, {}).get("schema", {}))
                self.ui.param_form.set_params(self.instance_params.get(key, {}))
                break

        # -- Splitter (nach Fenster-Geometrie!) --------------------------
        for attr, key_name in (("splitter", "splitter_main"),
                               ("left_splitter", "splitter_left")):
            sizes = data.get(key_name)
            if isinstance(sizes, list) and len(sizes) == 2:
                try:
                    getattr(self.ui, attr).setSizes(
                        [int(sizes[0]), int(sizes[1])])
                except (TypeError, ValueError):
                    pass

    # ------------------------------------------------------------------
    # Canvas (Phase 4: Candlestick + Zeitraum-Slice, Konzept 2.4)
    # ------------------------------------------------------------------
    def refresh_chart(self) -> None:
        """Laedt (falls noetig) die Daten und rendert den Canvas neu.

        Wird vom View beim Start (nach restore_state) und nach einem
        erfolgreichen Sync aufgerufen. Symbol/TF-Wechsel invalidiert den
        Cache (Konzept 2.4: einmaliges Laden pro (Symbol, TF)-Paar).
        """
        self._load_candles()
        self._render_chart()

    def _load_candles(self) -> None:
        """Laedt OHLCV-Kerzen einmalig pro (Symbol, TF) in den Cache.

        Konzept 2.4: Beim Wechsel von Symbol oder TF wird der DataFrame
        einmalig aus dem MarketDataRepository geladen und gecacht; danach
        arbeiten alle Playground-Berechnungen auf diesem Cache (kein
        Neuladen bei Zeitraum-Aenderungen).
        """
        if not hasattr(self.ui, "current_symbol") or \
                not hasattr(self.ui, "current_timeframe"):
            return
        symbol = self.ui.current_symbol()
        timeframe = self.ui.current_timeframe()
        pair = (symbol, timeframe)
        if self._last_pair == pair and self._candles_cache is not None:
            return  # Cache fuer dieses Paar ist noch gueltig

        self._last_pair = pair
        candles, _ = self._data_repo.fetch_historical_candles(
            symbol, timeframe, limit=self.CANDLE_LIMIT)
        if candles:
            self._candles_cache = pd.DataFrame(candles)
        else:
            # Keine Daten (noch) vorhanden -> leerer Zustand.
            self._candles_cache = None
        self.ui.status_label.setText(
            f"Status: {len(candles)} Kerzen geladen ({symbol} {timeframe})")

    def _on_symbol_tf_changed(self, *_args) -> None:
        """Symbol oder Timeframe geaendert: Cache invalidieren + neu rendern."""
        self._last_pair = None  # Cache fuer das alte Paar verwerfen
        self.refresh_chart()

    def _on_range_changed(self, *_args) -> None:
        """Zeitraum geaendert: nur Slicen + rendern (Cache bleibt)."""
        self._render_chart()

    def _render_chart(self) -> None:
        """Baut das Candlestick-HTML (Zeitraum-Slice) und setzt es in den View.

        Die setHtml-BaseUrl zeigt auf das assets/-Verzeichnis, damit die
        lokale plotly.min.js-Datei geladen werden kann (Bugfix 17.08.2026:
        ohne baseUrl laedt QWebEngineView keine file://-Scripts).
        """
        canvas = getattr(self.ui, "canvas", None)
        if canvas is None:
            return  # kein Canvas (z.B. Tests mit FakeView)

        symbol = getattr(self.ui, "current_symbol", lambda: "?")()
        timeframe = getattr(self.ui, "current_timeframe", lambda: "?")()
        from_epoch, to_epoch = self.ui.time_range.get_range()
        html = self._chart_service.build_candlestick_html(
            self._candles_cache, from_epoch, to_epoch, symbol, timeframe)

        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets")
        canvas.setHtml(html, QUrl.fromLocalFile(
            os.path.normpath(assets_dir).replace("\\", "/") + "/"))

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    def _schema_defaults(self, algo_id: str) -> Dict[str, Any]:
        """Liest die Default-Werte aus dem parameter_schema eines AlgOS."""
        schema = self.registry.get(algo_id, {}).get("schema", {})
        return {
            name: spec.get("default")
            for name, spec in schema.items()
        }
