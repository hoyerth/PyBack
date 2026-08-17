# algos/ma_utils.py
"""
algos/ma_utils.py - Vektorisierte Moving-Average-Utility (aus PyTrader).

Wirtschaftlich uebernommen und auf PyBack adaptiert aus
`F:\\Python\\PyTrader\\chart\\indicators\\utils\\ma_template.py`
(MATemplateEngine, Phase 16.04) - 17.08.2026.

USER-REQ (17.08.2026): MA-Indikator aus PyTrader pruefen und als Algo
einbauen (EIN MA-Wert statt 8). Der Kern (12 MA-Typen, vektorisiert) wird
1:1 uebernommen; LWC-spezifische Teile entfallen.

  * MAType = TradingView-konformer 12er-Satz in exakter Reihenfolge:
    SMA, EMA, WMA, DEMA, TEMA, HMA, EHMA, ZLEMA, RMA, KAMA, ALMA, VWMA.
  * Defaults (wie PyTrader MA1): ma_type="EHMA", period=4, alpha_factor=2.0,
    smoothing=10.
  * Alpha-MAs (DEMA, TEMA, EHMA): alpha = alpha_factor / (period + 1).
  * VWMA: ohne gueltiges Volumen (fehlend/Null) Fallback auf SMA.
  * smoothing: optionaler zweiter EMA-Pass ueber die Basis-MA-Serie
    (alle 12 Typen anwendbar). smoothing > 1 = aktiv
    (ema_first = EMA(base, span=smoothing); base = EMA(ema_first,
    alpha=alpha_calc)); smoothing <= 1 = keine Glaettung.
  * NaN-Handling: Warmup period-1 als NaN; kurze Serien (len < period)
    liefern komplett NaN statt Crash (Defensiv-Guard, Bugfix 12.08.2026).

Alle 12 MA-Typen sind vektorisiert via NumPy/Pandas. KAMA ist inhärent
rekursiv (ER-basiert) und laeuft ueber eine kompakte Python-Schleife auf
dem NumPy-Array; alle anderen Typen ueber rolling/ewm/convolve.

NICHT uebernommen (PyTrader-LWC-spezifisch): `build_chart_payload`
(LWC-v5-Objekt-Array). `build_color_series` wird fuer die dual_color-
Semantik (aufsteigend/absteigend) des `alg_ma` genutzt.
"""

from __future__ import annotations

from typing import List, Literal, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Kerntypen & Konstanten
# ---------------------------------------------------------------------------

MAType = Literal[
    "SMA",
    "EMA",
    "WMA",
    "DEMA",
    "TEMA",
    "HMA",
    "EHMA",
    "ZLEMA",
    "RMA",
    "KAMA",
    "ALMA",
    "VWMA",
]

MA_TYPES: tuple = (
    "SMA",
    "EMA",
    "WMA",
    "DEMA",
    "TEMA",
    "HMA",
    "EHMA",
    "ZLEMA",
    "RMA",
    "KAMA",
    "ALMA",
    "VWMA",
)

# USER-REQ (17.08.2026): Farbfelder Vorgabe Gruen/Rot - beides TradingView-
# Standardfarben, die auch im Projekt-Palette (_PALETTE_COLORS) von
# ui/style_picker_widget.py stehen.
_DEFAULT_BULL_COLOR: str = "#089981"  # Gruen (TradingView-Up)
_DEFAULT_BEAR_COLOR: str = "#F23645"  # Rot (TradingView-Down)

# KAMA-Standardkonstanten: period = ER-Periode; fast/slow fest nach
# Kaufman (2/3 bzw. 2/31).
_KAMA_FAST_ALPHA: float = 2.0 / (2.0 + 1.0)
_KAMA_SLOW_ALPHA: float = 2.0 / (30.0 + 1.0)

# ALMA-Defaults (TradingView): Offset 0.85, Sigma = period/6.
_ALMA_OFFSET: float = 0.85


# ---------------------------------------------------------------------------
# Vektorisierte Kern-Bausteine (NumPy)
# ---------------------------------------------------------------------------


def _sma_values(values: np.ndarray, period: int) -> np.ndarray:
    """SMA ueber rollierende Fenster (konstanter Gewichtsvektor).

    Defensiv-Guard: Ist die Serie KUERZER als `period`, liefert
    np.convolve(..., mode="valid") ein Array der Laenge `period-n+1`,
    waehrend `result[period-1:]` leer ist -> ValueError. Kurze Serien
    -> komplett NaN (Warmup-Vertrag, kein Crash).
    """
    if period <= 1:
        return values.astype(float, copy=True)
    if len(values) < period:
        return np.full(len(values), np.nan, dtype=float)
    window = np.ones(period, dtype=float)
    conv = np.convolve(values, window, mode="valid") / float(period)
    result = np.full(len(values), np.nan, dtype=float)
    result[period - 1:] = conv
    return result


