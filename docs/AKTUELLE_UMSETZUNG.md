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

 1) Architektonische Prüfung

 Stärken
 - Saubere Schichtung: UI (Views) → Controller (Presenter) → QThread-Worker → Repository → DuckDB. SRP überwiegend eingehalten.
 - Gute Entkopplung: EventBus, Signals/Slots, WindowManager ohne MainWindow-Import, PersistentWindow-Registry, thread-lokaler DbPool mit WAL-Recovery.
 - Vektorisierte Algos (pandas rolling), defensive Algo-Registry, RAM-only Overlay-Cache mit Generation-Counter gegen veraltete Worker-Ergebnisse.

 Schwächen
 | # | Befund | Ort |
 |---|--------|-----|
 | A1 | Render-Strategie ist der architektonische Flaschenhals: Bei jeder Änderung (Zeitraum, Preset, Symbol/TF, Sync, Start) wird das komplette HTML neu gebaut und via canvas.setHtml() ein voller Seiten-Reload ausgelöst. Es gibt keine
 persistente JS-Bridge / kein Diffing – Inkrementelles addTraces/deleteTraces existiert nur für Overlay-Checkboxen. | _render_chart() (Controller) + build_candlestick_html() |
 | A2 | God-Object: algo_playground_controller.py (49 KB) macht alles: Registry, Liste, Params, Canvas, View-Persistenz, Overlay-Orchestrierung, Worker-Pool. | controllers/ |
 | A3 | Datenmodell redundant: 20,0 Mio. Zeilen / 1,31 GB. M2/M5/M10/M15/M30/H4 sind reine Aggregationen von M1 (z. B. BTCUSD M1=3,6 Mio + M2=1,9 Mio + M5=0,8 Mio …). Kein Index auf ohlcv_bars. | data/market_data.duckdb |
 | A4 | CANDLE_LIMIT=5000 vs. Zeitraum-Picker (1T…YTD): Default-Zeitraum = 30 Tage → M1 bräuchte 43.200 Kerzen. Geladen werden nur die letzten 5000 (~3,5 Tage). Ergebnis: fast leeres Chart mit gequetschten Kerzen am rechten Rand. |
 CANDLE_LIMIT im Controller |
 | A5 | Zwei Queries pro Laden (precision + Candles), beide mit LOWER() und ohne Index; Cache-Verhalten nur „1× pro Paar", kein Zeitraum-bezogenes Nachladen. | market_data_repository.py |
 | A6 | state_manager = Fassade + Migrationslogik (gemischte Verantwortung, minor). | state_manager.py |

 ---

 2) Performance-Messungen (real, gegen `market_data.duckdb`)

 | Messung | Ergebnis | Bewertung |
 |--------|----------|-----------|
 | DB-Load 5000 Kerzen (SILVER M1) | 64 ms | ok, aber 2 Queries + Full-Scan ohne Index |
 | HTML-Build (Python) 5000 Kerzen | 80–275 ms | spürbar, aber nicht dominant |
 | to_html()-Serialisierung allein | 13 ms | unkritisch |
 | `setHtml()` → Seiten-Reload (341 KB HTML + 4,8 MB plotly.min.js neu laden + 5000 Candlestick-Render + rangebreaks + x-unified-Hover) | ~0,5–2 s | dominant – passiert bei jedem Render |
 | Overlay-JS: [str(pd.to_datetime(int(t), unit='s')) for t in x] | 302 ms / 5000 Pkte | 16× langsamer als vektorisiert (19 ms) |
 | Datenmenge gesamt | 20 Mio. Zeilen, SILVER M1 allein 3,27 Mio. | Chart nutzt nur 5000 |

 Fazit: Die „Lahmheit" kommt fast ausschließlich vom Seiten-Reload pro Render (A1) – nicht von DuckDB (64 ms). Sekundär: 5000 volle Candlestick-Punkte ohne LOD + teures Hover + Overlay-Schleifen.

 ---

 3) Vorschläge (priorisiert, in eine Textbox kopierbar)

 P0 – Größte Wirkung gegen „lahm"
 1. `setHtml` nur noch einmal (Struktur/Initial). Alle Daten-Updates (Zeitraum, Preset, Kerzen, Params, Sync) künftig über `Plotly.react()`/`restyle` per `runJavaScript` mit neuen Daten-Arrays. Zoom/Pan bleibt erhalten, kein
 plotly.min.js-Re-Load mehr. Erwartung: 0,5–2 s → ~50–150 ms.
 2. LOD/Downsampling mit NumPy: Wenn sichtbare Kerzen > ~1.000–1.500, OHLC-Buckets vektorisiert bilden (np.minimum.reduceat / np.maximum.reduceat bzw. pandas resample) und nur reduzierte Daten rendern. Beim Reinzoomen volle Auflösung.
 3. Overlay-Datums-Konvertierung vektorisieren: pd.to_datetime(epochs, unit='s').strftime('%Y-%m-%dT%H:%M:%S').tolist() statt Element-Schleife in _js_add_overlay/_js_update_overlay (302 → 19 ms).

 P1 – Datenmengen / DB
 4. Index CREATE INDEX idx_ohlcv_pair_time ON ohlcv_bars(symbol, timeframe, time) – Range-Queries und Sync-Updates deutlich schneller.
 5. Zeitraum-basiertes Laden statt „immer letzte 5000": WHERE time BETWEEN ? AND ? – nur sichtbare Kerzen laden; Limit vom gewählten Zeitraum abhängig + LOD-Kappung. Behebt zugleich das „fast leere 30-Tage-Chart" (A4).
 6. Precision-Query cachen (1× pro Paar) oder in die Candle-Query integrieren.
 7. Datenmodell entschlacken: M2/M5/… aus M1 aggregieren (oder bei Bedarf lazy erzeugen) → DB-Größe massiv reduzieren; VOLLIMPORT nur einmalig, danach strikt Delta.

 P2 – Weitere Render-Optimierungen
 8. Hover: bei >N Kerzen hovermode="x unified" → "closest" schalten oder Bucket-Hover (hoverinfo="skip").
 9. Startup-Doppel-Render vermeiden (Konstruktor-refresh_chart + Sync-Refresh bündeln).
 10. _poll_view-Timer (500 ms) + singleShot(500) nach jedem Render → nur bei Interaktion lesen.
 11. Statt pro Overlay-Instanz ein QThread → ein Worker mit Job-Queue (weniger Thread-Overhead bei vielen Instanzen).

 P3 – Architektur / Struktur
 12. Controller aufteilen (ChartRenderService, OverlayManager, ViewPersistence).
 13. In Algos konsequent to_numpy() / NumPy verwenden (pandas-Index-Overhead vermeiden); laut Agents.md profile-Modus von vectorbt als Standard-Schritt nutzen.

  Kurzfassung: Hauptproblem = kompletter Seiten-Reload bei jedem Render (A1). Fix 1+2 bringt die größte spürbare Beschleunigung; Fix 4+5 macht die Datenmengen beherrschbar.

