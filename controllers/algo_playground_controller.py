# controllers/algo_playground_controller.py
"""
controllers/algo_playground_controller.py - Presenter fuer den Algo-Playground.

Phase 2: Koppelt die UI-Events der Algo-Liste an den AlgoPickerDialog und
haelt den Zustand (aktive Algos). Phase 3: Parameter-Form – Auswahl eines
Listeneintrags befuellt die ParamFormWidget aus dessen parameter_schema;
Aenderungen werden pro Instanz (instance_key) gespeichert. Phase 4: Canvas-
Basics – Daten-Cache (Konzept 2.4: einmalig pro Symbol/TF aus dem
MarketDataRepository laden), Zeitraum-Slice (client-seitig) und Candlestick-
Render in den QWebEngineView. Phase 5: Algo-Overlays – get_overlay_series-
Hooks werden nach Param- oder Daten-Aenderung (Debounce 300ms) vektorisiert
berechnet und als farbige Linien ueber die Candles gelegt (Checkbox ein/aus).
USER-REQ (17.08.2026): NUR RAM-Berechnung + Plot (keine DB-Persistenz,
AlgoResultsRepository folgt in einer spaeteren Phase); der "+"-Dialog ist
eine einfache Liste, Klick uebernimmt den Algo als UNCHECKED Instanz.

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
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWidgets import QDialog

from algos.algo_registry import AlgoRegistry
from repositories.market_data_repository import MarketDataRepository
from ui.algo_picker_dialog import AlgoPickerDialog
from ui.playground_chart_service import OVERLAY_COLORS, PlaygroundChartService

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

        # Phase 5: Overlay-Ergebnisse (RAM-only, USER-REQ 17.08.2026) +
        # stabile Farben + Debounce. KEINE DB-Persistenz (kommt spaeter
        # ueber AlgoResultsRepository in einer weiteren Phase).
        self._overlays: Dict[str, Dict[str, pd.Series]] = {}
        # Stabile Overlay-Farbe je Instanz (Farbzyklus, Phase 5.3).
        self._overlay_colors: Dict[str, str] = {}
        # Debounce (300ms, Konzept 2.2): nur der betroffene Algo wird
        # nach Parameter-Aenderung neu berechnet (kurze Wartezeit, damit
        # schnelles Eintippen nicht jede Zwischenstufe berechnet).
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._on_debounce_timeout)
        self._pending_recalc: Optional[str] = None  # instance_key oder "all"

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
        """'+' geklickt: AlgoPickerDialog oeffnen, gewaehlten Algo uebernehmen.

        USER-REQ (17.08.2026): Der Dialog ist eine einfache Liste aller
        Algos; ein Klick uebernimmt den Algo sofort als UNCHECKED Instanz
        (Overlay zunaechst unsichtbar – erst nach Checkbox-Aktivierung).
        Esc schliesst ohne Uebernahme. Die Instanz wird im RAM berechnet
        (keine DB-Persistenz).
        """
        if not self.registry:
            self.ui.status_label.setText(
                "Status: keine Algos verfuegbar (algos/ ist leer)")
            return
        dlg = AlgoPickerDialog(self.registry, self.ui)
        if dlg.exec() == QDialog.Accepted:
            algo_id = dlg.selected_algo_id()
            if algo_id is None:
                return
            meta = self.registry.get(algo_id, {})
            key = self.ui.algo_panel.add_algo(
                algo_id, meta.get("name"), checked=False)
            self.active_algos = list(self.ui.algo_panel.algo_ids())
            # Neue Instanz mit Schema-Defaults initialisieren.
            self.instance_params[key] = self._schema_defaults(algo_id)
            # Overlay im RAM berechnen (nicht sichtbar bis Checkbox an).
            self._recalc_overlay(key)
            self.ui.status_label.setText(
                f"Status: {algo_id} hinzugefuegt "
                f"(unsichtbar – Checkbox aktivieren)")
            # Canvas neu rendern, damit die Liste/Overlays aktuell sind.
            self._render_chart()

    def _on_algo_removed(self, algo_id: str, instance_key: str) -> None:
        """Kontextmenue 'Entfernen': Zustand nachfuehren (RAM-only)."""
        # Parameter/Overlay/Farbe der entfernten Instanz verwerfen.
        self.instance_params.pop(instance_key, None)
        self._overlays.pop(instance_key, None)
        self._overlay_colors.pop(instance_key, None)
        # USER-REQ: keine DB-Persistenz in Phase 5 (kommt spaeter) –
        # daher hier auch kein Repository-Delete noetig.
        if self.selected_instance == instance_key:
            self.selected_instance = None
            self.ui.param_form.set_schema({})
        self.active_algos = list(self.ui.algo_panel.algo_ids())
        self.ui.status_label.setText(
            f"Status: {algo_id} entfernt (aktiv: {len(self.active_algos)})")
        # Canvas neu rendern, damit das Overlay verschwindet.
        self._render_chart()

    def _on_visibility_changed(self, algo_id: str, instance_key: str,
                               checked: bool) -> None:
        """Checkbox geaendert: Overlay ein/ausblenden (KEIN Neuberechnen)."""
        state = "ein" if checked else "aus"
        self.ui.status_label.setText(
            f"Status: {algo_id} Darstellung {state}")
        self._render_chart()

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
        """Parameter geaendert: speichern + Neuberechnung (Debounce 300ms).

        Phase 5: Nach Aenderung wird NUR die betroffene Instanz neu
        berechnet (get_overlay_series) – aber erst nach Ablauf der
        Debounce-Frist, damit schnelles Tippen in SpinBoxes nicht hunderte
        Berechnungen/DB-Writes ausloest.
        """
        if self.selected_instance is None:
            return
        self.instance_params[self.selected_instance] = dict(params)
        self.ui.status_label.setText(
            "Status: Parameter geaendert – Neuberechnung ...")
        self._pending_recalc = self.selected_instance
        self._debounce.start()

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
    def refresh_chart(self, force_reload: bool = False) -> None:
        """Laedt (falls noetig) die Daten und rendert den Canvas neu.

        Args:
            force_reload: True -> Cache verwerfen und neu aus DuckDB laden
                (nach einem Sync, damit neue Kerzen erscheinen). False ->
                Konzept 2.4: gleiches (Symbol, TF)-Paar nutzt den Cache.

        Wird vom View beim Start (nach restore_state) und nach einem
        erfolgreichen Sync aufgerufen. Symbol/TF-Wechsel invalidiert den
        Cache (Konzept 2.4: einmaliges Laden pro (Symbol, TF)-Paar).
        """
        self._load_candles(force_reload=force_reload)
        # Phase 5: Datenkontext geaendert -> ALLE Overlays neu berechnen
        # (neue Kerzen koennen neue Overlay-Werte liefern) + DB aktualisieren.
        self._recalc_all_overlays()
        if force_reload:
            # Nach einem Sync: sichtbaren Bereich bis zur neuesten Kerze
            # erweitern, damit die neuen Daten auch angezeigt werden.
            self._extend_range_to_latest()
        self._render_chart()

    def _load_candles(self, force_reload: bool = False) -> None:
        """Laedt OHLCV-Kerzen (einmalig pro (Symbol, TF) oder erzwungen).

        Konzept 2.4: Beim Wechsel von Symbol oder TF wird der DataFrame
        einmalig aus dem MarketDataRepository geladen und gecacht; danach
        arbeiten alle Playground-Berechnungen auf diesem Cache (kein
        Neuladen bei Zeitraum-Aenderungen). Nach einem Scan (force_reload)
        wird der Cache verworfen und frisch geladen (Bugfix 17.08.2026:
        sonst blieben alte Kerzen trotz neuer DB-Daten sichtbar).
        """
        if not hasattr(self.ui, "current_symbol") or \
                not hasattr(self.ui, "current_timeframe"):
            return
        symbol = self.ui.current_symbol()
        timeframe = self.ui.current_timeframe()
        pair = (symbol, timeframe)
        if not force_reload and self._last_pair == pair and \
                self._candles_cache is not None:
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

    def _extend_range_to_latest(self) -> None:
        """Erweitert das Bis-Datum auf die neueste Kerze (nach Sync).

        Nur wenn die neueste gecachte Kerze neuer als das aktuelle Bis-Datum
        ist. Von/Bis setzen emittiert range_changed -> _on_range_changed
        rendert den Canvas einmal neu (keine Schleife).
        """
        if self._candles_cache is None or self._candles_cache.empty:
            return
        max_epoch = int(self._candles_cache["time"].max())
        _, to_epoch = self.ui.time_range.get_range()
        if max_epoch > to_epoch:
            from datetime import datetime
            from_epoch, _ = self.ui.time_range.get_range()
            self.ui.time_range.set_range(
                datetime.fromtimestamp(from_epoch),
                datetime.fromtimestamp(max_epoch))

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
        # Phase 5: sichtbare Overlay-Traces (Checkbox ein + Zeitraum-Slice).
        checked: Dict[str, bool] = {}
        if hasattr(self.ui, "algo_panel"):
            checked = self.ui.algo_panel.checked_states()
        overlays = self._build_overlay_traces(checked)
        html = self._chart_service.build_candlestick_html(
            self._candles_cache, from_epoch, to_epoch, symbol, timeframe,
            overlays=overlays)

        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets")
        canvas.setHtml(html, QUrl.fromLocalFile(
            os.path.normpath(assets_dir).replace("\\", "/") + "/"))

    # ------------------------------------------------------------------
    # Phase 5: Overlays (Berechnung, Farben, DB-Persistenz, Debounce)
    # ------------------------------------------------------------------
    def _on_debounce_timeout(self) -> None:
        """Debounce abgelaufen: betroffene Instanz neu berechnen + rendern."""
        if self._pending_recalc is None:
            return
        key = self._pending_recalc
        self._pending_recalc = None
        self._recalc_overlay(key)
        self.ui.status_label.setText("Status: Parameter angewendet")
        self._render_chart()

    def _recalc_all_overlays(self) -> None:
        """Berechnet alle Instanz-Overlays neu (Datenkontext geaendert)."""
        for key in list(self.ui.algo_panel.instance_keys()):
            self._recalc_overlay(key)

    def _recalc_overlay(self, instance_key: str) -> None:
        """Berechnet die Overlay-Serien EINER Instanz im RAM (vektorisiert).

        Holt algo_id + Parameter, instanziiert den Algo und ruft
        `get_overlay_series(self._candles_cache)` auf. Ergebnis wird
        gehalten in self._overlays[instance_key].

        USER-REQ (17.08.2026): NUR RAM-Berechnung + Plot – KEINE DB-
        Persistenz (AlgoResultsRepository folgt in einer spaeteren Phase).
        """
        panel = self.ui.algo_panel
        keys = panel.instance_keys()
        if instance_key not in keys:
            return
        algo_id = panel.algo_ids()[keys.index(instance_key)]
        entry = self.registry.get(algo_id)
        if entry is None or not entry.get("has_overlay"):
            # Algo ohne Overlay-Hook -> keine Traces fuer diese Instanz.
            self._overlays.pop(instance_key, None)
            return

        try:
            algo = entry["class"](
                **(self.instance_params.get(instance_key) or {}))
        except Exception as exc:
            print(f"WARN [Playground] Instanzierung {algo_id}: {exc}")
            self._overlays.pop(instance_key, None)
            return

        if self._candles_cache is None or self._candles_cache.empty:
            self._overlays[instance_key] = {}
            return

        try:
            series_dict = algo.get_overlay_series(self._candles_cache)
        except Exception as exc:
            print(f"WARN [Playground] Overlay {algo_id}: {exc}")
            self._overlays[instance_key] = {}
            return

        self._overlays[instance_key] = (
            dict(series_dict) if isinstance(series_dict, dict) else {})

    def _color_for_instance(self, instance_key: str) -> str:
        """Weist einer Instanz eine stabile Overlay-Farbe zu (Farbzyklus)."""
        if instance_key not in self._overlay_colors:
            idx = len(self._overlay_colors) % len(OVERLAY_COLORS)
            self._overlay_colors[instance_key] = OVERLAY_COLORS[idx]
        return self._overlay_colors[instance_key]

    def _build_overlay_traces(self, checked: Dict[str, bool]) -> List[dict]:
        """Baut die sichtbaren Overlay-Traces fuer den Canvas (Zeitraum-Slice).

        Nur Instanzen mit aktivierter Checkbox. Die Serien werden auf den
        sichtbaren Zeitraum geschnitten, damit sie die X-Achse nicht ueber
        den Candlestick-Bereich hinaus dehnen (Konzept 2.4: Slicen statt
        Neuladen). Rueckgabe-Format passt zum `overlays`-Parameter des
        PlaygroundChartService.
        """
        if not self._overlays:
            return []
        from_epoch, to_epoch = self.ui.time_range.get_range()
        panel = self.ui.algo_panel
        keys = panel.instance_keys()
        ids = panel.algo_ids()
        traces: List[dict] = []
        for key in keys:
            if checked.get(key, True) is False:
                continue  # Checkbox aus -> Overlay unsichtbar
            overlays = self._overlays.get(key)
            if not overlays:
                continue
            idx = keys.index(key)
            algo_id = ids[idx] if idx < len(ids) else "?"
            color = self._color_for_instance(key)
            for name, series in overlays.items():
                if series is None or len(series) == 0:
                    continue
                mask = (series.index >= int(from_epoch)) & \
                       (series.index <= int(to_epoch))
                s = series.loc[mask]
                if s.empty:
                    continue
                traces.append({
                    "name": f"{algo_id} ({name})",
                    "x": s.index.tolist(),
                    "y": s.tolist(),
                    "color": color,
                })
        return traces

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
