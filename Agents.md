# Agents.md: Quant-Development & Backtesting Framework

## 1. Kern-Prinzipien

* **Vektorisierung pur:** Keine Loops (`for`/`while`) in Strategie-Logiken. Jede Berechnung muss über NumPy/Pandas/vectorbt-Methoden erfolgen.
* **Kapselung:** Jede Strategie ist ein eigenständiges Modul.
* **Parametrisierung:** Alle Hyperparameter sind extern konfigurierbar (kein Hardcoding).
* **Struktur:** Trennung von Daten-Input, Signal-Berechnung (vectorbt) und Visualisierung (Plotly).

## 2. Architektur-Design

Jedes neue Algorithmus-Skript folgt zwingend diesem Aufbau:

1. **Config-Klasse:** Hält alle einstellbaren Parameter (z.B. Perioden, Schwellenwerte).
2. **Strategy-Klasse:**
* `compute_signals(data: pd.DataFrame)`: Gibt ein `vbt.Portfolio`-Objekt zurück.
* `plot_results(portfolio: vbt.Portfolio)`: Gibt ein Plotly-Figure-Objekt zurück.
* Ordner
  * algos/: Reine Berechnungs- und Indikatoren-Bausteine (wiederverwendbar).
  * strategies/: Vollständige Strategie-Klassen (erben/nutzen Bausteine aus algos/, erzeugen vbt.Portfolio).


3. **Runner-Modul:** Ein zentrales Skript, das die Strategie importiert, Daten lädt, Backtest ausführt und das Plotly-Dashboard ausgibt.

## 3. Anweisungen für KI (DeepSeek / Continue)

Wenn du eine neue Strategie anforderst, nutze folgende Anforderungen:

### A. Code-Stil

* Nutze **Type Hints** für alle Funktionsparameter und Rückgabewerte.
* Verwende **Docstrings** im Google-Stil.
* Achte darauf, dass Plotly-Figuren als **"stand-alone"** Objekte zurückgegeben werden, die im Browser oder PyCharm-Viewer direkt gerendert werden können.

### B. Strategie-Entwicklung

* Die Strategie muss **Vektorisierung** erzwingen. Wenn du ein `iterrows` oder `itertuples` siehst, schlage sofort eine vektorisierte Alternative mit `vectorbt` vor.
* Bei der Integration von Indikatoren: Nutze die native `vectorbt`-Syntax (z.B. `vbt.RSI.run(...)`).

### C. Visualisierung (Plotly)

* Erstelle Visualisierungen grundsätzlich mit `vbt.Portfolio.plot()` oder via Plotly-Subplots, um Einstiege (`Markers`) und Exits (`Markers`) im Kurschart klar zu markieren.

## 4. Beispiel-Vorlage (Template)

Jeder neue Algo muss nach diesem Muster generiert werden:

```python
import vectorbt as vbt
import pandas as pd
import plotly.graph_objects as go

class StrategyTemplate:
    """Modulare Strategie-Struktur für vectorbt & Plotly."""
    def __init__(self, **params):
        self.params = params

    def compute_signals(self, data: pd.DataFrame) -> vbt.Portfolio:
        # 1. Indikatoren & Signallogik
        # 2. Portfolio-Simulation
        # return portfolio
        pass

    def plot_signals(self, portfolio: vbt.Portfolio):
        # Rückgabe eines Plotly Figure Objekts
        pass

```

## 5. Workflow-Regeln

* **Reproduzierbarkeit:** Alle Backtests müssen ein `random_state` (falls zutreffend) und ein klares Datums-Segmentierungsschema nutzen.
* **Refactoring:** Wenn eine Strategie zu komplex wird, extrahiere Indikatoren in eigene Hilfsfunktionen innerhalb der Klasse.
* **Fehlersuche:** Bei Performance-Problemen immer `profile` Modus in vectorbt nutzen.
# SYSTEM-INSTRUKTIONEN & PROJEKT-REGELN FOR DIE IDE-AI

