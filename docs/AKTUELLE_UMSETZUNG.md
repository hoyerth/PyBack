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

## 2. Konzept: Algo-Playground im Hauptfenster

### 2.1 Grundsatzentscheidung: Zwei getrennte Welten

| | **Playground** (in `main_win`) | **Backtest-Fenster** (separat, später) |
|---|---|---|
| Zweck | Algos **spielerisch** auf Kerzen testen (Overlays) | Echtes **vbt.Portfolio-Backtesting** |
| Ergebnis | Overlays (Linien, Bänder, Marker) direkt auf dem Kurschart | vbt.Portfolio: Equity, Drawdown, Trades, Kennzahlen |
| Algo-Vertrag | Overlay-Hook (liefert Serien/Traces) | `compute_signals()` → `vbt.Portfolio` (bestehender Agents.md-Vertrag) |
| Massentests | – | Parameter-Sweeps, Serien-Runs (eigenes Fenster) |

Ein Algo kann **beide** APIs optional anbieten. Der Playground nutzt nur den Overlay-Teil, das Backtest-Fenster nur den Portfolio-Teil – kein API-Konflikt.

### 2.2 UI-Gestaltung (verbindlich, UI-Reviews durch Anwender)

```text
┌──────────────────────────────────────────────────────────────────┐
│ Top-Zeile (bestehend): [Symbol] [TF] [★ Favoriten]  [Scan] [Opt] │
├──────────────────────────────────────────────────────────────────┤
│ Zeitraum: [Von ▾] [Bis ▾]  [1T][1W][1M][3M][YTD]  (Presets)      │
├──────────────┬────────────┬──────────────────────────────────────┤
│ ALGO-LISTE   │            │            CANVAS                     │
│ (Checkboxen) │  SPLITTER  │     QWebEngineView / Plotly-HTML      │
│ [x] SMA 20   │ (verschieb-│     ┌─ Candlestick (Kurs)            │
│ [ ] SMA 50   │   bar)     │     │ + Overlays der AKTIVEN Algos    │
│ [x] ATR 14   │            │     │ + Marker (Einstieg/Exit)        │
│ [+] Add      │            │     └───────────────────────────────  │
│              │            │     Legende, Sync-Zoom, Hover-X       │
│ PARAMETER    │            │                                       │
│ (für den     │            │                                       │
│  angeklickten│            │                                       │
│  Algo)       │            │                                       │
│  period [__] │            │                                       │
├──────────────┴────────────┴──────────────────────────────────────┤
│ Statuszeile: Datenquelle, Kerzenzahl, Rechenzeit                  │
└──────────────────────────────────────────────────────────────────┘
```

**Widget-Beschreibung:**

| Widget | Details |
|---|---|
| **Zeitraum-Zeile** | Unter der Top-Zeile. `Von/Bis`-DateTimeEdit + Preset-Buttons `1T/1W/1M/3M/YTD`. Eigenständiges Widget `TimeRangeWidget`. |
| **Algo-Liste** | Links oben. `QListWidget` **mit Checkboxen** (schnelles Ein-/Ausblenden in der Darstellung). Enthält **nur die hinzugefügten** Algos (nicht alle verfügbaren). |
| **„+"-Add-Button** | Unter der Liste. Öffnet `AlgoPickerDialog`: **einfache Checkliste aus einem Dropdown** mit allen verfügbaren Algos (Registry). Algos können **mehrfach** ausgewählt werden (mehrere Instanzen desselben Algos mit unterschiedlichen Parametern). |
| **Kontextmenü** | An Listeneinträgen: **„Entfernen"** löscht den Eintrag aus der Liste. Zusätzlich Checkbox in der Auswahlliste für schnelles Switchen der Darstellung. |
| **Parameter-Feld** | Links unten. `ParamFormWidget` – zeigt die Parameter des **gerade angeklickten** Listeneintrags, dynamisch aus dessen `parameter_schema` generiert (SpinBoxen/Checkboxen). |
| **Canvas** | Rechts. `QWebEngineView` mit Plotly-HTML (offline, plotly.js eingebettet), Dark-Theme passend zur App. |
| **Splitter** | Zwischen linkem Panel und Canvas: **verschiebbar** (`QSplitter`), Position wird persistiert. |

