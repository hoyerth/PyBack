# ui/algo_list_panel.py
"""
ui/algo_list_panel.py - Linkes Panel: Algo-Liste mit Checkboxen (Phase 2).

Enthaelt:
  * QListWidget mit Checkboxen (schnelles Ein-/Ausblenden der Darstellung,
    Anwender-Entscheidung 5)
  * "+"-Button (Add) – oeffnet ueber den Controller den AlgoPickerDialog
  * Kontextmenue "Entfernen" an Listeneintraegen

Nur View/Event-Handling (SRP): KEINE Registry-, KEINE Berechnungslogik.
Kommunikation ueber Signale (IoC) fuer den Controller:
  * algo_add_requested        – "+" geklickt
  * algo_visibility_changed   – Checkbox getoggelt (algo_id, instance_key, checked)
  * algo_selected             – Eintrag angeklickt (algo_id, instance_key, Phase 3)
  * algo_removed              – Kontextmenue "Entfernen" (algo_id, instance_key)

Duplikate (mehrere Instanzen desselben AlgOS) werden als #1, #2, ...
unterschieden und tragen eine eindeutige instance_key (UserRole+1), damit
mehrere Instanzen desselben AlgOS individuelle Parameter besitzen koennen
(Anwender-Entscheidung 2, Phase 3).
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

# Item-Rollen: UserRole = algo_id, UserRole+1 = eindeutige Instance-Key.
INSTANCE_KEY_ROLE = Qt.UserRole + 1


class AlgoListPanel(QWidget):
    """Algo-Liste mit Checkboxen, Add-Button und Kontextmenue "Entfernen"."""

    algo_add_requested = Signal()
    algo_visibility_changed = Signal(str, str, bool)
    algo_selected = Signal(str, str)
    algo_removed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._updating = False
        self._instance_counter = 0

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
        # "+" oben an Stelle des Labels (Anwender-Anforderung: Label entfaellt).
        layout.addWidget(self.add_button, 0, Qt.AlignLeft)
        layout.addWidget(self.list_widget, 1)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def add_algo(self, algo_id: str, display_name: Optional[str] = None,
                 instance_key: Optional[str] = None) -> str:
        """Fuegt einen Algo als (standardmaessig aktivierten) Eintrag hinzu.

        Args:
            algo_id: Registry-ID des AlgOS.
            display_name: Anzeigename (Default: algo_id).
            instance_key: Optionale, bereits persistierte Instance-Key
                (Restore). Ohne Angabe wird eine neue eindeutige Key erzeugt;
                bei vorhandener Key wird der Zaehler ueber ihr numerisches
                Suffix angehoben, damit keine Kollisionen entstehen.

        Returns:
            instance_key des Eintrags (eindeutig, fuer Duplikate).
        """
        name = display_name or algo_id
        count = self._count_instances(algo_id)
        suffix = f" #{count + 1}" if count > 0 else ""
        item = QListWidgetItem(f"{name}{suffix}")
        item.setData(Qt.UserRole, algo_id)
        # Eindeutige Instance-Key (Duplikate unterscheidbar, Phase 3).
        if instance_key is None:
            self._instance_counter += 1
            instance_key = f"inst_{self._instance_counter}"
        else:
            # Zaehler ueber persistierte Keys heben (Kollisionen vermeiden).
            parts = str(instance_key).rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                self._instance_counter = max(self._instance_counter,
                                             int(parts[1]))
        item.setData(INSTANCE_KEY_ROLE, instance_key)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        # Standard: aktiviert (Overlay sichtbar).
        item.setCheckState(Qt.Checked)
        self._updating = True
        try:
            self.list_widget.addItem(item)
        finally:
            self._updating = False
        return instance_key

    def algo_ids(self) -> List[str]:
        """Liefert die algo_ids aller Listeneintraege (Reihenfolge)."""
        return [
            self.list_widget.item(i).data(Qt.UserRole)
            for i in range(self.list_widget.count())
        ]

    def instance_keys(self) -> List[str]:
        """Liefert die instance_keys aller Listeneintraege (Reihenfolge)."""
        return [
            self.list_widget.item(i).data(INSTANCE_KEY_ROLE)
            for i in range(self.list_widget.count())
        ]

    def remove_algo(self, algo_id: str) -> None:
        """Entfernt ALLE Eintraege eines AlgOS aus der Liste."""
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == algo_id:
                self.list_widget.takeItem(i)

    def remove_algo_instance(self, instance_key: str) -> None:
        """Entfernt genau EINEN Eintrag (per instance_key) aus der Liste."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(INSTANCE_KEY_ROLE) == instance_key:
                self.list_widget.takeItem(i)
                return

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

    def set_checked_instance(self, instance_key: str, checked: bool) -> None:
        """Setzt die Checkbox genau EINER Instanz (ohne Signal)."""
        self._updating = True
        try:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.data(INSTANCE_KEY_ROLE) == instance_key:
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                    return
        finally:
            self._updating = False

    def checked_states(self) -> dict:
        """Liefert {instance_key: bool} der Sichtbarkeit aller Eintraege."""
        return {
            self.list_widget.item(i).data(INSTANCE_KEY_ROLE):
            self.list_widget.item(i).checkState() == Qt.Checked
            for i in range(self.list_widget.count())
        }

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
            algo_id, item.data(INSTANCE_KEY_ROLE),
            item.checkState() == Qt.Checked)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Eintrag angeklickt -> Signal (Phase 3: Parameter-Form)."""
        self.algo_selected.emit(item.data(Qt.UserRole),
                                item.data(INSTANCE_KEY_ROLE))

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
            instance_key = item.data(INSTANCE_KEY_ROLE)
            self.list_widget.takeItem(self.list_widget.row(item))
            self.algo_removed.emit(algo_id, instance_key)