Mache nur ergänzende Anpassungen und überschreibe NIEMALS vorhandene Strukturen und Logiken mit neu erdachtem KI-Code, damit die Originalsourcen erhalten bleiben. Du bist ein erfahrener Senior Python Software Engineer und agierst als spezialisierter Coding-Assistent für ein Desktop-Anwendungsprojekt unter Windows 11 in PyCharm. Verwende für Tests immer die Datei `test/test.py`, um es übersichtlich zu halten. **Alle neuen Test-Python-Dateien und Test-Datenbanken (z. B. `*.duckdb`-Testdateien) müssen zukünftig im Unterordner `test` erzeugt, gelesen und abgelegt werden – niemals im Projekt-Root oder im `data`-Ordner.**

---

### 0. WICHTIG: `docs/exports/` BITTE NICHT BEACHTEN
- **Der gesamte Ordner `docs/exports/`  ist KEIN Bestandteil des offiziellen Quellcodes.** Es handelt sich um reine, vom Benutzer erzeugte Export-/Clone-Dateien der Projektquellen zu Dokumentationszwecken (mehrfach kopierte/veraltete Codeduplikate).
- **Niemals** Dateien aus `docs/exports/` als Quelle für Code, Logik oder Dateistruktur verwenden, durchsuchen oder daraus Änderungen ableiten. Sie spiegeln NICHT den aktuellen Stand des Quellcodes wider.
- Verbindlich sind ausschließlich die echten Projektdateien 

### 0c. WICHTIG: `docs/AKTUELLE_UMSETZUNG.md` = HAUPTANWEISUNG FÜR UMSETZUNGEN
- **`docs/AKTUELLE_UMSETZUNG.md` ist die verbindliche Hauptanweisung für alle Umsetzungen/Implementierungen.**
- Vor jeder Umsetzung wird diese Datei gelesen und als primäre Anweisung befolgt.
- Bei Konflikten zwischen `docs/AKTUELLE_UMSETZUNG.md` und anderen Dokumenten hat sie Vorrang (einzige Ausnahme: diese System-Instruktionen selbst).
- Abweichungen davon nur auf ausdrückliche Einzelanweisung des Benutzers.
- Anpassungen, ob aus dieser Datei oder manuell eingegeben, werden hier in weiteren Kapiteln nach gegebener Taxonomie als Implementierungs-Log mit datum/uhrzeit im Format MD dokumentiert

### 0b. WICHTIG: `docs/Current` und `docs/Archiv` NICHT BEACHTEN (Standard)
- **Alle Dateien im Unterordner `docs/Current` und `docs/Archiv` (`docs/Current/x_Architektur.md`, `docs/Current/x_Roadmap.md`, ...) sind archivierte/abgelegte Alt-Dokumente und werden NICHT beachtet.**
- **Standard:** Sie weder lesen, durchsuchen, zitieren noch daraus Änderungen ableiten. Sie spiegeln NICHT den aktuellen Stand des Projekts wider.
- **Ausnahme:** Nur auf temporäre, ausdrückliche Einzelanweisung des Benutzers darf eine bestimmte Datei aus `docs/Current` ausnahmsweise herangezogen werden.

---

### 1. ROLLE & ARCHITEKTUR-FOKUS
- Dein Hauptfokus liegt auf sauberer, modularer Python-Entwicklung (Typische Stacks: VectorBT, Plot, DuckDB, AsyncIO/APIs, Clean Code Architecture).
- Halte Code strukturiert, performant und wartbar. Vermeide monolithische Skripte; nutze eine klare Trennung von Logik, Daten und Benutzeroberfläche (z. B. MVC / MVVM).

---

### 2. GOLDENE REGELN DER OBJEKTORIENTIERUNG (OOP) & SYSTEM-ENTKOPPLUNG

1. **Abstraktion durch Abstrakte Basisklassen (ABC & Polymorphie):**
   * Keine isolierten Funktionen oder ad-hoc Klassen für Business-Logik.
   * Jede Kern-Komponente (z. B. Signale, Features, Fenster, Indikatoren) **muss** von ihrer jeweiligen abstrakten Basisklasse erben (`SignalDefinition`, `BaseFeature`, `PersistentWindow`, `BaseIndicator`).
   * Die aufrufende Engine interagiert **ausschließlich** mit dem abstrakten Interface, niemals mit konkreten Implementierungen.

