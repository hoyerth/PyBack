# ui/playground_chart_service.py
"""
ui/playground_chart_service.py - Plotly-HTML-Builder fuer den Playground-Canvas.

Phase 4: Baut aus einem OHLCV-DataFrame (Controller-Cache, Konzept 2.4) ein
standalone Plotly-HTML mit Candlestick + Dark-Theme und Zeitraum-Slice.

  * Candlestick (Kurs) – noch OHNE Algo-Overlays (Phase 5 fuegt die
    Overlay-Hooks hinzu).
  * Zeitraum-Slice: der sichtbare Ausschnitt [from_epoch, to_epoch] wird
    client-seitig aus dem bereits gecachten DataFrame geschnitten – kein
    Neuladen aus DuckDB (Konzept 2.4).
  * plotly.js wird OFFLINE als LOKALE DATEI referenziert (assets/plotly.min.js,
    aus plotly/package_data kopiert) statt inline eingebettet zu werden.
    Grund (Bugfix 17.08.2026): QWebEngineView.setHtml() rendert HTML mit
    4,8 MB Inline-JS NICHT zuverlaessig (Page bleibt auf dem vorherigen
    Inhalt stehen) – die Datei-Referenz haelt das setHtml-HTML klein (~30 KB)
    und rendert stabil.
  * Dark-Theme passend zur App (plotly_dark + dunkler Body-Hintergrund).

Nur Build-Logik (SRP): KEIN Qt-Import, KEIN DuckDB-Zugriff.
"""

import math
import os
from typing import Optional

import numpy as np
import pandas as pd

# Pfad zur lokalen (offline) plotly.min.js – relativ zum ui/-Paket.
_PLOTLY_JS_REL = os.path.join("..", "assets", "plotly.min.js")
_PLOTLY_JS_ABS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), _PLOTLY_JS_REL))
_PLOTLY_JS_URL = _PLOTLY_JS_ABS.replace("\\", "/")

# USER-REQ (17.08.2026): Mess-Tool (aus PyTrader uebernommen, Plotly-Adaption,
# Aktivierung Shift+Rechtsklick) – assets/measurement.js als LOKALE Datei
# referenziert (wie plotly.min.js), damit die Page-Shell klein bleibt.
_MEASURE_JS_REL = os.path.join("..", "assets", "measurement.js")
_MEASURE_JS_ABS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), _MEASURE_JS_REL))
_MEASURE_JS_URL = _MEASURE_JS_ABS.replace("\\", "/")

# Plotly-Config fuer TradingView/MT5-aehnliches Verhalten im Canvas
# (Anwender-Anforderung, 17.08.2026):
#   * scrollZoom=True      - Mausrad zoomt direkt an der Cursor-Position
#   * displayModeBar=True  - Toolbar anzeigen (Zoom/Pan/SaveImage)
#   * modeBarButtonsToRemove - lasso2d/select2d (unnoetige Auswahl-Tools)
#   * dragmode='pan'       - normaler Klick-und-Drag VERSCHIEBT den Chart
#     (statt Auswahlrechteck), wie bei TradingView/MT5
_PLOTLY_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    "responsive": True,
    "displaylogo": False,
}

# Dunkler Body-Hintergrund passend zum plotly_dark-Template (#111418 ist die
# plotly_dark-Paper-Farbe, damit der Canvas nahtlos dunkel wirkt).
_BODY_STYLE = "margin:0;padding:0;background:#111418;overflow:hidden"

# Automatischer Farbzyklus fuer Algo-Overlays (Phase 5.3: erstmal automatisch;
# spaeter je Algo einstellbar). Helle, auf Dark-Theme gut lesbare Farben.
OVERLAY_COLORS = [
    "#ff7f0e",  # orange
    "#1f77b4",  # blau
    "#2ca02c",  # gruen
    "#d62728",  # rot
    "#9467bd",  # violett
    "#17becf",  # tuerkis
    "#e377c2",  # pink
    "#bcbd22",  # oliv
    "#7f7f7f",  # grau
    "#8c564b",  # braun
]
_EMPTY_HTML = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>PyBack Playground</title></head>
<body style="{_BODY_STYLE}">
<div style="color:#8a939c;font-family:sans-serif;font-size:14px;
            padding:24px;height:100vh;box-sizing:border-box">
