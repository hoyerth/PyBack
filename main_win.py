# main_win.py
"""
main_win.py - Hauptfenster (PySide6) mit automatischer MT5-Anbindung,
Symbol-/Timeframe-Auswahl inkl. Favoritenlogik, Scan- & Opt-Button.

Uebernommen aus pytrader (PyBack, 17.08.2026):
  * Automatische MT5-Verbindung + sofortiges Marktdaten-Update beim Start.
  * Symbol-/Timeframe-Auswahl in der obersten Zeile (Favoriten zuerst).
  * "★ Favoriten"-Button oeffnet das SymbolsWindow (Favoriten-Toggle).
  * "Scan"-Button synchronisiert das aktuell gewaehlte Symbol/Timeframe.
  * "Opt"-Button (ganz rechts) oeffnet das PropertiesWindow (Systemoptionen).

Nutzt die uebernommenen Bausteine:
  * db/schema_initializer.py      - check_and_init_databases
  * data_sync/mt5_sync_service.py - TF_SECONDS_MAP, sync_market_data (MT5-Import)
  * workers/data_sync_worker.py   - DataSyncWorker (QThread, SRP)
  * repositories/symbol_repository.py - SymbolRepository (Favoriten)
  * config/event_bus.py           - EventBus.favorites_changed
  * serviceui/symbols_win.py      - SymbolsWindow (Favoriten-Verwaltung)
  * ui/properties_win.py          - PropertiesWindow (Systemoptionen)
"""

import sys

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.event_bus import event_bus
from data_sync.mt5_sync_service import TF_SECONDS_MAP
from repositories.symbol_repository import SymbolRepository, get_symbol_repository
from serviceui.symbols_win import SymbolsWindow
from state_manager import StateManager
from ui.properties_win import PropertiesWindow
from workers.data_sync_worker import DataSyncWorker

