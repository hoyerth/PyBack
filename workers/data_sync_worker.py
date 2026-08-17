# workers/data_sync_worker.py
"""
workers/data_sync_worker.py - Hintergrund-Sync der historischen Marktdaten.

Ausgelagert aus main.py im Rahmen von 18.01.02 (E7). Der Worker fuehrt den
Voll-/Update-Import der MT5-Historie in einem QThread aus und emittiert die
aktualisierten Symbol/Timeframe-Paare. Keine UI-Logik (SRP).
"""

from typing import Optional, Set, Tuple

from PySide6.QtCore import QThread, Signal

from data_sync.mt5_sync_service import sync_market_data


class DataSyncWorker(QThread):
    """Führt den Hintergrund-Sync für alle historischen Daten aus."""

    sync_completed = Signal(set)

    def __init__(self, pairs: Optional[Set[Tuple[str, str]]] = None,
                 parent=None) -> None:
        """Initialisiert den Sync-Worker.

        Phase 21.03.22 (Full Market-Data Sync Button): Optionales `pairs`-Set
        an (symbol, timeframe)-Paaren. Wird es uebergeben (nicht None),
        synchronisiert sync_market_data() exakt diese Paare statt des
        Standard-Rasters (SYMBOLS x Timeframes). Ohne Angabe ist das
        Verhalten unveraendert (Abwaertskompatibilitaet zu main.py).
        """
        super().__init__(parent)
        self.pairs = pairs

    def run(self) -> None:
        """Führt den Hintergrund-Sync für alle historischen Daten aus."""
        try:
            updated_pairs: Set[Tuple[str, str]] = sync_market_data(
                target_pairs=self.pairs)
            self.sync_completed.emit(updated_pairs)
        except Exception as e:
            print(f"❌ Fehler im DataSyncWorker: {e}")
            self.sync_completed.emit(set())
