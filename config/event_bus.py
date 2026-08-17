# config/event_bus.py
"""
config/event_bus.py - Zentraler Signal-Hub (EventBus-Singleton) fuer die
schwellenfreie Entkopplung der Fenster (Phase 15, Invariante 5).

Fenster kommunizieren NIE direkt miteinander (keine zirkulaeren
Abhaengigkeiten, kein Hardcoding von Fensterklassen). Stattdessen emittieren
sie Events auf dem zentralen EventBus und andere Fenster/Module abonnieren
diese Signale.

Phase 15.01: `favorites_changed` wird vom SymbolsWindow nach jedem
Favoriten-Toggle emittiert; ServiceWindow (und spaeter AnalyticsWindow)
befuellen daraufhin ihre Symbol-Dropdowns neu.

Verwendungsbeispiel:
    from config.event_bus import event_bus
    event_bus.favorites_changed.connect(self._refresh_symbol_combo)
    event_bus.favorites_changed.emit()
"""

from typing import ClassVar, Optional

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    """Zentraler Signal-Hub (Singleton) fuer fensteruebergreifende Events.

    Signaldefinitionen (minimal gehalten, Phase-15-Entscheidungs-Protokoll):
    - favorites_changed : Favoriten-Liste wurde geaendert (SymbolsWindow).
    - profile_changed   : Analytics-Profil wurde geaendert (15.03, Payload =
                          Profil-Name/-ID).
    - service_set_changed: Service-Set wurde gespeichert/geloescht (15.02).
    - service_run_started: Intensiver Service-Run/Scan wurde gestartet
                           (ServiceWindow) – MainWindow pausiert den 45s-
                           sync_timer (Concurrency-Guard, 05.08.2026).
    - service_run_finished: Alle gestarteten Service-Runs/Scans sind beendet
                            (Referenzzähler auf 0) – MainWindow startet den
                            sync_timer wieder.
    """

    favorites_changed = Signal()
    profile_changed = Signal(str)
    service_set_changed = Signal()
    # 21.03.11 (Bug 6): Tabellen-Sortierung der Filterleiste. Das
    # ChartWindow emittiert nach jeder Sortier-Aenderung ('date'|'signal'|
    # 'tf'); das AnalyticsWindow wendet sie auf die TablePage an. Entkoppelt
    # via EventBus – das ChartWindow kennt das AnalyticsWindow NICHT (IoC).
    mtf_fc_sort_changed = Signal(str)
    # Phase 16 (05.08.2026): Concurrency-Guard gegen Konflikte zwischen
    # Service-Berechnungen (SetRunWorker/ServiceRunWorker/HistoricalScanner)
    # und dem 45s-Hintergrund-Sync (sync_timer in main.py). Entkoppelt via
    # EventBus – das ServiceWindow kennt den MainWindow NICHT (IoC).
    service_run_started = Signal()
    service_run_finished = Signal()

    # 22.01 (14.08.2026): Peak-Grabber (Frage 5). Der AnalyticsWindow-Button
    # emittiert grabber_toggle(dict) mit {"active", "symbol", "timeframe"} –
    # chart_win subscribed und ruft generisch set_button_active(active) auf
    # allen Indikatoren mit diesem Hook auf (IoC). Live-Trigger/-Updates
    # emittieren grabber_event(GrabberResultRecord) – das AnalyticsWindow
    # persistiert über GrabberRepository und öffnet die OrderPreviewDialog
    # (UI ohne SQL, MVVM).
    grabber_toggle = Signal(dict)
    grabber_event = Signal(object)

    _instance: ClassVar[Optional["EventBus"]] = None

    def __init__(self) -> None:
        # QObject ohne Parent: Der Singleton lebt app-weit und wird nie
        # geloescht (gehoert keiner Fenster-Hierarchie an).
        super().__init__(None)
        # Phase 21.02 (12.08.2026): Referenzzaehler fuer laufende Service-
        # Berechnungen/Scans. Wird von MainWindow in service_run_started/
        # finished mitgepflegt; PropertiesWindow nutzt ihn als
        # Concurrency-Guard fuer die DB-Kompaktierung (getattr-Fallback 0,
        # falls ein Modul ohne Initialisierung liest).
        self.sync_pause_count: int = 0

    @classmethod
    def instance(cls) -> "EventBus":
        """Liefert die app-weite Singleton-Instanz (lazy)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Bequeme Modul-Level-Instanz: `from config.event_bus import event_bus`
event_bus = EventBus.instance()