# Anzeige-Reihenfolge der Timeframes (aufsteigend nach Sekunden).
_TIMEFRAMES = sorted(TF_SECONDS_MAP.keys(), key=lambda tf: TF_SECONDS_MAP[tf])
DEFAULT_SYMBOL = "SILVER"
DEFAULT_TIMEFRAME = "M1"


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
    """Hauptfenster: Auto-MT5-Connect, Symbol/TF-Auswahl, Scan & Opt."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyBack - MT5 Backtest Framework")
        self.resize(1100, 640)

        # --- Infrastruktur -------------------------------------------------
        self.state_manager = StateManager()
        self._symbol_repo = get_symbol_repository()
        self._worker = None

        # --- UI-Aufbau -----------------------------------------------------
        self._build_ui()

        self._stdout_redirect = _QtStdout(self.log)
        self._orig_stdout = sys.stdout

        # Favoriten-Aenderungen (SymbolsWindow) refreshen die Symbol-Combo.
        event_bus.favorites_changed.connect(self._refresh_symbol_combo)

        # Beim Start automatisch verbinden + sofortiges Update (nach Show).
        QTimer.singleShot(0, self.start_sync)

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Baut die Top-Zeile (Symbol/TF + Favoriten | Scan + Opt) und das Log."""
        # -- Status-Zeile (unter der Top-Zeile) ----------------------------
        self.status_label = QLabel("Status: initialisiere ...")
        self.status_label.setStyleSheet("font-weight: bold; padding: 2px;")

        # -- Top-Zeile: Symbol / Timeframe / Favoriten ---------------------
        self.symbol_combo = QComboBox()
        self.symbol_combo.setMinimumWidth(120)
        self.symbol_combo.setToolTip("Symbol (Favoriten zuerst)")

        self.tf_combo = QComboBox()
        self.tf_combo.addItems(_TIMEFRAMES)
        self.tf_combo.setCurrentText(DEFAULT_TIMEFRAME)
        self.tf_combo.setMinimumWidth(80)
        self.tf_combo.setToolTip("Timeframe")

        self.fav_button = QPushButton("\u2605 Favoriten")
        self.fav_button.setToolTip("Symbol- & Favoriten-Verwaltung oeffnen")
        self.fav_button.clicked.connect(self.open_symbols_window)

        # -- Scan-Button (links neben Opt) ---------------------------------
        self.scan_button = QPushButton("Scan")
        self.scan_button.setMinimumWidth(90)
        self.scan_button.setToolTip(
            "Synchronisiert das aktuell gewaehlte Symbol/Timeframe "
            "(Voll-/Update-Import) von MT5 im Hintergrund."
        )
        self.scan_button.clicked.connect(self.start_sync)

        # -- Opt-Button (ganz rechts) --------------------------------------
        self.opt_button = QPushButton("Opt")
        self.opt_button.setMinimumWidth(70)
        self.opt_button.setToolTip("Systemoptionen (Candle-Limits etc.)")
        self.opt_button.clicked.connect(self.open_properties_window)

        # Top-Zeile anordnen:
        # [Symbol] [TF] [★ Favoriten] .......... [Scan] [Opt]
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Symbol:"))
        top_row.addWidget(self.symbol_combo)
        top_row.addSpacing(8)
        top_row.addWidget(QLabel("TF:"))
        top_row.addWidget(self.tf_combo)
        top_row.addSpacing(8)
        top_row.addWidget(self.fav_button)
        top_row.addStretch(1)
        top_row.addWidget(self.scan_button)
        top_row.addWidget(self.opt_button)

        # -- Log-Feld ------------------------------------------------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # Symbol-Combo initial befuellen (Favoriten zuerst).
        self._refresh_symbol_combo()

    # ------------------------------------------------------------------
    # Symbol-/Favoriten-Logik
    # ------------------------------------------------------------------
    def _refresh_symbol_combo(self) -> None:
        """Befuellt die Symbol-ComboBox aus den Favoriten (Favoriten zuerst).

        Das aktuell angezeigte Symbol bleibt immer in der Liste, damit die
        Auswahl beim Favoriten-Wechsel nicht ungewollt springt (Muster aus
        pytrader chart_win_symboltf._refresh_symbol_combo).
        """
        if not hasattr(self, "symbol_combo"):
            return
        favorites = self._symbol_repo.get_favorite_symbols()
        if not favorites:
            favorites = list(SymbolRepository.DEFAULT_SYMBOLS)
        current = self.symbol_combo.currentText() or DEFAULT_SYMBOL
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.clear()
        for sym in favorites:
            self.symbol_combo.addItem(sym)
        if current and current not in favorites:
            self.symbol_combo.addItem(current)
        idx = self.symbol_combo.findText(current)
        self.symbol_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.symbol_combo.blockSignals(False)

    def current_symbol(self) -> str:
        """Liefert das aktuell gewaehlte Symbol."""
        return self.symbol_combo.currentText() or DEFAULT_SYMBOL

    def current_timeframe(self) -> str:
        """Liefert den aktuell gewaehlten Timeframe."""
        return self.tf_combo.currentText() or DEFAULT_TIMEFRAME

    # ------------------------------------------------------------------
    # Fenster-Oeffnen (nicht-modal, Singleton-Verhalten)
    # ------------------------------------------------------------------
    def open_symbols_window(self) -> None:
        """Oeffnet das nicht-modale SymbolsWindow (Favoriten-Verwaltung).

        Existiert bereits eine sichtbare Instanz, wird sie in den
        Vordergrund geholt statt neu geoeffnet (PersistentWindow-API).
        """
        existing = SymbolsWindow.get_existing_instance()
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        win = SymbolsWindow(self)  # parent nur fuer state_manager-Zugriff
        win.show()

    def open_properties_window(self) -> None:
        """Oeffnet das nicht-modale PropertiesWindow (Systemoptionen)."""
        existing = PropertiesWindow.get_existing_instance()
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        win = PropertiesWindow(self)  # parent nur fuer state_manager-Zugriff
        win.show()

    # ------------------------------------------------------------------
    # Sync (Scan-Button & Auto-Start)
    # ------------------------------------------------------------------
    def start_sync(self) -> None:
        """Startet die MT5-Verbindung + Synchronisation im Hintergrund-Thread.

        Synchronisiert das aktuell gewaehlte (Symbol, Timeframe)-Paar.
        """
        if self._worker is not None and self._worker.isRunning():
            return  # laeuft bereits

        pair = (self.current_symbol(), self.current_timeframe())

        # print()-Ausgaben des Sync-Prozesses ins Log-Fenster umleiten
        sys.stdout = self._stdout_redirect
        self.status_label.setText(f"Status: MT5-Sync {pair[0]} {pair[1]} laeuft ...")
        self.scan_button.setEnabled(False)
        self.log.appendPlainText(f">>> Starte MT5-Synchronisation fuer {pair[0]} {pair[1]} ...")

        self._worker = DataSyncWorker(pairs={pair}, parent=self)
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
