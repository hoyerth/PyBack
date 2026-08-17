# workers/playground_worker.py
"""
workers/playground_worker.py - Hintergrund-Berechnung der Algo-Overlays.

Phase 6.1: Die Overlay-Berechnung (`get_overlay_series`) laeuft in einem
QThread, damit das UI waehrend rechenintensiver AlgOS nicht einfriert
(Konzept 2.2 / Anforderung 6.1). Der Worker erhaelt eine konkrete
Rechenaufgabe (Algo-Klasse, instance_key, Parameter, Kerzen-DataFrame)
und emittiert das Ergebnis als dict von pd.Series.

Muster wie DataSyncWorker (QThread, run()): Keine UI-Logik (SRP), kein
Zugriff auf Controller/View – nur Berechnung + Signal. Der Controller
instanziiert pro Auftrag einen Worker und verwirft veraltete Ergebnisse
ueber eine Generationsnummer.
"""

import pandas as pd
from PySide6.QtCore import QThread, Signal


class PlaygroundWorker(QThread):
    """Berechnet die Overlay-Serien EINES Algos im Hintergrund-Thread.

    Signals:
        overlay_computed(generation: int, instance_key: str, series: dict)
            – erfolgreiche Berechnung; series = {name: pd.Series} mit
              Index = Epoch-Ints (Wanduhr-encoded).
        overlay_failed(generation: int, instance_key: str, error: str)
            – Fehler bei Instanzierung oder Berechnung.
    """

    overlay_computed = Signal(int, str, dict)
    overlay_failed = Signal(int, str, str)

    def __init__(self, algo_class, instance_key: str, params: dict,
                 candles_df: pd.DataFrame, generation: int = 0,
                 parent=None) -> None:
        """Initialisiert die Overlay-Berechnung.

        Args:
            algo_class: AlgoPlugin-Klasse (aus der Registry, NICHT die
                Registry selbst – der Scan laeuft nur einmal im Controller).
            instance_key: Eindeutige Instanz-Key (fuer das Ergebnis-Signal).
            params: Parameter-Dict fuer die Instanziierung.
            candles_df: OHLCV-DataFrame mit 'time' (Epoch-Int, Wanduhr).
            generation: Generationsnummer des Auftrags (der Controller
                verwirft damit veraltete Ergebnisse).
            parent: Qt-Parent (Controller).
        """
        super().__init__(parent)
        self._algo_class = algo_class
        self._instance_key = instance_key
        self._params = dict(params or {})
        self._candles = candles_df
        self._generation = int(generation)

    def run(self) -> None:
        """Instanziiert den Algo und berechnet die Overlay-Serien.

        Laueft im Worker-Thread; das Ergebnis wird per Signal (Queued-
        Connection) an den Controller im UI-Thread zurueckgegeben.
        """
        try:
            algo = self._algo_class(**self._params)
        except Exception as exc:
            self.overlay_failed.emit(
                self._generation, self._instance_key,
                f"Instanzierung fehlgeschlagen: {exc}")
            return

        try:
            series_dict = algo.get_overlay_series(self._candles)
        except Exception as exc:
            self.overlay_failed.emit(
                self._generation, self._instance_key,
                f"get_overlay_series fehlgeschlagen: {exc}")
            return

        if isinstance(series_dict, dict):
            self.overlay_computed.emit(
                self._generation, self._instance_key, dict(series_dict))
        else:
            self.overlay_failed.emit(
                self._generation, self._instance_key,
                "get_overlay_series liefert kein dict")
