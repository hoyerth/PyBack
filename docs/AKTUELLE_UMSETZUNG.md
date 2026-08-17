# AKTUELLE_UMSETZUNG (PyBack) – Algo-Playground im Hauptfenster

> **Verbindliche Hauptanweisung für alle Umsetzungen in PyBack.** Diese Datei hat Vorrang vor anderen Projekt-Dokumenten (einzige Ausnahme: die System-Instruktionen selbst). Abweichungen nur auf ausdrückliche Einzelanweisung des Anwenders.

---

## 1. Allgemeine Grundsätze & Architektur-Invarianten (PyBack)

1. **Git-Backup vor jedem Schritt:** Vor Beginn jedes Teilkapitels automatischer Git-Commit/Tag setzen (`playground_step1`, `playground_step2` usw.).
2. **Headless-Validierung (Keine UI-Tests):** Validierungen erfolgen rein headless (kein `QApplication.exec()`) über gezielte Python-Skripte im Unterordner `test/` (primär `test/test.py`-Harness plus fokussierte `check_*.py`-Validator). UI-Änderungen werden durch sorgfältige Code-Inspektion abgesichert, nicht durch Ausführen der GUI.
3. **Codebase-Formatierung:** Exakt **4 Leerzeichen** Einrückung (PEP8-Standard) und exakt 1 Leerzeile zwischen Methoden und Funktionsblöcken.
4. **Strikte Trennung & SRP/IoC (Kein SQL in UI):** `main_win.py` enthält NUR Controls und leitet Events weiter – KEINE Berechnung, KEINE Registry, KEIN DuckDB-Zugriff. Logik liegt in `controllers/`, `algos/`, `repositories/`, `workers/`.
5. **Vektorisierung pur (Agents.md):** Keine Loops (`for`/`while`) in Algo-Logiken. Jede Berechnung über NumPy/Pandas/vectorbt.
6. **Fenster-Persistenz:** Alle Einstellungen werden zusammen mit den Fensterdaten gespeichert und beim Neustart restored (StateManager / `global_settings`, Muster `win_main`-Geometrie).
7. **Concurrency-Guard:** Intensive Playground-Berechnungen laufen in einem QThread (`PlaygroundWorker`), damit das UI nicht einfriert und der Sync-Timer nicht blockiert.
8. **Defensive Module-Loader:** `AlgoRegistry` scannt `algos/` dynamisch; ein fehlerhaftes Modul wird einzeln übersprungen (try/except pro Modul), der Rest funktioniert.
9. **Open/Closed & Code-Preserving:** Erweiterungen strikt additiv durch neue Dateien; bestehende Kern-Klassen bleiben geschützt. Algos in `algos/` mit Präfix `alg_`; Playground-Klassen in `ui/` mit `playground_` bzw. `ParamFormWidget`/`TimeRangeWidget`/`AlgoPickerDialog`.
10. **PineScript-Input-Zone:** Jeder Algo deklariert sein `parameter_schema` direkt am Dateianfang unter dem Header-Docstring (wie `srv_*`-Services).

---

## 2. Konzept: Algo-Darstellungs-Picker (Farbe, Linienart, Stärke, Symbol)

> **Anwender-Anforderung (17.08.2026):** Für **jeden Algo** einen **Color Picker** plus **Auswahl der Linienart**, der **Linienstärke** und – falls der Algo **nicht als Linie** definiert ist – ein **Symbol**. Die Werte werden **gespeichert/restored**; der **Default** liegt in der **Algo-Definition** (`parameter_schema`). **Alles in einem Widget.**

### 2.1 Entscheidung: Übernahme aus PyTrader (wirtschaftlich)

Im Projekt `F:\Python\PyTrader` existiert bereits ein **ausgereifter, generischer Stil-Wähler** (Phase 16, 06.–07.08.2026) – **Kopieren ist wirtschaftlicher als Neuentwicklung:**

| Datei (PyTrader) | Inhalt | Passung |
|---|---|---|
| `chart/widgets/style_picker_widget.py` | `StylePickerWidget` (kompakter Button: Farb-Swatch-Icon + Vorschau-Text, z. B. `● 2px Solid`) + `StylePickerDialog` (modaler Popover: TradingView-Farbpalette, Hex/RGB-Eingabe, Transparenz-Slider 0–100 %, `[Anpassen...]`-Fallback auf `QColorDialog.getColor()`, Linienstärke 1–10 px + Linienart-Dropdown, bzw. Markergröße 1–20 px + Markerform-Dropdown, optionale `sichtbar`-Checkbox) | **1:1 passend** |
| `chart/overlays/style_models.py` | `LineStyle`/`MarkerStyle` Dataclasses + `LINE_STYLES`/`MARKER_SHAPES` + `to_js_dict()`/`to_dict()`/`from_dict()` | **Passend**, aber LWC-v5-Werte → auf Plotly umzustellen |

Beide Dateien sind eigenständig (nur PySide6 + Standardbibliothek). Das Widget erfüllt bereits: Farbe, Linienart, Stärke, Markerform, Persistenz-Verträge (`to_dict`/`from_dict`), `style_changed`-Signal, `get_style()`/`set_style()`/`set_color()`-Fassade.

