# ui/window_manager.py
"""
ui/window_manager.py - Zentraler Fenster-Lifecycle-Manager (PyBack).

Uebernommen aus pytrader (ui/window_manager.py, 18.01.02/E5) und auf die
in PyBack vorhandenen Fenster reduziert:

  * Wiederherstellung aller gespeicherten Fenster (restore_all_windows)
  * Oeffnen/Fokussieren der PropertiesWindow- und SymbolsWindow-Fenster

Der WindowManager kennt MainWindow NICHT (kein Import von main.py). Die
Kopplung erfolgt ueber Konstruktor-Parameter (parent + state_manager +
Listen-Referenzen). `restore_main_window_geometry` verbleibt im MainWindow.

Hinweis (PyBack): Unbekannte Instanz-IDs aus einer uebernommenen
app_data.duckdb (z. B. Alt-Fenster aus pytrader wie win_service,
win_statistics) werden DEFENSIV UEBERSPRUNGEN, statt versucht zu oeffnen –
PyBack kennt diese Fensterklassen nicht.
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt

from persistent_win import PersistentWindow
from serviceui.symbols_win import SymbolsWindow
from state_manager import StateManager
from ui.properties_win import PropertiesWindow


class WindowManager:
    """Verwaltet die Sub-Fenster des MainWindow (Fenster-Lifecycle)."""

    def __init__(self, parent, state_manager: StateManager,
                 persistent_sub_windows: List[PersistentWindow]) -> None:
        """Erstellt den Fenster-Manager.

        Args:
            parent: Qt-Parent fuer neu erzeugte Fenster (duck-typed, z. B. das
                    MainWindow – wird NIE importiert, E5/IoC).
            state_manager: StateManager – Persistenz (Instanzen, Geometrien).
            persistent_sub_windows: Referenz auf die PersistentWindow-Liste
                           des Aufrufers (gemeinsames List-Objekt).
        """
        self._parent = parent
        self.state_manager = state_manager
        self.persistent_sub_windows = persistent_sub_windows

    # ------------------------------------------------------------------
    # Restore aller gespeicherten Fenster
    # ------------------------------------------------------------------
    def restore_all_windows(self) -> None:
        """Stellt ALLE gespeicherten Fenster vollautomatisch und generisch wieder her.

        Nutzt die Klassen-Registry aus persistent_win.py. Instanzen, deren
        Klasse NICHT registriert ist (z. B. Alt-Fenster aus einer ueber-
        nommenen pytrader-app_data.duckdb), werden uebersprungen.
        """
        all_instances: List[Dict[str, Any]] = self.state_manager.load_all_instances()
        if not all_instances:
            return

        for inst in all_instances:
            inst_id = str(inst.get("instance_id", ""))
            if not inst_id or inst_id == "win_main":
                continue

            # Nur registrierte PersistentWindow-Subklassen wiederherstellen.
            window_cls = PersistentWindow.get_registered_class(inst_id)
            if window_cls is None:
                print(f"  → Ueberspringe {inst_id}: keine registrierte Klasse in PyBack")
                continue
            if not PersistentWindow.should_auto_restore(inst_id):
                print(f"  → Ueberspringe {inst_id} ({window_cls.__name__}): auto_restore=False")
                continue

            print(f"  → Oeffne registriertes Fenster: {inst_id} ({window_cls.__name__})")
            # parent=self nur fuer state_manager-Zugriff, nicht als Qt-Parent!
            win = window_cls(parent=self._parent)
            self.persistent_sub_windows.append(win)
            # Ohne Fokus anzeigen (damit MainWindow den Fokus behaelt)
            win.setAttribute(Qt.WA_ShowWithoutActivating, True)
            win.show()
            win.setAttribute(Qt.WA_ShowWithoutActivating, False)
            # Maximiert wiederherstellen (nach show(), ohne Fokus-Klau)
            if getattr(win, '_restored_is_maximized', False):
                win.showMaximized()

    # ------------------------------------------------------------------
    # Fenster oeffnen (Singleton-Verhalten)
    # ------------------------------------------------------------------
    def open_properties_window(self) -> None:
        """Oeffnet das PropertiesWindow (Systemoptionen) im Singleton-Modus."""
        existing = PropertiesWindow.get_existing_instance()
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        win = PropertiesWindow(self._parent)
        self.persistent_sub_windows.append(win)
        win.show()

    def open_symbols_window(self) -> None:
        """Oeffnet das SymbolsWindow (Symbol- & Favoriten-Verwaltung)."""
        existing = SymbolsWindow.get_existing_instance()
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        win = SymbolsWindow(self._parent)
        self.persistent_sub_windows.append(win)
        win.show()
