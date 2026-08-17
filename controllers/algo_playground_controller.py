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

import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QDialog

from algos.algo_registry import AlgoRegistry
from repositories.market_data_repository import MarketDataRepository
from ui.algo_picker_dialog import AlgoPickerDialog
from ui.playground_chart_service import OVERLAY_COLORS, PlaygroundChartService
from ui.style_models import collect_style_keys
from workers.playground_worker import PlaygroundWorker

# Persistenz-Key (Anforderung 0, Phase 3): kompletter Playground-Zustand
# wird in global_settings abgelegt (save_global_value/get_global_value).
PLAYGROUND_STATE_KEY = "playground_state"

# Marker des leeren Canvas-HTML (_EMPTY_HTML in playground_chart_service).
# P0#1: Seit dem Figure-basierten Render prueft der Controller den leeren
# Zustand ueber `figure is None` (statt den Marker im HTML zu suchen) –
# die Konstante bleibt als Referenz auf den Text des Hinweis-HTML erhalten.
_EMPTY_MARKER = "Keine Daten"

# Bugfix 17.08.2026 (SMA-Overlay-Regression nach P0#1): Seit der Page-Shell
# rendert Plotly in die feste Div 'pg-chart'. Die von to_html generierte
# Klasse '.plotly-graph-div' existiert dort NICHT (plotly.js v3.7.0 vergibt
# diese Klasse nicht auf der Ziel-Div, nur 'js-plotly-plot') – der alte
# Klassen-Selector liess alle inkrementellen Overlay-JS still ins Leere
# laufen (gd=null). ALLE Chart-JS-Snippets nutzen daher diesen Ausdruck mit
# Fallback auf die alte Klasse (Abwaertskompatibilitaet).
_JS_CHART_DIV = (
    "document.getElementById('pg-chart')"
    "||document.querySelector('.plotly-graph-div')"
)

# Phase 6: JS-Snippet zum Auslesen des aktuellen Plotly-Views (Zoom/Skala).
# Liefert den sichtbaren Ausschnitt aus _fullLayout: xrange (Datumsachse)
# und yrange (Preisachse, als Zahlen) – exakt das, was Plotly nach einem
# Zoom/Pan intern haelt (Autorange ist dabei bereits in konkrete Ranges
# aufgeloest). Bugfix 17.08.2026: xrange wird als WANDUHR-naiver
# "YYYY-MM-DDTHH:MM:SS"-String geliefert (via getFullYear/getMonth/...),
# damit der gespeicherte View exakt dem Kerzen-x-Format entspricht (plotly
# parst beide als lokale Zeit) – ein UTC-toISOString wuerde je nach
# Zeitzone einen Offset zwischen Achse und Kerzen erzeugen.
_JS_READ_VIEW_TMPL = """
(function(){
  var gd = __CHART_DIV__;
  if (!gd || !gd._fullLayout || !gd._fullLayout.xaxis) return null;
  function toWallClock(v) {
    if (!(v instanceof Date)) return v;
    function p(n){ return (n < 10 ? '0' : '') + n; }
    return v.getFullYear() + '-' + p(v.getMonth() + 1) + '-' + p(v.getDate()) +
           'T' + p(v.getHours()) + ':' + p(v.getMinutes()) + ':' + p(v.getSeconds());
  }
  var xr = gd._fullLayout.xaxis.range;
  var yr = gd._fullLayout.yaxis.range;
  return {
    xrange: (xr && xr.length === 2) ? [toWallClock(xr[0]), toWallClock(xr[1])] : null,
    yrange: (yr && yr.length === 2) ? [toWallClock(yr[0]), toWallClock(yr[1])] : null
  };
})()
"""
_JS_READ_VIEW = _JS_READ_VIEW_TMPL.replace("__CHART_DIV__", _JS_CHART_DIV)


def _epochs_to_iso(epochs) -> List[str]:
    """Vektorisiert Epoch-Ints -> Wanduhr-ISO-Strings (P0#3, 302ms -> ~19ms).

    Formt Epoch-Zahlen (Wanduhr-encoded) in "YYYY-MM-DDTHH:MM:SS"-Strings um
    (exakt das Format der Chart-Figur/_JS_READ_VIEW – plotly parst beide als
    lokale Zeit, daher konsistent mit den Kerzen).
    """
    arr = np.asarray(list(epochs), dtype="int64")
    if arr.size == 0:
        return []
    return pd.to_datetime(arr, unit="s").strftime(
        "%Y-%m-%dT%H:%M:%S").tolist()


class _NumpyJSONEncoder(json.JSONEncoder):
    """JSON-Encoder fuer Figure-Dicts (numpy/Timestamp-sicher, P0#1)."""

    def default(self, o: Any) -> Any:
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, np.datetime64):
            return str(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (pd.Timestamp, datetime.datetime)):
            return o.strftime("%Y-%m-%dT%H:%M:%S")
        return super().default(o)


def _fig_to_json(figure: dict) -> str:
    """Serialisiert ein Figure-Dict (data/layout/config) zu JSON (P0#1)."""
    return json.dumps(figure, cls=_NumpyJSONEncoder)


def _js_apply_figure(fig_json: str) -> str:
    """Baut JS zum Aktualisieren des Charts per Plotly.react (P0#1).

    Aktualisiert NUR den Chart auf der BEREITS GELADENEN Page-Shell
    (festes Div _CHART_DIV_ID) – kein setHtml/Seiten-Reload, Zoom/Pan und
    die plotly.min.js bleiben erhalten. Plotly.react diffed data/layout und
    rendert nur die Aenderungen (0,5–2s Seiten-Reload -> ~50–150ms).
    """
    return (
        "(function(){var gd=" + _JS_CHART_DIV + ";"
        "if(!gd||typeof Plotly==='undefined')return;"
        "var fig=" + fig_json + ";"
        "Plotly.react(gd,fig.data,fig.layout,fig.config);})()"
    )