**Notwendige Anpassungen an Plotly (statt TradingView LWC v5):**
- `LINE_STYLES`: LWC `solid/dashed/dotted/dashdotted` → **Plotly-Dash-Werte** `solid/dot/dash/longdash/dashdot/longdashdot` (exakt die 6 Werte aus dem Plotly-Validator `scatter.line.dash`).
- `MARKER_SHAPES` → **`PLOTLY_SYMBOLS`** (Marker-Symbol-Dropdown): Plotly bietet ~130+ Symbole (`scatter.marker.symbol`). Für das Dropdown wird eine **kuratierte Auswahl** der gebräuchlichsten übernommen (circle, square, diamond, cross, x, triangle-up/down/left/right, pentagon, hexagon, octagon, star, hourglass, bowtie, arrow-up/down, line-ew/ns, …) – übersichtlich und direkt plotly-kompatibel.
- `to_js_dict()`/`to_dict()`: statt LWC-JS-Bridge werden die Werte **1:1 auf Plotly-HTML-Attribute** abgebildet (`line=dict(color, dash, width)` bzw. `mode="lines+markers"`/`"markers"` + `marker=dict(symbol, size, color)`).

**Ziel-Ablage in PyBack:** `ui/style_models.py` + `ui/style_picker_widget.py` (an PyBack-Namensraum angepasst, keine `chart/`-Pfade).

### 2.2 UI: Alles in einem Widget

- **`StylePickerWidget` = Button-Only** (Farb-Swatch-Icon + Vorschau-Text, z. B. `● 2px Solid` bzw. `● Circle`) – wird in der `ParamFormWidget` als **eine Zeile** pro Darstellungs-Parameter gerendert.
- **Klick → `StylePickerDialog`** (modaler Popover) bündelt alle Einstellungen:
  - **Farbe:** TradingView-Palette (16 Schnellfarben) + Hex/RGB-Eingabe + Transparenz-Slider (0–100 %) + `[Anpassen...]` → natives `QColorDialog.getColor()`. (Der native Dialog allein zeigt unter Win11/Qt6 Rendering-Macken → nur als Fallback.)
  - **Linienmodus** (`style_type="line"`): Linienstärke `QSpinBox` (1–10 px) + Linienart `QComboBox` (6 Plotly-Dash-Werte).
  - **Markermodus** (`style_type="marker"`): Markergröße `QSpinBox` (1–20 px) + Symbol-`QComboBox` (kuratierte Plotly-Symbole) – **genau das gewünschte Dropdown mit den verfügbaren Symbolen.**
  - Optional `sichtbar`-Checkbox (`show_visibility=True`).
- Farb-Logik wie in PyTrader: Alpha 255 → `#RRGGBB`, Alpha < 255 → `rgba(r,g,b,a)` – **1:1 kompatibel mit Plotly/HTML/CSS**.

### 2.3 Schema-Konvention (Default in der Algo-Definition, PineScript-Input-Zone)

Jeder Algo deklariert in `parameter_schema` einen **Darstellungs-Block** neben seinen Fach-Parametern (Konvention aus PyTrader `indicator_dialog_schema.py`):

```python
parameter_schema = {
    # -- Fach-Parameter (bestehend) -----------------------------------
    "period":   {"type": "int",  "default": 20, ...},
    # -- Darstellung (NEU, Default in der Definition) ------------------
    "line_color": {"type": "color", "default": "#ff7f0e",
                   "style_type": "line", "allow_alpha": True},
    # Sibling-Keys (Konvention 'color' -> 'style'/'width' bei line,
    # 'shape'/'size' bei marker) – werden NICHT als eigene Controls
    # gerendert, sondern ueber das StylePickerWidget mitgesetzt und
    # flach in instance_params persistiert:
    "line_style": {"type": "choice", "default": "solid",
                   "options": ["solid", "dot", "dash", "longdash",
                               "dashdot", "longdashdot"], "hidden": True},
    "line_width": {"type": "int", "default": 2, "min": 1, "max": 10,
                   "hidden": True},
}
```

- **Nur der `color`-Key** wird als `StylePickerWidget` gerendert; die Sibling-Keys liegen als `"hidden": True` im Schema, werden über `get_style()` ausgelesen und flach in `instance_params` geschrieben.
- **Default-Werte stehen damit exakt in der Algo-Definition** (Anforderung erfüllt).
- **`style_type`** (`"line"` | `"marker"`) steuert Linien- vs. Marker-Modus. **Marker-Symbole kommen nur zum Tragen, wenn der Algo nicht als Linie definiert ist** (result_schema-`store != "series"` bzw. optionales `"render"`-Feld: `"line"` | `"marker"` | `"lines+markers"`).
- Weitere Schema-Flags aus PyTrader: `allow_alpha`, `show_visibility`, `color_only` (reiner Farbwähler ohne Geschwister).

### 2.4 Datenfluss & Integration (PyBack)

