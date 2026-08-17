# algos/ma_utils.py
"""
algos/ma_utils.py - Vektorisierte Moving-Average-Utility.

Wirtschaftlich uebernommen und auf PyBack adaptiert aus
`F:\\Python\\PyTrader\\chart\\indicators\\utils\\ma_template.py`
(MATemplateEngine, Phase 16.04) - 17.08.2026.

USER-REQ (17.08.2026): MA-Indikator aus PyTrader pruefen und als Algo
einbauen (EIN MA-Wert statt 8). Der Kern (12 MA-Typen, vektorisiert) wird
1:1 uebernommen; LWC-spezifische Teile entfallen.

USER-REQ (18.08.2026, PARITY-KORREKTUR gegen TradingView): Die 5 Typen der
Pivot-HMA-Familie (HMA, EMA, DEMA, TEMA, EHMA) werden jetzt EXAKT nach den
Original-Algos des PineScripts "TH Pivot v478" berechnet (nicht mehr nach
der PyTrader-Interpretation). Referenz:

  hma_ema(_src, _len, _smoothing, _alphaFactor) =>
      hma_alphaCalc = _alphaFactor / (_len + 1)
      hma_sum := na(hma_sum[1]) ? _src :
          hma_alphaCalc * ta.ema(_src, _smoothing) +
          (1 - hma_alphaCalc) * nz(ta.ema(hma_sum[1], _smoothing))

  hma_dema(_src, _len, _smoothing, _alphaFactor) =>
      hma_ema(2.0*hma_ema(_src, int(_len/2), ...) - hma_ema(_src, _len, ...),
              int(sqrt(_len/2)), ...)

Der Filter hma_ema laesst sich VEKTORISIERT schreiben (kein Loop):
  alpha = alphaFactor/(len+1); beta = 2/(smoothing+1); gamma = beta*alpha
  e_src = EMA(src, span=smoothing)
  v     = EWM(e_src, alpha=gamma)                (Seed gamma*e_src[0])
  w     = v + (beta-gamma)*e_src[0]*(1-gamma)^t  (Seed-Korrektur w[0]=beta*src[0])
  out[0] = src[0];  out[t] = alpha*e_src[t] + (1-alpha)*w[t-1]   (t>=1)

  * MAType = TradingView-konformer 12er-Satz in exakter Reihenfolge:
    SMA, EMA, WMA, DEMA, TEMA, HMA, EHMA, ZLEMA, RMA, KAMA, ALMA, VWMA.
  * Defaults (PineScript TH Pivot v478): ma_type="EHMA", period=4,
    alpha_factor=2.0, smoothing=10.
  * VWMA: ohne gueltiges Volumen (fehlend/Null) Fallback auf SMA.
  * smoothing (nur fuer die PineScript-Typen EMA/DEMA/TEMA/EHMA eingebaut;
    HMA laesst es laut PineScript unberuecksichtigt -> ta.hma direkt):
      * smoothing <= 1: hma_ema reduziert sich exakt auf EMA(alpha) mit
        alpha = alpha_factor/(len+1)  (ta.ema(src,1) = src).
      * smoothing > 1: der gewichtete EMA-Feedback-Filter oben.
    Fuer die NICHT-PineScript-Typen (SMA, WMA, ZLEMA, RMA, KAMA, ALMA,
    VWMA) bleibt die optionale Alpha-EMA-Glaettung (PyTrader Vertrag B)
    erhalten: ema_first = EMA(base, span=smoothing); base =
    EMA(ema_first, alpha=alpha_calc).
  * NaN-Handling: Die PineScript-EMA-Familie ist ab Bar 0 definiert
    (Seed src[0], wie TradingView ta.ema/ta.hma - kein Warmup-NaN).
    Fenster-Typen (SMA/WMA/ALMA/VWMA) und HMA behalten ihren natuerlichen
    Warmup; kurze Serien (len < period) liefern komplett NaN statt Crash
    (Defensiv-Guard, Bugfix 12.08.2026).

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

# USER-REQ (18.08.2026, PARITY): Die 5 Typen der PineScript-Pivot-HMA-
# Familie enthalten das smoothing bereits in ihrer Definition (hma_ema-
# Familie); HMA laesst es laut PineScript unberuecksichtigt (ta.hma direkt).
# Fuer diese Typen wird KEINE generische Alpha-EMA-Glaettung mehr angewandt.
_PINEPIVOT_TYPES: frozenset = frozenset({"HMA", "EMA", "DEMA", "TEMA", "EHMA"})

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
    """HMA (Hull) EXAKT wie TradingView `ta.hma` (PineScript TH Pivot v478):
    WMA(2*WMA(half) - WMA(len), round(sqrt(len))).

    USER-REQ (18.08.2026, PARITY-KORREKTUR): Der aeussere WMA nutzt jetzt
    `round(sqrt(len))` (TradingView-Formel) statt einer erzwungenen
    UNGERADEN Ganzzahl - fuer len=4/5/16 war der alte (PyTrader-)Wert um 1
    daneben (z. B. HMA(4): 3 statt 2) und wich damit sichtbar von
    TradingView ab.
    """
    if period <= 1:
        return values.astype(float, copy=True)
    if len(values) < period:
        return np.full(len(values), np.nan, dtype=float)
    half = max(period // 2, 1)
    sqrt_period = max(int(round(np.sqrt(period))), 1)
    inner = 2.0 * _wma_values(values, half) - _wma_values(values, period)
    return _wma_values(inner, sqrt_period)


def _hma_ema_values(
    values: np.ndarray,
    length: int,
    smoothing: int,
    alpha_factor: float,
) -> np.ndarray:
    """PineScript `hma_ema` (TH Pivot v478) - vektorisiert, kein Loop.

    Referenz (PineScript):
      hma_alphaCalc = _alphaFactor / (_len + 1)
      hma_sum := na(hma_sum[1]) ? _src
                 : hma_alphaCalc * ta.ema(_src, _smoothing)
                   + (1 - hma_alphaCalc) * nz(ta.ema(hma_sum[1], _smoothing))

    Vektorisierte Herleitung (Seed-Details, s. Doku im Modul-Header):
      alpha = alpha_factor/(length+1); beta = 2/(smoothing+1);
      gamma = beta*alpha
      e_src[t] = EMA(src, span=smoothing)[t]
      v = EWM(e_src, alpha=gamma)   (pandas adjust=False: v[0]=e_src[0])
      w[t] = v[t] + (beta-1)*e_src[0]*(1-gamma)^t
             (w[0]=beta*e_src[0] = exakter PineScript-Seed von EMA(hma_sum[1]))
      out[0] = src[0]
      out[t] = alpha*e_src[t] + (1-alpha)*w[t-1]     (t>=1)

    Mit smoothing <= 1 (ta.ema(src,1)=src) reduziert sich der Filter exakt
    auf EMA(src, alpha) mit alpha = alpha_factor/(length+1).
    """
    n = len(values)
    if n == 0:
        return values.astype(float, copy=True)
    alpha = float(alpha_factor) / (length + 1)
    smoothing = max(int(smoothing), 1)
    if smoothing <= 1:
        return _ema_alpha_values(values, alpha)
    beta = 2.0 / (smoothing + 1.0)
    gamma = beta * alpha
    e_src = _ema_span_values(values, smoothing)
    # v = EMA(e_src, gamma) mit pandas-Seed v[0]=e_src[0]; Seed-Korrektur
    # auf den PineScript-Seed w[0]=beta*e_src[0] (Differenz (beta-1)*e_src[0],
    # abklingend mit (1-gamma)^t).
    v = np.array(
        pd.Series(e_src).ewm(alpha=gamma, adjust=False).mean().to_numpy(),
        dtype=float,
        copy=True,
    )
    if np.isfinite(e_src[0]):
        v = v + (beta - 1.0) * float(e_src[0]) * np.power(
            1.0 - gamma, np.arange(n)
        )
    result = np.empty(n, dtype=float)
    result[0] = float(values[0]) if np.isfinite(values[0]) else float(e_src[0])
    result[1:] = alpha * e_src[1:] + (1.0 - alpha) * v[:-1]
    return result


def _hma_dema_values(
    values: np.ndarray,
    length: int,
    smoothing: int,
    alpha_factor: float,
) -> np.ndarray:
    """PineScript `hma_dema` (TH Pivot v478): 2*e1 - e2 auf hma_ema-Basis."""
    e1 = _hma_ema_values(values, length, smoothing, alpha_factor)
    e2 = _hma_ema_values(e1, length, smoothing, alpha_factor)
    return 2.0 * e1 - e2


def _hma_tema_values(
    values: np.ndarray,
    length: int,
    smoothing: int,
    alpha_factor: float,
) -> np.ndarray:
    """PineScript `hma_tema` (TH Pivot v478): 3*(e1-e2) + e3 auf hma_ema."""
    e1 = _hma_ema_values(values, length, smoothing, alpha_factor)
    e2 = _hma_ema_values(e1, length, smoothing, alpha_factor)
    e3 = _hma_ema_values(e2, length, smoothing, alpha_factor)
    return 3.0 * (e1 - e2) + e3


def _hma_ehma_values(
    values: np.ndarray,
    length: int,
    smoothing: int,
    alpha_factor: float,
) -> np.ndarray:
    """PineScript `hma_ehma` (TH Pivot v478):
    hma_ema(2*hma_ema(src, int(len/2)) - hma_ema(src, len), int(sqrt(len/2))).

    Die hma_ema-Stufen verwenden jeweils ihren EIGENEN Stufen-alpha
    (alpha_factor/(stufen_len+1)); die Stufen-Laengen sind int(_len/2) und
    int(sqrt(_len/2)) (PineScript-int = truncation, floor fuer positive).
    """
    half = max(int(length / 2), 1)
    sqrt_half = max(int(np.sqrt(length / 2.0)), 1)
    inner = (2.0 * _hma_ema_values(values, half, smoothing, alpha_factor)
             - _hma_ema_values(values, length, smoothing, alpha_factor))
    return _hma_ema_values(inner, sqrt_half, smoothing, alpha_factor)


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
            alpha_factor: Decay-Faktor fuer die PineScript-hma_ema-Familie
                (EMA/DEMA/TEMA/EHMA); entfaellt bei KAMA.
            volume: Volumen-Serie fuer VWMA (z. B. df['tick_volume']). Fehlt
                sie oder ist sie Null/NaN, faellt VWMA auf SMA zurueck.
            smoothing: USER-REQ (18.08.2026, PARITY gegen PineScript
                TH Pivot v478):
                  * EMA/DEMA/TEMA/EHMA: Smoothing ist IN der Definition
                    eingebaut (hma_ema-Filter, ta.ema(src, smoothing)).
                    smoothing <= 1 = keine Glaettung -> exakt
                    EMA(alpha=alpha_factor/(period+1)).
                  * HMA: laesst smoothing laut PineScript unberuecksichtigt
                    (ta.hma direkt).
                  * Uebrige Typen (SMA/WMA/ZLEMA/RMA/KAMA/ALMA/VWMA):
                    optionale Alpha-EMA-Glaettung auf die Basis-MA-Serie
                    (PyTrader Vertrag B): ema_first = EMA(base,
                    span=smoothing); base = EMA(ema_first,
                    alpha=alpha_calc) mit alpha_calc =
                    alpha_factor/(period+1). smoothing > 1 = aktiv.

        Returns:
            pd.Series mit demselben Index wie `source`. Die PineScript-EMA-
            Familie ist ab Bar 0 definiert (Seed src[0], wie TradingView);
            Fenster-Typen (SMA/WMA/ALMA/VWMA) und HMA behalten ihren
            natuerlichen Warmup.
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
        alpha_factor_f = float(alpha_factor)
        smoothing_int = max(int(smoothing or 0), 0)

        key = str(ma_type or "").strip().upper()
        if key == "SMA":
            result = _sma_values(values, period_int)
        elif key == "EMA":
            # PineScript TH Pivot v478: hma_ema (Smoothing eingebaut).
            result = _hma_ema_values(values, period_int, smoothing_int,
                                     alpha_factor_f)
        elif key == "WMA":
            result = _wma_values(values, period_int)
        elif key == "DEMA":
            result = _hma_dema_values(values, period_int, smoothing_int,
                                      alpha_factor_f)
        elif key == "TEMA":
            result = _hma_tema_values(values, period_int, smoothing_int,
                                      alpha_factor_f)
        elif key == "HMA":
            # PineScript: ta.hma direkt - smoothing wird ignoriert.
            result = _hma_values(values, period_int)
        elif key == "EHMA":
            result = _hma_ehma_values(values, period_int, smoothing_int,
                                      alpha_factor_f)
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

        # Optionale Alpha-EMA-Glaettung NUR fuer die NICHT-PineScript-Typen
        # (PyTrader Vertrag B). Die 5 PineScript-Typen (HMA/EMA/DEMA/TEMA/
        # EHMA) enthalten das Smoothing bereits in ihrer Definition bzw.
        # ignorieren es (HMA) - eine zweite generische Glaettung wuerde vom
        # TradingView-Verhalten abweichen (USER-REQ 18.08.2026, PARITY).
        if key not in _PINEPIVOT_TYPES and smoothing_int > 1:
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
