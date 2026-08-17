# ui/param_form_widget.py
"""
ui/param_form_widget.py - Parameter-Form (Playground, Phase 3).

Generiert aus dem `parameter_schema` eines AlgOS dynamisch die Eingabe-Widgets
(PineScript-Input-Zone, Agents.md Kap. 7.2):
  * int   -> QSpinBox
  * float -> QDoubleSpinBox
  * bool  -> QCheckBox
  * choice/str mit options -> QComboBox

Schema-Format (je Algo unter dem Header-Docstring):
    parameter_schema = {
        "period":    {"type": "int",    "default": 20,  "min": 1, "max": 500},
        "deviation": {"type": "float",  "default": 2.0, "min": 0.1, "max": 10.0,
                      "step": 0.1, "decimals": 2},
        "use_close": {"type": "bool",   "default": True},
        "mode":      {"type": "choice", "default": "ema",
                      "options": ["sma", "ema", "wma"]},
    }

Verhalten:
  * params_changed(dict) wird bei JEDER Nutzer-Aenderung emittiert (der
    Controller debounced in Phase 6; keine Neuberechnung hier).
  * set_params() setzt Werte programmatisch und emittiert NICHTS (Guard).

Nur View/Event-Handling (SRP): KEINE Registry-, KEINE Berechnungslogik,
kein DuckDB-Zugriff.
"""

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Default-Ranges fuer SpinBoxen (wenn Schema kein min/max angibt).
_INT_RANGE: tuple = (1, 1_000_000)
_FLOAT_RANGE: tuple = (-1_000_000.0, 1_000_000.0)


class ParamFormWidget(QWidget):
    """Dynamische Parameter-Form aus einem parameter_schema (Phase 3)."""

    params_changed = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._updating = False
        self._schema: Dict[str, Dict[str, Any]] = {}
        self._controls: Dict[str, QWidget] = {}

        # Platzhalter, solange kein Algo selektiert ist.
        self._empty_label = QLabel("Kein Algo selektiert")
        self._empty_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        # Scroll-Bereich um die Form (viele Parameter) - ohne Rahmen.
        self._form = QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form_host = QWidget()
        self._form_host.setLayout(self._form)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._form_host)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._scroll)
        self.setLayout(layout)

        self.set_schema({})

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def set_schema(self, schema: Optional[Dict[str, Dict[str, Any]]]) -> None:
        """Baut die Form aus dem parameter_schema neu auf (alte Controls weg).

        Args:
            schema: {param_name: {type, default, min, max, step, options, ...}}
                    Leer/None -> Platzhalter "Kein Algo selektiert".
        """
        # Alte Zeilen + Controls entfernen.
        while self._form.rowCount() > 0:
            self._form.removeRow(0)
        self._controls.clear()
        self._schema = dict(schema or {})

        for name, spec in self._schema.items():
            widget = self._create_control(name, spec)
            label = str(spec.get("label") or name)
            self._form.addRow(f"{label}:", widget)

        has_fields = bool(self._schema)
        self._empty_label.setVisible(not has_fields)
        self._scroll.setVisible(has_fields)

    def get_params(self) -> Dict[str, Any]:
        """Liest die aktuellen Werte aller Controls als dict."""
        params: Dict[str, Any] = {}
        for name, spec in self._schema.items():
            widget = self._controls.get(name)
            if widget is None:
                continue
            ctype = str(spec.get("type", "int")).lower()
            if ctype == "bool":
                params[name] = widget.isChecked()
            elif ctype == "float":
                params[name] = widget.value()
            elif ctype == "choice":
                data = widget.currentData()
                params[name] = data if data is not None else widget.currentText()
            else:
                params[name] = widget.value()
        return params

    def set_params(self, params: Optional[Dict[str, Any]]) -> None:
        """Setzt Werte programmatisch (OHNE params_changed-Signal)."""
        self._updating = True
        try:
            for name, value in (params or {}).items():
                widget = self._controls.get(name)
                if widget is None:
                    continue
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, QComboBox):
                    idx = widget.findData(value)
                    widget.setCurrentIndex(idx if idx >= 0 else 0)
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(value))
        finally:
            self._updating = False

    def has_schema(self) -> bool:
        """True, wenn aktuell eine Schema-Form angezeigt wird."""
        return bool(self._schema)

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    def _create_control(self, name: str, spec: Dict[str, Any]) -> QWidget:
        """Erzeugt das passende Eingabe-Widget fuer den Schema-Typ."""
        ctype = str(spec.get("type", "int")).lower()
        default = spec.get("default")

        if ctype == "bool":
            widget: QWidget = QCheckBox()
            widget.setChecked(bool(default))
        elif ctype == "float":
            widget = QDoubleSpinBox()
            widget.setRange(
                float(spec.get("min", _FLOAT_RANGE[0])),
                float(spec.get("max", _FLOAT_RANGE[1])),
            )
            widget.setSingleStep(float(spec.get("step", 0.1)))
            widget.setDecimals(int(spec.get("decimals", 2)))
            if default is not None:
                widget.setValue(float(default))
        elif ctype == "choice":
            widget = QComboBox()
            for opt in (spec.get("options") or []):
                widget.addItem(str(opt), opt)
            if default is not None:
                idx = widget.findData(default)
                widget.setCurrentIndex(idx if idx >= 0 else 0)
        else:  # int (Standard)
            widget = QSpinBox()
            widget.setRange(
                int(spec.get("min", _INT_RANGE[0])),
                int(spec.get("max", _INT_RANGE[1])),
            )
            widget.setSingleStep(int(spec.get("step", 1)))
            if default is not None:
                widget.setValue(int(default))

        self._wire(widget)
        self._controls[name] = widget
        return widget

    def _wire(self, widget: QWidget) -> None:
        """Verbindet Nutzer-Aenderungen mit dem params_changed-Signal."""
        if isinstance(widget, QCheckBox):
            widget.toggled.connect(self._on_user_change)
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self._on_user_change)
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(self._on_user_change)
        else:
            widget.valueChanged.connect(self._on_user_change)

    def _on_user_change(self, *_args: Any) -> None:
        """Nutzer hat einen Wert geaendert -> Signal (nicht bei set_params)."""
        if self._updating:
            return
        self.params_changed.emit(self.get_params())
