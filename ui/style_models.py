# ui/style_models.py
# Phase 7 (17.08.2026): Generische Style-Vertraege (Dataclasses) - aus
# PyTrader (chart/overlays/style_models.py, Phase 16 P16.03) uebernommen
# und auf PLOTLY angepasst.
#
# Visuelle Attribute (Farbe, Dicke, Stil, Symbol) werden nicht mehr als
# flache Einzelparameter (line_color, line_width, ...) durch das System
# gereicht, sondern in typisierten Styling-Klassen gebuendelt:
#
#   * LineStyle   - Linien: color / width / style
#   * MarkerStyle - Marker/Symbole (falls nicht als Linie definiert):
#                   color / symbol / size
#
# USER-REQ (17.08.2026): Der `show`-Wert und die 'sichtbar'-Checkbox wurden
# entfernt - die Sichtbarkeit eines Overlays steuert ausschliesslich die
# Instanz-Checkbox im Algo-Panel (AlgoListPanel), nicht der StylePicker.
#
# Konvertierungen:
#   * to_dict()     - JSON-kompatibles Dict (Persistenz in instance_params
#                     / playground_state).
#   * from_dict()   - Rueck-Konvertierung (tolerant gegen fehlende Felder:
#                     Defaults werden ergaenzt).
#   * to_plotly()   - Werte direkt fuer Plotly-HTML/JS: line=dict(color,
#                     dash, width) bzw. marker=dict(symbol, size, color).
#
# Farb-Logik (Paritaet zum StylePickerWidget):
#   - Alpha == 255 -> '#RRGGBB' (Hex, Grossbuchstaben, volle Deckkraft).
#   - Alpha < 255  -> 'rgba(r, g, b, a)' mit a als Float (0..1) -
#                     1:1 kompatibel mit Plotly/HTML/CSS.

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Konvertierungs-Helfer
# ---------------------------------------------------------------------------

def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


# ---------------------------------------------------------------------------
# Sibling-Konvention (Phase 7)
# ---------------------------------------------------------------------------

def style_sibling_keys(key: str, style_type: str) -> tuple:
    """Leitet die Sibling-Keys eines color-Params her.

    Konvention (wie PyTrader indicator_dialog): 'color' im Key -> bei line
    'style'/'width', bei marker 'symbol'/'size'. Liefert (None, None), wenn
    keine Konvention passt.
    """
    if "color" not in key:
        return None, None
    if style_type == "marker":
        return key.replace("color", "symbol"), key.replace("color", "size")
    return key.replace("color", "style"), key.replace("color", "width")


def collect_style_keys(schema: Optional[Dict[str, Any]]) -> set:
    """Alle Keys eines parameter_schema, die zur DARSTELLUNG gehoeren.

    Das sind die color-Keys (type='color') plus ihre Sibling-Keys
    (style/width bzw. symbol/size). Diese Keys werden NICHT als Fach-
    Parameter an die Algo-Klasse gereicht (PlaygroundWorker) und nicht in
    die Overlay-Berechnung einbezogen – sie steuern nur das Zeichnen.

    USER-REQ (17.08.2026, alg_ma dual_color): Keys mit dem Schema-Flag
    ``algo_param`` sind FACH-Parameter des AlgOS (z. B. bull_color/
    bear_color fuer die Segment-Farben) und werden an die Algo-Klasse
    gereicht – sie werden hier NICHT gesammelt.
    """
    keys: set = set()
    for name, spec in (schema or {}).items():
        if spec.get("algo_param"):
            continue
        if str(spec.get("type", "")).lower() == "color":
            keys.add(name)
            if not spec.get("color_only"):
                sib1, sib2 = style_sibling_keys(
                    name, str(spec.get("style_type", "line")))
                if sib1:
                    keys.add(sib1)
                if sib2:
                    keys.add(sib2)
    return keys


# ---------------------------------------------------------------------------
# LineStyle
# ---------------------------------------------------------------------------

#: Gueltige Linienarten - Plotly-Dash-Werte (scatter.line.dash): exakt diese
#: 6 Werte (aus dem Plotly-Validator), NICHT die TradingView-LWC-Werte aus
#: PyTrader (solid/dashed/dotted/dashdotted).
LINE_STYLES: List[str] = ["solid", "dot", "dash", "longdash", "dashdot",
                          "longdashdot"]

#: Anzeige-Namen der Linienarten (fuer die Vorschau im Button/ComboBox).
LINE_STYLE_LABELS: Dict[str, str] = {
    "solid": "Solid",
    "dot": "Dot",
    "dash": "Dash",
    "longdash": "Long Dash",
    "dashdot": "Dash Dot",
    "longdashdot": "Long Dash Dot",
}