def _wma_values(values: np.ndarray, period: int) -> np.ndarray:
    """WMA (linear gewichtet, juengster Wert = hoechstes Gewicht) via Faltung.

    np.convolve wendet die Gewichte rueckwaerts an (v[0] trifft den
    aeltesten Wert). Daher wird der Gewichtsvektor absteigend angelegt
    ([p, p-1, .., 1]), damit der juengste Wert das hoechste Gewicht p
    erhaelt. result[t] ist am Fensterende positioniert (Warmup = period-1
    NaNs).
    """
    if period <= 1:
        return values.astype(float, copy=True)
    if len(values) < period:
        return np.full(len(values), np.nan, dtype=float)
    weights = np.arange(period, 0, -1, dtype=float)
    conv = np.convolve(values, weights, mode="valid") / weights.sum()
    result = np.full(len(values), np.nan, dtype=float)
    result[period - 1:] = conv
    return result


def _ema_alpha_values(values: np.ndarray, alpha: float) -> np.ndarray:
    """EMA mit explizitem Decay-Faktor alpha (Alpha-MAs).

    Nutzt pandas ewm(alpha=alpha, adjust=False) - vektorisiert und exakt.
    """
    return np.array(
        pd.Series(values).ewm(alpha=alpha, adjust=False).mean().to_numpy(),
        dtype=float,
        copy=True,
    )


def _ema_span_values(values: np.ndarray, period: int) -> np.ndarray:
    """Standard-EMA (span=period) fuer Nicht-Alpha-MAs (EMA, ZLEMA)."""
    return np.array(
        pd.Series(values).ewm(span=period, adjust=False).mean().to_numpy(),
        dtype=float,
        copy=True,
    )


def _rma_values(values: np.ndarray, period: int) -> np.ndarray:
    """RMA (Wilder): ewm(alpha=1/period, adjust=False)."""
    if period <= 1:
        return values.astype(float, copy=True)
    return np.array(
        pd.Series(values).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy(),
        dtype=float,
        copy=True,
    )