def _js_add_overlay(trace: dict) -> str:
    """Baut JS zum inkrementellen Hinzufuegen eines Overlay-Trace.

    Wird per Plotly.addTraces auf der BEREITS GELADENEN Seite ausgefuehrt
    (kein setHtml/kein Seiten-Neuaufbau) -> Zoom/Skala bleiben erhalten.
    x-Epochs werden in Datums-Strings umgewandelt (Datumsachse).
    Phase 7: Stil-Attribute (dash/width/symbol/size/render) werden wie im
    HTML-Render gesetzt.
    P0#3 (Optimierung): Datums-Konvertierung VEKTORISIERT (302 ms -> ~19 ms
    bei 5000 Punkten) statt Element-Schleife.
    """
    x_iso = _epochs_to_iso(trace["x"])
    mode = str(trace.get("render", "line"))
    if mode not in ("line", "lines+markers", "markers"):
        mode = "line"
    # Intern "line" -> Plotly-mode "lines" (Plotly kennt kein "line").
    plotly_mode = "lines" if mode == "line" else mode
    color = trace.get("color", "#ff7f0e")
    payload: dict = {
        "name": trace.get("name", "Overlay"),
        "x": x_iso,
        "y": trace.get("y", []),
        "mode": plotly_mode,
        "hovertemplate": "%{y:.4f}<extra>%{fullData.name}</extra>",
    }
    if mode in ("line", "lines+markers"):
        line = {"color": color, "width": float(trace.get("width", 1.5))}
        if trace.get("dash"):
            line["dash"] = str(trace["dash"])
        payload["line"] = line
    if mode in ("markers", "lines+markers"):
        marker = {"color": color, "size": float(trace.get("size", 6))}
        if trace.get("symbol"):
            marker["symbol"] = str(trace["symbol"])
        payload["marker"] = marker
    trace_json = json.dumps(payload)
    return (
        "(function(){var gd=" + _JS_CHART_DIV + ";"
        "if(!gd||typeof Plotly==='undefined')return;"
        f"Plotly.addTraces(gd,{trace_json});}})()"
    )


def _js_remove_overlay(idx: int) -> str:
    """Baut JS zum Entfernen eines Overlay-Trace per Index (deleteTraces)."""
    return (
        "(function(){var gd=" + _JS_CHART_DIV + ";"
        "if(!gd||typeof Plotly==='undefined')return;"
        f"if({idx}<(gd.data||[]).length)Plotly.deleteTraces(gd,{idx});}})()"
    )


def _js_update_overlay(idx: int, trace: dict) -> str:
    """Baut JS zum Aktualisieren eines Overlay-Trace per Index (restyle).

    P0#3: Datums-Konvertierung vektorisiert (siehe _js_add_overlay).
    """
    x_iso = _epochs_to_iso(trace["x"])
    payload: dict = {"x": x_iso, "y": trace.get("y", [])}
    if trace.get("render") in ("line", "lines+markers"):
        line = {"width": float(trace.get("width", 1.5))}
        if trace.get("dash"):
            line["dash"] = str(trace["dash"])
        payload["line"] = line
    if trace.get("render") in ("markers", "lines+markers"):
        marker = {"size": float(trace.get("size", 6))}
        if trace.get("symbol"):
            marker["symbol"] = str(trace["symbol"])
        payload["marker"] = marker
    payload_json = json.dumps(payload)
    return (
        "(function(){var gd=" + _JS_CHART_DIV + ";"
        "if(!gd||typeof Plotly==='undefined')return;"
        f"if({idx}<(gd.data||[]).length)Plotly.restyle(gd,{payload_json},{idx});}})()"
    )


