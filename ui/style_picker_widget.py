# ui/style_picker_widget.py
# Phase 7 (17.08.2026): Generischer Stil-Waehler - KOPIE aus PyTrader
# (chart/widgets/style_picker_widget.py, Phase 16.06.01 vom 07.08.2026),
# angepasst auf PLOTLY:
#   * LINE_STYLES = Plotly-Dash-Werte (solid/dot/dash/longdash/dashdot/
#     longdashdot) statt LWC-Werte.
#   * PLOTLY_SYMBOLS = kuratierte Plotly-Marker-Symbole statt LWC-Shapes.
#
# Design (wie PyTrader Phase 16.06.01): Das StylePickerWidget besteht
# AUSSCHLIESSLICH aus einem kompakten QPushButton (Farb-Swatch als Icon +
# Vorschau-Text, z. B. '● 2px Solid' bzw. '● Circle'). Erst ein Klick
# oeffnet den modalen StylePickerDialog (exec):
#   * Oberer Bereich: kompaktes Custom-Color-Grid (TradingView-Palette +
#     Hex/RGB-Eingabe + Transparenz-Slider 0-100% + [Anpassen...]-Fallback
#     auf QColorDialog.getColor()). KEIN QColorDialog(Qt.Widget).
#   * QFrame.HLine-Trennlinie.
#   * Unterer Bereich (QFormLayout): Zeichnungsparameter (style_type='line':
#     Linienstaerke 1-10 px + Linienart solid/dot/dash/longdash/dashdot/
#     longdashdot; style_type='marker': Markergroesse 1-20 px + Symbol-
#     Dropdown aus PLOTLY_SYMBOLS). Optional 'sichtbar'-Checkbox, wenn
#     show_visibility=True.
#   * QDialogButtonBox [Abbrechen] / [Übernehmen].
# Bei color_only=True werden Trennlinie und unterer Bereich per
# setVisible(False) ausgeblendet und der Dialog auf die reine Farbwahl
# verkleinert.
#
# Fassade (wie PyTrader, damit die ParamFormWidget/Controller-Integration
# analog bleibt):
#   * Methoden: get_style(), set_style(obj), set_color(color_str), color()
#   * Properties: style_type ("line"|"marker"), color_only (bool),
#     show_visibility (bool)
#   * Signal: style_changed(object) - emittiert bei Uebernahme im Dialog das
#     aktualisierte LineStyle- bzw. MarkerStyle-Objekt.
#
# Die Style-Vertraege LineStyle/MarkerStyle leben zentral in
# `ui/style_models.py` (to_dict / from_dict / to_plotly).
#
# Farb-Logik (Paritaet zum PyTrader-ColorButton / Inline-Composite):
#   - Alpha == 255 -> '#RRGGBB' (Hex, Grossbuchstaben, volle Deckkraft).
#   - Alpha < 255  -> 'rgba(r, g, b, a)' mit a als Float (0..1) -
#                     1:1 kompatibel mit Plotly/HTML/CSS.

from typing import Optional, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.style_models import (
    LINE_STYLES,
    LINE_STYLE_LABELS,
    PLOTLY_SYMBOLS,
    SYMBOL_LABELS,
    LineStyle,
    MarkerStyle,
)


# ---------------------------------------------------------------------------
# Modul-Helfer (Palette + Farb-Konvertierung)
# ---------------------------------------------------------------------------

#: Kompakte Schnell-Auswahl-Palette (TradingView-Standardfarben).
_PALETTE_COLORS: list = [
    "#2962FF", "#089981", "#FF6D00", "#F23645",
    "#B47157", "#FF9800", "#FFEB3B", "#787B86",
    "#E91E63", "#9C27B0", "#3F51B5", "#009688",
    "#4CAF50", "#FF5722", "#795548", "#607D8B",
]


def _format_color(color: QColor) -> str:
    """Farb-String aus einem QColor (Paritaet zum PyTrader-ColorButton):
    Alpha == 255 -> '#RRGGBB' (Hex, Grossbuchstaben), sonst
    'rgba(r, g, b, a)' mit a als Float (0..1, plotly/HTML-CSS-kompatibel).
    """
    if not color.isValid():
        return "#000000"
    if color.alpha() < 255:
        a = round(color.alpha() / 255.0, 2)
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {a})"
    return color.name().upper()