def _hma_values(values: np.ndarray, period: int) -> np.ndarray:
    """HMA (Hull): WMA(2*WMA(half) - WMA(len), sqrt(len)).

    sqrt(len) wird auf eine ungerade Ganzzahl gerundet (min 1).
    """
    if period <= 1:
        return values.astype(float, copy=True)
    half = max(period // 2, 1)
    sqrt_period = max(int(np.sqrt(period)), 1)
    if sqrt_period % 2 == 0:
        sqrt_period += 1
    inner = 2.0 * _wma_values(values, half) - _wma_values(values, period)
    return _wma_values(inner, sqrt_period)


def _dema_values(values: np.ndarray, period: int, alpha: float) -> np.ndarray:
    """DEMA: 2*EMA_alpha - EMA_alpha(EMA_alpha)."""
    ema1 = _ema_alpha_values(values, alpha)
    return 2.0 * ema1 - _ema_alpha_values(ema1, alpha)


def _tema_values(values: np.ndarray, period: int, alpha: float) -> np.ndarray:
    """TEMA: 3*E1 - 3*E2 + E3."""
    ema1 = _ema_alpha_values(values, alpha)
    ema2 = _ema_alpha_values(ema1, alpha)
    ema3 = _ema_alpha_values(ema2, alpha)
    return 3.0 * ema1 - 3.0 * ema2 + ema3


def _ehma_values(values: np.ndarray, period: int, alpha: float) -> np.ndarray:
    """EHMA: EMA_alpha(HMA(src, len), len)."""
    return _ema_alpha_values(_hma_values(values, period), alpha)


def _zlema_values(values: np.ndarray, period: int) -> np.ndarray:
    """ZLEMA: EMA(xt, len) mit xt = src + (src - src[lag]), lag=(len-1)/2."""
    if period <= 1:
        return values.astype(float, copy=True)
    lag = int((period - 1) / 2)
    if lag < 1:
        lag = 1
    shifted = np.empty_like(values, dtype=float)
    shifted[:lag] = np.nan
    shifted[lag:] = values[:-lag]
    xt = values + (values - shifted)
    result = _ema_span_values(xt, period)
    # Warmup: xt ist erst ab Index lag definiert; die EMA laeuft ueber die
    # NaN-Periode ohnehin erst ab period-1 vollstaendig auf. Defensiv werden
    # die ersten period-1 Werte als NaN markiert (Warmup-Vertrag).
    result[: max(period - 1, 0)] = np.nan
    return result


def _kama_values(values: np.ndarray, period: int) -> np.ndarray:
    """KAMA (Kaufman Adaptive MA), ER-basiert.

    - period dient als ER-Periode (Change vs. Volatilitaet, Default 10).
    - alpha_factor entfaellt bei KAMA.
    - Seed = values[length] (erster Wert mit definierter ER).
    - Inhaerent rekursiv -> kompakte Python-Schleife ueber das NumPy-Array.
    """
    length = max(int(period), 1)
    n = len(values)
    result = np.full(n, np.nan, dtype=float)
    if n <= length:
        return result

    diff = np.abs(np.diff(values))  # len n-1
    # vol[i-length] = Summe der letzten `length` Bar-Diffs bis Index i
    vol = np.convolve(diff, np.ones(length, dtype=float), mode="valid")  # len n-length
    change = np.abs(values[length:] - values[:-length])  # len n-length
    er = np.divide(
        change, vol, out=np.zeros_like(change, dtype=float), where=vol > 0.0
    )
    sc = np.square(er * (_KAMA_FAST_ALPHA - _KAMA_SLOW_ALPHA) + _KAMA_SLOW_ALPHA)

    m = n - length
    kama = np.empty(m, dtype=float)
    prev = float(values[length])
    kama[0] = prev
    for i in range(1, m):
        val = float(values[length + i])
        prev = prev + sc[i] * (val - prev)
        kama[i] = prev
    result[length:] = kama
    return result


def _alma_values(values: np.ndarray, period: int) -> np.ndarray:
    """ALMA (Arnaud Legoux MA) mit TradingView-Defaults.

    Gewichte werden ueber np.convolve angewendet - wegen der rueckwaertigen
    Faltungs-Orientierung (v[0] trifft den aeltesten Wert) wird weights[::-1]
    gefaltet, damit weights[0] auf den aeltesten und weights[period-1] auf
    den juengsten Wert des Fensters wirken.
    """
    if period <= 1:
        return values.astype(float, copy=True)
    if len(values) < period:
        return np.full(len(values), np.nan, dtype=float)
    offset = (period - 1) * _ALMA_OFFSET
    sigma = period / 6.0
    m = np.arange(period, dtype=float) - offset
    weights = np.exp(-(m * m) / (2.0 * sigma * sigma))
    weights = weights / weights.sum()
    conv = np.convolve(values, weights[::-1], mode="valid")
    result = np.full(len(values), np.nan, dtype=float)
    result[period - 1:] = conv
    return result


def _vwma_values(
    values: np.ndarray, volume: np.ndarray, period: int
) -> np.ndarray:
    """VWMA: sum(price*volume) / sum(volume) ueber das Fenster.

    Null-/NaN-Volumen wird mit 0 normalisiert. Ist die rollierende
    Volumen-Summe eines Fensters <= 0, faellt dieses Fenster auf den
    SMA-Wert zurueck (kein Division-by-Zero).
    """
    if period <= 1:
        return values.astype(float, copy=True)
    if len(values) < period:
        return np.full(len(values), np.nan, dtype=float)
    vol = np.where(np.isnan(volume), 0.0, volume)
    pv = values * vol
    pv_sum = np.convolve(pv, np.ones(period, dtype=float), mode="valid")
    vol_sum = np.convolve(vol, np.ones(period, dtype=float), mode="valid")
    sma_tail = _sma_values(values, period)[period - 1:]
    valid = vol_sum > 0.0
    wv = np.full(len(vol_sum), np.nan, dtype=float)
    wv[valid] = pv_sum[valid] / vol_sum[valid]
    wv[~valid] = sma_tail[~valid]
    result = np.full(len(values), np.nan, dtype=float)
    result[period - 1:] = wv
    return result


# ---------------------------------------------------------------------------
# Oeffentliche Klasse
# ---------------------------------------------------------------------------


class MATemplateEngine:
    """Vektorisierte Moving-Average-Utility (aus PyTrader Phase 16.04).

    Rein stateless - alle Methoden sind abhaengigkeitsfrei und koennen
    direkt aus Algo-Plugins heraus genutzt werden (Open/Closed, additiv).
    """

    @staticmethod
    def calculate_ma(
        source: pd.Series,
        ma_type: MAType,
        period: int,
        alpha_factor: float = 2.0,
        volume: Optional[pd.Series] = None,
        smoothing: int = 1,
    ) -> pd.Series:
        """Berechnet einen der 12 MA-Typen vektorisiert.

        Args:
            source: Preis-Serie (z. B. df['close']).
            ma_type: Einer der 12 MA-Typen (MAType).
            period: MA-Periode (min 1).
            alpha_factor: Decay-Faktor fuer Alpha-MAs (DEMA/TEMA/EHMA);
                entfaellt bei KAMA.
            volume: Volumen-Serie fuer VWMA (z. B. df['tick_volume']). Fehlt
                sie oder ist sie Null/NaN, faellt VWMA auf SMA zurueck.
            smoothing: Optionale Alpha-EMA-Glaettung auf die Basis-MA-Serie
                (alle 12 Typen anwendbar):
                  * smoothing > 1 (aktiv): ema_first = EMA(base,
                    span=smoothing); base = EMA(ema_first, alpha=alpha_calc)
                    mit alpha_calc = alpha_factor / (period + 1).
                  * smoothing <= 1: keine Glaettung (Basis-Serie
                    unveraendert, Default).

        Returns:
            pd.Series mit demselben Index wie `source`; die ersten
            `period - 1` Werte sind NaN (Warmup).
        """
        if source is None:
            return pd.Series(dtype=float)
        src = pd.to_numeric(source, errors="coerce")
        period_int = max(int(period), 1)
        values = src.to_numpy(dtype=float, na_value=np.nan)
        n = len(values)
        if n == 0:
            return pd.Series(index=src.index, dtype=float)

        alpha = float(alpha_factor) / (period_int + 1.0)

        key = str(ma_type or "").strip().upper()
        if key == "SMA":
            result = _sma_values(values, period_int)
        elif key == "EMA":
            result = _ema_span_values(values, period_int)
        elif key == "WMA":
            result = _wma_values(values, period_int)
        elif key == "DEMA":
            result = _dema_values(values, period_int, alpha)
        elif key == "TEMA":
            result = _tema_values(values, period_int, alpha)
        elif key == "HMA":
            result = _hma_values(values, period_int)
        elif key == "EHMA":
            result = _ehma_values(values, period_int, alpha)
        elif key == "ZLEMA":
            result = _zlema_values(values, period_int)
        elif key == "RMA":
            result = _rma_values(values, period_int)
        elif key == "KAMA":
            result = _kama_values(values, period_int)
        elif key == "ALMA":
            result = _alma_values(values, period_int)
        elif key == "VWMA":
            if volume is None or len(volume) != n:
                result = _sma_values(values, period_int)  # Fallback auf SMA
            else:
                vol = pd.to_numeric(volume, errors="coerce").to_numpy(
                    dtype=float, na_value=np.nan
                )
                result = _vwma_values(values, vol, period_int)
        else:
            raise ValueError(
                f"Unbekannter MA-Typ '{ma_type}'. Gueltig: {MA_TYPES}"
            )

        # Optionale Alpha-EMA-Glaettung (PyTrader Vertrag B): zweifach
        # verschachtelte EMA-Filterung auf die Basis-MA-Serie.
        # smoothing > 1 = aktiv; smoothing <= 1 = keine Glaettung.
        smoothing_int = max(int(smoothing or 0), 0)
        if smoothing_int > 1:
            ema_first = _ema_span_values(result, smoothing_int)
            result = _ema_alpha_values(ema_first, alpha)

        return pd.Series(result, index=src.index, dtype=float)

    @staticmethod
    def build_color_series(
        ma_series: pd.Series,
        dual_color: bool,
        bull_color: str,
        bear_color: str,
    ) -> List[str]:
        """Faerbt die MA-Serie gemaeß dual_color-Semantik (USER-REQ 17.08.2026).

        - dual_color=False: durchgehend bull_color.
        - dual_color=True: ma_t >= ma_{t-1} -> bull_color, sonst bear_color.
        - NaN-Vergleiche (Warmup) gelten als bull_color (defensiv).
        - Index 0 hat keinen Vorgaenger -> bull_color.

        Returns:
            Liste mit einer Farbe pro Punkt (gleiche Laenge wie ma_series).
        """
        n = len(ma_series)
        if n == 0:
            return []
        if not dual_color:
            return [str(bull_color)] * n
        values = pd.to_numeric(ma_series, errors="coerce").to_numpy(
            dtype=float, na_value=np.nan
        )
        diff = np.diff(values)  # len n-1; NaN propagiert
        is_up = np.ones(n, dtype=bool)
        finite = ~np.isnan(diff)
        is_up[1:] = np.where(finite, diff >= 0.0, True)
        return np.where(is_up, str(bull_color), str(bear_color)).tolist()
