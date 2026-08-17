# controllers/__init__.py
"""
controllers-Paket (PyBack): Presenter/Controller-Schicht (MVC).

  * algo_playground_controller.py – AlgoPlaygroundController (Presenter des
    Algo-Playgrounds). Phase 2: Add/Remove-Algo-Liste; Phase 6: Daten-Slicing,
    Berechnung (Debounce) und Canvas-Update.

Die Controller kennen main_win NICHT als Modul (IoC) – sie erhalten die
UI-Elemente als View-Objekt ueber den Konstruktor.
"""