---

## Implementierungs-Log: Performance-Optimierung Playground (17.08.2026)

> Taxonomie: P0#1/P0#2/P0#3 (Render), P1#4/P1#5/P1#6 (Daten/DB), P2#8/P2#10 (Render/View).
> Auftrag: "Arbeite alles ab in sinnvoller Reihenfolge" – umgesetzt auf Basis der Pro-Analyse (Abschnitte 1-3 oben).

### Umgesetzte Punkte

| # | Massnahme | Datei(en) | Messung / Bewertung |
|---|-----------|-----------|---------------------|
| P0#1 | **setHtml nur 1x** – _render_chart baut jetzt eine schlanke Plotly-Figur (uild_chart_figure), die Page-Shell (uild_page_html, feste Div pg-chart) wird EINMAL via setHtml gesetzt; alle Folge-Render laufen per Plotly.react() über unJavaScript (kein Seiten-Reload, kein plotly.min.js-Re-Load, Zoom/Pan bleibt erhalten). _on_canvas_load_finished + _pending_figure_json sichern den Erst-Render (idempotent). | ui/playground_chart_service.py, controllers/algo_playground_controller.py | 342 KB HTML + 4,8 MB JS-Reload (0,5–2 s) → ~119 KB react-JSON (~50–150 ms). Leere Zustände (Hinweis-HTML) bleiben der seltene setHtml-Ausnahmefall. |
| P0#2 | **LOD/Downsampling** – Candlestick: bei > 1200 sichtbaren Kerzen vektorisiert zu OHLC-Buckets via 
p.maximum/np.minimum.reduceat (Bucket-Zeit = erste Kerze, Standard-Aggregationskonvention); Overlays: _decimate_series (np.linspace, Endpunkte erhalten). Rangebreaks werden auf den ORIGINAL-Zeiten vor dem Downsampling berechnet (Lücken-Grenzen exakt). | ui/playground_chart_service.py (_maybe_downsample), controllers/algo_playground_controller.py | 43.200 M1-Kerzen (30 Tage) → 1.200 Render-Punkte; 30-Tage-M1 rendert jetzt vollständig (A4-Fix-Voraussetzung). |
| P0#3 | **Overlay-Datums-Konvertierung vektorisiert** – _epochs_to_iso statt Element-Schleife in _js_add_overlay/_js_update_overlay. | controllers/algo_playground_controller.py | gemessen: 29,8 ms vs. 336 ms pro 5.000 Punkte (~11× schneller; Pro-Schätzung 302→19 ms bestätigt). |
| P1#4 | **Index** – CREATE INDEX IF NOT EXISTS idx_ohlcv_pair_time ON ohlcv_bars(symbol, timeframe, time) idempotent in schema_initializer (einmalig ~15 s bei 20 Mio. Zeilen). **Wichtig (eigene Messung):** DuckDB 1.5.5 nutzt ARTEMIS fuer die Range-Query in der Praxis nicht (Spaltenscan der ~2 Mio. SILVER-M1-Zeilen ist warm ohnehin ~0 ms) – der Index bleibt als Punkt-Lookup/Reproduzierbarkeit, ist aber NICHT der Performance-Gewinn. | db/schema_initializer.py | 0 Indexe vorher → 1 Index; Range-Query 27.624 Zeilen in 68,7 ms. |
| P1#5 | **Zeitraum-basiertes Laden** – etch_candles_in_range (WHERE time BETWEEN, ORDER BY time DESC LIMIT ? → ASC) statt "letzte 5000"; Controller-Cache deckt den Zeitraum + 15 % Margin ab (_cache_covers mit 1 %/1 h Toleranz), _on_range_changed lädt nur bei nicht abgedecktem Bereich nach. Behebt A4 (fast leeres 30-Tage-Chart). CANDLE_LIMIT=5000 ersetzt durch MAX_CACHE_CANDLES=200_000. | epositories/market_data_repository.py, controllers/algo_playground_controller.py | 30-Tage-M1: 27.624 Kerzen in 68,7 ms (statt 5.000/3,5 Tage); Cache bleibt bei kleinen Verschiebungen erhalten (Tests grün). |
| P1#6 | **Precision-Query gecacht** (1× pro Paar, _precision_cache) + Queries von LOWER(symbol)=LOWER(?) auf symbol = UPPER(?) umgestellt (Daten sind UPPER-normalisiert, Verifikation: 0 Abweichungen) → keine Funktions-Scans auf der Spalte. | epositories/market_data_repository.py | 1 Precision-Query statt 1 pro Load; DRY über _get_precision_cached/_rows_to_candles. |
| P2#8 | **Hover adaptiv** – > 1200 sichtbare Kerzen → hovermode="closest", sonst "x unified" (Entscheidung vor LOD auf Original-Anzahl). | ui/playground_chart_service.py | teures unified-Hover entfällt bei grossen Zeiträumen. |
| P2#10 | **View-Poll nur bei Interaktion** – _poll_view überspringt den Timer nach 5 s Idle (VIEW_POLL_IDLE_S); save_state liest beim Schliessen weiterhin explizit (+ 300 ms Event-Pump) → kein Zoom-Verlust. | controllers/algo_playground_controller.py | Idle-Lasten (runJavaScript alle 500 ms) sinken auf ~0. |

