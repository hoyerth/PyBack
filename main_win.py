# main_win.py
"""
main_win.py - Hauptfenster (PySide6) mit automatischer MT5-Anbindung.

Uebernommen aus pytrader (PyBack, 17.08.2026):
  * Automatische MT5-Verbindung + sofortiges Marktdaten-Update beim Start.
  * "Scan"-Button fuer manuelles erneutes Synchronisieren (Voll-/Update-Import).
  * Log-Ausgabe des Sync-Prozesses im Fenster (print-Redirect via Qt-Signal).

Nutzt die uebernommenen Bausteine:
  * db/schema_initializer.py   - check_and_init_databases
  * data_sync/mt5_sync_service.py - sync_market_data (MT5-Import)
  * workers/data_sync_worker.py   - DataSyncWorker (QThread, SRP)
"""

import sys

from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from workers.data_sync_worker import DataSyncWorker


class _QtStdout(QObject):
    """Leitet print()-Ausgaben (auch aus Qt-Threads) thread-safe ins Log-Feld.

    Der DataSyncWorker (QThread) laesst sync_market_data() via print()
    laufen; diese Ausgaben sollen im Fenster sichtbar sein. Der Redirect
    erfolgt ueber ein QueuedConnection-Signal (thread-safe).
    """

    _write_signal = Signal(str)

    def __init__(self, log_widget: QPlainTextEdit) -> None:
        super().__init__()
        self._log = log_widget
        self._write_signal.connect(self._append)

    def write(self, text: str) -> int:
        if text.strip():
            self._write_signal.emit(text)
        return len(text)

    def flush(self) -> None:
        pass

    def _append(self, text: str) -> None:
        self._log.appendPlainText(text.rstrip("\n"))


class MainWin(QMainWindow):
    """Hauptfenster: Auto-MT5-Connect + Sofort-Update + Scan-Button."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyBack - MT5 Backtest Framework")
        self.resize(980, 620)

        # --- UI-Aufbau -----------------------------------------------------
        self.status_label = QLabel("Status: initialisiere ...")
        self.status_label.setStyleSheet("font-weight: bold; padding: 2px;")
        self.scan_button = QPushButton("Scan")
        self.scan_button.setMinimumWidth(140)
        self.scan_button.setToolTip(
            "Startet die MT5-Synchronisation (Voll-/Update-Import aller "
            "Symbole & Timeframes) im Hintergrund."
        )
        self.scan_button.clicked.connect(self.start_sync)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.status_label)
        btn_row.addStretch(1)
        btn_row.addWidget(self.scan_button)

        layout = QVBoxLayout()
        layout.addLayout(btn_row)
        layout.addWidget(self.log)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # --- Sync-Infrastruktur -------------------------------------------
        self._worker = None
        self._stdout_redirect = _QtStdout(self.log)
        self._orig_stdout = sys.stdout

        # Beim Start automatisch verbinden + sofortiges Update (nach Show).
        QTimer.singleShot(0, self.start_sync)

    # ------------------------------------------------------------------
    def start_sync(self) -> None:
        """Startet die MT5-Verbindung + Synchronisation im Hintergrund-Thread."""
        if self._worker is not None and self._worker.isRunning():
            return  # laeuft bereits

        # print()-Ausgaben des Sync-Prozesses ins Log-Fenster umleiten
        sys.stdout = self._stdout_redirect
        self.status_label.setText("Status: MT5-Verbindung & Update laeuft ...")
        self.scan_button.setEnabled(False)
        self.log.appendPlainText(">>> Starte MT5-Synchronisation ...")

        self._worker = DataSyncWorker(parent=self)
        self._worker.sync_completed.connect(self._on_sync_completed)
        # Fallback: Endet der QThread ohne sync_completed (z. B. SystemExit
        # aus check_mt5_connection bei nicht laufendem MT5), wird das UI
        # trotzdem zurueckgesetzt, damit der Scan-Button wieder nutzbar ist.
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    # ------------------------------------------------------------------
    def _on_sync_completed(self, updated_pairs) -> None:
        """Slot: Sync beendet - Status zuruecksetzen und Log aktualisieren."""
        sys.stdout = self._orig_stdout
        self.scan_button.setEnabled(True)
        count = len(updated_pairs) if updated_pairs else 0
        self.status_label.setText(f"Status: bereit (zuletzt {count} Paare aktualisiert)")
        self.log.appendPlainText(
            f">>> Sync abgeschlossen. Aktualisierte Paare: {count}"
        )

    # ------------------------------------------------------------------
    def _on_worker_finished(self) -> None:
        """Fallback: QThread ohne sync_completed beendet - UI zuruecksetzen."""
        if self.scan_button.isEnabled():
            return  # sync_completed kam bereits
        sys.stdout = self._orig_stdout
        self.scan_button.setEnabled(True)
        self.status_label.setText("Status: Sync fehlgeschlagen (MT5-Verbindung?)")

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """Stellt den Original-stdout wieder her, bevor das Fenster schliesst."""
        sys.stdout = self._orig_stdout
        super().closeEvent(event)


def main() -> None:
    """Anwendungseinstieg: erstellt das Hauptfenster und startet die GUI."""
    app = QApplication(sys.argv)
    win = MainWin()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
