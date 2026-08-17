# config/base_state_model.py
"""
AbstractStateModel – Abstrakte Basisklasse für typsichere State-Modelle.
Ermöglicht einheitliches Serialisieren/Deserialisieren für DuckDB-Persistierung.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class AbstractStateModel(ABC):
    """Abstrakte Basisklasse für persistierbare State-Modelle."""

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Serialisiert das Modell in ein JSON-kompatibles Dict."""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AbstractStateModel":
        """Deserialisiert ein Dict zurück in eine Modell-Instanz."""
        pass
