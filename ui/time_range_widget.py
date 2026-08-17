# ui/time_range_widget.py
"""
ui/time_range_widget.py - Zeitraum-Picker fuer den Algo-Playground (Phase 1).

Eigenstaendiges Control-Widget (SRP): Nur Anzeige + Event-Rausgabe. Es enthaelt
KEINE Datenlogik (kein DuckDB, kein Slice) – der Controller bestimmt, was mit
dem gewaehlten Zeitraum passiert.

Aufbau (Konzept 2.2):
  [Von ▾] [Bis ▾]  [1T][1W][1M][3M][YTD]

Signale:
  * range_changed(from_ts, to_ts)  – bei manueller Aenderung von Von/Bis
                                     oder bei Preset-Klick (int-Epochs).

Hinweis (Konzept 2.4): Die Kerzen werden IMMER per aktueller TF geladen
(Top-Zeile). Der Zeitraum bestimmt nur den SICHTBAREN Ausschnitt – kein
Neuladen aus der DB.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

# Presets in Tagen (fuer relative Verschiebung vom aktuellen Ende).
_PRESETS_DAYS = {
    "1T": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "YTD": None,  # Sonderfall: Jahresbeginn
}

_PRESET_ORDER = ("1T", "1W", "1M", "3M", "YTD")


class TimeRangeWidget(QWidget):
    """Zeitraum-Picker mit Von/Bis-Editoren und Preset-Buttons."""

    # Signal: (from_epoch: int, to_epoch: int) – Wanduhr-Epochs (UTC).
    range_changed = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # -- Von/Bis-Editoren ----------------------------------------------
        self.date_from = QDateTimeEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.date_from.setMinimumWidth(150)

        self.date_to = QDateTimeEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.date_to.setMinimumWidth(150)

        # -- Preset-Buttons ------------------------------------------------
        self.preset_buttons = []
        for preset_id in _PRESET_ORDER:
            btn = QPushButton(preset_id)
            btn.setCheckable(False)
            btn.setToolTip(f"Zeitraum: {_preset_tooltip(preset_id)}")
            btn.clicked.connect(
                lambda _=False, pid=preset_id: self._apply_preset(pid))
            self.preset_buttons.append(btn)

        # -- Layout --------------------------------------------------------
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Zeitraum:"))
        layout.addWidget(self.date_from)
        layout.addWidget(QLabel("bis"))
        layout.addWidget(self.date_to)
        layout.addSpacing(8)
        for btn in self.preset_buttons:
            layout.addWidget(btn)
        layout.addStretch(1)
        self.setLayout(layout)

        # -- Signal-Verbindungen -------------------------------------------
        self.date_from.dateTimeChanged.connect(self._on_manual_change)
        self.date_to.dateTimeChanged.connect(self._on_manual_change)

        # Initialer Bereich: letzte 30 Tage (Standard "1M").
        now = datetime.utcnow()
        self.set_range(now - timedelta(days=30), now)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def set_range(self, from_dt: datetime, to_dt: datetime) -> None:
        """Setzt Von/Bis programmatisch und emittiert range_changed EINMAL.

        Waehrend des Setzens sind die Editor-Signale blockiert, damit die
        beiden setDateTime()-Aufrufe nicht je ein range_changed ausloesen –
        stattdessen wird genau EIN Signal am Ende emittiert.

        Args:
            from_dt: Start-Zeitpunkt.
            to_dt:   End-Zeitpunkt (>= from_dt).
        """
        if to_dt < from_dt:
            to_dt = from_dt
        self.date_from.blockSignals(True)
        self.date_to.blockSignals(True)
        try:
            self.date_from.setDateTime(_to_qdt(from_dt))
            self.date_to.setDateTime(_to_qdt(to_dt))
        finally:
            self.date_from.blockSignals(False)
            self.date_to.blockSignals(False)
        self._emit_range()

    def get_range(self) -> tuple:
        """Liefert (from_epoch: int, to_epoch: int) der aktuellen Auswahl."""
        return (self.date_from.dateTime().toSecsSinceEpoch(),
                self.date_to.dateTime().toSecsSinceEpoch())

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------
    def _apply_preset(self, preset_id: str) -> None:
        """Wendet ein Preset relativ zum aktuellen Bis-Datum an."""
        to_dt = self.date_to.dateTime().toPython()
        days = _PRESETS_DAYS.get(preset_id, 1)
        if preset_id == "YTD":
            from_dt = datetime(to_dt.year, 1, 1, 0, 0, 0)
        else:
            from_dt = to_dt - timedelta(days=days)
        self.set_range(from_dt, to_dt)

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    def _on_manual_change(self) -> None:
        """Manuelle Aenderung an Von/Bis -> range_changed emittieren."""
        self._emit_range()

    def _emit_range(self) -> None:
        f_epoch, t_epoch = self.get_range()
        self.range_changed.emit(f_epoch, t_epoch)


def _to_qdt(dt: datetime) -> QDateTime:
    """Wandelt ein Python-datetime (naive UTC) in QDateTime um."""
    return QDateTime(
        dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _preset_tooltip(preset_id: str) -> str:
    """Tooltip-Text fuer die Preset-Buttons."""
    tips = {
        "1T": "Letzter Tag",
        "1W": "Letzte Woche (7 Tage)",
        "1M": "Letzter Monat (30 Tage)",
        "3M": "Letzte 3 Monate (90 Tage)",
        "YTD": "Seit Jahresbeginn",
    }
    return tips.get(preset_id, preset_id)