class AlgoPlaygroundController(QObject):
    """Presenter des Algo-Playgrounds (Phase 2-4: Liste, Dialog, Params, Canvas)."""

    # P1#5 (Optimierung): Obergrenze des RAM-Caches pro (Symbol, TF)-Paar.
    # Der Cache wird ZEITRAUM-basiert geladen (statt "letzte 5000") – die
    # Grenze schuetzt nur den Speicher bei sehr grossen Zeitraeumen
    # (z. B. YTD/M1 ~320k Kerzen -> letzte 200k reichen fuer den sichtbaren
    # Bereich + LOD-Render).
    MAX_CACHE_CANDLES = 200_000
    # Panning-Margin beim Laden (Anteil des Zeitraums je Seite): kleine
    # Zeitraum-Verschiebungen innerhalb des Puffers laden NICHT neu.
    CACHE_MARGIN_RATIO = 0.15
    # P0#2: LOD-Grenze fuer Overlay-Serien (Rendering-Punkte je Trace).
    LOD_MAX_OVERLAY_POINTS = 1200
    # P2#10: nach dieser Idle-Zeit (s) pollen wir den Plotly-View nicht mehr
    # periodisch (nur bei Interaktion lesen; save_state liest explizit).
    VIEW_POLL_IDLE_S = 5.0
    # Bugfix 17.08.2026 (SMA-Linie unsichtbar, Legende sichtbar):
    # QWebEngine kann loadFinished bei raschen setHtml-Aufrufen verlieren
    # (bekanntes Problem, s. playground_chart_service-Kommentar). Bleibt
    # _page_ready dadurch dauerhaft False, wuerde jede Folge-Figur per
    # setHtml (Vollreload) statt per Plotly.react angewendet -> die Seite
    # kann in einem veralteten Zustand haengen (Overlay-Legende da, Linie
    # fehlt). Der View-Timer (500ms) heilt diesen Zustand: laengere Zeit
    # kein loadFinished -> Pending-Figur erneut per react anwenden
    # (idempotent; laeuft still ins Leere, solange Plotly fehlt).
    SHELL_READY_TIMEOUT_S = 2.5

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
        # P1#5 (Optimierung): tatsaechliche Daten-Grenzen des Caches
        # (min/max der 'time'-Spalte) – Grundlage der Zeitraum-Abdeckung
        # (_cache_covers, kein Reload bei kleinen Verschiebungen).
        self._cache_from: Optional[int] = None
        self._cache_to: Optional[int] = None
        # Bugfix 17.08.2026 (Datums-Persistenz): Max-Epoch des KACHES VOR dem
        # letzten (force-)Reload. _extend_range_to_latest erweitert das Bis-
        # Datum NUR, wenn der Anwender vorher schon am Datumsrand war – ein
        # bewusst gewaehlter (aelt.) Zeitraum wird durch den Sync nicht mehr
        # ueberschrieben (Restore von Datumseinstellungen bleibt erhalten).
        self._last_cache_max: Optional[int] = None
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
        # P2#10 (Optimierung): Zeitpunkt der letzten Interaktion – der
        # View-Timer pollt nur noch 5s nach einer Interaktion (sonst Idle-
        # Skip; save_state liest beim Schliessen explizit + 300ms Event-Pump).
        self._last_interaction: float = time.monotonic()
        # P0#1 (Optimierung): Zustand der Page-Shell (setHtml nur 1x, alle
        # Folge-Render per Plotly.react auf der geladenen Seite).
        self._shell_set: bool = False
        self._page_ready: bool = False
        self._pending_figure_json: Optional[str] = None
        self._pending_empty: bool = False
        # Bugfix 17.08.2026 (Self-Heal): Zeitpunkt des letzten setHtml der
        # Shell. _poll_view wendet die Pending-Figur per react erneut an,
        # wenn die Seite nach SHELL_READY_TIMEOUT_S immer noch nicht
        # _page_ready meldet (verlorenes loadFinished in QWebEngine).
        self._shell_set_at: float = 0.0
        # Bugfix 17.08.2026 (View-Persistenz): 500ms statt 2s – der Zoom
        # ist damit max. 0.5s alt, wenn save_state gelesen wird.
        self._view_timer = QTimer(self)
        self._view_timer.setInterval(500)
        self._view_timer.timeout.connect(self._poll_view)
        self._view_timer.start()
        # Reihenfolge der AKTUELL in der Figur sichtbaren Overlay-Traces
        # (wird bei jedem _render_chart neu gesetzt; Dokumentation der
        # Trace-Reihenfolge hinter dem Candlestick auf Index 0).
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

        # P0#1 (Optimierung): loadFinished der Canvas-Page abonnieren – sobald
        # die Page-Shell geladen ist, laufen alle Folge-Render per
        # Plotly.react (kein setHtml/Seiten-Reload mehr). Defensiv: FakeViews
        # in Tests haben ggf. keine echte QWebEnginePage (kein loadFinished).
        canvas = getattr(self.ui, "canvas", None)
        if canvas is not None:
            page_fn = getattr(canvas, "page", None)
            if callable(page_fn):
                try:
                    qpage = page_fn()
                except Exception:
                    qpage = None
                if qpage is not None:
                    lf = getattr(qpage, "loadFinished", None)
                    if lf is not None:
                        try:
                            lf.connect(self._on_canvas_load_finished)
                        except Exception:
                            pass

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
            # robust via _render_chart/Plotly.react, Bugfix 17.08.2026).

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
        # Bugfix 17.08.2026 (SMA-Overlay-Regression): Die Figur wird per
        # Plotly.react neu gerendert – sie enthaelt die Overlays der
        # verbleibenden Instanzen (kein setHtml, Zoom bleibt via
        # _pending_view). Der fruehere inkrementelle deleteTraces-Pfad
        # verlor das Overlay still, wenn die Seite nicht bereit war.
        self._render_chart(preserve_view=True)
        self.ui.status_label.setText(
            f"Status: {algo_id} entfernt (aktiv: {len(self.active_algos)})")

    def _on_visibility_changed(self, algo_id: str, instance_key: str,
                               checked: bool) -> None:
        """Checkbox geaendert: Overlay ein/aus (robust via Plotly.react).

        USER-REQ (17.08.2026): kein kompletter Canvas-Neuaufbau – KEIN
        setHtml/kein Seiten-Reload. Die Figur wird per Plotly.react()
        aktualisiert und enthaelt exakt die sichtbaren Overlay-Traces;
        der aktuelle Zoom bleibt via _pending_view (Phase 6) erhalten.

        Bugfix 17.08.2026 (SMA-Overlay-Regression): Der fruehere
        inkrementelle Pfad (Plotly.addTraces/deleteTraces) verlor Overlays
        still, wenn das Worker-Ergebnis vor dem Seiten-Load eintraf
        (gd=null / Plotly undefined). Der react-Pfad ist gegen diese Race
        robust: die Figur enthaelt die Overlays IMMER im Figure-Dict,
        unabhaengig vom JS-Seiten-Zustand.
        """
        state = "ein" if checked else "aus"
        self.ui.status_label.setText(
            f"Status: {algo_id} Darstellung {state}")
        self._render_chart(preserve_view=True)

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
        # Bugfix 17.08.2026 (View-Persistenz): den allerletzten Zoom/eingriff
        # einfangen, auch wenn der View-Timer ihn noch nicht gecacht hat.
        # Der begrenzte Event-Pump (max. 300ms) liefert den asynchronen
        # runJavaScript-Callback, bevor der State geschrieben wird. Kein
        # QEventLoop/nested exec (blockierte sonst UI + Datumsfelder).
        if self._request_view_read():
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                try:
                    QApplication.processEvents()
                except Exception:
                    break
                time.sleep(0.005)

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
            # Bugfix 17.08.2026 (Symbol/TF-Persistenz): Symbol und Timeframe
            # werden mitgespeichert und beim Neustart wiederhergestellt.
            "symbol": getattr(self.ui, "current_symbol", lambda: None)(),
            "timeframe": getattr(self.ui, "current_timeframe", lambda: None)(),
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

        # -- Symbol + Timeframe (Bugfix 17.08.2026) ----------------------
        # Signal-blockiert setzen: der folgende refresh_chart() (main_win)
        # laedt dann das restaurierte Paar – ein vorzeitiges refresh_chart
        # pro Combo-Change (jeweils alter Zustand) entfaellt.
        symbol = data.get("symbol")
        if isinstance(symbol, str) and hasattr(self.ui, "symbol_combo"):
            combo = self.ui.symbol_combo
            combo.blockSignals(True)
            try:
                if combo.findText(symbol) < 0:
                    combo.addItem(symbol)
                combo.setCurrentText(symbol)
            finally:
                combo.blockSignals(False)
        timeframe = data.get("timeframe")
        if isinstance(timeframe, str) and hasattr(self.ui, "tf_combo"):
            combo = self.ui.tf_combo
            combo.blockSignals(True)
            try:
                if combo.findText(timeframe) < 0:
                    combo.addItem(timeframe)
                combo.setCurrentText(timeframe)
            finally:
                combo.blockSignals(False)

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
        """Laedt OHLCV-Kerzen zeitraum-basiert (P1#5, statt 'letzte 5000').

        P1#5 (Optimierung, behebt A4 'fast leeres 30-Tage-Chart'):
          * Geladen wird der AKTUELLE Zeitraum [from_epoch, to_epoch] plus
            Panning-Margin (CACHE_MARGIN_RATIO) – nicht mehr pauschal die
            letzten CANDLE_LIMIT-Kerzen.
          * Der Cache bleibt gueltig, solange ein Zeitraum-Wechsel innerhalb
            der geladenen Grenzen liegt (_cache_covers, kein Neuladen).
          * Nach einem Scan (force_reload) wird der Cache verworfen und
            frisch geladen (Bugfix 17.08.2026: alte Kerzen trotz neuer
            DB-Daten waren sonst sichtbar).
        """
        if not hasattr(self.ui, "current_symbol") or \
                not hasattr(self.ui, "current_timeframe"):
            return
        symbol = self.ui.current_symbol()
        timeframe = self.ui.current_timeframe()
        pair = (symbol, timeframe)
        from_epoch, to_epoch = self.ui.time_range.get_range()
        if (not force_reload and self._last_pair == pair and
                self._candles_cache is not None and
                self._cache_covers(from_epoch, to_epoch)):
            return  # Cache fuer dieses Paar deckt den Zeitraum ab

        # Bugfix 17.08.2026 (Datums-Persistenz): Max-Epoch des ALTEN Caches
        # merken, damit _extend_range_to_latest den Anwender-Zeitraum nicht
        # ungewollt ueberschreibt (nur am Datumsrand erweitern).
        if self._candles_cache is not None and not self._candles_cache.empty:
            self._last_cache_max = int(self._candles_cache["time"].max())
        else:
            self._last_cache_max = None

        self._last_pair = pair
        # P1#5: Zeitraum + Margin als Query-Fenster (Panning-Puffer).
        span = max(1, int(to_epoch) - int(from_epoch))
        margin = int(span * self.CACHE_MARGIN_RATIO)
        q_from = int(from_epoch) - margin
        q_to = int(to_epoch) + margin
        candles, _ = self._data_repo.fetch_candles_in_range(
            symbol, timeframe, q_from, q_to, limit=self.MAX_CACHE_CANDLES)
        if candles:
            self._candles_cache = pd.DataFrame(candles)
            # Tatsaechliche Daten-Grenzen (nicht Query-Fenster): bei Cap-
            # Ueberschreitung (sehr grosse Zeitraeume) ist nur der geladene
            # Teil abgedeckt.
            self._cache_from = int(self._candles_cache["time"].min())
            self._cache_to = int(self._candles_cache["time"].max())
        else:
            # Keine Daten (noch) vorhanden -> leerer Zustand.
            self._candles_cache = None
            self._cache_from = None
            self._cache_to = None
        self.ui.status_label.setText(
            f"Status: {len(candles)} Kerzen geladen ({symbol} {timeframe})")

    def _cache_covers(self, from_epoch: int, to_epoch: int) -> bool:
        """Prueft, ob der RAM-Cache den Zeitraum abdeckt (P1#5).

        Toleranz: 1% des Zeitraums (min. 1 Stunde) – verhindert Reload-
        Flapping an den Raendern (z. B. Bis-Datum kurz hinter der letzten
        Kerze = 'Zukunft', fuer die es keine Daten gibt).
        """
        if self._candles_cache is None or self._candles_cache.empty:
            return False
        if self._cache_from is None or self._cache_to is None:
            return False
        from_epoch = int(from_epoch)
        to_epoch = int(to_epoch)
        span = max(1, to_epoch - from_epoch)
        slack = max(int(span * 0.01), 3600)
        return (self._cache_from <= from_epoch + slack and
                self._cache_to >= to_epoch - slack)

    def _extend_range_to_latest(self) -> None:
        """Erweitert das Bis-Datum auf die neueste Kerze (nach Sync).

        Nur wenn die neueste gecachte Kerze neuer als das aktuelle Bis-Datum
        ist UND der Anwender vorher schon am Datumsrand war (Bis >= Max des
        vorherigen Caches). Sonst bleibt ein bewusst gewaehlter (aelt.)
        Zeitraum unangetastet – restaurierte Datumseinstellungen werden
        durch den Startup-Sync nicht mehr ueberschrieben (Bugfix
        17.08.2026). Von/Bis setzen emittiert range_changed ->
        _on_range_changed rendert den Canvas einmal neu (keine Schleife).
        """
        if self._candles_cache is None or self._candles_cache.empty:
            return
        max_epoch = int(self._candles_cache["time"].max())
        _, to_epoch = self.ui.time_range.get_range()
        if max_epoch <= to_epoch:
            return
        # Nur erweitern, wenn der Anwender vorher am Datumsrand war. Nach
        # einem Restore/Neustart ist _last_cache_max None (frischer Cache) ->
        # der restaurierte Zeitraum bleibt exakt erhalten.
        if self._last_cache_max is not None and to_epoch >= self._last_cache_max:
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
        """Zeitraum geaendert: Cache-Check + Slicen + rendern.

        P1#5 (Optimierung): Liegt der neue Zeitraum ausserhalb der geladenen
        Cache-Grenzen (inkl. Panning-Toleranz), wird zeitraum-basiert
        nachgeladen – der Cache bleibt nur fuer kleine Verschiebungen
        erhalten (Konzept 2.4).

        Bugfix 17.08.2026 (Datumsfelder setzen die Skala): Ein noch nicht
        konsumierter _pending_view (z. B. Restore-View) darf die NEU
        gewaehlte Zeitraum-Skala nicht ueberschreiben – die Datumsfelder
        sind massgebend (build_chart_figure setzt die X-Achse exakt auf
        [from_epoch, to_epoch]).
        """
        self._pending_view = None
        if self._candles_cache is not None and \
                not self._cache_covers(*self.ui.time_range.get_range()):
            self._load_candles()  # P1#5: Zeitraum ausserhalb des Caches
        self._render_chart(preserve_view=False)

    def _render_chart(self, preserve_view: bool = True) -> None:
        """Baut die Plotly-Figur (Zeitraum-Slice) und wendet sie an (P0#1).

        Args:
            preserve_view: True (Default) -> der zuletzt bekannte Plotly-
                Zoom/Skala wird beim Neuaufbau wieder angewendet (als
                Achsen-Range in die Figur), damit die Ansicht nach
                Checkbox-/Param-Aenderungen nicht zurueckspringt. False ->
                keine Kontinuitaet (z. B. Zeitraum oder Symbol/TF gewechselt);
                ein via restore_state gesetzter _pending_view wird in jedem
                Fall angewendet.

        P0#1 (Optimierung): Statt bei jedem Render ein komplettes HTML zu
        bauen und via setHtml() einen vollen Seiten-Reload (inkl. 4,8 MB
        plotly.min.js) auszuloesen, wird die Figur als JSON gebaut und –
        sobald die Page-Shell geladen ist – per Plotly.react() an die
        bereits geladene Seite uebergeben (kein Reload, Zoom/Pan bleibt
        erhalten; 0,5–2 s -> ~50–150 ms).
        """
        canvas = getattr(self.ui, "canvas", None)
        if canvas is None:
            return  # kein Canvas (z.B. Tests mit FakeView)

        # P2#10: Interaktion merken -> View-Timer pollt die naechsten 5s.
        self._last_interaction = time.monotonic()

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
        # Reihenfolge der sichtbaren Overlay-Traces merken (Doku der
        # Trace-Reihenfolge hinter dem Candlestick auf Index 0).
        self._overlay_order = [t["key"] for t in overlays]

        # Der gespeicherte View (Zoom/Skala) wird direkt als Achsen-Range in
        # die Plotly-Figur eingebettet (kein Post-Render-Skript/kein Race).
        figure = self._chart_service.build_chart_figure(
            self._candles_cache, from_epoch, to_epoch, symbol, timeframe,
            overlays=overlays, initial_view=self._pending_view)
        # Bugfix 17.08.2026: _pending_view nur konsumieren, wenn ein echter
        # Chart gerendert wurde. Leere Render (keine Kerzen -> Figure None)
        # duerfen den Restore-View nicht verwerfen – der View gilt fuer den
        # naechsten Render MIT Daten.
        if figure is not None:
            self._pending_view = None

        self._apply_figure(figure, symbol, timeframe)

        # Nach dem Render den frischen View (Restore-/Standardwerte)
        # einsammeln – fuer den naechsten preserve-Render bzw. save_state.
        QTimer.singleShot(500, self._poll_view)

    def _apply_figure(self, figure: Optional[dict], symbol: str,
                      timeframe: str) -> None:
        """Wendet eine Plotly-Figur auf den Canvas an (P0#1, kein Reload).

        Zustandsmaschine der Page-Shell:
          * `figure is None` (leerer Zeitraum) -> Hinweis-HTML via setHtml;
            die Shell wird beim naechsten Daten-Render neu aufgebaut.
          * Sonst: Ist die Shell bereits geladen (_page_ready), wird die
            Figur per Plotly.react() angewendet (kein setHtml). Ist die
            Seite noch nicht (nachweislich) geladen, wird die Shell mit der
            eingebetteten Figur via setHtml gesetzt – der loadFinished-
            Handler wendet das Pending zusaetzlich per react an (idempotent),
            und Test-Stubs ohne loadFinished sehen die Figur sofort.

        Die setHtml-BaseUrl zeigt auf das assets/-Verzeichnis, damit die
        lokale plotly.min.js-Datei geladen werden kann (Bugfix 17.08.2026:
        ohne baseUrl laedt QWebEngineView keine file://-Scripts).
        """
        canvas = getattr(self.ui, "canvas", None)
        if canvas is None or not hasattr(canvas, "setHtml"):
            return  # kein echter Canvas (z.B. Minimal-FakeView in Tests)
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets")
        base_url = QUrl.fromLocalFile(
            os.path.normpath(assets_dir).replace("\\", "/") + "/")

        if figure is None:
            # Leerer Zustand -> Hinweis-HTML; _pending_view bleibt erhalten.
            self._pending_figure_json = None
            self._pending_empty = True
            self._shell_set = False
            self._shell_set_at = 0.0
            self._page_ready = False
            canvas.setHtml(
                self._chart_service.build_page_html(symbol, timeframe, None),
                base_url)
            return

        self._pending_figure_json = _fig_to_json(figure)
        self._pending_empty = False
        if self._page_ready:
            # P0#1: Seite (Shell) geladen -> nur per Plotly.react.
            self._run_js(_js_apply_figure(self._pending_figure_json))
            return
        # Seite noch nicht geladen -> Shell (einmalig) setzen.
        self._shell_set = True
        self._shell_set_at = time.monotonic()
        canvas.setHtml(
            self._chart_service.build_page_html(
                symbol, timeframe, self._pending_figure_json),
            base_url)

    def _on_canvas_load_finished(self, ok: bool) -> None:
        """Slot: Page-Shell geladen -> Figur frisch per Plotly.react anwenden.

        P0#1: Erst wenn die Shell (plotly.min.js + Chart-Div) geladen ist,
        darf per runJavaScript auf Plotly zugegriffen werden. Loads der
        initialen Platzhalter-/Hinweis-Seiten (_shell_set False) werden
        ignoriert.

        Bugfix 17.08.2026 (SMA-Overlay-Regression): Statt nur die beim
        setHtml eingebettete Pending-Figur erneut per react anzuwenden, wird
        die Figur FRISCH gebaut und gerendert (_render_chart). Dadurch
        erscheinen auch Overlays, deren Worker-Ergebnis VOR dem Seiten-Load
        eintraf und deren addTraces gegen die noch ladende Seite (gd=null)
        still verloren ging. _render_chart nutzt mit _page_ready=True den
        react-Pfad -> kein zweiter setHtml, keine Schleife.

        Bugfix 17.08.2026 (Reload-Schleife): Ein ok=False (abgebrochener
        Load, z. B. setHtml-Abloesung des Platzhalters) darf _page_ready
        NICHT zuruecksetzen – sonst wuerde der naechste Render die Shell
        erneut per setHtml laden (Reload-Schleife) und Overlay-JS wieder in
        die ladende Seite laufen.
        """
        if not self._shell_set:
            return  # kein Shell-Load (z. B. Platzhalter oder _EMPTY_HTML)
        if not ok:
            return  # abgebrochener/interrupted Load ignoriert (kein Downgrade)
        self._page_ready = True
        self._render_chart(preserve_view=False)

    # ------------------------------------------------------------------
    # Phase 6: Plotly-View (Zoom/Skala) lesen + Overlays inkrementell
    # ------------------------------------------------------------------
    def _poll_view(self) -> None:
        """Timer: liest den aktuellen Plotly-View ASYNCHRON (kein Blocking).

        Wird periodisch (500ms) und nach jedem Render (singleShot 500ms)
        ausgefuehrt. runJavaScript ist asynchron – das Ergebnis kommt als
        Queued-Callback im UI-Thread an (_on_view_read) und wird in
        _last_view gecacht. Kein QEventLoop/kein nested exec mehr
        (Bugfix 17.08.2026): der fruehere synchrone QEventLoop blockierte
        bis zu 800ms alle 2s die UI und vertrug sich schlecht mit
        QWebEngineView + QDateTimeEdit-Popups (Datumsfelder wirkten tot).

        P2#10 (Optimierung): Nach VIEW_POLL_IDLE_S ohne Interaktion wird
        nicht mehr gepollt (Idle-Skip) – save_state liest beim Schliessen
        explizit (+ 300ms Event-Pump), daher geht kein Zoom verloren.

        Bugfix 17.08.2026 (Self-Heal, SMA-Linie unsichtbar): QWebEngine
        verliert bei raschen setHtml-Aufrufen gelegentlich loadFinished.
        Bleibt _page_ready dadurch False, wendet dieser Timer die
        Pending-Figur nach SHELL_READY_TIMEOUT_S erneut per Plotly.react
        an (idempotent; no-op, solange Plotly noch fehlt). Kein setHtml ->
        keine Reload-Schleife.
        """
        if self._shell_set and not self._page_ready and \
                self._pending_figure_json is not None:
            if time.monotonic() - self._shell_set_at > self.SHELL_READY_TIMEOUT_S:
                # Seite meldet kein loadFinished -> Pending-Figur erneut per
                # react anwenden (heilt verlorenes loadFinished in QWebEngine).
                self._run_js(_js_apply_figure(self._pending_figure_json))
                self._shell_set_at = time.monotonic()

        if time.monotonic() - self._last_interaction > self.VIEW_POLL_IDLE_S:
            return  # Idle: kein Lesen mehr noetig (P2#10)
        self._request_view_read()

    def _request_view_read(self) -> bool:
        """Startet das asynchrone Lesen des Plotly-Views (nicht blockierend).

        Returns:
            True, wenn ein Leseversuch gestartet wurde (Canvas+Page da),
            sonst False (kein Canvas z. B. in Tests/ohne WebEngine).
        """
        canvas = getattr(self.ui, "canvas", None)
        page = getattr(canvas, "page", None)
        if page is None or not callable(page):
            return False  # z. B. CanvasStub in Tests
        try:
            qpage = page()
        except Exception:
            return False
        if qpage is None:
            return False
        try:
            qpage.runJavaScript(_JS_READ_VIEW, self._on_view_read)
            return True
        except Exception as exc:
            print(f"WARN [Playground] View-Read fehlgeschlagen: {exc}")
            return False

    def _on_view_read(self, value: Any) -> None:
        """Callback von _request_view_read: gueltigen View in _last_view cachen."""
        if isinstance(value, dict) and (value.get("xrange") or value.get("yrange")):
            self._last_view = value

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

    # ------------------------------------------------------------------
    # Phase 5: Overlays (Berechnung, Farben, DB-Persistenz, Debounce)
    # ------------------------------------------------------------------
    def _on_debounce_timeout(self) -> None:
        """Debounce abgelaufen: betroffene Instanz neu berechnen (async).

        Phase 6.1: Die Berechnung startet einen PlaygroundWorker (QThread)
        statt im UI-Thread zu rechnen. Sobald das Ergebnis eintrifft
        (_on_overlay_computed), wird die Figur per Plotly.react neu
        gerendert (kein Canvas-Neuaufbau, Zoom bleibt) – der Zoom und die
        anderen Traces bleiben erhalten.
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
        gespeichert und ggf. per Plotly.react sichtbar gemacht (kein
        setHtml, Zoom bleibt). Eine Generationsnummer pro Instanz verwirft
        veraltete Ergebnisse (z. B. bei schnellen Parameter-Aenderungen
        oder Daten-Neuladen).

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

        # Phase 7: Darstellungs-Keys (line_color/line_style/line_width bzw.
        # marker_*) sind KEINE Fach-Parameter – sie steuern nur das Zeichnen
        # und duerfen nicht an die Algo-Klasse gereicht werden (die kennt sie
        # nicht). Die Instanzierung bleibt damit exakt auf den Fach-Parametern.
        schema = (entry.get("schema") or {})
        algo_params = {
            k: v for k, v in (self.instance_params.get(instance_key) or {}).items()
            if k not in collect_style_keys(schema)
        }

        worker = PlaygroundWorker(
            entry["class"],
            instance_key,
            algo_params,
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
        per Plotly.react gerendert.

        Bugfix 17.08.2026 (SMA-Overlay-Regression): Der fruehere
        inkrementelle Pfad (addTraces per JS) verlor das Overlay still,
        wenn das Worker-Ergebnis vor dem Seiten-Load eintraf (gd=null /
        Plotly undefined). _render_chart(preserve_view=True) baut die Figur
        mit den Overlays IM Figure-Dict und wendet sie per react an
        (kein setHtml, Zoom bleibt via _pending_view) – unabhaengig vom
        JS-Seiten-Zustand zuverlaessig.
        """
        if self._overlay_generation.get(instance_key) != generation:
            return  # veraltet (Param/Instanz inzwischen geaendert)
        if instance_key not in self.ui.algo_panel.instance_keys():
            return  # Instanz wurde inzwischen entfernt
        self._overlays[instance_key] = dict(series)
        # Sichtbar? -> Figur neu rendern (Overlays im Figure-Dict, RAM-only).
        checked: Dict[str, bool] = {}
        if hasattr(self.ui, "algo_panel"):
            checked = self.ui.algo_panel.checked_states()
        if checked.get(instance_key, True) is not False:
            self._render_chart(preserve_view=True)
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
        """Weist einer Instanz eine stabile Overlay-Farbe zu (Farbzyklus).

        Phase 7: Nur FALLBACK – wenn die Instanz keine Stil-Params besitzt
        (z. B. alte gespeicherte States ohne line_color/marker_color).
        """
        if instance_key not in self._overlay_colors:
            idx = len(self._overlay_colors) % len(OVERLAY_COLORS)
            self._overlay_colors[instance_key] = OVERLAY_COLORS[idx]
        return self._overlay_colors[instance_key]

    def _style_for_instance(self, instance_key: str) -> dict:
        """Stil-Attribute einer Instanz aus instance_params (Phase 7).

        Liest die flach persistierten Stil-Params des AlgOS:
          line_color/line_style/line_width  (style_type='line')
          marker_color/marker_symbol/marker_size (style_type='marker')
        Fehlt eine Farbe (alte States), faellt die Instanz auf den stabilen
        Farbzyklus zurueck (Abwaertskompatibilitaet).

        Returns:
            dict mit color (+ optional dash/width/symbol/size).
        """
        params = self.instance_params.get(instance_key) or {}
        color = (params.get("line_color")
                 or params.get("marker_color")
                 or self._color_for_instance(instance_key))
        style: Dict[str, Any] = {"color": color}
        if params.get("line_style"):
            style["dash"] = str(params["line_style"])
        if params.get("line_width") is not None:
            style["width"] = float(params["line_width"])
        if params.get("marker_symbol"):
            style["symbol"] = str(params["marker_symbol"])
        if params.get("marker_size") is not None:
            style["size"] = float(params["marker_size"])
        return style

    def _render_mode_for_result(self, algo_id: str, name: str) -> str:
        """Render-Modus eines Ergebnis-Feldes (Phase 7).

        result_schema[field]['store']:
          'series' (Durchgehende Linie) -> "line"
          'agg'    (Signal je Lauf, KEINE Linie) -> "markers"
        Optionales 'render'-Feld im result_schema ueberschreibt den Modus
        ("line"|"lines+markers"|"markers"). Default: "line".
        """
        entry = self.registry.get(algo_id) or {}
        rs = (entry.get("result_schema") or {}).get(name) or {}
        mode = rs.get("render")
        if mode in ("line", "lines+markers", "markers"):
            return mode
        return "markers" if rs.get("store") == "agg" else "line"

    def _build_instance_traces(self, instance_key: str) -> List[dict]:
        """Baut die Overlay-Trace-Dicts EINER Instanz (Zeitraum-Slice).

        Pro Ergebnis-Feld der Instanz ein Trace mit eindeutigem "key"
        ("instance_key::name") – wird fuer die Plotly-Figur des
        PlaygroundChartService verwendet (Figure-Render, Bugfix 17.08.2026:
        Overlays stecken im Figure-Dict, kein fragiles addTraces-JS).
        Phase 7: Farbe/Linienart/Staerke/Symbol kommen aus den Stil-Params
        der Instanz; der Render-Modus aus dem result_schema.
        USER-REQ (17.08.2026, alg_ma dual_color): Ein Overlay-Wert kann ein
        Tupel (pd.Series, Farbliste je Punkt) sein – wird in Segment-Traces
        je Farbblock gesplittet (_build_segment_traces).
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
        style = self._style_for_instance(instance_key)
        from_epoch, to_epoch = self.ui.time_range.get_range()
        traces: List[dict] = []
        for name, value in overlays.items():
            if value is None or len(value) == 0:
                continue
            # dual_color: Tupel (Serie, Pro-Punkt-Farben) -> Segment-Traces.
            if (isinstance(value, tuple) and len(value) == 2 and
                    isinstance(value[1], (list, tuple))):
                traces.extend(self._build_segment_traces(
                    instance_key, name, value[0], list(value[1]),
                    algo_id, style, from_epoch, to_epoch))
                continue
            series = value
            # Zeitraum-Slice (Konzept 2.4: Slicen statt Neuladen).
            mask = (series.index >= int(from_epoch)) & \
                   (series.index <= int(to_epoch))
            s = series.loc[mask]
            if s.empty:
                continue
            # P0#2 (Optimierung): LOD fuer Overlay-Serien – bei sehr grossen
            # Zeitraeumen (z. B. 30 Tage M1 = 43k Punkte) nur noch max.
            # LOD_MAX_OVERLAY_POINTS gleichmaessig verteilte Punkte rendern
            # (vektorisiert, Endpunkte bleiben erhalten). Der RAM-Cache der
            # Serie bleibt unveraendert (nur der Render wird reduziert).
            if len(s) > self.LOD_MAX_OVERLAY_POINTS:
                s = self._decimate_series(s, self.LOD_MAX_OVERLAY_POINTS)
            trace = {
                "key": f"{instance_key}::{name}",
                "name": f"{algo_id} ({name})",
                "x": s.index.tolist(),   # Epoch-Ints (HTML/JS-Konvertierung)
                "y": s.tolist(),
                "color": style["color"],
                "render": self._render_mode_for_result(algo_id, name),
            }
            if "dash" in style:
                trace["dash"] = style["dash"]
            if "width" in style:
                trace["width"] = style["width"]
            if "symbol" in style:
                trace["symbol"] = style["symbol"]
            if "size" in style:
                trace["size"] = style["size"]
            traces.append(trace)
        return traces

    def _build_segment_traces(
        self, instance_key: str, name: str, series: pd.Series,
        colors: list, algo_id: str, style: Dict[str, Any],
        from_epoch: int, to_epoch: int,
    ) -> List[dict]:
        """Baut je Farbwert EINEN Trace mit NaN-Luecken zwischen Farbbloecken.

        USER-REQ (17.08.2026, alg_ma dual_color): Die MA-Serie traegt eine
        Pro-Punkt-Farbe (bull/bear). Plotly kann in EINEM Linien-Trace keine
        wechselnden Farben darstellen – daher wird die Serie je eindeutiger
        Farbe dupliziert und an andersfarbigen Abschnitten auf NaN gesetzt
        (connectgaps=False -> sichtbare Brueche statt Verbindung). Zeitraum-
        Slice + LOD wie beim Einzel-Trace (vektorisiert, keine Loops).

        Args:
            instance_key: Instanz-Key (fuer den Trace-"key").
            name: Ergebnis-Feld-Name (z. B. "ma").
            series: MA-Serie (Index = Epoch-Ints, Wanduhr).
            colors: Farbe je Punkt (gleiche Laenge wie series).
            algo_id: Registry-ID (fuer den Trace-"name").
            style: Stil-Dict aus _style_for_instance (dash/width).
            from_epoch, to_epoch: Zeitraum-Slice-Grenzen.
        """
        traces: List[dict] = []
        if series is None or len(series) == 0:
            return traces
        colors_list = list(colors)
        n = len(series)
        if len(colors_list) != n:
            # Defensiv angleichen (trimmen bzw. mit letzter Farbe auffuellen).
            if len(colors_list) > n:
                colors_list = colors_list[:n]
            elif colors_list:
                last = colors_list[-1]
                colors_list = colors_list + [last] * (n - len(colors_list))
            else:
                colors_list = ["#ff7f0e"] * n
        values = pd.to_numeric(series, errors="coerce")
        valid = ~values.isna()
        # Eindeutige Farben in Reihenfolge des ersten Auftretens (bei
        # bull/bear maximal 2, defensiv beliebig viele).
        unique: List[str] = []
        for c in colors_list:
            if c not in unique:
                unique.append(str(c))
        for color in unique:
            mask = (np.asarray(colors_list) == color) & np.asarray(valid)
            seg = values.where(pd.Series(mask, index=values.index))
            # Zeitraum-Slice
            seg = seg.loc[(seg.index >= int(from_epoch)) &
                          (seg.index <= int(to_epoch))]
            if seg.empty:
                continue
            if len(seg) > self.LOD_MAX_OVERLAY_POINTS:
                seg = self._decimate_series(seg, self.LOD_MAX_OVERLAY_POINTS)
            trace: Dict[str, Any] = {
                "key": f"{instance_key}::{name}::{color}",
                "name": f"{algo_id} ({name})",
                "x": seg.index.tolist(),   # Epoch-Ints
                "y": seg.tolist(),
                "color": color,
                "render": "line",
                # connectgaps=False: NaNs (Farbwechsel) als sichtbare Brueche
                # (der Einzel-Trace nutzt connectgaps=True, Bugfix SMA).
                "connectgaps": False,
            }
            if "dash" in style:
                trace["dash"] = style["dash"]
            if "width" in style:
                trace["width"] = style["width"]
            traces.append(trace)
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
    @staticmethod
    def _decimate_series(s: pd.Series, max_points: int) -> pd.Series:
        """Gleichmaessiges Downsampling einer Serie (P0#2, vektorisiert).

        Behaelt Endpunkte: np.linspace(0, n-1, max_points) liefert
        gleichmaessig verteilte Indizes inkl. erstem/letztem Punkt – kein
        Loop (Vektorisierung pur, Agents.md).
        """
        n = len(s)
        if n <= max_points:
            return s
        idx = np.linspace(0, n - 1, max_points).astype(int)
        return s.iloc[idx]

    def _schema_defaults(self, algo_id: str) -> Dict[str, Any]:
        """Liest die Default-Werte aus dem parameter_schema eines AlgOS."""
        schema = self.registry.get(algo_id, {}).get("schema", {})
        return {
            name: spec.get("default")
            for name, spec in schema.items()
        }
