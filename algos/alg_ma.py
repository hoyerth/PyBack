# algos/alg_ma.py
"""
algos/alg_ma.py - MA (Moving Average, 12 Typen) - Algo fuer den Playground.

Vektorisiert (Agents.md): Die Berechnung laeuft ueber die adaptierte
`MATemplateEngine` aus algos/ma_utils.py (NumPy/Pandas, KEINE Loops in der
Strategie-Logik).

USER-REQ (17.08.2026): MA-Indikator aus PyTrader wirtschaftlich
uebernommen - EIN MA-Wert statt 8 (Multi-MA-Referenz `ind_moving_averages`):
  * Defaults wie PyTrader MA1: EHMA / period 4 / smoothing 10 / alpha 2.0.
  * Preisquelle: nur close (kein use_close-Schalter / kein H+L+C/3).
  * dual_color (Checkbox "Auf/Ab verschiedene Farben"): MA t >= t-1 ->
    bull-Farbe, sonst bear-Farbe; Farbfelder Vorgabe Gruen/Rot
    (_DEFAULT_BULL_COLOR/_DEFAULT_BEAR_COLOR aus ma_utils).
  * Overlay-Vertrag: get_overlay_series -> {"ma": pd.Series}; Index =
    candles_df["time"] (Epoch-Int, Wanduhr). Bei dual_color=True wird ein
    Tupel (pd.Series, Farbliste je Punkt) geliefert - der Controller
    splittet es in Segment-Traces (je Farbblock ein Trace mit NaN-Luecken,
    connectgaps=False).
"""

from typing import Optional

import pandas as pd

from algos.ma_utils import (
    MA_TYPES,
    MATemplateEngine,
    _DEFAULT_BEAR_COLOR,
    _DEFAULT_BULL_COLOR,
)


