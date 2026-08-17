# algos/alg_sma.py
"""
algos/alg_sma.py - SMA (Gleitender Mittelwert) - Referenz-Algo (Phase 5).

Vektorisiert (Agents.md): reine Pandas-Berechnung, KEINE Loops.
Der Overlay-Hook `get_overlay_series` liefert die SMA-Serie fuer den
Playground-Canvas (durchgehende Linie, store='series').
"""

import pandas as pd


class AlgoPlugin:
    # ==================================================================
    # DEFINITION-ZONE (PineScript-Input-Zone - manuell schnell anpassbar)
    # ==================================================================
    algo_id = "alg_sma"
    name = "SMA"
    description = "Gleitender Mittelwert (Simple Moving Average)"

    # 1) Parameter – vom Anwender in der ParamFormWidget aenderbar
    parameter_schema = {
        "period": {"type": "int", "default": 20, "min": 1, "max": 500,
                   "label": "Periode"},
        "use_close": {"type": "bool", "default": True,
                      "label": "Close verwenden"},
        # -- Darstellung (Phase 7, Defaults in der Definition) -------------
        # Nur line_color wird als StylePickerWidget gerendert; die Sibling-
        # Keys (line_style/line_width) sind hidden und stecken im Widget.
        "line_color": {"type": "color", "default": "#ff7f0e",
                       "label": "Farbe", "style_type": "line",
                       "allow_alpha": True},
        "line_style": {"type": "choice", "default": "solid",
                       "options": ["solid", "dot", "dash", "longdash",
                                   "dashdot", "longdashdot"],
                       "hidden": True},
        "line_width": {"type": "int", "default": 2, "min": 1, "max": 10,
                       "hidden": True},
    }

    # 2) Ergebnis-Felder – in der DB gespeichert (store: series|agg|both)
    #    series = pro Kerze (durchgehende Linie), agg = pro Lauf (Signale)
    result_schema = {
        "sma": {"type": "float", "store": "series",
                "description": "SMA-Wert je Kerze"},
    }
    # ==================================================================

    def __init__(self, period: int = 20, use_close: bool = True) -> None:
        """Initialisiert den SMA mit den Parametern aus parameter_schema."""
        self.period = int(period)
        self.use_close = bool(use_close)

    def get_overlay_series(self, candles_df: pd.DataFrame) -> dict:
        """Liefert die Overlay-Serie (vektorisiert, Phase-5-Vertrag).

        Args:
            candles_df: OHLCV-DataFrame mit Spalten 'time', 'open',
                'high', 'low', 'close'.

        Returns:
            {name: pd.Series} – Index = candles_df['time'] (Epoch-Int),
            Werte = SMA (NaN bis Periode-1, wie erwartet).
        """
        if self.use_close:
            src = candles_df["close"]
        else:
            # Typische Alternative: (H + L + C) / 3
            src = (candles_df["high"] + candles_df["low"] +
                   candles_df["close"]) / 3.0
        sma = src.rolling(window=self.period,
                          min_periods=self.period).mean()
        sma.index = candles_df["time"]
        return {"sma": sma}

    # Optional fuer das spaetere Backtest-Fenster (Agents.md-Vertrag).
    def compute_signals(self, data: pd.DataFrame):
        """Bereitet die Signale fuer ein vbt.Portfolio vor (Phase 8+)."""
        raise NotImplementedError("Backtest-Vertrag folgt in spaeterer Phase")