@dataclass
class LineStyle:
    """Stil-Definition fuer Linien: Farbe, Staerke, Linienart.

    USER-REQ (17.08.2026): `show` entfernt - die Sichtbarkeit eines
    Overlays steuert die Instanz-Checkbox im Algo-Panel (AlgoListPanel),
    nicht mehr der StylePicker.

    Attributes:
        color: str   – '#RRGGBB' (Alpha=255) oder 'rgba(r,g,b,a)' (Teil-Transparenz).
        width: int   – Linienstaerke in px (1–10, QSpinBox).
        style: str   – Plotly-Dash-Wert: 'solid'|'dot'|'dash'|'longdash'|
                       'dashdot'|'longdashdot' (QComboBox).
    """

    color: str = "#ff7f0e"
    width: int = 2
    style: str = "solid"

    # -- Plotly-HTML/JS ------------------------------------------------------
    def to_plotly(self) -> Dict[str, Any]:
        """Plotly-line-Dict: {'color': ..., 'dash': ..., 'width': ...}."""
        style = str(self.style) if str(self.style) in LINE_STYLES else "solid"
        return {
            "color": str(self.color),
            "dash": style,
            "width": int(self.width),
        }

    # -- JSON-Persistenz -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """JSON-kompatibles Dict (instance_params / playground_state)."""
        return {
            "color": str(self.color),
            "width": int(self.width),
            "style": str(self.style) if str(self.style) in LINE_STYLES else "solid",
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "LineStyle":
        """Erzeugt eine LineStyle aus einem (JSON-)Dict – tolerant: fehlende
        Felder erhalten ihre Defaults, ungueltige style-Werte fallen auf
        'solid' zurueck. None -> Default-Instanz.
        """
        if not isinstance(data, dict):
            return cls()
        style = _as_str(data.get("style"), "solid")
        if style not in LINE_STYLES:
            style = "solid"
        return cls(
            color=_as_str(data.get("color"), "#ff7f0e"),
            width=_as_int(data.get("width"), 2),
            style=style,
        )


# ---------------------------------------------------------------------------
# MarkerStyle
# ---------------------------------------------------------------------------

#: Gueltige Marker-Symbole - KURATIERTE Auswahl aus den ~130 Plotly-Symbolen
#: (scatter.marker.symbol). Uebersichtlich fuer das Dropdown und direkt
#: plotly-kompatibel (kein Mapping noetig).
PLOTLY_SYMBOLS: List[str] = [
    "circle", "square", "diamond", "cross", "x",
    "triangle-up", "triangle-down", "triangle-left", "triangle-right",
    "pentagon", "hexagon", "octagon", "star", "hourglass", "bowtie",
    "asterisk", "hash", "diamond-tall", "diamond-wide",
    "arrow-up", "arrow-down", "arrow-left", "arrow-right",
    "line-ew", "line-ns", "line-ne", "line-nw",
]

#: Anzeige-Namen der Marker-Symbole (Vorschau im Button/ComboBox).
SYMBOL_LABELS: Dict[str, str] = {
    "circle": "Circle",
    "square": "Square",
    "diamond": "Diamond",
    "cross": "Cross",
    "x": "X",
    "triangle-up": "Triangle Up",
    "triangle-down": "Triangle Down",
    "triangle-left": "Triangle Left",
    "triangle-right": "Triangle Right",
    "pentagon": "Pentagon",
    "hexagon": "Hexagon",
    "octagon": "Octagon",
    "star": "Star",
    "hourglass": "Hourglass",
    "bowtie": "Bowtie",
    "asterisk": "Asterisk",
    "hash": "Hash",
    "diamond-tall": "Diamond Tall",
    "diamond-wide": "Diamond Wide",
    "arrow-up": "Arrow Up",
    "arrow-down": "Arrow Down",
    "arrow-left": "Arrow Left",
    "arrow-right": "Arrow Right",
    "line-ew": "Line EW",
    "line-ns": "Line NS",
    "line-ne": "Line NE",
    "line-nw": "Line NW",
}


@dataclass
class MarkerStyle:
    """Stil-Definition fuer Marker/Punkte: Farbe, Symbol, Groesse.

    Wird verwendet, wenn ein Algo NICHT als Linie definiert ist
    (result_schema render != 'line', z. B. store='agg' mit Markern).

    USER-REQ (17.08.2026): `show` entfernt - die Sichtbarkeit eines
    Overlays steuert die Instanz-Checkbox im Algo-Panel (AlgoListPanel),
    nicht mehr der StylePicker.

    Attributes:
        color: str   – '#RRGGBB' (Alpha=255) oder 'rgba(r,g,b,a)' (Teil-Transparenz).
        symbol: str  – Plotly-Symbol aus PLOTLY_SYMBOLS (QComboBox).
        size:  int   – Markergroesse in px (QSpinBox).
    """

    color: str = "#ff7f0e"
    symbol: str = "circle"
    size: int = 6

    # -- Plotly-HTML/JS ------------------------------------------------------
    def to_plotly(self) -> Dict[str, Any]:
        """Plotly-marker-Dict: {'color': ..., 'symbol': ..., 'size': ...}."""
        symbol = str(self.symbol) if str(self.symbol) in PLOTLY_SYMBOLS else "circle"
        return {
            "color": str(self.color),
            "symbol": symbol,
            "size": int(self.size),
        }

    # -- JSON-Persistenz -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """JSON-kompatibles Dict (instance_params / playground_state)."""
        return {
            "color": str(self.color),
            "symbol": str(self.symbol) if str(self.symbol) in PLOTLY_SYMBOLS else "circle",
            "size": int(self.size),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "MarkerStyle":
        """Erzeugt eine MarkerStyle aus einem (JSON-)Dict – tolerant: fehlende
        Felder erhalten ihre Defaults, ungueltige Symbole fallen auf 'circle'
        zurueck. None -> Default-Instanz.
        """
        if not isinstance(data, dict):
            return cls()
        symbol = _as_str(data.get("symbol"), "circle")
        if symbol not in PLOTLY_SYMBOLS:
            symbol = "circle"
        return cls(
            color=_as_str(data.get("color"), "#ff7f0e"),
            symbol=symbol,
            size=_as_int(data.get("size"), 6),
        )
