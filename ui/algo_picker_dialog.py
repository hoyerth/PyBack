# ui/algo_picker_dialog.py
"""
ui/algo_picker_dialog.py - Algo-Auswahl: einfache Liste aller Algos (Phase 5.4).

Anwender-Anforderung (USER-REQ, 17.08.2026): Statt Dropdown-Checkliste mit
"Übernehmen"/"Entfernen"-Buttons zeigt der Dialog NUR eine einfache Liste
ALLER verfuegbaren Algos (Registry).

  * Ein KLICK auf einen Eintrag uebernimmt den Algo sofort in die Main-
    Algo-Liste (der Controller fuegt ihn UNCHECKED hinzu) und schliesst
    den Dialog.
  * Esc schliesst den Dialog ohne Uebernahme (Qt-Standard fuer QDialog).
  * Alle Buttons entfallen.

Nur View/Event-Handling (SRP): KEINE Registry-Logik, KEINE Berechnung.
"""

from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


class AlgoPickerDialog(QDialog):
    """Einfache Algo-Auswahlliste (Klick = uebernehmen + schliessen)."""

    def __init__(self, registry: Dict[str, dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Algo hinzufuegen")
        self.setMinimumWidth(340)
        self._selected_algo_id: Optional[str] = None

        # USER-REQ: Hinweistext statt Buttons – Klick uebernimmt, Esc schliesst.
        hint = QLabel(
            "Klick: Algo uebernehmen (zunaechst unsichtbar).\n"
            "Esc: schliessen.")
        hint.setStyleSheet("color:#8a939c; padding:2px;")

        # Einfache Liste ALLER Registry-Algos (alphabetisch sortiert).
        self.list_widget = QListWidget()
        self.list_widget.setToolTip(
            "Klick uebernimmt den Algo in die Algo-Liste (unchecked)")
        for algo_id in sorted(registry.keys()):
            meta = registry[algo_id] or {}
            item = QListWidgetItem(str(meta.get("name") or algo_id))
            item.setData(Qt.UserRole, algo_id)
            desc = str(meta.get("description") or "")
            if desc:
                item.setToolTip(desc)
            self.list_widget.addItem(item)

        self.list_widget.itemClicked.connect(self._on_item_clicked)
        # Esc = QDialog.reject (Qt-Standard), keine Buttons noetig.

        layout = QVBoxLayout()
        layout.addWidget(hint)
        layout.addWidget(self.list_widget)
        self.setLayout(layout)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Eintrag geklickt: Algo merken und Dialog mit Accepted schliessen."""
        self._selected_algo_id = item.data(Qt.UserRole)
        self.accept()

    def selected_algo_id(self) -> Optional[str]:
        """Liefert die per Klick gewaehlte algo_id (None ohne Auswahl)."""
        return self._selected_algo_id