def _parse_color(color_str: str) -> Optional[QColor]:
    """Parst Hex- oder rgba(...)-Strings in ein QColor (oder None)."""
    s = (color_str or "").strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("rgba("):
        try:
            inner = s[s.index("(") + 1:s.rindex(")")]
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) != 4:
                return None
            r = int(round(float(parts[0])))
            g = int(round(float(parts[1])))
            b = int(round(float(parts[2])))
            a_frac = float(parts[3])
        except (ValueError, TypeError):
            return None
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        alpha = max(0, min(255, int(round(a_frac * 255))))
        return QColor(r, g, b, alpha)
    c = QColor(s)
    return c if c.isValid() else None


# ---------------------------------------------------------------------------
# StylePickerDialog (modaler Popover)
# ---------------------------------------------------------------------------

class StylePickerDialog(QDialog):
    """Modaler Popover-Dialog fuer den StylePicker (Phase 7, aus PyTrader).

    Vertikal zweigeteilt:
      1. **Oberer Bereich:** kompaktes Custom-Color-Grid (Palette-Schnellwahl
         + Hex/RGB-Eingabefeld + Transparenz-Slider 0-100% +
         [Anpassen...]-Fallback auf QColorDialog.getColor()).
      2. **Trennlinie:** QFrame.HLine (Sunken).
      3. **Unterer Bereich (QFormLayout):** Zeichnungsparameter.
         * style_type='line':  Linienstaerke (QSpinBox 1-10 px) + Linienart
           (QComboBox: Plotly-Dash-Werte).
         * style_type='marker': Markergroesse (QSpinBox 1-20 px) +
           Symbol (QComboBox: PLOTLY_SYMBOLS).
         * Optional 'sichtbar'-Checkbox, wenn show_visibility=True.
      4. **Buttons:** QDialogButtonBox [Abbrechen] / [Übernehmen].

    Bei color_only=True werden Trennlinie und unterer Bereich per
    setVisible(False) ausgeblendet und der Dialog auf die reine Farbwahl
    verkleinert (adjustSize).

    get_style() liefert nach Uebernahme (accept) ein frisches
    LineStyle-/MarkerStyle-Objekt mit allen uebernommenen Werten.
    """

    def __init__(
        self,
        style: Optional[Union[LineStyle, MarkerStyle]] = None,
        style_type: str = "line",
        color_only: bool = False,
        enable_alpha: bool = True,
        show_visibility: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._style_type: str = "marker" if style_type == "marker" else "line"
        self._color_only: bool = bool(color_only)
        self._enable_alpha: bool = bool(enable_alpha)
        self._show_visibility: bool = bool(show_visibility)

        # Arbeitskopie des Style-Objekts (Typ passend zum Modus).
        if self._style_type == "marker":
            self._style: MarkerStyle = (
                style if isinstance(style, MarkerStyle) else MarkerStyle()
            )
        else:
            self._style: LineStyle = (
                style if isinstance(style, LineStyle) else LineStyle()
            )
        self._color: QColor = QColor()

        self.setWindowTitle("Farbe & Stil" if not self._color_only else "Farbe")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Oberer Bereich: Farbe -----------------------------------------
        layout.addLayout(self._build_color_area())

        # --- Trennlinie ----------------------------------------------------
        self._separator = QFrame()
        self._separator.setFrameShape(QFrame.HLine)
        self._separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(self._separator)

        # --- Unterer Bereich: Zeichnungsparameter --------------------------
        self._params_widget = self._build_params_widget()
        layout.addWidget(self._params_widget)

        # --- Buttons -------------------------------------------------------
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = self._button_box.button(QDialogButtonBox.Ok)
        cancel_btn = self._button_box.button(QDialogButtonBox.Cancel)
        ok_btn.setText("Übernehmen")
        cancel_btn.setText("Abbrechen")
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

        # --- Initialwerte aus dem uebergebenen Style ------------------------
        self._apply_style_to_ui(self._style)
        self._set_color_internal(self._style.color)

        # --- color_only: Trennlinie + unterer Bereich ausblenden, kompakt ---
        if self._color_only:
            self._separator.setVisible(False)
            self._params_widget.setVisible(False)
        self.adjustSize()

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _build_color_area(self) -> QVBoxLayout:
        """Oberer Farbbereich: Palette-Grid + Hex/RGB-Eingabe + Alpha-Slider.

        Bewusst KEIN QColorDialog(Qt.Widget)-Trick: Der native Dialog zeigt
        unter Windows 11 / Qt 6 Rendering-Macken und einen grossen Footprint.
        Der [Anpassen...]-Button oeffnet QColorDialog.getColor() nur als
        modalen Fallback.
        """
        area = QVBoxLayout()
        area.setSpacing(6)

        # 1) Palette-Grid (TradingView-Schnellwahl, Quick-Click)
        grid = QGridLayout()
        grid.setSpacing(3)
        for i, hex_color in enumerate(_PALETTE_COLORS):
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(hex_color)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hex_color}; "
                f"border: 1px solid #555555; border-radius: 3px; }}")
            btn.clicked.connect(
                lambda _=False, c=hex_color: self._set_color_internal(c))
            grid.addWidget(btn, i // 8, i % 8)
        area.addLayout(grid)

        # 2) Vorschau-Swatch + Hex/RGB-Eingabe + [Anpassen...]
        row = QHBoxLayout()
        row.setSpacing(6)
        self._preview = QLabel()
        self._preview.setObjectName("StylePickerPreview")
        self._preview.setFixedSize(40, 24)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setToolTip("Aktuelle Farbe")
        row.addWidget(self._preview)

        self._hex_edit = QLineEdit()
        self._hex_edit.setPlaceholderText("#RRGGBB / rgb(r,g,b)")
        self._hex_edit.setToolTip("Exakter Farbcode (Hex oder rgb/rgba)")
        self._hex_edit.editingFinished.connect(self._on_hex_edited)
        row.addWidget(self._hex_edit, 1)

        self._custom_btn = QPushButton("Anpassen...")
        self._custom_btn.setToolTip("System-Farbpalette öffnen (QColorDialog)")
        self._custom_btn.clicked.connect(self._open_custom_color_dialog)
        row.addWidget(self._custom_btn)
        area.addLayout(row)

        # 3) Transparenz-Slider (0-100 %)
        self._alpha_container = QWidget()
        alpha_layout = QHBoxLayout(self._alpha_container)
        alpha_layout.setContentsMargins(0, 0, 0, 0)
        alpha_layout.setSpacing(6)
        alpha_layout.addWidget(QLabel("Transparenz:"))
        self._alpha_slider = QSlider(Qt.Horizontal)
        self._alpha_slider.setRange(0, 100)
        self._alpha_slider.setToolTip(
            "Transparenz (0% = deckend, 100% = unsichtbar)")
        self._alpha_slider.valueChanged.connect(self._on_alpha_changed)
        alpha_layout.addWidget(self._alpha_slider, 1)
        self._alpha_label = QLabel("0%")
        self._alpha_label.setFixedWidth(36)
        alpha_layout.addWidget(self._alpha_label)
        # enable_alpha=False: Slider ausblenden (Schema-Flag 'allow_alpha').
        self._alpha_container.setVisible(self._enable_alpha)
        area.addWidget(self._alpha_container)

        return area

    def _build_params_widget(self) -> QWidget:
        """Unterer Bereich: Zeichnungsparameter (width|size + style|symbol).

        Optional eine 'sichtbar'-Checkbox (show_visibility=True), wenn die
        Sichtbarkeit nicht ueber einen separaten 'show_*'-Parameter laeuft.
        """
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._show_check = QCheckBox("sichtbar")
        self._show_check.setToolTip(
            "Element anzeigen" if self._style_type == "line"
            else "Marker anzeigen")
        if self._show_visibility:
            form.addRow("", self._show_check)

        self._param_spin = QSpinBox()
        if self._style_type == "marker":
            self._param_spin.setRange(1, 20)
            self._param_spin.setSuffix(" px")
            self._param_spin.setToolTip("Markergröße in Pixel")
            form.addRow("Markergröße:", self._param_spin)
        else:
            self._param_spin.setRange(1, 10)
            self._param_spin.setSuffix(" px")
            self._param_spin.setToolTip("Linienstärke in Pixel")
            form.addRow("Linienstärke:", self._param_spin)

        self._param_combo = QComboBox()
        if self._style_type == "marker":
            # Dropdown mit den verfuegbaren Plotly-Symbolen (Anwender-REQ).
            for sym in PLOTLY_SYMBOLS:
                self._param_combo.addItem(SYMBOL_LABELS.get(sym, sym), sym)
            self._param_combo.setToolTip("Marker-Symbol")
            form.addRow("Symbol:", self._param_combo)
        else:
            for style in LINE_STYLES:
                self._param_combo.addItem(LINE_STYLE_LABELS.get(style, style),
                                          style)
            self._param_combo.setToolTip("Linienart")
            form.addRow("Linienart:", self._param_combo)

        return w

    # ------------------------------------------------------------------
    # Oeffentliche API
    # ------------------------------------------------------------------

    def get_style(self) -> Union[LineStyle, MarkerStyle]:
        """Liefert den aktuell im Dialog eingestellten Stil als NEUES Objekt.

        Beruecksichtigt Farbe (inkl. Transparenz), width|size und style|symbol
        sowie - falls show_visibility=True - den Zustand der
        'sichtbar'-Checkbox.
        """
        show = (self._show_check.isChecked()
                if self._show_visibility else bool(self._style.show))
        if self._style_type == "marker":
            return MarkerStyle(
                show=show,
                color=self._color_str(),
                symbol=str(self._param_combo.currentData())
                       or "circle",
                size=int(self._param_spin.value()),
            )
        return LineStyle(
            show=show,
            color=self._color_str(),
            width=int(self._param_spin.value()),
            style=str(self._param_combo.currentData()) or "solid",
        )

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _apply_style_to_ui(
        self, style: Union[LineStyle, MarkerStyle]
    ) -> None:
        """Uebernimmt ein Style-Objekt in die Dialog-Controls."""
        if self._style_type == "marker" and isinstance(style, MarkerStyle):
            self._show_check.setChecked(bool(style.show))
            self._param_spin.setValue(int(style.size))
            self._set_combo_data(self._param_combo, style.symbol,
                                 PLOTLY_SYMBOLS, "circle")
        elif self._style_type == "line" and isinstance(style, LineStyle):
            self._show_check.setChecked(bool(style.show))
            self._param_spin.setValue(int(style.width))
            self._set_combo_data(self._param_combo, style.style,
                                 LINE_STYLES, "solid")

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str,
                        valid: list, fallback: str) -> None:
        """Setzt ein ComboBox-Item ueber sein userData (tolerant)."""
        idx = combo.findData(value) if value in valid else combo.findData(fallback)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _set_color_internal(self, color_str: str) -> None:
        """Setzt die Farbe aus einem Hex-/rgba()-String (ungueltig -> ignoriert)."""
        parsed = _parse_color(color_str)
        if parsed is not None:
            self._set_color_qcolor(parsed)

    def _set_color_qcolor(self, color: QColor) -> None:
        """Uebernimmt ein QColor in Hex-Feld, Alpha-Slider und Vorschau."""
        self._color = QColor(color)
        self._hex_edit.blockSignals(True)
        self._hex_edit.setText(_format_color(self._color))
        self._hex_edit.blockSignals(False)
        transparency = 100 - int(round(self._color.alpha() * 100 / 255))
        self._alpha_slider.blockSignals(True)
        self._alpha_slider.setValue(transparency)
        self._alpha_slider.blockSignals(False)
        self._alpha_label.setText(f"{transparency}%")
        self._update_preview()

    def _color_str(self) -> str:
        """Aktuelle Dialog-Farbe als String (hex/rgba, Paritaet)."""
        return _format_color(getattr(self, "_color", QColor()))

    def _update_preview(self) -> None:
        """Setzt das Vorschau-Swatch auf die aktuelle Farbe inkl. Deckkraft."""
        c = self._color if self._color.isValid() else QColor("#000000")
        self._preview.setStyleSheet(
            "QLabel#StylePickerPreview { background-color: "
            f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha() / 255.0}); "
            "border: 1px solid #555555; border-radius: 3px; }"
        )

    def _on_hex_edited(self) -> None:
        """Hex/RGB-Eingabe: gueltiger Farbcode wird uebernommen."""
        parsed = _parse_color(self._hex_edit.text())
        if parsed is not None:
            self._set_color_qcolor(parsed)

    def _on_alpha_changed(self, value: int) -> None:
        """Transparenz-Slider (0-100%) -> Alpha-Kanal (255-0)."""
        self._alpha_label.setText(f"{value}%")
        c = QColor(self._color)
        c.setAlpha(int(round((100 - value) * 255 / 100)))
        self._set_color_qcolor(c)

    def _open_custom_color_dialog(self) -> None:
        """Fallback: modaler QColorDialog.getColor() (optional mit Alpha)."""
        options = QColorDialog.ColorDialogOption(0)
        if self._enable_alpha:
            options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
        chosen = QColorDialog.getColor(
            self._color, self, "Farbe anpassen", options)
        if chosen.isValid():
            self._set_color_qcolor(chosen)