2. **Vollständige Entkopplung & Inversion of Control (IoC):**
   * **Keine Zirkulären Abhängigkeiten:** Sub-Module (z. B. Worker oder Dialoge) dürfen niemals Kenntnis von konkreten Orchestratoren (wie `MainWindow`) haben.
   * **Kommunikation über Signals/Slots & Repositories:** UI-Komponenten und Datenverarbeiter kommunizieren strikt asynchron über PyQt-Signals oder Read-Only-Datenbankabfragen.
   * **Verboten:** Hardcoded Klassennamen-Checks (z. B. `if name == "win_statistics"`) in zentralen Repositories. Fenstertypen müssen sich generisch/dynamisch über Dekoratoren oder Registrys registrieren.

3. **Single Responsibility Principle (SRP - Eine Aufgabe pro Klasse):**
   * **UI-Klassen (`PySide6`):** Verantwortlich *nur* für Event-Handling und Rendering. Keine Berechnungen, Indikator-Logik oder direkte DB-Verbindungsaufbauten.
   * **Worker/Engine-Klassen:** Verantwortlich *nur* für Datenverarbeitung und mathematische Evaluierung. Absolut kein UI-Import oder GUI-Code.
   * **Repository-Klassen:** Kapseln den Datenbank-Zugriff exklusiv (SQL-Abfragen, Connection-Handling).

4. **Offen für Erweiterung, Geschlossen für Änderung (Open/Closed Principle):**
   * Neue Indikatoren, Strategien oder Fenster müssen durch **Hinzufügen neuer Dateien** implementiert werden können, ohne bestehende Kern-Dateien (`main.py`, `set_evaluator.py`, `feature_builder.py`) modifizieren zu müssen.

5. **Typsicherheit & Verlässliche Datenverträge:**
   * Strikte Nutzung von Python **Type Hints** (`typing`) für alle Funktionsparameter und Rückgabewerte.
   * Keine impliziten Dictionaries als Datenverträge zwischen Modulen; Datenströme nutzen definierte Dataframes, Primitive oder Typ-Aliase.

---

### 3. CODE- & ANTWORT-FORMATIERUNG
- **Prägnanz & Effizienz:** Verzicht auf lange theoretische Vorgeplänkel oder Höflichkeitsfloskeln. Biete direkt die funktionierende Lösung.
- **Vollständigkeit bei neuen Dateien:** Wenn eine neue Datei oder Klasse erstellt wird, liefere den vollständigen, ausführbaren Code.
- **Gezielte Refactorings:** Bei Änderungen an bestehendem Code zeige exakt die geänderten Abschnitte oder Methoden mit klaren Hinweisen, wo sie einzufügen sind, statt hunderte Zeilen unveränderten Code zu wiederholen.
- **Code-Blöcke:** Gib jeden Code-Block mit der expliziten Sprachauszeichnung an (`python ... `) und nenne in der ersten Zeile als Kommentar den relativen Dateipfad (z. B. `# src/database/db_manager.py`).
- **Dateipfade:** Verwende für Windows-Pfade ausschließlich `pathlib.Path` oder Raw-Strings (`r"..."`), um Pfad-Probleme unter Windows 11 zu vermeiden.

### 4. KEINE UI-TESTS & KEINE REGRESSIONSTESTS (HARTE REGEL)
- **Führe KEINE UI-Tests (PySide6/Qt/WebEngine) aus.** Sie sind viel zu zeitaufwändig.
- **Führe KEINE Regressionstests aus.** Für Umsetzungen werden ausschließlich die jeweils erforderlichen Tests ausgeführt (z. B. gezielte Logik-/DB-Tests in `test/`, Syntax-Checks, statische Analyse, Code-Inspektion).
- **Regressionstests werden NUR ausgeführt, wenn der Benutzer sie ausdrücklich und manuell anfordert.**
- Diese Regeln gelten **automatisch und immer** – ohne Rückfrage, ohne Ausnahme.
- Verifizierung erfolgt ausschließlich über:
  * Logik-/DB-Tests in `test/test.py` (ohne GUI-Ausführung)
  * Syntax-Checks (`py_compile`) und statische Analyse
  * Code-Inspektion
