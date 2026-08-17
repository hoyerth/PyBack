# properties_win.py
"""
Properties-Fenster für PyTrader – nicht-modale Konfiguration der App-Einstellungen.
Mit automatischem State Persistence via PersistentWindow.
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from config.app_settings import AppSettings
from config.event_bus import event_bus
from db.db_utils import compact_database
from persistent_win import PersistentWindow, register_persistent_window
from state_manager import StateManager

BASE_DIR = Path(__file__).resolve().parent


@register_persistent_window()
class PropertiesWindow(PersistentWindow):
    INSTANCE_ID = "win_properties"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state_mgr: StateManager = getattr(parent, 'state_manager', None) or StateManager()
        self._settings: AppSettings = self._state_mgr.get_app_settings()

        self.setWindowTitle("PyBack - Optionen")
        self.setMinimumWidth(420)

        # Zentral-Widget
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Form
        form = QFormLayout()
        form.setSpacing(8)

        self.spin_chart_limit = QSpinBox()
        self.spin_chart_limit.setRange(100, 50000)
        self.spin_chart_limit.setSingleStep(100)
        self.spin_chart_limit.setValue(self._settings.chart_candle_limit)
        form.addRow("Chart-Candles (max):", self.spin_chart_limit)

        self.spin_feature_limit = QSpinBox()
        self.spin_feature_limit.setRange(100, 50000)
        self.spin_feature_limit.setSingleStep(100)
        self.spin_feature_limit.setValue(self._settings.feature_builder_limit)
        form.addRow("Feature-Builder Candles:", self.spin_feature_limit)

        self.spin_scanner_limit = QSpinBox()
        self.spin_scanner_limit.setRange(1000, 500000)
        self.spin_scanner_limit.setSingleStep(1000)
        self.spin_scanner_limit.setValue(self._settings.scanner_candle_limit)
        form.addRow("Scanner Candles (max):", self.spin_scanner_limit)

        self.spin_stats_limit = QSpinBox()
        self.spin_stats_limit.setRange(100, 100000)
        self.spin_stats_limit.setSingleStep(100)
        self.spin_stats_limit.setValue(self._settings.statistics_signal_limit)
        form.addRow("Statistik-Signale (max):", self.spin_stats_limit)

        self.spin_marker_limit = QSpinBox()
        self.spin_marker_limit.setRange(50, 5000)
        self.spin_marker_limit.setSingleStep(50)
        self.spin_marker_limit.setValue(self._settings.signal_marker_limit)
        form.addRow("Chart-Marker (max):", self.spin_marker_limit)

        self.spin_page_size = QSpinBox()
        self.spin_page_size.setRange(10, 500)
        self.spin_page_size.setSingleStep(10)
        self.spin_page_size.setValue(self._settings.statistics_page_size)
        form.addRow("Statistik-Seitengröße:", self.spin_page_size)

        layout.addLayout(form)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("💾 Speichern")
        btn_save.clicked.connect(self._save_settings)
        btn_layout.addWidget(btn_save)

        # Phase 21.02 (12.08.2026): DB-Service-Button für die Kompaktierung
        # (COPY FROM DATABASE – echte Verkleinerung). NICHT VACUUM: Die
        # reguläre DB-Pflege (CHECKPOINT+VACUUM) läuft beim App-Exit.
        btn_db_service = QPushButton("🧹 DB Service")
        btn_db_service.clicked.connect(self._on_btn_vacuum_clicked)
        btn_db_service.setToolTip(
            "Kompaktiert analytics.duckdb und market_data.duckdb "
            "(COPY FROM DATABASE). Gesperrt, solange Scans/Worker laufen."
        )
        btn_layout.addWidget(btn_db_service)

        btn_close = QPushButton("Schließen")
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)
        layout.addStretch()

        # State asynchron wiederherstellen
        QTimer.singleShot(0, self.restore_state)

    def _save_settings(self) -> None:
        self._settings = AppSettings(
            chart_candle_limit=self.spin_chart_limit.value(),
            feature_builder_limit=self.spin_feature_limit.value(),
            scanner_candle_limit=self.spin_scanner_limit.value(),
            statistics_signal_limit=self.spin_stats_limit.value(),
            signal_marker_limit=self.spin_marker_limit.value(),
            statistics_page_size=self.spin_page_size.value(),
        )
        self._state_mgr.save_app_settings(self._settings)
        print(f"✅ Einstellungen gespeichert: {self._settings}")

    # ------------------------------------------------------------------
    # Phase 21.02 (12.08.2026): DB-Service / Kompaktierung
    # ------------------------------------------------------------------
    def _on_btn_vacuum_clicked(self) -> None:
        """Kompaktiert analytics.duckdb & market_data.duckdb (COPY FROM DATABASE).

        Concurrency-Guard über den EventBus-Zähler (Phase 21.02 K1): NICHT
        `self.parent()` – PersistentWindow übergibt kein Qt-Parent. Der
        Zähler wird von MainWindow in service_run_started/finished gepflegt.
        """
        if getattr(event_bus, "sync_pause_count", 0) > 0:
            print("⚠️ DB-Service gesperrt: Scans/Worker laufen aktuell.")
            return
        for _db_name in ("analytics", "market_data"):
            # Seit 23.02 liegt properties_win.py unter ui/ – Projekt-Root = parent.
            db_path = str(BASE_DIR.parent / "data" / f"{_db_name}.duckdb")
            try:
                info = compact_database(db_path)
                print(f"✅ DB-Service: {_db_name}.duckdb kompaktiert "
                      f"({info['size_mb']} MB, {info['pct']}% fragmentiert)")
            except Exception as exc:
                print(f"❌ DB-Service: {_db_name}.duckdb fehlgeschlagen: {exc}")

    def get_settings(self) -> AppSettings:
        """Gibt die aktuell geladenen Einstellungen zurück."""
        return self._settings

    def closeEvent(self, event):
        super().closeEvent(event)
