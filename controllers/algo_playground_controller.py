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
Phase 6-Ergaenzung: der aktuelle Plotly-View (Zoom/Skala, x/y-Range) wird
mit dem Fensterzustand gespeichert und beim Neustart wieder angewendet;
bei Checkbox-/Param-Aenderungen bleibt der Zoom erhalten (Kontinuitaet).
Phase 6.1: Overlay-Berechnungen (get_overlay_series) laufen in einem
PlaygroundWorker (QThread), damit das UI nicht einfriert. Ergebnisse
kommen asynchron per Signal zurueck; eine Generationsnummer pro Instanz
verwirft veraltete Ergebnisse (z. B. bei schnellen Param-Aenderungen).

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

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl
from PySide6.QtWidgets import QDialog

from algos.algo_registry import AlgoRegistry
from repositories.market_data_repository import MarketDataRepository
from ui.algo_picker_dialog import AlgoPickerDialog
from ui.playground_chart_service import (
    OVERLAY_COLORS,
    VIEW_SCRIPT_MARKER,
    PlaygroundChartService,
)
from workers.playground_worker import PlaygroundWorker

# Persistenz-Key (Anforderung 0, Phase 3): kompletter Playground-Zustand
# wird in global_settings abgelegt (save_global_value/get_global_value).
PLAYGROUND_STATE_KEY = "playground_state"

# Phase 6: JS-Snippet zum Auslesen des aktuellen Plotly-Views (Zoom/Skala).
# Liefert den sichtbaren Ausschnitt aus _fullLayout: xrange (Datumsachse,
# als ISO-Strings) und yrange (Preisachse, als Zahlen) – exakt das, was
# Plotly nach einem Zoom/Pan intern haelt. Autorange ist dabei bereits in
# konkrete Ranges aufgeloest.
_JS_READ_VIEW = """
(function(){
  var gd = document.querySelector('.plotly-graph-div');
  if (!gd || !gd._fullLayout || !gd._fullLayout.xaxis) return null;
  function toISO(v) {
    return (v && typeof v.toISOString === 'function') ? v.toISOString() : v;
  }
  var xr = gd._fullLayout.xaxis.range;
  var yr = gd._fullLayout.yaxis.range;
  return {
    xrange: (xr && xr.length === 2) ? [toISO(xr[0]), toISO(xr[1])] : null,
    yrange: (yr && yr.length === 2) ? [toISO(yr[0]), toISO(yr[1])] : null
  };
})()
"""


def _js_add_overlay(trace: dict) -> str:
    """Baut JS zum inkrementellen Hinzufuegen eines Overlay-Trace.

    Wird per Plotly.addTraces auf der BEREITS GELADENEN Seite ausgefuehrt
    (kein setHtml/kein Seiten-Neuaufbau) -> Zoom/Skala bleiben erhalten.
    x-Epochs werden in Datums-Strings umgewandelt (Datumsachse).
    """
    x_iso = [str(pd.to_datetime(int(t), unit="s")) for t in trace["x"]]
    trace_json = json.dumps({
        "name": trace.get("name", "Overlay"),
        "x": x_iso,
        "y": trace.get("y", []),
        "mode": "lines",
        "line": {"color": trace.get("color", "#ff7f0e"), "width": 1.5},
        "hovertemplate": "%{y:.4f}<extra>%{fullData.name}</extra>",
    })
    return (
        "(function(){var gd=document.querySelector('.plotly-graph-div');"
        "if(!gd||typeof Plotly==='undefined')return;"
        f"Plotly.addTraces(gd,{trace_json});}})()"
    )


def _js_remove_overlay(idx: int) -> str:
    """Baut JS zum Entfernen eines Overlay-Trace per Index (deleteTraces)."""
    return (
        "(function(){var gd=document.querySelector('.plotly-graph-div');"
        "if(!gd||typeof Plotly==='undefined')return;"
        f"if({idx}<(gd.data||[]).length)Plotly.deleteTraces(gd,{idx});}})()"
    )