**Wechselwirkung:**
- Klick auf Listeneintrag (ohne Checkbox) → Parameter-Form wird befüllt.
- Checkbox an/aus → Overlay wird im Canvas ein-/ausgeblendet (ohne Neuberechnung der anderen).
- Parameter-Änderung → nur der betroffene Algo wird neu berechnet (Debounce 300 ms).

### 2.3 Architektur (Logik komplett außerhalb von `main_win`)

```text
main_win.py  = NUR Zusammenbau der Controls + 1 Controller-Zeile
   │
   ├── ui/time_range_widget.py       (Zeitraum-Picker, Signal range_changed)
   ├── ui/param_form_widget.py       (Schema→Widgets, Signal params_changed)
   ├── ui/algo_picker_dialog.py      ("+"-Dialog, alle Algos auswählen)
   ├── ui/playground_chart_service.py(Plotly-HTML-Builder, nutzt Overlay-Hooks)
   │
   └── controllers/algo_playground_controller.py  (QObject, Debounce 300ms,
            hält Zustand: active_algos, selected_algo, params, range)
            ├── nutzt repositories/market_data_repository.py (Daten, existiert!)
            ├── nutzt algos/algo_registry.py (dynamischer, defensiver Scan)
            └── startet workers/playground_worker.py (QThread, Rechnung)
```

**Regeln:**
- `main_win.py` kennt **keine** Berechnung, **keine** Registry, **kein** DuckDB – nur `AlgoPlaygroundController(self)`.
- Algos bleiben **vektorisiert** (NumPy/Pandas), kein `iterrows`.
- Registry-Scan ist **defensiv**: ein kaputtes Modul wird übersprungen, der Rest funktioniert.
- UI-Änderungen werden **nur durch Code-Inspektion + gezielte Tests** abgesichert (kein GUI-Ausführen).

### 2.4 Daten-Logik Zeitraum vs. Timeframe

- Die **Kerzen werden immer geladen wie aktuell im Fenster bei der eingestellten TF** (Symbol/TF-Auswahl aus der Top-Zeile).
- Der **Zeitraum-Picker bestimmt nur den sichtbaren Ausschnitt** des bereits geladenen Datensatzes – kein Neuladen aus DuckDB bei jedem Preset-Klick, sondern client-seitiges Slicen des im Controller gecachten DataFrames.
- Beim Wechsel von Symbol oder TF wird der DataFrame einmalig aus `MarketDataRepository` geladen und gecacht; danach arbeiten alle Playground-Berechnungen auf diesem Cache.

---

## 3. Schrittanleitung (mit UI-Review-Stopps nach jedem UI-Schritt)

### Phase 0 – Fundamente (kein UI)
- [ ] 0.1 `algos/algo_registry.py` erstellen (dynamischer, defensiver Scan von `alg_*.py`; Registry liest `parameter_schema` + Overlay-Hook aus)
- [ ] 0.2 Ordner `controllers/` und `workers/` anlegen
- [ ] 0.3 Test in `test/test.py`: Registry findet nichts, solange keine Algos existieren → sauberer 0-Zustand
- [ ] 0.4 **Commit** (`playground_step1`)

### Phase 1 – Basis-UI (UI-Review durch Anwender)
- [ ] 1.1 `TimeRangeWidget` erstellen (Von/Bis + Presets 1T/1W/1M/3M/YTD)
- [ ] 1.2 In `main_win` unter der Top-Zeile einbauen
- [ ] **STOPP → UI-Review:** Zeitraum-Zeile OK? Position, Größe, Presets?