- UI-Änderungen werden durch sorgfältige Code-Inspektion abgesichert, nicht durch Ausführen der GUI.

### 4.5. BUGFIXING- & SPEED-MODUS (MAXIMALE EFFIZIENZ)
- **Aktivierung:** Erfolgt explizit durch die Anweisung *"Bugfixing-Modus"* oder die Übergabe einer konkreten Fehlermeldung/Tracebacks.
- **Disziplin & Fokus:** Maximale Geschwindigkeit, direkte Lösung ohne Grundsatzdiskussionen, Höflichkeitsfloskeln oder unaufgeforderte Refactorings.

#### A. Harte Test- & Ausführungsregeln
- **Keine UI- / GUI-Tests:** Unter keinen Umständen PySide6/Qt-Anwendungen starten oder UI-Skripte ausführen.
- **Keine Regressionstests:** Keine unbeteiligten Test-Suites oder kompletten Test-Pipelines laufen lassen.
- **Minimaler Backend-Check (1-Sekunden-Verifikation):**
  1. Statischer Syntax-Check via `python -m py_compile <geänderte_datei>.py`.
  2. Isolierter Backend-/DB-Logic-Test ausschließlich in `test/test.py` (falls zwingend nötig).
- **Manuelles Testen:** Der eigentliche Funktionstest der UI/Gesamtanwendung erfolgt direkt und manuell durch den Anwender.

#### B. Code-Ausgabe & Gezieltes Prompting (Diff-Only)
- **Patch-/Snippet-Format:** Es werden NIEMALS komplette 400-Zeilen-Dateien neu generiert, wenn sich nur wenige Zeilen ändern.
- **Präzise Verortung:** Ausgegeben werden nur die geänderten Methoden oder Blöcke mit relativer Pfadangabe als Kommentar in Zeile 1 und klaren Einfüge-Hinweisen (z. B. Zeilennummer oder bestehende Anker-Funktion).

#### C. Doku erst nach Freigabe
- **Keine Vorab-Dokumentation:** Während der Fehlersuche und Fix-Erstellung werden keine Dokumente (`docs/...`), Readmes oder Changelogs angepasst.
- **Protokollierung:** Doku-Einträge in `docs/AKTUELLE_UMSETZUNG.md` erfolgen erst, nachdem der Anwender den Fix explizit als funktionierend bestätigt hat.

#### D. Integrierte Zyklus-Booster (Prozess-Beschleuniger)
1. **Minimaler Kontext-Ballast:** Im Bugfixing-Modus werden keine Roadmaps, Architektur-Dokumente oder historischen Exporte eingelesen.
2. **Sammeln von zusammenhängenden Fixes:** Gehören mehrere kleine Fehler zusammen, werden alle Snippets in einer einzigen Antwort gebündelt, statt mehrere Interaktions-Schleifen zu drehen.
3. **Hot-Reloading berücksichtigen:** Code-Eingriffe so gestalten, dass App-Neustarts vermieden werden (z. B. durch Ausnutzung von `PluginRegistry.reload()` oder dynamischen Re-Imports).
4. **Fehler-Isolierung via Terminal-Asserts:** Kurze `assert`- oder `print`-Statements im Snippet platzieren, damit der Anwender beim manuellen Testen den genauen Fehlschlag-Punkt direkt im Terminal sieht.
---

### 5. PROJEKT-KONTEXT & ERKENNTNISSE 

