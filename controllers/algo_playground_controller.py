# controllers/algo_playground_controller.py
"""
controllers/algo_playground_controller.py - Presenter fuer den Algo-Playground.

Phase 2: Koppelt die UI-Events der Algo-Liste an den AlgoPickerDialog und
haelt den Zustand (aktive Algos). Phase 3: Parameter-Form – Auswahl eines
Listeneintrags befuellt die ParamFormWidget aus dessen parameter_schema;
Aenderungen werden pro Instanz (instance_key) gespeichert. Die restliche
Logik (Daten-Slicing, Berechnung, Canvas-Update, Debounce) folgt in Phase 6.

Verantwortlich (SRP/IoC):
  * Registry einmalig laden (AlgoRegistry.discover_algos)
  * "+"-Event -> AlgoPickerDialog oeffnen, gewaehlte Algos zur Liste hinzufuegen
  * Selektion -> ParamFormWidget mit parameter_schema + gespeicherten Params
  * params_changed -> pro Instanz speichern (Phase 6: Debounce + Neuberechnung)
  * Kontextmenue "Entfernen" -> aktiven Zustand nachfuehren
  * Checkbox -> Signal (Phase 6 konsumiert: Overlay ein/aus)

Der Controller kennt main_win NICHT als Modul – er erhaelt das View-Objekt
(duck-typed) ueber den Konstruktor.
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog

from algos.algo_registry import AlgoRegistry
from ui.algo_picker_dialog import AlgoPickerDialog


class AlgoPlaygroundController(QObject):
    """Presenter des Algo-Playgrounds (Phase 2 + 3: Liste, Dialog, Params)."""

    def __init__(self, view) -> None:
        super().__init__()
        self.ui = view
        self.registry: Dict[str, Dict[str, Any]] = AlgoRegistry.discover_algos()
        # Spiegel des aktiven Algo-Zustands (algo_ids, mit Duplikaten).
        self.active_algos: List[str] = []
        # Parameter je Instanz (instance_key -> params-dict, Phase 3).
        self.instance_params: Dict[str, Dict[str, Any]] = {}
        # Aktuell in der ParamFormWidget angezeigte Instanz (Phase 3).
        self.selected_instance: Optional[str] = None

        self._init_bindings()

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------
    def _init_bindings(self) -> None:
        """Verbindet die Signale des Views mit diesem Controller."""
        self.ui.algo_panel.algo_add_requested.connect(self._on_add_requested)
        self.ui.algo_panel.algo_removed.connect(self._on_algo_removed)
        # Checkbox: in Phase 6 konsumiert (Overlay ein/aus) – hier nur Status.
        self.ui.algo_panel.algo_visibility_changed.connect(self._on_visibility_changed)
        self.ui.algo_panel.algo_selected.connect(self._on_algo_selected)
        # Parameter-Aenderungen (Phase 3): pro Instanz speichern.
        self.ui.param_form.params_changed.connect(self._on_params_changed)

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
            new_instances = []
            for algo_id in chosen:
                meta = self.registry.get(algo_id, {})
                key = self.ui.algo_panel.add_algo(algo_id, meta.get("name"))
                new_instances.append((algo_id, key))
            self.active_algos = list(self.ui.algo_panel.algo_ids())
            # Neue Instanzen mit Schema-Defaults initialisieren.
            for algo_id, key in new_instances:
                self.instance_params[key] = self._schema_defaults(algo_id)
            self.ui.status_label.setText(
                f"Status: {len(chosen)} Algo(s) hinzugefuegt "
                f"(gesamt {len(self.active_algos)})")

    def _on_algo_removed(self, algo_id: str, instance_key: str) -> None:
        """Kontextmenue 'Entfernen': Zustand nachfuehren."""
        # Parameter der entfernten Instanz verwerfen.
        self.instance_params.pop(instance_key, None)
        if self.selected_instance == instance_key:
            self.selected_instance = None
            self.ui.param_form.set_schema({})
        self.active_algos = list(self.ui.algo_panel.algo_ids())
        self.ui.status_label.setText(
            f"Status: {algo_id} entfernt (aktiv: {len(self.active_algos)})")

    def _on_visibility_changed(self, algo_id: str, instance_key: str,
                               checked: bool) -> None:
        """Checkbox geaendert (Darstellung ein/aus) – Phase 6 rendert neu."""
        state = "ein" if checked else "aus"
        self.ui.status_label.setText(
            f"Status: {algo_id} Darstellung {state}")

    def _on_algo_selected(self, algo_id: str, instance_key: str) -> None:
        """Eintrag angeklickt: Parameter-Form mit Schema + Werten befuellen."""
        self.selected_instance = instance_key
        schema = self.registry.get(algo_id, {}).get("schema", {})
        self.ui.param_form.set_schema(schema)
        params = self.instance_params.get(instance_key)
        self.ui.param_form.set_params(params if params is not None else {})
        self.ui.status_label.setText(
            f"Status: Parameter fuer {algo_id} geladen")

    def _on_params_changed(self, params: Dict[str, Any]) -> None:
        """Parameter geaendert: pro Instanz speichern (Phase 6: Debounce)."""
        if self.selected_instance is None:
            return
        self.instance_params[self.selected_instance] = dict(params)
        self.ui.status_label.setText(
            "Status: Parameter geaendert (Phase 6: Neuberechnung)")

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    def _schema_defaults(self, algo_id: str) -> Dict[str, Any]:
        """Liest die Default-Werte aus dem parameter_schema eines AlgOS."""
        schema = self.registry.get(algo_id, {}).get("schema", {})
        return {
            name: spec.get("default")
            for name, spec in schema.items()
        }
