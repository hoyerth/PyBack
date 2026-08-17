# ui/algo_picker_dialog.py
"""
ui/algo_picker_dialog.py - "+"-Dialog fuer den Algo-Playground (Phase 2).

Einfache Checkliste aus einem Dropdown (Anwender-Entscheidung 2):
  * Dropdown (QComboBox) listet ALLE verfuegbaren Algos aus der Registry.
  * "Hinzufuegen" uebernimmt den aktuell gewaehlten Algo in die Checkliste.
  * Algos koennen MEHRFACH ausgewaehlt werden (mehrere Instanzen desselben
    AlgOS fuer unterschiedliche Parameter -> Duplikate werden als #1, #2, ...
    angezeigt).
  * "Uebernehmen" (accept) liefert die Liste der gewaehlten algo_ids
    (mit Duplikaten).

Nur View/Event-Handling (SRP): Die Registry wird als fertiges Dict
uebergeben; der Dialog fuehrt KEINE Berechnung und keinen Scan aus.
"""

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class AlgoPickerDialog(QDialog):
    """Modal-Dialog: Algos aus einem Dropdown in eine Checkliste uebernehmen."""

    def __init__(self, registry: Dict[str, Dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Algos hinzufuegen")
        self.setMinimumWidth(380)
        self.setMinimumHeight(320)

        self._registry = registry

        # -- Dropdown: alle verfuegbaren Algos -----------------------------
        self.combo = QComboBox()
        self.combo.setToolTip("Verfuegbare Algos (Registry)")
        for algo_id, meta in sorted(registry.items()):
            name = meta.get("name") or algo_id
            self.combo.addItem(f"{name} ({algo_id})", userData=algo_id)

        # -- Checkliste der gewaehlten Algos -------------------------------
        self.checklist = QListWidget()
        self.checklist.setToolTip("Gewaehlte Algos (Mehrfachauswahl moeglich)")

        # -- Buttons --------------------------------------------------------
        self.btn_add = QPushButton("Hinzufuegen")
        self.btn_add.setToolTip("Ausgewaehlten Algo zur Checkliste hinzufuegen")
        self.btn_add.clicked.connect(self._on_add)

        self.btn_remove = QPushButton("Entfernen")
        self.btn_remove.setToolTip("Markierten Eintrag aus der Checkliste entfernen")
        self.btn_remove.clicked.connect(self._on_remove)

        self.btn_apply = QPushButton("Uebernehmen")
        self.btn_apply.clicked.connect(self.accept)

        self.btn_cancel = QPushButton("Abbrechen")
        self.btn_cancel.clicked.connect(self.reject)

        # -- Layout ---------------------------------------------------------
        combo_row = QHBoxLayout()
        combo_row.addWidget(self.combo, 1)
        combo_row.addWidget(self.btn_add)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_apply)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Algo waehlen:"))
        layout.addLayout(combo_row)
        layout.addWidget(QLabel("Gewaehlte Algos:"))
        layout.addWidget(self.checklist, 1)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        # Add-Button nur aktiv, wenn der Dropdown ueberhaupt Algos enthaelt.
        self.btn_add.setEnabled(self.combo.count() > 0)
        if self.combo.count() == 0:
            self.combo.setPlaceholderText("(keine Algos verfuegbar)")

    # ------------------------------------------------------------------
    # Interaktion
    # ------------------------------------------------------------------
    def _on_add(self) -> None:
        """Uebernimmt den aktuell gewaehlten Algo in die Checkliste."""
        algo_id = self.combo.currentData()
        if not algo_id:
            return
        meta = self._registry.get(algo_id, {})
        name = meta.get("name") or algo_id
        # Duplikat-Zaehler: gleicher Algo mehrfach -> #1, #2, ...
        count = sum(
            1 for i in range(self.checklist.count())
            if self.checklist.item(i).data(Qt.UserRole) == algo_id
        )
        suffix = f" #{count + 1}" if count > 0 else ""
        item = QListWidgetItem(f"{name}{suffix}")
        item.setData(Qt.UserRole, algo_id)
        self.checklist.addItem(item)

    def _on_remove(self) -> None:
        """Entfernt die markierten Eintraege aus der Checkliste."""
        for item in self.checklist.selectedItems():
            self.checklist.takeItem(self.checklist.row(item))

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def selected_algo_ids(self) -> List[str]:
        """Liefert die gewaehlten algo_ids (Reihenfolge der Checkliste)."""
        return [
            self.checklist.item(i).data(Qt.UserRole)
            for i in range(self.checklist.count())
        ]
