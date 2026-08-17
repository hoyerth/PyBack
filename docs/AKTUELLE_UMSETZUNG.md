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

