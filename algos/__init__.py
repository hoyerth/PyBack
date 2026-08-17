# algos/__init__.py
"""
algos-Paket (PyBack): Reine Berechnungs- und Indikatoren-Bausteine.

  * alg_*.py                 – Algo-Module (AlgoPlugin-Klasse)
  * algo_registry.py         – Dynamische Algo-Registry (Playground)

Konvention (Agents.md / Konzept 2.3):
  * Jeder Algo hat das Präfix `alg_`.
  * Das `parameter_schema` liegt direkt am Dateianfang unter dem Header-Docstring.
  * Der Playground nutzt den Overlay-Hook (`get_overlay_series`); das spätere
    Backtest-Fenster nutzt optional `compute_signals()` -> vbt.Portfolio.
"""