### Bewusst NICHT umgesetzt (Begründung)

* **P1#7 (Datenmodell entschlacken: M2/M5/… aus M1 aggregieren):** betrifft die Sync-/Import-Schicht, nicht die Playground-Lahmheit; grosser Eingriff mit Datenmigrations-Risiko – separat zu planen.
* **P2#9 (Startup-Doppel-Render bündeln):** durch P0#1 erledigt sich das Problem faktisch (Folge-Render sind billige react-Aufrufe, kein 2. Seiten-Reload mehr).
* **P2#11 (Worker-Job-Queue), P3#12/#13 (Controller aufteilen, Algos to_numpy):** Architektur-Refactoring ohne direkten Performance-Gewinn fuer die gemessene Lahmheit – Folgeschritt, nicht Teil dieser Runde.

### Verifikation (headless, keine UI)

* 	est/test.py – komplette Suite: **alle Phasen 0–7 OK** (Exit 0), inkl. angepasster FakeRepo-Stubs (etch_candles_in_range additiv).
* 	est/check_optimize_impl.py – LOD-OHLC-Korrektheit, Figure-JSON (numpy-sicher, hovermode), _epochs_to_iso-Geschwindigkeit, Repository-Range + Precision-Cache, Controller-React-Flow (setHtml 1× + Plotly.react).
* 	est/check_optimize_prep.py, check_optimize_index.py, check_optimize_index2.py, check_optimize_plotly_json.py, check_optimize_perf.py – Vorab-/Beweis-Messungen (Index, DuckDB-Version 1.5.5, JSON-Typen, Benchmark).
* py_compile auf allen geaenderten Dateien OK.