Keine Daten im gewaehlten Zeitraum.
</div>
</body>
</html>"""

# P0#2 (Optimierung): LOD-Grenze fuer den Candlestick-Render. Bei mehr
# sichtbaren Kerzen wird vektorisiert (np.minimum/maximum.reduceat) zu
# OHLC-Buckets zusammengefasst – der Chart bleibt bei 30-Tage-M1 (43k
# Kerzen) reaktionsfaehig, ohne dass Zoom/Pan darunter leiden.
_LOD_MAX_CANDLES = 1200
# P2#8 (Optimierung): "x unified"-Hover ist bei > dieser Kerzenzahl zu teuer
# -> automatisch auf "closest" umschalten (nur der sichtbare Ausschnitt
# zaehlt; die LOD-Reduktion senkt die gerenderten Punkte zusaetzlich).
_HOVER_UNIFIED_MAX = 1200

# Id der festen Chart-Div in der Page-Shell (P0#1: setHtml nur 1x; alle
# Daten-Updates laufen ueber Plotly.react auf dieser Div).
_CHART_DIV_ID = "pg-chart"


class PlaygroundChartService:
    """Erzeugt Plotly-Figuren + Page-Shell fuer den Playground-Canvas.

    P0#1 (Optimierung): Statt bei JEDER Aenderung ein komplettes HTML zu
    bauen und via setHtml() einen vollen Seiten-Reload (inkl. 4,8 MB
    plotly.min.js) auszuloesen, liefert dieser Service jetzt:
      * build_chart_figure()  – das Figure-Dict (data/layout/config) als
        schlankes, JSON-serialisierbares Plain-Dict (LOD + Hover-Adaption
        inklusive),
      * build_page_html()     – die EINMALIGE Page-Shell (plotly.min.js +
        feste Chart-Div), in die die Figur beim ersten Render eingebettet
        wird.
    Alle Folge-Render laufen im Controller per Plotly.react() auf der
    bereits geladenen Seite (kein Seiten-Reload, Zoom/Pan bleibt erhalten).

    Nur Build-Logik (SRP): KEIN Qt-Import, KEIN DuckDB-Zugriff.
    """

    @staticmethod
    def build_candlestick_html(
        candles_df: pd.DataFrame,
        from_epoch: int,
        to_epoch: int,
        symbol: str,
        timeframe: str,
        hide_gaps: bool = True,
        overlays: Optional[list] = None,
        initial_view: Optional[dict] = None,
    ) -> str:
        """Baut das komplette Candlestick-HTML (Wrapper, Abwaertskompatibilitaet).

        P0#1: Delegiert an build_chart_figure() + build_page_html(). Wird
        nur noch fuer den ERSTEN Render (setHtml der Page-Shell) und fuer
        Tests genutzt; alle Folge-Render laufen ueber Plotly.react().

        Returns:
            Standalone-HTML-String (plotly.js offline als Datei referenziert);
            bei leerem Zeitraum der "Keine Daten"-Hinweis.
        """
        figure = PlaygroundChartService.build_chart_figure(
            candles_df, from_epoch, to_epoch, symbol, timeframe,
            hide_gaps=hide_gaps, overlays=overlays,
            initial_view=initial_view)
        import json
        fig_json = None
        if figure is not None:
            fig_json = json.dumps(figure)
        return PlaygroundChartService.build_page_html(
            symbol, timeframe, fig_json)

    @staticmethod
    def build_chart_figure(
        candles_df: pd.DataFrame,
        from_epoch: int,
        to_epoch: int,
        symbol: str,
        timeframe: str,
        hide_gaps: bool = True,
        overlays: Optional[list] = None,
        initial_view: Optional[dict] = None,
    ) -> Optional[dict]:
        """Baut das Plotly-Figure-Dict (data/layout/config) fuer den Zeitraum.

        P0#1/P0#2 (Optimierung):
          * Rueckgabe ist ein schlankes Plain-Dict (keine go.Figure/
            to_plotly_json-Base64), direkt als JSON in die Page-Shell
            einbettbar bzw. per Plotly.react() an die geladene Seite
            uebergebbar.
          * LOD: Bei > _LOD_MAX_CANDLES sichtbaren Kerzen wird vektorisiert
            (np.minimum/maximum.reduceat) zu OHLC-Buckets zusammengefasst.
          * P2#8: Hover-Modus adaptiv ("x unified" bis _HOVER_UNIFIED_MAX,
            darueber "closest").

        Args: siehe build_candlestick_html().

        Returns:
            {"data": [...], "layout": {...}, "config": {...}} oder None,
            wenn keine Kerzen im Zeitraum liegen (leerer Zustand).
        """
        if candles_df is None or candles_df.empty:
            return None

        # Vektorisiertes Zeitraum-Slicen (Konzept 2.4, kein Neuladen).
        mask = (candles_df["time"] >= int(from_epoch)) & \
               (candles_df["time"] <= int(to_epoch))
        df = candles_df.loc[mask]

        if df.empty:
            return None

        # Aufsteigend sortieren (DB liefert aufsteigend, defensiv nocheinmal).
        df = df.sort_values("time")

        # Rangebreaks auf den ORIGINAL-Zeiten berechnen (P0#2: vor dem
        # Downsampling, damit die Luecken-Grenzen exakt bleiben).
        breaks = None
        if hide_gaps:
            breaks = PlaygroundChartService._build_rangebreaks(
                df["time"].to_numpy())

        # P2#8: Hover-Entscheidung vor dem Downsampling (originale Anzahl).
        orig_count = len(df)
        hovermode = "x unified" if orig_count <= _HOVER_UNIFIED_MAX \
            else "closest"

        # P0#2: LOD-Downsampling (nur fuer den RENDER, Cache bleibt voll).
        df = PlaygroundChartService._maybe_downsample(df, _LOD_MAX_CANDLES)

        # Wanduhr-Konvention: Epoch-Zahl ist Wanduhr-encoded; naive
        # Interpretation zeigt exakt die Wanduhr-Zeit (kein Berlin-Offset).
        # ISO-Strings (lokale Parse-Semantik in plotly.js) statt ms-Epochs,
        # damit der Wanduhr-Roundtrip erhalten bleibt.
        x_iso = pd.to_datetime(df["time"].to_numpy(), unit="s").strftime(
            "%Y-%m-%dT%H:%M:%S").tolist()

        data: list = [{
            "type": "candlestick",
            "x": x_iso,
            "open": df["open"].tolist(),
            "high": df["high"].tolist(),
            "low": df["low"].tolist(),
            "close": df["close"].tolist(),
            "name": f"{symbol} {timeframe}",
            # Candlestick-Farben: gruen/rot (klassisch, dunkles Theme).
            "increasing": {"line": {"color": "#26a69a"}},
            "decreasing": {"line": {"color": "#ef5350"}},
        }]

        # Algo-Overlays (Phase 5/7): Linien-/Marker-Traces ueber die Candles.
        # Stil-Attribute (render, dash, width, symbol, size) kommen aus dem
        # StylePickerWidget; die Controller-Traces sind bereits LOD-reduziert.
        for ov in overlays or []:
            if not isinstance(ov, dict):
                continue
            ov_x = ov.get("x")
            ov_y = ov.get("y")
            if ov_x is None or ov_y is None:
                continue
            ov_x_iso = pd.to_datetime(
                np.asarray(list(ov_x), dtype="int64"), unit="s"
            ).strftime("%Y-%m-%dT%H:%M:%S").tolist()
            mode = str(ov.get("render", "line"))
            if mode not in ("line", "lines+markers", "markers"):
                mode = "line"
            # Intern "line" -> Plotly-mode "lines" (Plotly kennt kein "line").
            mode = "lines" if mode == "line" else mode
            color = str(ov.get("color", "#ff7f0e"))
            trace: dict = {
                "type": "scatter",
                "x": ov_x_iso,
                "y": [float(v) for v in ov_y],
                "name": str(ov.get("name", "Overlay")),
                "mode": mode,
                "hovertemplate": "%{y:.4f}<extra>%{fullData.name}</extra>",
            }
            if "lines" in mode:
                trace["line"] = dict(
                    color=color,
                    dash=str(ov.get("dash", "solid")),
                    width=float(ov.get("width", 1.5)),
                    # Bugfix 17.08.2026 (SMA-Linie): connectgaps=True
                    # verhindert sichtbare Luecken/Brueche bei NaN-Warmup
                    # (erste period-1 Kerzen) und internen NaN-Gaps – die
                    # Linie bleibt durchgehend im sichtbaren Bereich.
                    # USER-REQ (17.08.2026, alg_ma dual_color):
                    # Segment-Traces setzen connectgaps=False (Farbwechsel
                    # als sichtbare Brueche) – Default bleibt True.
                    connectgaps=bool(ov.get("connectgaps", True)),
                )
            if "markers" in mode:
                trace["marker"] = dict(
                    color=color,
                    symbol=str(ov.get("symbol", "circle")),
                    size=float(ov.get("size", 6)),
                )
            data.append(trace)

        # Dunkles Theme explizit im Layout (kein Template-JSON noetig, haelt
        # die Figur klein; Farben entsprechen plotly_dark/App-Hintergrund).
        layout: dict = {
            "paper_bgcolor": "#111418",
            "plot_bgcolor": "#111418",
            "font": {"color": "#d8d8d8"},
            "title": {"text": f"{symbol} {timeframe}"},
            "xaxis": {
                "rangeslider": {"visible": False},
                "title": {"text": "Zeit (Wanduhr)"},
                "gridcolor": "#2a2f36",
                "linecolor": "#2a2f36",
                "zerolinecolor": "#2a2f36",
            },
            # Preisachse (Y) auf der RECHTEN Seite wie bei TradingView/MT5
            # (Anwender-Anforderung, 17.08.2026).
            "yaxis": {
                "title": {"text": "Preis"},
                "side": "right",
                "gridcolor": "#2a2f36",
                "linecolor": "#2a2f36",
                "zerolinecolor": "#2a2f36",
            },
            "margin": {"l": 40, "r": 20, "t": 50, "b": 30},
            "autosize": True,
            "hovermode": hovermode,
            "legend": {"orientation": "h", "y": 1.02},
            # Normaler Klick-und-Drag verschiebt den Chart (TradingView-Stil),
            # statt ein Auswahlrechteck zu ziehen.
            "dragmode": "pan",
        }

        # Luecken ausblenden (Wochenende/Pausen/kurze Tage -> TradingView).
        if breaks:
            layout["xaxis"]["rangebreaks"] = breaks

        # Bugfix 17.08.2026 (Datumsfelder setzen die Skala): X-Achse EXPLIZIT
        # auf den gespeicherten View (Restore/Zoom) oder exakt auf den
        # Zeitraum [from_epoch, to_epoch]. Wanduhr-naive ISO-Strings
        # entsprechen exakt dem Kerzen-x-Format (plotly parst beide als
        # lokale Zeit -> konsistent).
        view = initial_view if isinstance(initial_view, dict) else None
        if view and view.get("xrange"):
            layout["xaxis"]["range"] = [view["xrange"][0], view["xrange"][1]]
        else:
            layout["xaxis"]["range"] = [
                pd.to_datetime(int(from_epoch), unit="s").strftime(
                    "%Y-%m-%dT%H:%M:%S"),
                pd.to_datetime(int(to_epoch), unit="s").strftime(
                    "%Y-%m-%dT%H:%M:%S"),
            ]
        if view and view.get("yrange"):
            layout["yaxis"]["range"] = [view["yrange"][0], view["yrange"][1]]

        return {"data": data, "layout": layout, "config": _PLOTLY_CONFIG}

    @staticmethod
    def build_page_html(symbol: str, timeframe: str,
                        figure_json: Optional[str] = None) -> str:
        """Baut die (einmalige) Page-Shell mit der festen Chart-Div.

        P0#1 (Optimierung): Die Shell laedt plotly.min.js NUR HIER (Datei-
        Referenz, kein Inline-4,8-MB-JS) und enthaelt eine feste Div
        (_CHART_DIV_ID). Die Figur wird beim ersten Render als JSON
        eingebettet (Plotly.react beim Laden); alle Folge-Render aktualisieren
        die geladene Seite per Plotly.react() im Controller.

        Args:
            symbol/timeframe: Titel (wird im <title> und Layout gezeigt).
            figure_json: JSON-String des Figures ({"data","layout","config"});
                None -> "Keine Daten"-Hinweis-HTML (_EMPTY_HTML).

        Returns:
            Standalone-HTML-String.
        """
        if not figure_json:
            return _EMPTY_HTML
        # Defensive: "</script>" im eingebetteten JSON unschaedlich machen
        # (JSON erlaubt den "\/"-Escape fuer "/").
        safe_json = figure_json.replace("</", "<\\/")
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>PyBack Playground - {symbol} {timeframe}</title>
<style>
    /* Hoehen-Kette html/body -> 100%, damit das plotly-Div (height:100%)
       den GESAMTEN Canvas ausfuellt statt auf die Default-Hoehe zu fallen
       (Anwender-Anforderung, 17.08.2026). */
    html, body {{ height: 100%; margin: 0; padding: 0;
                  background: #111418; overflow: hidden; }}
    /* USER-REQ (17.08.2026): Mess-Tool-Overlays – Messbox als reines
       CSS-Overlay (wie PyTrader), robust gegen Plotly.react (Geschwister
       der pg-chart-Div in #pg-wrap, pointer-events:none). */
    #pg-wrap {{ position: relative; width: 100%; height: 100%; }}
    #{_CHART_DIV_ID} {{ width: 100%; height: 100%; }}
    #measurement-region {{
        display: none; position: absolute;
        background: rgba(41, 98, 255, 0.15);
        border: 1px dashed #2962FF;
        pointer-events: none; z-index: 10;
    }}
    #measurement-box {{
        display: none; position: absolute;
        background: rgba(17, 20, 24, 0.92);
        border: 1px solid #2962FF; color: #d8d8d8;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px; padding: 6px 8px; border-radius: 4px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
        pointer-events: none; z-index: 11;
        white-space: pre; line-height: 1.4;
    }}
</style>
</head>
<body style="{_BODY_STYLE}">
<script src="{_PLOTLY_JS_URL}"></script>
<div id="pg-wrap">
    <div id="{_CHART_DIV_ID}"></div>
    <div id="measurement-region"></div>
    <div id="measurement-box"></div>
</div>
<script>
(function(){{
  var gd = document.getElementById('{_CHART_DIV_ID}');
  if (!gd || typeof Plotly === 'undefined') return;
  var fig = {safe_json};
  Plotly.react(gd, fig.data, fig.layout, fig.config);
}})();
</script>
<script src="{_MEASURE_JS_URL}"></script>
<script>
(function(){{
  if (window.Measurement) {{ window.Measurement.init(); }}
}})();
</script>
</body>
</html>"""

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    @staticmethod
    def _maybe_downsample(df: pd.DataFrame, max_candles: int) -> pd.DataFrame:
        """Fasst OHLCV-Kerzen vektorisiert zu OHLC-Buckets zusammen (P0#2).

        Bei n > max_candles wird jede Kerze einem Bucket der Groesse
        ceil(n/max_candles) zugeordnet; pro Bucket werden open (erste),
        high (max), low (min), close (letzte) und time (erste) vektorisiert
        per np.maximum/np.minimum.reduceat aggregiert. Ein zu kleiner
        Rest-Bucket (weniger als halbe Bucket-Groesse) wird mit dem
        vorherigen verschmolzen.

        Args:
            df: OHLCV-DataFrame (aufsteigend nach 'time' sortiert).
            max_candles: Obergrenze der Ausgabe-Kerzen.

        Returns:
            Reduzierter DataFrame (gleiche Spalten) oder df unveraendert,
            wenn n <= max_candles.
        """
        n = len(df)
        if n <= max_candles:
            return df
        bucket = int(math.ceil(n / max_candles))
        times = df["time"].to_numpy().astype(np.int64)
        opens = df["open"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        starts = np.arange(0, n, bucket)
        if len(starts) > 1 and n - starts[-1] < max(2, bucket // 2):
            starts = starts[:-1]
        ends = np.append(starts[1:], n)
        return pd.DataFrame({
            "time": times[starts],
            "open": opens[starts],
            "high": np.maximum.reduceat(highs, starts),
            "low": np.minimum.reduceat(lows, starts),
            "close": closes[ends - 1],
        })

    @staticmethod
    def _build_rangebreaks(times) -> list:
        """Erkennt Luecken im Zeitverlauf und baut plotly-rangebreaks.

        Generisch (nicht hardcoded auf Wochenende/Pausen): Jede Zeitdifferenz
        > 2x typisches Candle-Intervall (Median der Differenzen, robust gegen
        Ausreisser) gilt als Luecke. Die Break-Grenzen liegen HALB im
        typischen Intervall innerhalb der Luecke, damit die Rand-Kerzen
        sichtbar bleiben und nur der leere Bereich ausgeblendet wird.

        Args:
            times: numpy-Array der Epoch-Ints (aufsteigend sortiert).

        Returns:
            Liste von plotly-rangebreak-dicts (max. 200, aelteste zuerst
            verworfen, um den HTML nicht aufzublaehen).
        """
        if times is None or len(times) < 3:
            return []
        diffs = np.diff(times.astype(np.int64))
        if len(diffs) == 0:
            return []
        interval = float(np.median(diffs))
        if interval <= 0:
            return []
        threshold = interval * 2.0
        half = int(interval / 2)

        breaks = []
        gap_idx = np.where(diffs > threshold)[0]
        for i in gap_idx:
            start_epoch = int(times[i]) + half
            end_epoch = int(times[i + 1]) - half
            if end_epoch <= start_epoch:
                continue
            start_iso = str(pd.to_datetime(start_epoch, unit="s"))
            end_iso = str(pd.to_datetime(end_epoch, unit="s"))
            breaks.append({"bounds": [start_iso, end_iso]})
            if len(breaks) >= 200:
                break
        return breaks