def _js_update_overlay(idx: int, trace: dict) -> str:
    """Baut JS zum Aktualisieren eines Overlay-Trace per Index (restyle)."""
    x_iso = [str(pd.to_datetime(int(t), unit="s")) for t in trace["x"]]
    payload = json.dumps({"x": x_iso, "y": trace.get("y", [])})
    return (
        "(function(){var gd=document.querySelector('.plotly-graph-div');"
        "if(!gd||typeof Plotly==='undefined')return;"
        f"if({idx}<(gd.data||[]).length)Plotly.restyle(gd,{payload},{idx});}})()"
    )


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
        # Phase 6: gespeicherter Plotly-View (Zoom/Skala), der nach dem
        # naechsten Render angewendet werden soll (Restore/Kontinuitaet).
        self._pending_view: Optional[dict] = None
        # Zuletzt aus dem Canvas gelesener View (periodischer Timer). Wird
        # in save_state genutzt, damit beim Schliessen KEIN JS mehr laufen
        # muss (QEventLoop waehrend closeEvent ist unzuverlaessig).
        self._last_view: Optional[dict] = None
        self._view_timer = QTimer(self)
        self._view_timer.setInterval(2000)
        self._view_timer.timeout.connect(self._poll_view)
        self._view_timer.start()
        # Reihenfolge der AKTUELL im Canvas sichtbaren Overlay-Traces
        # (Phase 6, USER-REQ: kein Neuaufbau beim Check/Uncheck). Der
        # Candlestick-Trace liegt immer auf Index 0 -> Overlay-Index =
        # 1 + Position in dieser Liste (Trace-Key = "instance_key::name").
        self._overlay_order: List[str] = []
        # Debounce (300ms, Konzept 2.2): nur der betroffene Algo wird
        # nach Parameter-Aenderung neu berechnet (kurze Wartezeit, damit
        # schnelles Eintippen nicht jede Zwischenstufe berechnet).
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._on_debounce_timeout)
        self._pending_recalc: Optional[str] = None  # instance_key oder "all"

        # Phase 6.1: Overlay-Berechnung im Hintergrund (QThread). Pro Instanz
        # eine Generationsnummer – veraltete Worker-Ergebnisse (schnelle
        # Param-/Daten-Aenderungen) werden beim Eintreffen verworfen.
        self._overlay_generation: Dict[str, int] = {}
        # Referenzen auf laufende PlaygroundWorker (GC-Schutz + Aufraeumen).
        self._workers: List[PlaygroundWorker] = []

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
            # Kein Canvas-Neuaufbau noetig: die Instanz ist UNCHECKED und
            # zeichnet nichts – sichtbar wird sie erst per Checkbox (dann
            # inkrementell via Plotly.addTraces, Phase 6).

    def _on_algo_removed(self, algo_id: str, instance_key: str) -> None:
        """Kontextmenue 'Entfernen': Zustand nachfuehren (RAM-only)."""
        # Sichtbare Overlay-Traces der Instanz zuerst entfernen (inkrementell,
        # KEIN Canvas-Neuaufbau).
        self._remove_overlay_traces(instance_key)
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

    def _on_visibility_changed(self, algo_id: str, instance_key: str,
                               checked: bool) -> None:
        """Checkbox geaendert: Overlay INKREMENTELL ein/ausblenden.

        USER-REQ (17.08.2026): kein kompletter Canvas-Neuaufbau. An statt
        setHtml wird nur der/die Overlay-Trace(s) der Instanz per
        Plotly.addTraces/deleteTraces hinzugefuegt/entfernt – der aktuelle
        Zoom und alle anderen Traces bleiben unveraendert.
        """
        state = "ein" if checked else "aus"
        self.ui.status_label.setText(
            f"Status: {algo_id} Darstellung {state}")
        if checked:
            for t in self._build_instance_traces(instance_key):
                self._add_overlay_trace(t)
        else:
            self._remove_overlay_traces(instance_key)

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
        Parameter je Instanz, selektierte Instanz, Zeitraum (Von/Bis),
        beide Splitter-Positionen (horizontal + vertikal im linken Panel)
        und der aktuelle Plotly-View (Zoom/Skala, Phase 6-Ergaenzung).
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
            # Phase 6: letzter vom View-Timer gelesener Plotly-View
            # (Zoom/Skala) fuer den Neustart. KEIN JS waehrend closeEvent.
            "view": self._last_view,
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

        # -- Plotly-View (Phase 6-Ergaenzung) ----------------------------
        # Gespeicherten Zoom/Skala merken; angewendet wird er erst beim
        # ersten Render MIT Daten (refresh_chart nach restore_state), damit
        # die leere/initiale Seite nicht den View auf ein Nichts legt.
        view = data.get("view")
        if isinstance(view, dict) and (view.get("xrange") or view.get("yrange")):
            self._pending_view = view

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
        # (neue Kerzen koennen neue Overlay-Werte liefern), RAM-only.
        self._recalc_all_overlays()
        if force_reload:
            # Nach einem Sync: sichtbaren Bereich bis zur neuesten Kerze
            # erweitern, damit die neuen Daten auch angezeigt werden.
            self._extend_range_to_latest()
        # Neuer Datenkontext -> alten Zoom nicht konservieren (der Zeitraum
        # bzw. die neuen Daten sind jetzt massgebend; ein via restore_state
        # gesetzter _pending_view wird trotzdem angewendet).
        self._render_chart(preserve_view=False)

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
        """Zeitraum geaendert: nur Slicen + rendern (Cache bleibt).

        Der Zeitraum-Picker ist jetzt massgebend -> keinen alten Zoom
        konservieren (sonst wuerde der Chart zurueck auf den alten
        Ausschnitt springen).
        """
        self._render_chart(preserve_view=False)

    def _render_chart(self, preserve_view: bool = True) -> None:
        """Baut das Candlestick-HTML (Zeitraum-Slice) und setzt es in den View.

        Args:
            preserve_view: True (Default) -> der zuletzt bekannte Plotly-
                Zoom/Skala wird beim Neuaufbau wieder angewendet (eingebettet
                ins HTML), damit die Ansicht nach Checkbox-/Param-Aenderungen
                nicht zurueckspringt. False -> keine Kontinuitaet (z. B.
                Zeitraum oder Symbol/TF gewechselt); ein via restore_state
                gesetzter _pending_view wird in jedem Fall angewendet.

        Die setHtml-BaseUrl zeigt auf das assets/-Verzeichnis, damit die
        lokale plotly.min.js-Datei geladen werden kann (Bugfix 17.08.2026:
        ohne baseUrl laedt QWebEngineView keine file://-Scripts).
        """
        canvas = getattr(self.ui, "canvas", None)
        if canvas is None:
            return  # kein Canvas (z.B. Tests mit FakeView)

        # Phase 6: Zoom-Kontinuitaet – letzten bekannten View uebernehmen.
        if preserve_view and self._last_view is not None:
            self._pending_view = self._last_view

        symbol = getattr(self.ui, "current_symbol", lambda: "?")()
        timeframe = getattr(self.ui, "current_timeframe", lambda: "?")()
        from_epoch, to_epoch = self.ui.time_range.get_range()
        # Phase 5: sichtbare Overlay-Traces (Checkbox ein + Zeitraum-Slice).
        checked: Dict[str, bool] = {}
        if hasattr(self.ui, "algo_panel"):
            checked = self.ui.algo_panel.checked_states()
        overlays = self._build_overlay_traces(checked)
        # Reihenfolge der sichtbaren Overlay-Traces merken: fuer spaetere
        # inkrementelle Aenderungen per JS (Trace-Index = 1 + Position).
        self._overlay_order = [t["key"] for t in overlays]

        # Der gespeicherte View wird direkt ins HTML eingebettet und dort
        # NACH dem Plotly-Render angewendet – zuverlaessiger als ein
        # runJavaScript direkt nach setHtml (das liefe auf der alten Seite).
        html = self._chart_service.build_candlestick_html(
            self._candles_cache, from_epoch, to_epoch, symbol, timeframe,
            overlays=overlays, initial_view=self._pending_view)
        # Phase 6 (Bugfix 17.08.2026): _pending_view nur konsumieren, wenn
        # das HTML das View-Restore-Skript tatsaechlich eingebettet hat.
        # Leere Render (keine Kerzen -> _EMPTY_HTML ohne Skript) duerfen den
        # Restore-View nicht verwerfen – sonst geht der Zoom verloren, wenn
        # vor dem ersten Daten-Render z. B. ein set_range (range_changed ->
        # Render auf leerem Cache) feuert. Der View bleibt dann fuer den
        # naechsten Render MIT Daten erhalten.
        if VIEW_SCRIPT_MARKER in html:
            self._pending_view = None

        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets")
        canvas.setHtml(html, QUrl.fromLocalFile(
            os.path.normpath(assets_dir).replace("\\", "/") + "/"))

        # Nach dem Render den frischen View (Restore-/Standardwerte)
        # einsammeln – fuer den naechsten preserve-Render bzw. save_state.
        QTimer.singleShot(500, self._poll_view)

    # ------------------------------------------------------------------
    # Phase 6: Plotly-View (Zoom/Skala) lesen + Overlays inkrementell
    # ------------------------------------------------------------------
    def _poll_view(self) -> None:
        """Timer: liest den aktuellen Plotly-View und cached ihn in _last_view.

        Wird periodisch (2s) und nach jedem Render (singleShot 500ms)
        ausgefuehrt. save_state nutzt dann nur noch den Cache – kein JS
        beim Schliessen.
        """
        view = self._capture_view()
        if view is not None:
            self._last_view = view

    def _capture_view(self) -> Optional[dict]:
        """Liest den aktuellen Plotly-View (x/y-Range) synchron aus dem Canvas.

        runJavaScript ist asynchron -> QEventLoop + Timeout (800ms). Ohne
        Canvas/Page oder ohne gerendertes Plot -> None (Tests, leerer Canvas).
        """
        canvas = getattr(self.ui, "canvas", None)
        page = getattr(canvas, "page", None)
        if page is None or not callable(page):
            return None  # z. B. CanvasStub in Tests
        try:
            qpage = page()
        except Exception:
            return None
        if qpage is None:
            return None

        result: Dict[str, Any] = {"view": None}
        loop = QEventLoop()
        try:
            def _cb(value: Any) -> None:
                result["view"] = value
                loop.quit()

            qpage.runJavaScript(_JS_READ_VIEW, _cb)
            # Sicherheits-Timeout (z. B. Seite noch am Laden).
            QTimer.singleShot(800, loop.quit)
            loop.exec()
        except Exception as exc:
            print(f"WARN [Playground] View-Lesen fehlgeschlagen: {exc}")
            return None
        view = result["view"]
        if isinstance(view, dict) and (view.get("xrange") or view.get("yrange")):
            return view
        return None

    # ------------------------------------------------------------------
    # Phase 6: Inkrementelle Overlay-Aenderungen (USER-REQ, kein Neuaufbau)
    # ------------------------------------------------------------------
    def _run_js(self, js: str) -> bool:
        """Fuehrt JS auf dem aktuellen Canvas aus (True wenn ausgefuehrt)."""
        canvas = getattr(self.ui, "canvas", None)
        page = getattr(canvas, "page", None)
        if page is None or not callable(page):
            return False  # z. B. CanvasStub in Tests
        try:
            qpage = page()
            if qpage is not None:
                qpage.runJavaScript(js)
                return True
        except Exception as exc:
            print(f"WARN [Playground] runJavaScript fehlgeschlagen: {exc}")
        return False

    def _add_overlay_trace(self, trace: dict) -> None:
        """Fuegt einen Overlay-Trace per Plotly.addTraces hinzu (inkrementell)."""
        if not self._run_js(_js_add_overlay(trace)):
            return
        self._overlay_order.append(trace["key"])

    def _remove_overlay_traces(self, instance_key: str) -> None:
        """Entfernt alle Overlay-Traces einer Instanz per deleteTraces."""
        for k in [k for k in self._overlay_order
                  if k.startswith(instance_key + "::")]:
            idx = 1 + self._overlay_order.index(k)
            self._run_js(_js_remove_overlay(idx))
            self._overlay_order.remove(k)

    def _update_overlay_traces(self, instance_key: str) -> None:
        """Aktualisiert sichtbare Overlay-Traces einer Instanz (Param-Change).

        Entfernt und fuegt die Traces neu hinzu – die Daten (x/y) haben sich
        geaendert; der Canvas wird dabei NICHT neu aufgebaut.
        """
        self._remove_overlay_traces(instance_key)
        for t in self._build_instance_traces(instance_key):
            self._add_overlay_trace(t)

    # ------------------------------------------------------------------
    # Phase 5: Overlays (Berechnung, Farben, DB-Persistenz, Debounce)
    # ------------------------------------------------------------------
    def _on_debounce_timeout(self) -> None:
        """Debounce abgelaufen: betroffene Instanz neu berechnen (async).

        Phase 6.1: Die Berechnung startet einen PlaygroundWorker (QThread)
        statt im UI-Thread zu rechnen. Sobald das Ergebnis eintrifft
        (_on_overlay_computed), werden sichtbare Overlays INKREMENTELL per
        JS aktualisiert (kein Canvas-Neuaufbau) – der Zoom und die anderen
        Traces bleiben erhalten.
        """
        if self._pending_recalc is None:
            return
        key = self._pending_recalc
        self._pending_recalc = None
        self._recalc_overlay(key)
        self.ui.status_label.setText(
            "Status: Parameter wird neu berechnet ...")

    def _recalc_all_overlays(self) -> None:
        """Berechnet alle Instanz-Overlays neu (Datenkontext geaendert).

        Phase 6.1: asynchron ueber PlaygroundWorker. Die bisherigen Overlays
        gehoeren zum alten Datenkontext (alte Kerzen) und werden verworfen;
        die frischen Ergebnisse kommen per Signal in _on_overlay_computed
        zurueck und werden dort sichtbar gemacht.
        """
        self._overlays.clear()
        for key in list(self.ui.algo_panel.instance_keys()):
            self._recalc_overlay(key)

    def _recalc_overlay(self, instance_key: str) -> None:
        """Startet die Overlay-Berechnung EINER Instanz im Hintergrund.

        Phase 6.1: Die Berechnung (get_overlay_series) laeuft in einem
        PlaygroundWorker (QThread) statt im UI-Thread. Das Ergebnis kommt
        asynchron via Signal zurueck (_on_overlay_computed) und wird dort
        gespeichert und ggf. per JS sichtbar gemacht. Eine Generationsnummer
        pro Instanz verwirft veraltete Ergebnisse (z. B. bei schnellen
        Parameter-Aenderungen oder Daten-Neuladen).

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

        if self._candles_cache is None or self._candles_cache.empty:
            self._overlays[instance_key] = {}
            return

        # Generationsnummer erhoehen -> aeltere Ergebnisse werden verworfen.
        generation = self._overlay_generation.get(instance_key, 0) + 1
        self._overlay_generation[instance_key] = generation

        worker = PlaygroundWorker(
            entry["class"],
            instance_key,
            self.instance_params.get(instance_key) or {},
            self._candles_cache,
            generation=generation,
            parent=self,
        )
        worker.overlay_computed.connect(self._on_overlay_computed)
        worker.overlay_failed.connect(self._on_overlay_failed)
        worker.finished.connect(self._on_worker_finished)
        self._workers.append(worker)
        worker.start()

    def _on_overlay_computed(self, generation: int, instance_key: str,
                             series: dict) -> None:
        """Ergebnis eines PlaygroundWorkers: speichern + sichtbar machen.

        Wirft veraltete Ergebnisse ab (Generationsnummer ungleich aktueller
        Auftrag oder Instanz inzwischen entfernt). Sichtbare Overlays werden
        INKREMENTELL per JS aktualisiert (kein Canvas-Neuaufbau).
        """
        if self._overlay_generation.get(instance_key) != generation:
            return  # veraltet (Param/Instanz inzwischen geaendert)
        if instance_key not in self.ui.algo_panel.instance_keys():
            return  # Instanz wurde inzwischen entfernt
        self._overlays[instance_key] = dict(series)
        # Sichtbar? -> Traces der Instanz per JS aktualisieren (nur RAM).
        checked: Dict[str, bool] = {}
        if hasattr(self.ui, "algo_panel"):
            checked = self.ui.algo_panel.checked_states()
        if checked.get(instance_key, True) is not False:
            self._update_overlay_traces(instance_key)
            self.ui.status_label.setText("Status: Parameter angewendet")

    def _on_overlay_failed(self, generation: int, instance_key: str,
                           error: str) -> None:
        """Fehler eines PlaygroundWorkers: protokollieren + RAM loeschen."""
        print(f"WARN [Playground] Overlay {instance_key}: {error}")
        if self._overlay_generation.get(instance_key) == generation:
            self._overlays.pop(instance_key, None)

    def _on_worker_finished(self) -> None:
        """QThread beendet: Worker-Referenz aufraeumen (GC-Schutz)."""
        worker = self.sender()
        if worker is not None and worker in self._workers:
            self._workers.remove(worker)
        if worker is not None:
            worker.deleteLater()

    def _color_for_instance(self, instance_key: str) -> str:
        """Weist einer Instanz eine stabile Overlay-Farbe zu (Farbzyklus)."""
        if instance_key not in self._overlay_colors:
            idx = len(self._overlay_colors) % len(OVERLAY_COLORS)
            self._overlay_colors[instance_key] = OVERLAY_COLORS[idx]
        return self._overlay_colors[instance_key]

    def _build_instance_traces(self, instance_key: str) -> List[dict]:
        """Baut die Overlay-Trace-Dicts EINER Instanz (Zeitraum-Slice).

        Pro Ergebnis-Feld der Instanz ein Trace mit eindeutigem "key"
        ("instance_key::name") – wird fuer den initialen HTML-Render und die
        inkrementellen JS-Aenderungen (add/remove/update) verwendet.
        """
        panel = self.ui.algo_panel
        keys = panel.instance_keys()
        if instance_key not in keys:
            return []
        idx = keys.index(instance_key)
        algo_id = panel.algo_ids()[idx] if idx < len(keys) else "?"
        overlays = self._overlays.get(instance_key)
        if not overlays:
            return []
        color = self._color_for_instance(instance_key)
        from_epoch, to_epoch = self.ui.time_range.get_range()
        traces: List[dict] = []
        for name, series in overlays.items():
            if series is None or len(series) == 0:
                continue
            # Zeitraum-Slice (Konzept 2.4: Slicen statt Neuladen).
            mask = (series.index >= int(from_epoch)) & \
                   (series.index <= int(to_epoch))
            s = series.loc[mask]
            if s.empty:
                continue
            traces.append({
                "key": f"{instance_key}::{name}",
                "name": f"{algo_id} ({name})",
                "x": s.index.tolist(),   # Epoch-Ints (HTML/JS-Konvertierung)
                "y": s.tolist(),
                "color": color,
            })
        return traces

    def _build_overlay_traces(self, checked: Dict[str, bool]) -> List[dict]:
        """Baut die sichtbaren Overlay-Traces ALLER Instanzen (fuer HTML).

        Nur Instanzen mit aktivierter Checkbox (Zeitraum-Slice). Das Format
        passt zum `overlays`-Parameter des PlaygroundChartService; der
        "key" wird zusaetzlich fuer die Overlay-Reihenfolge genutzt.
        """
        traces: List[dict] = []
        for key in self.ui.algo_panel.instance_keys():
            if checked.get(key, True) is False:
                continue  # Checkbox aus -> Overlay unsichtbar
            traces.extend(self._build_instance_traces(key))
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
