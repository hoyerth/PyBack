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

import os
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# Pfad zur lokalen (offline) plotly.min.js – relativ zum ui/-Paket.
_PLOTLY_JS_REL = os.path.join("..", "assets", "plotly.min.js")
_PLOTLY_JS_ABS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), _PLOTLY_JS_REL))
_PLOTLY_JS_URL = _PLOTLY_JS_ABS.replace("\\", "/")

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


class PlaygroundChartService:
    """Erzeugt Plotly-HTML fuer den Playground-Canvas (Phase 4: Candlestick)."""

    @staticmethod
    def build_candlestick_html(
        candles_df: pd.DataFrame,
        from_epoch: int,
        to_epoch: int,
        symbol: str,
        timeframe: str,
        hide_gaps: bool = True,
    ) -> str:
        """Baut das Candlestick-HTML fuer den sichtbaren Zeitraum.

        Args:
            candles_df: OHLCV-DataFrame mit Spalten 'time' (Epoch-Int,
                Wanduhr-encoded), 'open', 'high', 'low', 'close'.
            from_epoch: Start-Epoch des sichtbaren Ausschnitts (inkl.).
            to_epoch:   End-Epoch des sichtbaren Ausschnitts (inkl.).
            symbol:     Anzeige-Symbol (Titel).
            timeframe:  Anzeige-Timeframe (Titel).
            hide_gaps:  True (Default) -> Zeitluecken ohne Kerzen (Wochenende,
                Handelspausen, kurze Handelstage) werden in der X-Achse
                ausgeblendet (wie bei TradingView, Anwender-Anforderung).

        Returns:
            Standalone-HTML-String (plotly.js offline als Datei referenziert).
        """
        if candles_df is None or candles_df.empty:
            return _EMPTY_HTML

        # Vektorisiertes Zeitraum-Slicen (Konzept 2.4, kein Neuladen).
        mask = (candles_df["time"] >= int(from_epoch)) & \
               (candles_df["time"] <= int(to_epoch))
        df = candles_df.loc[mask]

        if df.empty:
            return _EMPTY_HTML

        # Aufsteigend sortieren (DB liefert aufsteigend, defensiv nocheinmal).
        df = df.sort_values("time")

        # Wanduhr-Konvention: Epoch-Zahl ist Wanduhr-encoded; naive
        # Interpretation zeigt exakt die Wanduhr-Zeit (kein Berlin-Offset).
        x = pd.to_datetime(df["time"], unit="s")

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=x,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=f"{symbol} {timeframe}",
            # Candlestick-Farben: gruen/rot (klassisch, dunkles Theme).
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ))

        fig.update_layout(
            template="plotly_dark",
            title=f"{symbol} {timeframe}",
            xaxis_rangeslider_visible=False,
            xaxis_title="Zeit (Wanduhr)",
            # Preisachse (Y) auf der RECHTEN Seite wie bei TradingView/MT5
            # (Anwender-Anforderung, 17.08.2026).
            yaxis=dict(title="Preis", side="right"),
            margin=dict(l=40, r=20, t=50, b=30),
            autosize=True,
            hovermode="x unified",
            legend=dict(orientation="h", y=1.02),
            # Normaler Klick-und-Drag verschiebt den Chart (TradingView-Stil),
            # statt ein Auswahlrechteck zu ziehen.
            dragmode="pan",
        )

        # Luecken ausblenden (Wochenende/Pausen/kurze Tage -> wie TradingView).
        if hide_gaps:
            breaks = PlaygroundChartService._build_rangebreaks(
                df["time"].to_numpy())
            if breaks:
                fig.update_xaxes(rangebreaks=breaks)

        plot_div = fig.to_html(
            full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>PyBack Playground - {symbol} {timeframe}</title></head>
<body style="{_BODY_STYLE}">
<script src="{_PLOTLY_JS_URL}"></script>
{plot_div}
</body>
</html>"""

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
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