# ---------------------------------------------------------------------------
# StylePickerWidget (Button-Only)
# ---------------------------------------------------------------------------

class StylePickerWidget(QWidget):
    """Kombinierter Stil-Waehler – kompakter Button (Phase 7, aus PyTrader).

    Das Widget besteht ausschliesslich aus einem QPushButton (Farb-Swatch als
    Icon + Vorschau-Text, z. B. '● 2px Solid' bzw. '● Circle'). Ein Klick
    oeffnet den modalen StylePickerDialog (exec); bei Uebernahme wird das
    geaenderte LineStyle-/MarkerStyle-Objekt uebernommen und style_changed
    emittiert.

    Fassade (wie PyTrader): get_style()/set_style()/set_color()/color(),
    Properties style_type/color_only/show_visibility, Signal style_changed.
    """

    style_changed = Signal(object)

    # Kompakter Button: Swatch-Icon (16x16) + Vorschau-Text.
    _SWATCH_W = 16
    _SWATCH_H = 16

    def __init__(
        self,
        style: Optional[Union[LineStyle, MarkerStyle]] = None,
        enable_alpha: bool = True,
        parent=None,
        style_type: str = "line",
        color_only: bool = False,
        show_visibility: bool = True,
    ) -> None:
        super().__init__(parent)
        self._enable_alpha: bool = bool(enable_alpha)
        self._color_only: bool = bool(color_only)
        self._show_visibility: bool = bool(show_visibility)
        self._style_type: str = "marker" if style_type == "marker" else "line"
        if self._style_type == "marker":
            self._style: MarkerStyle = (
                style if isinstance(style, MarkerStyle) else MarkerStyle()
            )
        else:
            self._style: LineStyle = (
                style if isinstance(style, LineStyle) else LineStyle()
            )
        self._color: QColor = QColor()

        # --- Einziger sichtbarer Bestandteil: der kompakte Button ------------
        self._btn = QPushButton()
        self._btn.setObjectName("StylePickerSwatch")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setToolTip(
            "Stil festlegen (Farbe, Stärke, Art)" if not self._color_only
            else "Farbe auswählen (inkl. Transparenz)")
        self._btn.setStyleSheet(
            "QPushButton#StylePickerSwatch { border: 1px solid #555555; "
            "border-radius: 3px; padding: 2px 8px; }"
        )
        self._btn.clicked.connect(self._open_picker_dialog)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._btn)
        layout.addStretch(1)

        self._set_color_internal(self._style.color)
        self._update_swatch()

    # ------------------------------------------------------------------
    # Oeffentliche API (Fassade wie PyTrader)
    # ------------------------------------------------------------------

    @property
    def style_type(self) -> str:
        """Aktueller Widget-Modus: 'line' (LineStyle) oder 'marker' (MarkerStyle)."""
        return self._style_type

    @property
    def color_only(self) -> bool:
        """True = reiner Farbwaehler (Dialog zeigt nur den Farbbereich)."""
        return self._color_only

    @property
    def show_visibility(self) -> bool:
        """True = Dialog zeigt eine 'sichtbar'-Checkbox (Default)."""
        return self._show_visibility

    def get_style(self) -> Union[LineStyle, MarkerStyle]:
        """Liefert den aktuellen Stil als NEUES Style-Objekt (LineStyle bei
        style_type='line', MarkerStyle bei style_type='marker').

        Es wird eine frische Instanz zurueckgegeben, damit externe Aenderungen
        den internen Zustand nicht unbeabsichtigt mutieren.
        """
        if self._style_type == "marker":
            return MarkerStyle(
                show=bool(self._style.show),
                color=self._color_button_value(),
                symbol=str(self._style.symbol),
                size=int(self._style.size),
            )
        return LineStyle(
            show=bool(self._style.show),
            color=self._color_button_value(),
            width=int(self._style.width),
            style=str(self._style.style),
        )

    def set_style(self, style: Union[LineStyle, MarkerStyle]) -> None:
        """Setzt den Stil aus einem Style-Objekt und aktualisiert die Vorschau.

        Akzeptiert LineStyle und MarkerStyle. Emittiert bewusst KEIN
        style_changed (programmatisches Setzen).
        """
        if style is None:
            return
        self._style = style
        self._set_color_internal(style.color)
        self._update_swatch()

    def set_color(self, color_str: str) -> None:
        """Setzt ausschliesslich die Farbe (behaelt show/width|size/style|symbol)."""
        self._set_color_internal(color_str)

    def color(self) -> str:
        """Kompatibilitaets-Shim: aktuelle Farbe als String (hex/rgba)."""
        return self._color_button_value()

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _open_picker_dialog(self) -> None:
        """Oeffnet den modalen StylePickerDialog (exec) und uebernimmt das
        geaenderte Style-Objekt bei 'Übernehmen'. Emittiert style_changed."""
        dlg = StylePickerDialog(
            self.get_style(), style_type=self._style_type,
            color_only=self._color_only, enable_alpha=self._enable_alpha,
            show_visibility=self._show_visibility, parent=self)
        if dlg.exec() == QDialog.Accepted:
            new_style = dlg.get_style()
            self._style = new_style
            self._set_color_internal(new_style.color)
            self._update_swatch()
            self.style_changed.emit(new_style)

    def _color_button_value(self) -> str:
        """Farb-String aus dem internen QColor (Paritaet zum PyTrader-ColorButton)."""
        return _format_color(getattr(self, "_color", QColor()))

    def _set_color_internal(self, color_str: str) -> None:
        """Parst einen Hex-/rgba()-String und aktualisiert Swatch + Zustand.

        Ungueltige Eingaben werden ignoriert (bisherige Farbe bleibt erhalten).
        """
        parsed = _parse_color(color_str)
        if parsed is not None:
            self._color = parsed
            self._update_swatch()

    def _make_swatch_icon(self) -> QIcon:
        """Farb-Swatch-Icon (16x16) in der aktuellen Farbe inkl. Deckkraft."""
        c = self._color if self._color.isValid() else QColor("#000000")
        pm = QPixmap(self._SWATCH_W, self._SWATCH_H)
        pm.fill(QColor(c.red(), c.green(), c.blue(), c.alpha()))
        return QIcon(pm)

    def _preview_text(self) -> str:
        """Vorschau-Text des Buttons: '● 2px Solid' bzw. '● Circle'."""
        if self._style_type == "marker":
            label = SYMBOL_LABELS.get(str(self._style.symbol),
                                      str(self._style.symbol).capitalize())
            return f"● {label}"
        label = LINE_STYLE_LABELS.get(str(self._style.style),
                                      str(self._style.style).capitalize())
        return f"● {int(self._style.width)}px {label}"

    def _update_swatch(self) -> None:
        """Setzt Swatch-Icon + Vorschau-Text des Buttons auf den aktuellen Stil."""
        self._btn.setIcon(self._make_swatch_icon())
        self._btn.setText(self._preview_text())
