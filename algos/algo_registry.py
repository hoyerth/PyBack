# algos/algo_registry.py
"""
algos/algo_registry.py - Dynamische Algo-Registry (Playground, Phase 0).

Scannt das Paket `algos/` nach allen Modulen mit dem Praefix `alg_`, laedt
daraus die AlgoPlugin-Klasse und liest deren Metadaten:
  * algo_id           (eindeutige ID, Default: Modulname)
  * name              (Anzeigename)
  * description       (Beschreibung)
  * parameter_schema  (PineScript-Input-Zone fuer ParamFormWidget)
  * Overlay-Hook      (get_overlay_series: liefert Serien/Traces fuer den Canvas)
  * compute_signals   (optional, fuer das spaetere Backtest-Fenster)

Defensiv (Konzept 2.3, Regel 8): Ein fehlerhaftes Modul wird einzeln
uebersprungen (try/except pro Modul) und als Fehler protokolliert – der
Rest funktioniert weiter. Der 0-Zustand (keine alg_-Module) liefert eine
leere Registry, keine Exception.

Kein UI-Import, kein DuckDB-Zugriff (SRP) – reinster Modul-Loader.
"""

import importlib
import pkgutil
from typing import Any, Dict, Optional

import algos

# Name der erwarteten Plugin-Klasse in jedem alg_-Modul.
ALGO_PLUGIN_CLASS = "AlgoPlugin"


class AlgoRegistry:
    """Scannt und registriert alle 'alg_*'-Module dynamisch."""

    @classmethod
    def discover_algos(cls) -> Dict[str, Dict[str, Any]]:
        """Scannt `algos/` und liefert die Registry als dict.

        Returns:
            {algo_id: {"class": AlgoPlugin-Klasse, "module": str, "name": str,
                        "description": str, "schema": parameter_schema, ...}}
            Leer ({}), wenn keine alg_-Module existieren.
        """
        registry: Dict[str, Dict[str, Any]] = {}
        if not hasattr(algos, "__path__"):
            return registry

        for mod_info in pkgutil.iter_modules(algos.__path__):
            module_name = mod_info.name
            if not module_name.startswith("alg_"):
                continue
            try:
                mod = importlib.import_module(f"algos.{module_name}")
                algo_cls = getattr(mod, ALGO_PLUGIN_CLASS, None)
                if algo_cls is None:
                    print(f"WARN [AlgoRegistry] {module_name}: "
                          f"keine '{ALGO_PLUGIN_CLASS}'-Klasse gefunden – uebersprungen")
                    continue

                # Metadaten defensiv auslesen (Defaults bei fehlenden Attributen).
                algo_id = str(getattr(algo_cls, "algo_id", None) or module_name)
                name = str(getattr(algo_cls, "name", None) or module_name)
                description = str(getattr(algo_cls, "description", None) or "")
                schema = getattr(algo_cls, "parameter_schema", None) or {}

                registry[algo_id] = {
                    "class": algo_cls,
                    "module": module_name,
                    "name": name,
                    "description": description,
                    "schema": schema,
                    # Overlay-Hook vorhanden? (Playground-Vertrag)
                    "has_overlay": callable(getattr(algo_cls, "get_overlay_series", None)),
                    # Optionaler Backtest-Vertrag (spaeteres Backtest-Fenster)
                    "has_backtest": callable(getattr(algo_cls, "compute_signals", None)),
                }
            except Exception as exc:
                # Defensiv: kaputtes Modul ueberspringen, Rest funktioniert.
                print(f"WARN [AlgoRegistry] {module_name} fehlerhaft – "
                      f"uebersprungen: {exc}")

        return registry

    @classmethod
    def get_algo(cls, algo_id: str) -> Optional[Dict[str, Any]]:
        """Liefert einen einzelnen Registry-Eintrag (oder None)."""
        return cls.discover_algos().get(algo_id)

    @classmethod
    def create_instance(cls, algo_id: str, params: Optional[Dict[str, Any]] = None):
        """Instanziiert einen Algo mit den uebergebenen Parametern.

        Args:
            algo_id: Registry-ID des AlgOS.
            params:  Parameter-Dict (Default: leeres Dict -> Klassen-Defaults).

        Returns:
            AlgoPlugin-Instanz oder None, wenn algo_id unbekannt ist.
        """
        entry = cls.get_algo(algo_id)
        if entry is None:
            return None
        try:
            return entry["class"](**(params or {}))
        except Exception as exc:
            print(f"WARN [AlgoRegistry] Instanzierung {algo_id} fehlgeschlagen: {exc}")
            return None
