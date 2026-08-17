# ui/algo_list_panel.py
"""
ui/algo_list_panel.py - Linkes Panel: Algo-Liste mit Checkboxen (Phase 2).

Enthaelt:
  * QListWidget mit Checkboxen (schnelles Ein-/Ausblenden der Darstellung,
    Anwender-Entscheidung 5)
  * "+"-Button (Add) – oeffnet ueber den Controller den AlgoPickerDialog
  * Kontextmenue "Entfernen" an Listeneintraegen

Nur View/Event-Handling (SRP): KEINE Registry-, KEINE Berechnungslogik.
Kommunikation ueber Signale (IoC) fuer den spaeteren Controller:
  * algo_add_requested        – "+" geklickt
  * algo_visibility_changed   – Checkbox getoggelt (algo_id, checked)
  * algo_selected             – Eintrag angeklickt (algo_id, fuer Phase 3)
  * algo_removed              – Kontextmenue "Entfernen" (algo_id)

Duplikate (mehrere Instanzen desselben AlgOS) werden als #1, #2, ...
unterschieden; die algo_id liegt als UserRole an jedem Item.
"""

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class AlgoListPanel(QWidget):
    """Algo-Liste mit Checkboxen, Add-Button und Kontextmenue "Entfernen"."""

    algo_add_requested = Signal()
    algo_visibility_changed = Signal(str, bool)
    algo_selected = Signal(str)
    algo_removed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._updating = False

        self.list_widget = QListWidget()
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.setToolTip(
            "Aktive Algos – Checkbox blendet die Darstellung ein/aus. "
            "Kontextmenue: Entfernen."
        )

        self.add_button = QPushButton("+")
        self.add_button.setFixedWidth(36)
        self.add_button.setToolTip("Algo aus allen verfuegbaren hinzufuegen")
        self.add_button.clicked.connect(self.algo_add_requested)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.add_button)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def add_algo(self, algo_id: str, display_name: Optional[str] = None) -> None:
        """Fuegt einen Algo als (standardmaessig aktivierten) Eintrag hinzu.

        Args:
            algo_id: Registry-ID des AlgOS.
            display_name: Anzeigename (Default: algo_id).
        """
        name = display_name or algo_id
        count = self._count_instances(algo_id)
        suffix = f" #{count + 1}" if count > 0 else ""
        item = QListWidgetItem(f"{name}{suffix}")
        item.setData(Qt.UserRole, algo_id)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        # Standard: aktiviert (Overlay sichtbar).
        item.setCheckState(Qt.Checked)
        self._updating = True
        try:
            self.list_widget.addItem(item)
        finally:
            self._updating = False

    def algo_ids(self) -> List[str]:
        """Liefert die algo_ids aller Listeneintraege (Reihenfolge)."""
        return [
            self.list_widget.item(i).data(Qt.UserRole)
            for i in range(self.list_widget.count())
        ]

    def remove_algo(self, algo_id: str) -> None:
        """Entfernt ALLE Eintraege eines AlgOS aus der Liste."""
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == algo_id:
                self.list_widget.takeItem(i)

    def set_checked(self, algo_id: str, checked: bool) -> None:
        """Setzt die Checkbox eines AlgOS programmatisch (ohne Signal)."""
        self._updating = True
        try:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.data(Qt.UserRole) == algo_id:
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                    return
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    def _count_instances(self, algo_id: str) -> int:
        return sum(
            1 for i in range(self.list_widget.count())
            if self.list_widget.item(i).data(Qt.UserRole) == algo_id
        )

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Checkbox geaendert -> Signal (nur bei Nutzer-Aenderung)."""
        if self._updating:
            return
        algo_id = item.data(Qt.UserRole)
        self.algo_visibility_changed.emit(
            algo_id, item.checkState() == Qt.Checked)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Eintrag angeklickt -> Signal (fuer Phase 3 Parameter-Form)."""
        self.algo_selected.emit(item.data(Qt.UserRole))

    def _show_context_menu(self, pos) -> None:
        """Kontextmenue "Entfernen" an Listeneintraegen."""
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Entfernen")
        action = menu.exec_(self.list_widget.mapToGlobal(pos))
        if action == remove_action:
            algo_id = item.data(Qt.UserRole)
            self.list_widget.takeItem(self.list_widget.row(item))
            self.algo_removed.emit(algo_id)
