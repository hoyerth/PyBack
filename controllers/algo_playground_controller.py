# controllers/algo_playground_controller.py
"""
controllers/algo_playground_controller.py - Presenter fuer den Algo-Playground.

MINIMAL-Version (Phase 2): Koppelt die UI-Events der Algo-Liste an den
AlgoPickerDialog und haelt den Zustand (aktive Algos). Die restliche Logik
(Daten-Slicing, Berechnung, Canvas-Update, Debounce) folgt in Phase 6.

Verantwortlich (SRP/IoC):
  * Registry einmalig laden (AlgoRegistry.discover_algos)
  * "+"-Event -> AlgoPickerDialog oeffnen, gewaehlte Algos zur Liste hinzufuegen
  * Kontextmenue "Entfernen" -> aktiven Zustand nachfuehren
  * Checkbox/Selektion -> Signale (Phase 3/6 konsumieren diese)

Der Controller kennt main_win NICHT als Modul – er erhaelt das View-Objekt
(duck-typed) ueber den Konstruktor.
"""

from typing import Dict, List

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog

from algos.algo_registry import AlgoRegistry
from ui.algo_picker_dialog import AlgoPickerDialog


class AlgoPlaygroundController(QObject):
    """Presenter des Algo-Playgrounds (Phase 2: Add/Remove-Liste)."""

    def __init__(self, view) -> None:
        super().__init__()
        self.ui = view
        self.registry: Dict[str, Dict] = AlgoRegistry.discover_algos()
        # Spiegel des aktiven Algo-Zustands (algo_ids, mit Duplikaten).
        self.active_algos: List[str] = []

        self._init_bindings()

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------
    def _init_bindings(self) -> None:
        """Verbindet die Algo-Listen-Signale des Views mit diesem Controller."""
        self.ui.algo_panel.algo_add_requested.connect(self._on_add_requested)
        self.ui.algo_panel.algo_removed.connect(self._on_algo_removed)
        # Checkbox/Selektion: in Phase 3 (Parameter-Form) bzw. 6 (Canvas)
        # konsumiert – hier nur protokollieren, damit der Zustand stimmt.
        self.ui.algo_panel.algo_visibility_changed.connect(self._on_visibility_changed)
        self.ui.algo_panel.algo_selected.connect(self._on_algo_selected)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _on_add_requested(self) -> None:
        """'+' geklickt: AlgoPickerDialog oeffnen und Auswahl uebernehmen."""
        if not self.registry:
            self.ui.status_label.setText(
                "Status: keine Algos verfuegbar (algos/ ist leer)")
            return
        dlg = AlgoPickerDialog(self.registry, self.ui)
        if dlg.exec() == QDialog.Accepted:
            chosen = dlg.selected_algo_ids()
            for algo_id in chosen:
                meta = self.registry.get(algo_id, {})
                self.ui.algo_panel.add_algo(algo_id, meta.get("name"))
            self.active_algos.extend(chosen)
            self.ui.status_label.setText(
                f"Status: {len(chosen)} Algo(s) hinzugefuegt "
                f"(gesamt {len(self.active_algos)})")

    def _on_algo_removed(self, algo_id: str) -> None:
        """Kontextmenue 'Entfernen': Zustand nachfuehren."""
        # Entfernt EIN Vorkommen von algo_id aus dem Spiegel.
        if algo_id in self.active_algos:
            self.active_algos.remove(algo_id)
        self.ui.status_label.setText(
            f"Status: {algo_id} entfernt (aktiv: {len(self.active_algos)})")

    def _on_visibility_changed(self, algo_id: str, checked: bool) -> None:
        """Checkbox geaendert (Darstellung ein/aus) – Phase 6 rendert neu."""
        state = "ein" if checked else "aus"
        self.ui.status_label.setText(
            f"Status: {algo_id} Darstellung {state}")

    def _on_algo_selected(self, algo_id: str) -> None:
        """Eintrag angeklickt – Phase 3 zeigt hier die Parameter-Form."""
        # Vorbereitet fuer Phase 3; aktuell nur Status-Rueckmeldung.
        self.ui.status_label.setText(f"Status: Algo {algo_id} selektiert")
