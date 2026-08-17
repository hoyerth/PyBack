# repositories/algo_results_repository.py
"""
repositories/algo_results_repository.py - Persistenz der Algo-Ergebnisse.

Speichert die result_schema-Felder eines AlgOS in der Tabelle `algo_results`
(app_data.duckdb). Definition (Phase 5, Anwender-Klaerung):

  * store='series' -> pro Kerze (bar_time) ein Wert (durchgehende Linie)
  * store='agg'    -> ein Wert pro Lauf (Signale/Kennzahlen)
  * store='both'   -> beides

Tabelle (normalisiert, defensiv angelegt):
    (algo_id, instance_key, symbol, timeframe, bar_time, result_name,
     result_value) mit PK ueber alle Felder ausser value.

Nur DB-Zugriff (SRP): KEIN Qt-Import, KEINE Berechnungslogik.
"""

from typing import Any, Dict, List, Optional, Union

import pandas as pd
from db.db_pool import DB_APP_DATA, DbPool

ALGO_RESULTS_TABLE = "algo_results"


class AlgoResultsRepository:
    """Persistiert Algo-Ergebnisse (Serien/Aggregate) in app_data.duckdb."""

    def __init__(self, db_path: str = DB_APP_DATA) -> None:
        self.db_path = db_path
        self._init_table()

    # ------------------------------------------------------------------
    # Tabellen-Init (defensiv)
    # ------------------------------------------------------------------
    def _init_table(self) -> None:
        con = DbPool.get(self.db_path)
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {ALGO_RESULTS_TABLE} (
                algo_id VARCHAR NOT NULL,
                instance_key VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                bar_time BIGINT NOT NULL,
                result_name VARCHAR NOT NULL,
                result_value DOUBLE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (algo_id, instance_key, symbol, timeframe,
                             bar_time, result_name)
            )
        """)

    # ------------------------------------------------------------------
    # Schreiben
    # ------------------------------------------------------------------
    def save_series(self, algo_id: str, instance_key: str, symbol: str,
                    timeframe: str, result_name: str,
                    series: Union[pd.Series, None]) -> int:
        """Speichert eine Ergebnis-Serie (Index = bar_time-Epochs).

        Args:
            series: pd.Series mit Epoch-Index und float-Werten (NaN wird
                uebersprungen). None/leer -> 0.

        Returns:
            Anzahl gespeicherter Zeilen.
        """
        if series is None or len(series) == 0:
            return 0
        rows = []
        for idx, val in series.items():
            if val is None or pd.isna(val):
                continue
            try:
                rows.append((algo_id, instance_key, symbol, timeframe,
                             int(idx), result_name, float(val)))
            except (TypeError, ValueError):
                continue
        if not rows:
            return 0
        con = DbPool.get(self.db_path)
        con.executemany(
            f"""INSERT OR REPLACE INTO {ALGO_RESULTS_TABLE}
                (algo_id, instance_key, symbol, timeframe, bar_time,
                 result_name, result_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows)
        return len(rows)

    def save_agg(self, algo_id: str, instance_key: str, symbol: str,
                 timeframe: str, result_name: str, value: Any,
                 run_epoch: Optional[int] = None) -> None:
        """Speichert ein Aggregat-Ergebnis (pro Lauf).

        Args:
            run_epoch: Zeitstempel des Laufs (Default: bar_time = None ->
                aktuelle Unix-Epoch).
        """
        import time
        bar_time = int(run_epoch) if run_epoch is not None else int(time.time())
        try:
            value_f = float(value) if value is not None else None
        except (TypeError, ValueError):
            value_f = None
        con = DbPool.get(self.db_path)
        con.execute(
            f"""INSERT OR REPLACE INTO {ALGO_RESULTS_TABLE}
                (algo_id, instance_key, symbol, timeframe, bar_time,
                 result_name, result_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [algo_id, instance_key, symbol, timeframe, bar_time,
             result_name, value_f])

    # ------------------------------------------------------------------
    # Lesen (fuer spaetere Phasen / Auswertung)
    # ------------------------------------------------------------------
    def fetch_series(self, algo_id: str, instance_key: str, symbol: str,
                     timeframe: str, result_name: str,
                     limit: int = 5000) -> pd.Series:
        """Liest eine gespeicherte Serie (aufsteigend, letzte `limit` Punkte)."""
        con = DbPool.get(self.db_path)
        rows = con.execute(
            f"""SELECT bar_time, result_value FROM {ALGO_RESULTS_TABLE}
                WHERE algo_id = ? AND instance_key = ?
                  AND symbol = ? AND timeframe = ? AND result_name = ?
                ORDER BY bar_time DESC
                LIMIT ?""",
            [algo_id, instance_key, symbol, timeframe, result_name, limit]
        ).fetchall()
        data = {int(r[0]): float(r[1]) for r in rows if r[1] is not None}
        return pd.Series(dict(sorted(data.items())))

    def delete_for_instance(self, algo_id: str, instance_key: str,
                            symbol: str, timeframe: str) -> int:
        """Loescht alle Ergebnis-Zeilen einer Instanz (bei 'Entfernen')."""
        con = DbPool.get(self.db_path)
        count = con.execute(
            f"""SELECT COUNT(*) FROM {ALGO_RESULTS_TABLE}
                WHERE algo_id = ? AND instance_key = ?
                  AND symbol = ? AND timeframe = ?""",
            [algo_id, instance_key, symbol, timeframe]).fetchone()[0]
        con.execute(
            f"""DELETE FROM {ALGO_RESULTS_TABLE}
                WHERE algo_id = ? AND instance_key = ?
                  AND symbol = ? AND timeframe = ?""",
            [algo_id, instance_key, symbol, timeframe])
        return int(count)