### Phase 2 – Algo-Liste + Add-Dialog (UI-Review)
- [ ] 2.1 `AlgoPickerDialog` erstellen (Dropdown-Checkliste, Mehrfachauswahl, „Übernehmen")
- [ ] 2.2 `QListWidget` mit Checkboxen im linken Panel + „+"-Button
- [ ] 2.3 Kontextmenü „Entfernen" an Listeneinträgen
- [ ] **STOPP → UI-Review:** Listenlayout, Checkbox-Interaktion, Dialog-Größe, Kontextmenü?

### Phase 3 – Parameter-Form (UI-Review)
- [ ] 3.1 `ParamFormWidget` erstellen (generiert aus `parameter_schema`)
- [ ] **STOPP → UI-Review:** Anordnung der Felder, SpinBoxen/Checkboxen/Typen?

### Phase 4 – Canvas-Basics (UI-Review)
- [ ] 4.1 `PlaygroundChartService`: erstmal **nur Candlestick** + Zeitraum-Slice
- [ ] 4.2 `QWebEngineView`-Canvas + Dark-Theme einbauen (offline plotly.js)
- [ ] **STOPP → UI-Review:** Canvas-Darstellung, Dark-Theme, Zoom/Sync?

### Phase 5 – Erster echter Algo (Vertrag festlegen)
- [ ] 5.1 Beispiel-Algo `algos/alg_sma.py` mit Overlay-Hook + `parameter_schema`
- [ ] 5.2 Overlay auf Canvas + Param-Änderung → Neuberechnung (Debounce)
- [ ] 5.3 Overlay-Farben: erstmal automatisch (farbiger Zyklus); später je Algo einstellbar
- [ ] **STOPP → UI-Review:** Overlay-Sichtbarkeit, Parameter-Live-Update, Farbzyklus?

### Phase 6 – Integration & Härtung
- [ ] 6.1 `PlaygroundWorker` (QThread, UI friert nicht)
- [ ] 6.2 Symbol/TF-Wechsel + Zeitraum → Daten-Slice aus Cache/MarketDataRepository
- [ ] 6.3 **Persistenz (Anforderung 0):** Alle Playground-Einstellungen (aktive Algos inkl. Parameter, Zeitraum, Splitter-Position) werden mit den Fensterdaten gespeichert und beim Neustart restored
- [ ] **STOPP → UI-Review:** Gesamter Workflow, Performance bei vielen Algos, Persistenz nach Neustart?

---

## 4. Anwender-Entscheidungen (eingearbeitet)

| # | Frage | Entscheidung |
|---|---|---|
| 0 | Persistenz | **Alle Einstellungen werden mit den Fensterdaten gespeichert und beim Neustart restored** |
| 1 | Linkes Panel | **Splitter** (verschiebbar), Position wird persistiert |
| 2 | „+"-Dialog | **Einfache Checkliste aus einem Dropdown**; Algos können **mehrfach** ausgewählt werden (versch. Parameter) |
| 3 | Overlay-Farben | Erstmal **automatisch** (farbiger Zyklus); später auch je Algo einstellbar |
| 4 | Zeitraum-Presets | **1T/1W/1M/3M/YTD**; Kerzen werden **immer geladen wie aktuell bei der eingestellten TF** (Zeitraum = nur sichtbarer Ausschnitt, kein Neuladen) |
| 5 | Entfernen/Switching | **Kontextmenü „Entfernen"** + zusätzlich **Checkbox in der Auswahlliste** für schnelles Switchen in der Darstellung |
| – | Playground-Zweck | Nur spielerisches Testen auf Kerzen (Overlays). Echtes vbt-Backtesting bekommt ein **eigenes Fenster** mit Massentests |

---

## 5. Implementierungs-Log (PyBack)

> Format: `**DD.MM.YYYY, HH:MM – <ID> <Beschreibung>**`. Einträge erfolgen erst nach expliziter Freigabe des Anwenders.

- *(noch keine Einträge – Dokument ist das Konzept für den Algo-Playground)*
