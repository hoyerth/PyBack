# config/app_settings.py
"""
AppSettings – typsichere Data-Class für anwendungsweite Konfiguration.
Alle Werte haben sinnvolle Defaults und werden in app_data.duckdb persistiert.
Erbt von AbstractStateModel für einheitliches Serialisieren/Deserialisieren.
"""

from dataclasses import dataclass
from typing import Any, Dict

from config.base_state_model import AbstractStateModel


@dataclass
class AppSettings(AbstractStateModel):
    # Chart: Maximale Anzahl Candles beim Laden
    chart_candle_limit: int = 3000

    # Feature-Builder: Default-Limit beim Laden von OHLCV
    feature_builder_limit: int = 3000

    # Historical Scanner: Maximale Candles pro Timeframe beim Scan
    scanner_candle_limit: int = 100_000

    # Statistik: Maximale Signale für die Detail-Tabelle
    statistics_signal_limit: int = 10_000

    # Signal-Overlay: Maximale Marker im Chart
    signal_marker_limit: int = 500

    # Statistik: Zeilen pro Seite in der Tabelle
    statistics_page_size: int = 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chart_candle_limit": self.chart_candle_limit,
            "feature_builder_limit": self.feature_builder_limit,
            "scanner_candle_limit": self.scanner_candle_limit,
            "statistics_signal_limit": self.statistics_signal_limit,
            "signal_marker_limit": self.signal_marker_limit,
            "statistics_page_size": self.statistics_page_size,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppSettings":
        return cls(
            chart_candle_limit=int(data.get("chart_candle_limit", 3000)),
            feature_builder_limit=int(data.get("feature_builder_limit", 3000)),
            scanner_candle_limit=int(data.get("scanner_candle_limit", 100_000)),
            statistics_signal_limit=int(data.get("statistics_signal_limit", 10_000)),
            signal_marker_limit=int(data.get("signal_marker_limit", 500)),
            statistics_page_size=int(data.get("statistics_page_size", 100)),
        )
