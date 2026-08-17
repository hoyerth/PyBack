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
from typing import List

from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from config.event_bus import event_bus
from controllers.algo_playground_controller import AlgoPlaygroundController
from data_sync.mt5_sync_service import TF_SECONDS_MAP
from persistent_win import PersistentWindow
from repositories.symbol_repository import SymbolRepository, get_symbol_repository
from state_manager import StateManager
from ui.algo_list_panel import AlgoListPanel
from ui.param_form_widget import ParamFormWidget
from ui.time_range_widget import TimeRangeWidget
from ui.window_manager import WindowManager
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


class _ElidedStatusLabel(QLabel):
    """Status-Label mit Text-Elision (USER-REQ 18.08.2026).

    Das Label reicht bis zum rechten Rand der Parameterbox (dieselbe Breite
    wie der linke Panel) und hat KEINEN Scrollbalken. Lange Texte werden
    mit '…' elidiert statt die Zeile/das Layout aufzublaehen – die
    SizePolicy.Ignored + MinimumWidth(0) garantieren, dass der
    minimumSizeHint des Panels konstant bleibt (keine Grafikschiebung).
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self._shown_text = None  # zuletzt gesetzter elidierter Text

    def setText(self, text: str) -> None:
        """Speichert den Volltext und elidiert auf die aktuelle Breite."""
        self._full_text = text
        self._apply_elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        """Elidiert den Volltext auf die aktuelle Widget-Breite (vektorisiert
        ueber QFontMetrics.elidedText; kein Layout-Input -> kein Aufblaehen)."""
        w = self.width()
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full_text, Qt.ElideRight, max(w - 4, 0))
        if shown != self._shown_text:
            self._shown_text = shown
            super().setText(shown)
            self.setToolTip(self._full_text if shown != self._full_text else "")


class MainWin(QMainWindow):
    """Hauptfenster: Auto-MT5-Connect, Symbol/TF-Auswahl, Scan & Opt."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyBack - MT5 Backtest Framework")

        # --- Infrastruktur -------------------------------------------------
        self.state_manager = StateManager()
        self._symbol_repo = get_symbol_repository()
        self._worker = None
        self.persistent_sub_windows: List[PersistentWindow] = []
        self.window_manager = WindowManager(
            parent=self,
            state_manager=self.state_manager,
            persistent_sub_windows=self.persistent_sub_windows,
        )

        # --- UI-Aufbau -----------------------------------------------------
        self._build_ui()

        self._stdout_redirect = _QtStdout(self.log)
        self._orig_stdout = sys.stdout

        # Playground-Controller (haelt die Logik aus main_win heraus).
        self.playground_controller = AlgoPlaygroundController(self)

        # Favoriten-Aenderungen (SymbolsWindow) refreshen die Symbol-Combo.
        event_bus.favorites_changed.connect(self._refresh_symbol_combo)

        # Letzte Groesse/Position des Hauptfensters wiederherstellen.
        self.restore_main_window_geometry()

        # Playground-Zustand (Anforderung 0): Algos, Parameter, Zeitraum,
        # Splitter-Positionen. Nach der Fenster-Geometrie, damit die
        # Splitter-Sizes nicht durch das Resize ueberschrieben werden.
        self.playground_controller.restore_state()

        # Canvas initial laden (Phase 4): Daten-Cache + Candlestick-Render.
        self.playground_controller.refresh_chart()

        # Gespeicherte Sub-Fenster (PropertiesWindow etc.) wiederherstellen.
        QTimer.singleShot(200, self.window_manager.restore_all_windows)

        # Beim Start automatisch verbinden + sofortiges Update (nach Show).
        QTimer.singleShot(0, self.start_sync)

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Baut die Top-Zeile, Zeitraum-Zeile (Playground), Canvas-Bereich
        und unten die Statuszeile + Log (5 Zeilen hoch)."""
        # -- Status-Zeile (ganz unten, ueber dem Log) ----------------------
        # USER-REQ (18.08.2026): Das Status-Label reicht bis zum rechten
        # Rand der Parameterbox und hat KEINEN Scrollbalken. Laengerer Text
        # wird mit '…' elidiert (_ElidedStatusLabel) statt die Zeile/den
        # linken Panel breiter wachsen zu lassen (SizePolicy.Ignored ->
        # minimumSizeHint bleibt konstant -> keine Grafikschiebung, Bugfix
        # 17.08.2026 bleibt erhalten; Scrollbalken entfaellt).
        self.status_label = _ElidedStatusLabel("Status: initialisiere ...")
        self.status_label.setStyleSheet("font-weight: bold; padding: 2px;")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.status_label.setMinimumWidth(0)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

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

        # -- Zeitraum-Zeile (Playground, Phase 1) --------------------------
        self.time_range = TimeRangeWidget()

        # -- Linkes Panel: Algo-Liste + Parameter-Form (vertikaler
        #    Splitter, Anwender-Anforderung 1) --------------------------
        # Beide Panes scrollen bei Platzmangel: QListWidget (Algo-Liste)
        # und QScrollArea (ParamFormWidget) haben eingebaute Scrollbars.
        self.algo_panel = AlgoListPanel()
        # Statuszeile rechts neben dem "+"-Button (kleiner Abstand).
        # add_layout = [0] '+' [1] Spacing, [2] Status-Label (stretch 1,
        # reicht bis zum rechten Rand der Parameterbox).
        self.algo_panel.add_layout.insertSpacing(1, 8)
        self.algo_panel.add_layout.insertWidget(2, self.status_label, 1)
        self.param_form = ParamFormWidget()

        algo_pane = QWidget()
        algo_layout = QVBoxLayout()
        algo_layout.setContentsMargins(0, 0, 0, 0)
        # Label "Algo-Liste:" entfaellt – der "+"-Button (im AlgoListPanel)
        # sitzt jetzt oben an seiner Stelle (Anwender-Anforderung).
        algo_layout.addWidget(self.algo_panel, 1)
        algo_pane.setLayout(algo_layout)

        param_pane = QWidget()
        param_layout = QVBoxLayout()
        param_layout.setContentsMargins(0, 0, 0, 0)
        param_layout.addWidget(QLabel("Parameter:"))
        param_layout.addWidget(self.param_form, 1)
        param_pane.setLayout(param_layout)

        self.left_splitter = QSplitter(Qt.Vertical)
        self.left_splitter.addWidget(algo_pane)
        self.left_splitter.addWidget(param_pane)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 1)
        self.left_splitter.setSizes([200, 160])  # Initiale Groessen

        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.left_splitter, 1)
        left_panel.setLayout(left_layout)

        # -- Rechter Bereich: Canvas (Phase 4: QWebEngineView + Plotly-HTML) -
        self.canvas = QWebEngineView()
        self.canvas.setMinimumHeight(200)
        self.canvas.setHtml(
            "<html><body style='background:#111418'>"
            "<div style='color:#8a939c;font-family:sans-serif;padding:24px'>"
            "Canvas initialisiere ...</div></body></html>")

        # -- Splitter (verschiebbar, Anwender-Entscheidung 1) --------------
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.canvas)
        self.splitter.setSizes([280, 820])  # Initiale Groessen
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        # -- Log-Feld (ganz unten, nur 5 Zeilen hoch) ----------------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        # 5 Zeilen Hoehe: Zeilenabstand * 5 + Rahmen/Innenabstand.
        line_h = QFontMetrics(self.log.font()).lineSpacing()
        self.log.setFixedHeight(line_h * 5 + 8)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addWidget(self.time_range)
        layout.addWidget(self.splitter, 1)  # dehnt sich aus
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
    # Fenster-Oeffnen (Delegation an WindowManager, IoC)
    # ------------------------------------------------------------------
    def open_symbols_window(self) -> None:
        """Oeffnet das nicht-modale SymbolsWindow (Favoriten-Verwaltung)."""
        self.window_manager.open_symbols_window()

    def open_properties_window(self) -> None:
        """Oeffnet das nicht-modale PropertiesWindow (Systemoptionen)."""
        self.window_manager.open_properties_window()

    # ------------------------------------------------------------------
    # Geometrie-Persistenz (win_main: letzte Groesse/Position)
    # ------------------------------------------------------------------
    def restore_main_window_geometry(self) -> None:
        """Stellt die zuletzt gespeicherte Groesse/Position von win_main wieder her.

        Muster aus pytrader main.py (restore_main_window_geometry): Positionen
        ausserhalb aller Screens fallen auf (100,100) zurueck; maximierte
        Fenster werden wieder maximiert.
        """
        geom = self.state_manager.get_window_geometry("win_main")
        if geom:
            pos_x = geom.get("pos_x")
            pos_y = geom.get("pos_y")
            width = geom.get("width") or self.width()
            height = geom.get("height") or self.height()

            screen_geo = QApplication.primaryScreen().availableGeometry()
            if pos_x is not None and pos_y is not None:
                if pos_x < screen_geo.x() - 100 or pos_x > screen_geo.right() or \
                        pos_y < screen_geo.y() - 100 or pos_y > screen_geo.bottom():
                    pos_x, pos_y = 100, 100
                self.move(pos_x, pos_y)
                self.resize(width, height)

            if geom.get("is_maximized"):
                self.showMaximized()
        else:
            # Keine gespeicherte Geometrie – Standardgroesse setzen.
            self.resize(1100, 640)

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
        # Neue Daten (Bugfix 17.08.2026): Cache MIT force_reload neu laden,
        # sonst blieben die alten Kerzen trotz aktualisierter DB sichtbar.
        # _extend_range_to_latest schiebt das Bis-Datum auf die neueste Kerze.
        self.playground_controller.refresh_chart(force_reload=True)

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
        """Speichert Playground-Zustand + Groesse/Position von win_main.

        Der Playground-Zustand (Anforderung 0: aktive Algos, Parameter,
        Zeitraum, Splitter-Positionen) wird ueber den Controller persistiert.
        Zusaetzlich speichert der StateManager die Geometrie unter der
        Instanz-ID 'win_main' – beim naechsten Start stellt
        restore_main_window_geometry() sie wieder her.
        """
        self.playground_controller.save_state()
        p, s = self.pos(), self.size()
        self.state_manager.save_window_geometry(
            "win_main", p.x(), p.y(), s.width(), s.height(), self.isMaximized())
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
