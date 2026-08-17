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
  * plotly.js wird OFFLINE eingebettet (include_plotlyjs=True), der HTML-
    String ist standalone im QWebEngineView renderbar.
  * Dark-Theme passend zur App (plotly_dark + dunkler Body-Hintergrund).

Nur Build-Logik (SRP): KEIN Qt-Import, KEIN DuckDB-Zugriff.
"""

from typing import Optional

import pandas as pd
import plotly.graph_objects as go

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
    ) -> str:
        """Baut das Candlestick-HTML fuer den sichtbaren Zeitraum.

        Args:
            candles_df: OHLCV-DataFrame mit Spalten 'time' (Epoch-Int,
                Wanduhr-encoded), 'open', 'high', 'low', 'close'.
            from_epoch: Start-Epoch des sichtbaren Ausschnitts (inkl.).
            to_epoch:   End-Epoch des sichtbaren Ausschnitts (inkl.).
            symbol:     Anzeige-Symbol (Titel).
            timeframe:  Anzeige-Timeframe (Titel).

        Returns:
            Standalone-HTML-String (plotly.js offline eingebettet).
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
            yaxis_title="Preis",
            margin=dict(l=40, r=20, t=50, b=30),
            autosize=True,
            hovermode="x unified",
            legend=dict(orientation="h", y=1.02),
        )

        plot_div = fig.to_html(
            full_html=False, include_plotlyjs=True, config={"displaylogo": False})
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>PyBack Playground - {symbol} {timeframe}</title></head>
<body style="{_BODY_STYLE}">
{plot_div}
</body>
</html>"""