class AlgoPlugin:
    # ==================================================================
    # DEFINITION-ZONE (PineScript-Input-Zone - manuell schnell anpassbar)
    # ==================================================================
    algo_id = "alg_ma"
    name = "MA"
    description = ("Moving Average (12 Typen: SMA, EMA, WMA, DEMA, TEMA, "
                   "HMA, EHMA, ZLEMA, RMA, KAMA, ALMA, VWMA)")

    # 1) Parameter - vom Anwender in der ParamFormWidget aenderbar
    parameter_schema = {
        "ma_type": {
            "type": "choice",
            "default": "EHMA",
            "options": list(MA_TYPES),
            "label": "MA-Typ",
        },
        "period": {
            "type": "int", "default": 4, "min": 1, "max": 500, "step": 1,
            "label": "Periode",
        },
        "smoothing": {
            "type": "int", "default": 10, "min": 0, "max": 500, "step": 1,
            "label": "Glättung (<=1 keine)",
        },
        "alpha_factor": {
            "type": "float", "default": 2.0, "min": 0.1, "max": 10.0,
            "step": 0.1, "decimals": 2,
            "label": "Decay-Faktor (Alpha-MAs)",
        },
        # USER-REQ (17.08.2026): Auf/Ab-Faerbung (dual_color) mit zwei
        # Farbfeldern (Vorgabe Gruen/Rot). Die Farb-Keys tragen `algo_param`:
        # sie werden NICHT von collect_style_keys gefiltert (sie steuern die
        # Segment-Farben des AlgOS), sondern an die Algo-Klasse gereicht.
        "dual_color": {
            "type": "bool", "default": False,
            "label": "Auf/Ab verschiedene Farben",
        },
        "bull_color": {
            "type": "color", "default": _DEFAULT_BULL_COLOR,
            "label": "Farbe aufsteigend", "color_only": True,
            "algo_param": True,
        },
        "bear_color": {
            "type": "color", "default": _DEFAULT_BEAR_COLOR,
            "label": "Farbe absteigend", "color_only": True,
            "algo_param": True,
        },
        # -- Darstellung (Phase 7) ------------------------------------------
        # Nur line_color wird als StylePickerWidget gerendert; die Sibling-
        # Keys (line_style/line_width) sind hidden und stecken im Widget.
        # Bei dual_color=True gelten die bull/bear-Farben (Segment-Render);
        # line_color ist dann nur der Fallback-Basisfarbe.
        "line_color": {
            "type": "color", "default": "#ff7f0e",
            "label": "Farbe", "style_type": "line",
            "allow_alpha": True,
        },
        "line_style": {
            "type": "choice", "default": "solid",
            "options": ["solid", "dot", "dash", "longdash",
                        "dashdot", "longdashdot"],
            "hidden": True,
        },
        "line_width": {
            "type": "int", "default": 2, "min": 1, "max": 10,
            "hidden": True,
        },
    }

    # 2) Ergebnis-Felder - in der DB gespeichert (store: series|agg|both)
    #    series = pro Kerze (durchgehende Linie), agg = pro Lauf (Signale)
    result_schema = {
        "ma": {"type": "float", "store": "series",
               "description": "MA-Wert je Kerze"},
    }
    # ==================================================================

    def __init__(
        self,
        ma_type: str = "EHMA",
        period: int = 4,
        smoothing: int = 10,
        alpha_factor: float = 2.0,
        dual_color: bool = False,
        bull_color: Optional[str] = None,
        bear_color: Optional[str] = None,
    ) -> None:
        """Initialisiert den MA-Algo (Defaults wie PyTrader MA1).

        Args:
            ma_type: Einer der 12 MA-Typen (MA_TYPES).
            period: MA-Periode (min 1).
            smoothing: Glaettung (zweiter EMA-Pass; <=1 = keine).
            alpha_factor: Decay-Faktor fuer Alpha-MAs.
            dual_color: True = aufsteigend/absteigend verschiedene Farben.
            bull_color: Farbe aufsteigend (Default Gruen).
            bear_color: Farbe absteigend (Default Rot).
        """
        self.ma_type = str(ma_type or "EHMA").strip().upper()
        self.period = int(period)
        self.smoothing = int(smoothing)
        self.alpha_factor = float(alpha_factor)
        self.dual_color = bool(dual_color)
        self.bull_color = str(bull_color or _DEFAULT_BULL_COLOR)
        self.bear_color = str(bear_color or _DEFAULT_BEAR_COLOR)

    def get_overlay_series(self, candles_df: pd.DataFrame) -> dict:
        """Liefert die Overlay-Serie(n) (vektorisiert, Phase-5-Vertrag).

        Args:
            candles_df: OHLCV-DataFrame mit Spalten 'time', 'open',
                'high', 'low', 'close' (optional 'tick_volume' fuer VWMA).

        Returns:
            dict - {"ma": pd.Series} mit Index = candles_df["time"].
            Bei dual_color=True: {"ma": (pd.Series, Farbliste je Punkt)} -
            der Controller splittet das Tupel in Segment-Traces.
        """
        close = candles_df["close"]
        volume: Optional[pd.Series] = None
        if "tick_volume" in candles_df.columns:
            volume = candles_df["tick_volume"]
        ma = MATemplateEngine.calculate_ma(
            close, self.ma_type, self.period,
            alpha_factor=self.alpha_factor, volume=volume,
            smoothing=self.smoothing,
        )
        ma.index = candles_df["time"]
        if self.dual_color:
            colors = MATemplateEngine.build_color_series(
                ma, True, self.bull_color, self.bear_color)
            return {"ma": (ma, colors)}
        return {"ma": ma}

    # Optional fuer das spaetere Backtest-Fenster (Agents.md-Vertrag).
    def compute_signals(self, data: pd.DataFrame):
        """Bereitet die Signale fuer ein vbt.Portfolio vor (Phase 8+)."""
        raise NotImplementedError("Backtest-Vertrag folgt in spaeterer Phase")
