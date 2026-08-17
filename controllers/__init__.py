# controllers/__init__.py
"""
controllers-Paket (PyBack): Presenter/Controller-Schicht (MVC).

  * algo_playground_controller.py (Phase 6) – reagiert auf UI-Events,
    koordiniert Datenbeschaffung & Neuberechnung (Debounce).

Die Controller kennen main_win NICHT als Modul (IoC) – sie erhalten die
UI-Elemente als View-Objekt ueber den Konstruktor.
"""