1. **`ParamFormWidget`** erweitert: neuer Schema-Typ `"color"` → `StylePickerWidget`-Zeile; `_ctrl_value` liest `ctrl.get_style().color` zurück, die Sibling-Keys werden beim `params_changed`-Emit mitgeschrieben.
2. **`instance_params`** (flaches Dict je Instanz) enthält damit automatisch `line_color`/`line_style`/`line_width` (bzw. Marker-Äquivalente) – **Persistenz läuft über den bestehenden Save/Restore-Roundtrip, kein Zusatzaufwand.**
3. **Controller `_build_instance_traces`**: Farbe/Linienart/Stärke/Symbol aus `instance_params[instance_key]` statt aus `_color_for_instance()` (Farbzyklus bleibt **Fallback**, wenn kein Stil gesetzt/gespeichert ist – Abwärtskompatibilität für alte States).
4. **`_js_add_overlay`/`_js_update_overlay`** (inkrementelle JS-Traces): `line=dict(color, dash, width)` bzw. `marker=dict(symbol, size)` ins Trace-JSON übernehmen (statt hartem `width=1.5`).
5. **`PlaygroundChartService.build_candlestick_html`**: Overlay-Dict erhält `dash`/`width`/`symbol`/`size`-Felder; `mode="lines"` bzw. `"lines+markers"`/`"markers"` je nach render-Typ; `line`/`marker`-Attribute werden gesetzt.

---

## 3. Schrittanleitung (mit UI-Review-Stopps)

### Phase 7 – Algo-Darstellungs-Picker (Stil-Widget aus PyTrader übernehmen)

- [ ] 7.1 **Commit vor Schritt** (`playground_step7`): sauberer Ausgangspunkt
- [ ] 7.2 `ui/style_models.py` anlegen (Plotly-Version: `LineStyle`/`MarkerStyle`, `LINE_STYLES` = 6 Plotly-Dash-Werte, `PLOTLY_SYMBOLS` = kuratierte Auswahl, `to_dict`/`from_dict`)
- [ ] 7.3 `ui/style_picker_widget.py` anlegen (aus PyTrader kopiert, angepasst: Imports `ui.style_models`, `LINE_STYLES`/`PLOTLY_SYMBOLS`, Plotly-kompatible Werte)
- [ ] 7.4 `ParamFormWidget`: Schema-Typ `"color"` → `StylePickerWidget` rendern; `_ctrl_value` + Sibling-Keys (`hidden`) beim Params-Emit mitnehmen
- [ ] 7.5 `PlaygroundChartService`: Overlay-Trace verarbeitet `dash`/`width`/`symbol`/`size` + `render`-Modus (lines/lines+markers/markers)
- [ ] 7.6 Controller: `_build_instance_traces`/`_js_add_overlay`/`_js_update_overlay` nutzen Stil-Params; Farbzyklus nur als Fallback
- [ ] 7.7 `alg_sma` (Referenz-Algo): Darstellungs-Defaults (`line_color`/`line_style`/`line_width`) ins `parameter_schema`
- [ ] 7.8 Test in `test/test.py`: Stil-Roundtrip (Defaults aus Schema, Widget-Read, Sibling-Keys in `instance_params`, Plotly-HTML enthält `dash`/`width`/`symbol`), Persistenz-Save/Restore
- [ ] 7.9 **Commit** + Implementierungs-Log in Kapitel 5
- [ ] **STOPP → UI-Review:** StylePicker-Button + Dialog (Farbe, Linienart, Stärke, Symbol-Dropdown), Persistenz nach Neustart?

---

## 4. Anwender-Entscheidungen (eingearbeitet)

| # | Frage | Entscheidung |
|---|---|---|
| 0 | Persistenz | **Alle Einstellungen werden mit den Fensterdaten gespeichert und beim Neustart restored** |
| 1 | Darstellungs-Widget | **Ein kompaktes Stil-Widget** (Farb-Swatch-Button → Dialog) je Algo: Farbe + Linienart + Stärke; **Symbol als Dropdown** bei Marker-Algos (falls nicht als Linie definiert). Defaults in der Algo-Definition (`parameter_schema`), Persistenz pro Instanz über `instance_params`. **Übernahme aus PyTrader** (`style_picker_widget` + `style_models`), angepasst auf Plotly |

---

## 5. Implementierungs-Log (PyBack)

> Format: `**DD.MM.YYYY, HH:MM – <ID> <Beschreibung>**`. Einträge erfolgen erst nach expliziter Freigabe des Anwenders.

- **17.08.2026 – Phase 7-Konzept** Algo-Darstellungs-Picker (Farbe/Linienart/Stärke/Symbol) dokumentiert: Übernahme des PyTrader-`StylePickerWidget`/`StylePickerDialog`/`style_models` (angepasst auf Plotly: 6 Dash-Werte, kuratierte Plotly-Symbole als Dropdown), Schema-Typ `"color"` in `ParamFormWidget`, Sibling-Keys-Konvention, Persistenz über `instance_params`, Farbzyklus als Fallback (Commit folgt nach Umsetzung).
  * **Kein Coding** – reine Konzept-/Schritt-Dokumentation (Anwender-Anforderung „erstmal prüfen").