---

## Implementierungs-Log: SMA-Overlay sichtbar – Diagnose & Bestaetigung (17.08.2026)

> **Anwender-Bestaetigung:** „es sind alle linien zu sehen, sma und test" – die SMA-Linie wird im Playground wieder angezeigt. Testcode danach entfernt.

### Befund & Diagnoseweg

* **Problem:** SMA-Linie unsichtbar (Legende sichtbar), nach der Performance-Optimierung (P0#1 Plotly.react). Die externe KI vermutete den Fehler in der Uebergabekette (NaN-Werte → JSON → Plotly), die Analyse des aktuellen Stands widerlegte das (headless mit echter plotly.min.js: SMA-x = ISO-Strings, 27605 gueltige Punkte im Range, Linie sichtbar – auch mit NaN-Warmup + NaN-Literal-eval).
* **Diagnose (reduziert, token-sparend):** Zwei Diagnose-Runden mit injizierten Test-Linien im Playground:
  1. Test-Linien DIREKT in `_build_overlay_traces` angehaengt (Bypass `_overlays`): Test-Linien sichtbar, SMA nicht.
  2. Test-Serien (`test_flat`/`test_close`) in den **`_overlays`-Speicher** injiziert (exakter SMA-Pfad `_overlays` → `_build_instance_traces` → `build_chart_figure` → `Plotly.react`): **alle** Linien sichtbar (SMA + beide Test-Linien), `[DIAG]`-Log zeigte `sma_present=True`.
* **Erkenntnis:** Der komplette additive Overlay-Renderpfad funktioniert; die SMA-Serie selbst ist korrekt (rolling/min_periods-Warmup, NaN unkritisch fuer plotly.js). Der Fehler war weder in `alg_sma.py` noch in der NaN-Serialisierung oder dem Trace-Builder – er trat nur im Laufzeit-Render des QWebEngine auf (Race/veraltete Page), adressiert durch die vorherigen Bugfixes (`#pg-chart`-Selector, Plotly.react statt addTraces, Self-Heal via View-Timer, connectgaps).

### Aenderungen

* **Kein Source-Code-Diff:** `controllers/algo_playground_controller.py` ist nach Entfernen der temporaeren Diagnose wieder exakt auf HEAD (Commit `9d51126`). `test/check_testlines.py` (Diagnose-Test) wurde entfernt.

### Verifikation

* `.venv\Scripts\python.exe -m py_compile controllers/algo_playground_controller.py` OK.
* Bestehende headless-Validatoren decken den SMA-Pfad ab: `test/check_sma_visibility.py`, `test/check_visibility_apppath.py`, `test/check_controller_render.py`, `test/check_overlay_regression.py`, `test/check_shell_selfheal.py`.

---

## Implementierungs-Log: Mess-Tool im Playground (17.08.2026)

> **USER-REQ:** Mess-Tool aus PyTrader wirtschaftlich uebernehmen (oder neu bauen) und in den Playground einbauen; Aktivierung per **Shift + Rechtsklick-Maus**.

### Entscheidung (Frage 1): Uebernehmen & anpassen, nicht neu bauen

* 1:1-Kopie aus PyTrader ist NICHT moeglich: PyTrader nutzt **Lightweight Charts v5** (`candleSeries.coordinateToPrice`, `timeScale.coordinateToLogical`), PyBack nutzt **Plotly 3.x** (`#pg-chart`, `gd._fullLayout.xaxis`). Die Koordinaten-/Event-APIs sind fundamental verschieden.
* Uebernommen (library-agnostisch, ~60 %): Mess-Zustandsmodell, CSS-Overlay-Design (`#measurement-region`/`#measurement-box`), Live-Messtext (Δ Preis % + absolut, Δ Zeit Bars + DD:HH:MM, Start/Ende), `formatDuration`, `formatMeasurementText`, Escape/normaler-Klick-loescht, temporaere Anzeige.
* Neu adaptiert (Plotly): Koordinaten via `xaxis.p2l/l2p` + `yaxis.p2l/l2p` (Plotly linearisiert Datumsachsen als UTC-ms → **Wanduhr-Korrektur** via `getTimezoneOffset`, da die X-Achse Wanduhr-ISO-Strings zeigt – kein Berlin-Offset, Projekt-Konvention); Trigger **Shift + rechte Maustaste** (`e.shiftKey && e.button === 2`, Kontextmenue wird bei Shift unterdrueckt); Bars-Zaehlung per Binary-Search (`fracIndexAtMs`) ueber die sichtbaren Candles; Nachkommastellen automatisch aus den Close-Werten.

### Aenderungen

| Datei | Inhalt |
|-------|--------|
| `assets/measurement.js` | **Neu:** Selbstenthaltenes Mess-Modul (IIFE, Plotly-Adaption, pure Funktionen testbar). |
| `ui/playground_chart_service.py` | `_MEASURE_JS_URL` (Datei-Referenz wie plotly.min.js), CSS fuer `#pg-wrap`/`#measurement-region`/`#measurement-box`, Page-Shell wrappt `#pg-chart` in `#pg-wrap` + Overlay-Divs, laedt `measurement.js` und ruft `Measurement.init()` nach dem ersten Render. |
| `test/check_measurement.py` | **Neu (lokal, gitignored):** Headless-End-to-End (echte SILVER-M1-Kerzen, jsdom, echte plotly.min.js + measurement.js inline) – simuliert Shift+Rechts-Drag und prueft Messbox + Escape-Clear. |

### Bedienung

* **Shift + rechte Maustaste ziehen:** Messbox mit Live-Werten (Δ Preis in % + absolut, Δ Zeit in Bars + DD:HH:MM, Start/Ende mit Wanduhr-Zeit).
* **Linker Klick oder Escape:** Messung entfernen.
* Die Box folgt Zoom/Pan/react automatisch (`plotly_afterplot`/`plotly_relayout` → `updatePositions`, Datenkoordinaten-basiert).

### Verifikation (headless, keine UI)

* `test/check_measurement.py` – **OK**: Messbox sichtbar, Messtext vollstaendig (`Δ Preis: -4.43% (-3.047)`, `Δ Zeit: 317 Bars · 10:13:00`, Start/Ende korrekt), Escape-Clear funktioniert.
* `py_compile ui/playground_chart_service.py` OK.

---

## Implementierungs-Log: Style-Picker – `show`-Wert & 'sichtbar'-Checkbox entfernt (17.08.2026, 18:16)

> **USER-REQ:** „Aus dem Style-Picker Wert und Checkbox 'visible' entfernen" – umgesetzt in Variante (b): `show` komplett entfernt (nicht nur ausgeblendet). Sichtbarkeit eines Overlays steuert ausschliesslich die Instanz-Checkbox im Algo-Panel (`AlgoListPanel`); der Controller/Render nutzte `show` nie (Analyse bestaetigt).

### Aenderungen

| Datei | Inhalt |
|-------|--------|
| `ui/style_models.py` | `LineStyle`/`MarkerStyle`: Feld `show` entfernt; `to_dict()` liefert kein `"show"` mehr; `from_dict()` liest es nicht mehr (tolerant: alte gespeicherte States mit `show` bleiben ladbar, Feld wird ignoriert – Abwaertskompatibilitaet). Ungenutzter `_as_bool`-Helper entfernt. |
| `ui/style_picker_widget.py` | `StylePickerDialog` + `StylePickerWidget`: Parameter `show_visibility`, Property `show_visibility`, Attribut `_show_visibility` und Checkbox `_show_check` ('sichtbar') komplett entfernt; `get_style()`/`_apply_style_to_ui()` ohne `show`-Logik; `_open_picker_dialog()` reicht kein `show_visibility` mehr durch; ungenutzter `QCheckBox`-Import entfernt. |
| `ui/param_form_widget.py` | `show_visibility = spec.get("show_visibility", True)`-Durchreichung an den StylePicker entfernt. |

### Verifikation (headless, keine UI)

* `py_compile` auf allen 3 Dateien OK.
* Keine Rest-Referenzen auf `show_visibility` / `_show_check` / `style.show` / `_as_bool` im Projekt (excl. `docs/`).
* `test/test.py` → `test_phase7_style_picker` **OK** (to_dict/from_dict-Roundtrip, StylePickerWidget, ParamFormWidget, Controller, Persistenz).
* Zusatz-Check: `to_dict()` enthaelt kein `show`, keine `show_visibility`-API mehr vorhanden – **OK**.

---

## Implementierungs-Log: MA-Algo aus PyTrader (17.08.2026, 18:26)

> **USER-REQ (final, 17.08.2026):** MA-Indikator aus PyTrader pruefen und als Algo einbauen – **EIN MA-Wert** statt 8 (`ind_moving_averages`). Vorgaben: Defaults wie PyTrader (`EHMA/4/smoothing 10/alpha 2.0`), Preisquelle **nur close** (kein `use_close`), zusaetzlich **dual_color** (Checkbox "Auf/Ab verschiedene Farben") mit zwei Farbfeldern **Vorgabe Gruen/Rot**.

### Entscheidungen

* **Uebernahme:** Kern (`MATemplateEngine.calculate_ma`, 12 MA-Typen, vektorisiert) als `algos/ma_utils.py` copy-adaptiert. LWC-spezifische Teile (`build_chart_payload`) entfallen; `build_color_series` wird fuer die dual_color-Semantik genutzt.
* **Farben:** `_DEFAULT_BULL_COLOR="#089981"` (Gruen), `_DEFAULT_BEAR_COLOR="#F23645"` (Rot) – TradingView-Standardfarben, identisch mit dem Projekt-Palette (`_PALETTE_COLORS`).
* **dual_color-Render (offener Punkt aus der Anforderung geloest):** PyBack rendert Overlays als EIN Trace mit EINER Farbe. Da Plotly in EINEM Linien-Trace keine wechselnden Farben darstellt, wurde der Overlay-Vertrag additiv erweitert:
  * `get_overlay_series` darf pro Feld ein **Tupel `(pd.Series, Farbliste je Punkt)`** liefern (statt reiner pd.Series).
  * Der Controller (`_build_segment_traces`) splittet die Serie **je eindeutiger Farbe** in einen Trace mit NaN-Luecken an Farbwechseln (`connectgaps=False` -> sichtbare Brueche). Zeitraum-Slice + LOD wie beim Einzel-Trace (vektorisiert).
  * Der Chart-Service honoriert `connectgaps` aus dem Trace-Dict (Default True fuer den bisherigen SMA-Pfad, False fuer Segment-Traces).
* **`algo_param`-Flag:** bull_color/bear_color sind `type=color` (StylePicker-Farbfelder), tragen aber das neue Schema-Flag `algo_param: True` → `collect_style_keys` filtert sie NICHT aus den Algo-Parameters (sie steuern die Segment-Farben der Algo-Klasse). `line_color`/`line_style`/`line_width` bleiben reine Darstellungs-Keys.

### Aenderungen

| Datei | Inhalt |
|-------|--------|
| `algos/ma_utils.py` | **Neu:** Vektorisierte MA-Utility (aus PyTrader `ma_template.py` adaptiert): 12 MA-Typen, Guards (kurze Serien/Warmup/VWMA-Fallback), `calculate_ma` (mit smoothing = zweiter EMA-Pass), `build_color_series` (dual_color). |
| `algos/alg_ma.py` | **Neu:** AlgoPlugin mit Definitions-Zone (`parameter_schema` als PineScript-Input-Zone, `result_schema = {"ma": {store: series}}`), Defaults EHMA/4/10/2.0, nur close, dual_color-Checkbox + bull/bear-Farbfelder (Gruen/Rot). `get_overlay_series` liefert `{"ma": pd.Series}` bzw. `{"ma": (Serie, Farben)}` bei dual_color. |
| `ui/style_models.py` | `collect_style_keys`: Keys mit Flag `algo_param` werden NICHT als Darstellungs-Keys gesammelt (bleiben Algo-Parameter). |
| `controllers/algo_playground_controller.py` | `_build_instance_traces` verarbeitet Tupel-Werte (Serie, Farben) → neue Methode `_build_segment_traces` (Segment-Traces je Farbe, connectgaps=False, Zeitraum-Slice + LOD). |
| `ui/playground_chart_service.py` | `build_chart_figure`: `connectgaps` wird aus dem Trace-Dict gelesen (Default True – SMA-Pfad unveraendert; Segment-Traces setzen False). |
| `test/check_alg_ma.py` | **Neu (lokal, gitignored):** Registry, Overlay-Serie, **Parity alle 12 MA-Typen gegen PyTrader-Referenz** (max diff < 1e-9), VWMA-Fallback, dual_color-Tupel, collect_style_keys, ParamFormWidget, Controller-Segment-Traces. |

### Verifikation (headless, keine UI)

* `test/check_alg_ma.py` – **alle 8 Checks OK** (inkl. Parity gegen `F:\Python\PyTrader\chart\indicators\utils\ma_template.py`).
* `test/test.py` – komplette Suite **alle Phasen 0–7 OK** (keine Regression).
* `py_compile` auf allen geaenderten/neuen Dateien OK.

### Bedienung

* Algo "MA" im Playground: MA-Typ (12 Optionen), Periode, Glaettung, Decay-Faktor; Checkbox "Auf/Ab verschiedene Farben" + Farbfelder Gruen/Rot fuer aufsteigend/absteigend. Ohne dual_color gilt der Stil-Picker (Farbe/Linienart/Staerke).