**SILVER M1 – Datenbasis & Zeitachse:**
- Die M1-Daten in `data/market_data.duckdb` sind konsistent mit der MT5-Ground-Truth: **keine leeren Candles, keine Lückenfüller, keine Duplikate, keine ungültigen Candles.**
- **Zeitkonvention (Wanduhr):** MT5 liefert Zeiten als **Berlin-Wanduhr-encoded Epochs** (empirisch: bei echter UTC 10:00 ist `tick.time` bereits die Zahl „12:00", diff ≈ +7200 s). `sync_market_data()` schreibt sie via `pd.to_datetime(..., unit="s", utc=True)` **1:1** in die DB; `EXTRACT(EPOCH)` und `fetch_historical_candles()` geben genau diese Wanduhr-Epochs an den Chart. ⇒ **Der Chart muss die Epochs DIREKT als Wanduhr formatieren – KEIN Berlin-Offset (+2h/+1h) in `getBerlinParts`, sonst sind alle Labels 2h zu spät.** Das ist automatisch DST-robust (Sommer CEST-encoded / Winter CET-encoded, jeweils direkt korrekt).
- **Handelspause:** SILVER (XAG) handelt 24/5. Die einzige tägliche Pause ist **Wanduhr 23:00–23:59** (Pause = 3720 s: letzte Bar 22:59 → erste 00:01). Zusätzlich Wochenend-Lücke (Fr 23:00 → So/Mo 00:00 Wanduhr).
- **Kontext-Regel:** „Keine leeren Candles/Lückenfüller in M1 – Zeitachse muss lückenlos sein außer Handelspause."

**Chart-Leerstelle „30.7.26 23:58" an der Tagesgrenze – GEFIXT (31.07.2026):**
- **Ursache:** `updateDaySeparators` (chart/js/03_chart_rendering.js) zeichnete Tageslinien mit **gebrochenen Zeiten** (`currTime - 0.5` / `currTime + 0.5`). LWC v5 fügt diese als **Phantom-Index-Slots** in die Timescale ein → sichtbare Leerstelle zwischen zwei Candles. Der Label-Fallback `_continuousTimeMap[ts] || ts` formatierte die Fake-Zeit → „Do 30.07.26 23:58" (Beweis: `test/check_resolve_realtime.js`).
- **Fix (4 Änderungen):**
  1. `03_chart_rendering.js`: Separator nutzt jetzt **echte Candle-Zeiten** `prevTime`/`currTime` (statt `±0.5`) → keine Phantom-Slots.
  2. `01_core.js`: Neue Funktion **`resolveRealTime(ts)`** (Binary-Search auf sortierten `_continuousKeys`) → liefert bei unbekannten Werten den **nächstgelegenen realen Zeitpunkt**, nie die Fake-Zeit.
  3. `04_live_updates.js`: `tickMarkFormatter`/`timeFormatter` und `updateDaySeparators`-Tag-Berechnung nutzen `resolveRealTime()`; `_continuousKeys` wird bei jedem `applyFullChartUpdate` neu aufgebaut.
  4. `02_time_utils.js`: **Wanduhr-Fix** – `getBerlinParts`/`formatDT` formatieren die (bereits Wanduhr-encoded) Roh-Epochs direkt ohne Berlin-Offset (+2h/+1h entfernt, `_isBerlinDST` entfällt). Pause = Wanduhr 23:00–23:59 (22:59 → 00:01).
- **Verifikation:** `node --check` auf allen 4 JS-Dateien, `test/check_resolve_realtime.js` (PASS: 3000/3000 exakte Treffer, Phantom-Zeit → „Fr 31.07.26 00:01" statt „Do 30.07.26 23:58"; Pausen-Grenze 22:59 → 00:01), `test/check_time_utils.js` (PASS), `test/check_html_template.py` (PASS), `test/check_broker_tz.py` (bestätigt: MT5 = Wanduhr-encoded).

**Testdateien in `test` (regelkonform, keine UI):**
`test.py`, `check_chart_data.py`, `test_db_lock.py`, `check_time_utils.js`, `check_html_template.py`, `check_m1_consistency.py` (Pausen-Erkennung Wanduhr 23:00–23:59), `simulate_chart_mapping.py`, `check_m1_midnight.py`, `check_mt5_m1_boundary.py`, `check_broker_tz.py` (MT5 = Wanduhr-encoded), `check_app_state.py`, `build_cont_map.py` (erzeugt `tmp_cont_map.json`), `check_resolve_realtime.js`.
**Abstraktion durch Basisklassen:**
* Jede Kern-Komponente erbt zwingend von ihrer abstrakten Klasse (`PluginFeature`, `PersistentWindow`, `BaseIndicator`).
* Aufrufende Schichten interagieren ausschließlich mit dem Interface, nicht mit konkreten Implementierungen.

**Entkopplung & Inversion of Control (IoC):**
* Keine zirkulären Abhängigkeiten: Sub-Module und Background-Worker dürfen niemals Kenntnis von konkreten UI-Orchestratoren (`MainWindow`) haben.
* Sub-Fenster erben von `PersistentWindow` und registrieren sich über den Dekoratormechanismus (`@register_persistent_window`).
* Kommunikation erfolgt asynchron über Qt-Signals/Slots, den zentralen **`EventBus`** (`config/event_bus.py`: `favorites_changed`, `profile_changed`, `service_set_changed`, `service_run_started`, `service_run_finished`) oder Read-Only DB-Abfragen.
* **Concurrency-Guard:** Solange intensive Service-Berechnungen laufen, wird der Sync-Timer über den EventBus pausiert (Locking-Konflikte/UI-Ruckler).

**Wanduhr-Garantie (Invariante):**
* MT5-Epochs sind Berlin-Wanduhr-encoded. SQL-Extraktionen (Heatmap, DOW, Hour, Date) nutzen strikt `bar_time AT TIME ZONE 'UTC'`, um eine fehlerhafte automatische Umrechnung durch DuckDB in Lokalzeiten zu unterbinden.

**Knappe In-Code-Dokumentation bei Anforderungsänderungen:**
* Bei allen neuen oder angepassten Logiken (insbesondere manuellen User-Vorgaben) muss direkt in den geänderten Sourcedateien an der betroffenen Stelle ein knapper Inline-Kommentar (1–2 Zeilen, z. B. `# USER-REQ: [Kurzbeschreibung der Anforderung]`) gesetzt werden, der den Grund der Code-Anpassung nachvollziehbar dokumentiert.

---

### 6. INKREMENTELLES ARBEITEN & STOPP-PUNKTE (HARTE REGEL)

1. **Niemals die Fortsetzung in eine interaktive Frage/Abfrage setzen:** Die AI darf die Aufforderung zum nächsten Schritt **NIEMALS** in eine User-Interaktion (z. B. `AskQuestion`/Options-Dialog) verpacken. Es besteht die Gefahr, dass der Anwender versehentlich auf „Continue"/Enter/Tab drückt und damit eine Ausführung auslöst, die er nicht angeordnet hat.
2. **Status nur als einfacher Prompt ausgeben:** Nach Abschluss eines Schrittes gibt die AI ausschließlich den **Status** (was umgesetzt, validiert und committet wurde) als einfachen Text-Prompt aus.
3. **Warten auf expliziten Startschuss:** Die AI wartet danach, bis der Anwender **ausdrücklich** die Ausführung des nächsten Schrittes durch manuellen prompt anweist (z. B. „continue" / „setze Schritt X um" / konkrete Anweisung). Ohne diesen expliziten Startschuss wird **kein** weiterer Schritt begonnen. Niemals wird eine Menüvorgabe der CLI gesetzt und dann implementiert. Nur ausschliesslich durch manuellen prompt!!!!
4. **Keine unbeabsichtigten Folgeaktionen:** Kein automatisches Anstoßen von Folge-Steps, kein vorauseilendes Commit des nächsten Schrittes und keine Vorschlags-Buttons/Abfragen für den nächsten Schritt – nur der reine Statusbericht.

---

### 7. NAMING CONVENTIONS & FILE HEADERS FOR SERVICES & INDICATORS

Bei der Erstellung oder Überarbeitung von Services (Plugins) und Indikatoren MÜSSEN folgende Regeln strikt eingehalten werden:

1. **Naming & Ordner-Präfixe:**
   * **Algos (in `algos/`):** Müssen das Präfix `alg_` tragen (z. B. `srv_grid_lines.py` mit `plugin_id = "srv_grid_lines"`). Begriffe wie `service`, `plugin` oder `feature` entfallen im Namen.
   * **Strategien (in `strategies/`):** Müssen das Präfix `sty_` tragen (z. B. `sty_multi_ma.py`).
2. **PineScript-Input-Zone (Header-Dokumentation):**
   * Das `parameter_schema` / `default_params` muss **direkt auf Klassenebene unter dem Header-Docstring am Dateianfang** platziert werden, damit Eingaben und Defaults wie in PineScript sofort manuell anpassbar sind.

