# serviceui/symbols_win.py
"""
serviceui/symbols_win.py - Nicht-modales SymbolsWindow (Phase 15.01).

Ermoeglicht die zentrale Symbol- & Favoriten-Verwaltung:
- 2-Spalten-Tabelle (Spalte 0: Symbol, Spalte 1: ★ Favoriten-Toggle per Klick).
- Live-Suche mit `scrollToItem` zum ersten Treffer.
- ESC schliesst das Fenster.
- Jeder Favoriten-Toggle persistiert ueber `SymbolRepository` und emittiert
  `EventBus.favorites_changed` – ServiceWindow (und spaeter AnalyticsWindow)
  befuellen daraufhin ihre Symbol-Dropdowns neu (Entkopplung via EventBus).

Architektur (SRP): Das Fenster ist NUR Event-Handling & Rendering. SQL-Zugriff
erfolgt exklusiv ueber `SymbolRepository` (kein SQL in UI, Invariante 4).
"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config.event_bus import event_bus
from persistent_win import PersistentWindow, register_persistent_window
from repositories.symbol_repository import SymbolRepository, get_symbol_repository

# Sichtbare Darstellung: ausgefuellter Stern = Favorit, leerer Stern = nicht.
STAR_FAVORITE = "★"
STAR_NORMAL = "☆"


@register_persistent_window(auto_restore=False)
class SymbolsWindow(PersistentWindow):
    """Nicht-modales Fenster zur Symbol- & Favoriten-Verwaltung."""

    INSTANCE_ID = "win_symbols"

    def __init__(self, parent=None, repo: Optional[SymbolRepository] = None) -> None:
        super().__init__(parent)
        self.repo: SymbolRepository = repo or get_symbol_repository()
        self._build_ui()
        self._load_symbols()

    # ------------------------------------------------------------------
    # UI-Aufbau (reines Rendering, kein SQL)
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("PyBack - Symbole & Favoriten")
        self.resize(420, 560)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Symbol suchen...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Symbol", "★"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Fixed)
            self.table.setColumnWidth(1, 48)
        # Spalte 1 zentriert darstellen (★ / ☆)
        self.table.setColumnWidth(1, 48)
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

    # ------------------------------------------------------------------
    # Daten-Befuellung
    # ------------------------------------------------------------------
    def _load_symbols(self) -> None:
        """Befuellt die Tabelle aus der gespeicherten Liste (`get_symbols()`).

        User-Anweisung 04.08.2026 (15.01-Nachtrag 3): Der MT5-Live-Fetch wurde
        aus diesem Fenster entfernt – alle Broker-Symbole werden NUR noch
        EINMALIG beim App-Start (main.py) von MT5 geladen und persistiert.
        Dieses Fenster liest ausschliesslich den gespeicherten DB-Stand
        (kein MT5-Zugriff beim Oeffnen -> keine Verzoegerungen).
        """
        self._rows: dict = {}  # symbol -> Zeilen-Index
        self.table.setRowCount(0)
        for entry in self.repo.get_symbols():
            self._append_symbol_row(entry["symbol"], entry["is_favorite"])

    def _append_symbol_row(self, symbol: str, is_favorite: bool) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        sym_item = QTableWidgetItem(symbol)
        sym_item.setData(Qt.UserRole, symbol)
        self.table.setItem(row, 0, sym_item)
        star_item = QTableWidgetItem(STAR_FAVORITE if is_favorite else STAR_NORMAL)
        star_item.setTextAlignment(Qt.AlignCenter)
        star_item.setData(Qt.UserRole, symbol)
        self.table.setItem(row, 1, star_item)
        self._rows[symbol] = row

    # ------------------------------------------------------------------
    # Interaktion
    # ------------------------------------------------------------------
    def _on_cell_clicked(self, row: int, column: int) -> None:
        """Klick in Spalte 1 togglet den Favoriten und emittiert das Event."""
        if column != 1:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        symbol = str(item.data(Qt.UserRole) or item.text())
        new_state = self.repo.toggle_favorite(symbol)
        star_item = self.table.item(row, 1)
        if star_item is not None:
            star_item.setText(STAR_FAVORITE if new_state else STAR_NORMAL)
        event_bus.favorites_changed.emit()

    def _apply_filter(self, text: str) -> None:
        """Live-Filter: blendet nicht passende Zeilen aus und scrollt zum
        ersten Treffer (scrollToItem, Spalte 0)."""
        query = text.strip().lower()
        first_visible_row = -1
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            symbol = item.text().lower() if item is not None else ""
            match = (query in symbol) if query else True
            self.table.setRowHidden(row, not match)
            if match and first_visible_row < 0:
                first_visible_row = row
        if first_visible_row >= 0:
            item = self.table.item(first_visible_row, 0)
            if item is not None:
                self.table.scrollToItem(item, QAbstractItemView.PositionAtTop)

    def keyPressEvent(self, event) -> None:
        """ESC schliesst das Fenster (Standard-PersistentWindow-Verhalten)."""
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
