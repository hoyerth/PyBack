# symbol_repository.py
"""
symbol_repository.py - Symbol- & Favoriten-Repository (Phase 15.01).

Kapselt den Lese-/Schreibzugriff auf die `broker_symbols`-Tabelle in
`app_data.duckdb` – entkoppelt aus `state_manager.py` (SRP: Repository-
Schicht). Die UI (SymbolsWindow, ServiceWindow/AnalyticsWindow-Dropdowns)
greift ausschliesslich ueber dieses Repository auf Symbole zu.

Datenfluss (Invariante 4, Kein SQL in UI):
    DuckDB (broker_symbols) <-- SymbolRepository <-- UI-Fenster

Verhalten:
- `ensure_defaults()`    : legt SILVER/GOLD/BTCUSD als Favoriten an (idempotent).
- `sync_from_broker()`   : liest Symbole live via mt5.symbols_get() (lazy
  MT5-Import), schreibt sie per Upsert in die DB und gibt die DB-Liste
  zurueck. Bei MT5-Ausfall (initialize()==False / Exception) automatischer
  Fallback auf die DB-Tabelle – die App bleibt voll funktionsfaehig.
  Seit 15.01-Nachtrag 3 (04.08.2026) wird der Fetch NUR noch EINMALIG beim
  App-Start (main.py) aufgerufen – die UI-Fenster (SymbolsWindow) lesen
  ausschliesslich die gespeicherte Liste ueber `get_symbols()`.
- `toggle_favorite()`    : kippt das Favoriten-Flag eines Symbols.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from db.db_pool import DB_APP_DATA, DbPool

# Komfort-Konstante fuer UI-Fallback (Dropdown nie leer), identisch zu den
# Defaults in db_service.check_and_init_databases().
DEFAULT_SYMBOLS: Tuple[str, ...] = ("SILVER", "GOLD", "BTCUSD")


class SymbolRepository:
    """Kapselt den Zugriff auf die broker_symbols-Tabelle (app_data.duckdb)."""

    def __init__(self, db_path: str = DB_APP_DATA) -> None:
        self.db_path = db_path
        self._ensure_table()

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------
    def _get_connection(self):
        return DbPool.get(self.db_path)

    def _ensure_table(self) -> None:
        """Legt die Tabelle (falls noetig) an und stellt die Defaults sicher.

        Additiv/idempotent: bestehende Zeilen und Favoriten-Flags werden
        nicht angetastet (Verbotsregel: Bestandsdaten nicht beschädigen).
        """
        con = self._get_connection()
        con.execute("""
            CREATE TABLE IF NOT EXISTS broker_symbols (
                symbol      VARCHAR PRIMARY KEY,
                path        VARCHAR,
                is_favorite BOOLEAN DEFAULT FALSE,
                updated_at  TIMESTAMP DEFAULT current_timestamp
            );
        """)
        self.ensure_defaults()

    def ensure_defaults(self) -> None:
        """Legt SILVER/GOLD/BTCUSD als Favoriten an (falls noch nicht vorhanden)."""
        con = self._get_connection()
        con.execute("""
            INSERT INTO broker_symbols (symbol, path, is_favorite)
            VALUES ('SILVER', '', TRUE), ('GOLD', '', TRUE), ('BTCUSD', '', TRUE)
            ON CONFLICT (symbol) DO NOTHING;
        """)

    # ------------------------------------------------------------------
    # Lese-API
    # ------------------------------------------------------------------
    def get_symbols(self) -> List[Dict[str, Any]]:
        """Liefert alle Broker-Symbole (aufsteigend nach Name).

        Rückgabe: [{"symbol", "path", "is_favorite", "updated_at"}, ...]
        """
        con = self._get_connection()
        rows = con.execute("""
            SELECT symbol, path, is_favorite, updated_at
            FROM broker_symbols
            ORDER BY symbol ASC
        """).fetchall()
        return [
            {
                "symbol": str(r[0]),
                "path": str(r[1]) if r[1] is not None else "",
                "is_favorite": bool(r[2]),
                "updated_at": r[3],
            }
            for r in rows
        ]

    def get_favorite_symbols(self) -> List[str]:
        """Liefert nur die Favoriten-Symbole (is_favorite == TRUE), sortiert."""
        con = self._get_connection()
        rows = con.execute("""
            SELECT symbol FROM broker_symbols
            WHERE is_favorite = TRUE
            ORDER BY symbol ASC
        """).fetchall()
        return [str(r[0]) for r in rows]

    def get_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Liefert ein einzelnes Symbol (oder None)."""
        con = self._get_connection()
        row = con.execute("""
            SELECT symbol, path, is_favorite, updated_at
            FROM broker_symbols
            WHERE LOWER(symbol) = LOWER(?)
        """, [symbol]).fetchone()
        if row is None:
            return None
        return {
            "symbol": str(row[0]),
            "path": str(row[1]) if row[1] is not None else "",
            "is_favorite": bool(row[2]),
            "updated_at": row[3],
        }

    def count(self) -> int:
        """Anzahl der gespeicherten Symbole."""
        con = self._get_connection()
        res = con.execute("SELECT COUNT(*) FROM broker_symbols").fetchone()
        return int(res[0]) if res and res[0] is not None else 0

    # ------------------------------------------------------------------
    # Schreib-API
    # ------------------------------------------------------------------
    def toggle_favorite(self, symbol: str) -> bool:
        """Kippt das Favoriten-Flag eines Symbols.

        Args:
            symbol: Symbol-Name (case-insensitive).

        Returns:
            Der NEUE Favoriten-Zustand (True = jetzt Favorit).
            Ist das Symbol unbekannt, wird es mit is_favorite=TRUE angelegt.
        """
        con = self._get_connection()
        current = self.get_symbol(symbol)
        if current is None:
            con.execute("""
                INSERT INTO broker_symbols (symbol, path, is_favorite)
                VALUES (?, '', TRUE)
                ON CONFLICT (symbol) DO NOTHING;
            """, [symbol])
            return True
        new_state = not current["is_favorite"]
        con.execute("""
            UPDATE broker_symbols
            SET is_favorite = ?, updated_at = current_timestamp
            WHERE LOWER(symbol) = LOWER(?)
        """, [new_state, symbol])
        return new_state

    def upsert_from_broker(self, broker_symbols: List[Tuple[str, str]]) -> int:
        """Schreibt Broker-Symbole per Upsert in die DB.

        Bestehende Zeilen werden aktualisiert (path/updated_at), Favoriten-
        Flags bleiben dabei unangetastet (additiv, keine Datenverluste).

        Args:
            broker_symbols: Liste von (symbol, path)-Paaren aus MT5.

        Returns:
            Anzahl der verarbeiteten Symbole.
        """
        if not broker_symbols:
            return 0
        con = self._get_connection()
        seen: Dict[str, str] = {}
        for symbol, path in broker_symbols:
            sym = str(symbol).strip()
            if not sym:
                continue
            # Letzter Eintrag pro Symbol gewinnt (MT5 kann Duplikate liefern)
            seen[sym] = str(path or "")
        for sym, path in seen.items():
            con.execute("""
                INSERT INTO broker_symbols (symbol, path, is_favorite)
                VALUES (?, ?, FALSE)
                ON CONFLICT (symbol) DO UPDATE SET
                    path = EXCLUDED.path,
                    updated_at = DEFAULT;
            """, [sym, path])
        return len(seen)

    def sync_from_broker(self) -> List[Dict[str, Any]]:
        """Synchronisiert Symbole live aus MT5 (mit automatischem DB-Fallback).

        - Versucht mt5.symbols_get() (lazy import; schaltet MT5 NICHT ein,
          wenn das Terminal geschlossen ist).
        - Bei Erfolg: Upsert aller Symbole in die DB, Rückgabe der DB-Liste.
        - Bei MT5-Ausfall (initialize()==False, Exception): Fallback auf die
          DB-Tabelle – die App bleibt voll funktionsfaehig (Roadmap 15.01).

        Detaillierter Status (live/fallback + Fehlermeldung) ist ueber
        `sync_from_broker_with_status()` verfuegbar.
        """
        symbols, _status, _error = self.sync_from_broker_with_status()
        return symbols

    def sync_from_broker_with_status(self) -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
        """Wie sync_from_broker(), liefert zusaetzlich Status & Fehlermeldung.

        Erweiterung fuer den App-Start-Sync (main.py, 15.01-Nachtrag 3): Die
        Liste aller verfuegbaren Symbole wird EINMALIG beim App-Start live von
        MT5 geladen; schlaegt der MT5-Zugriff fehl, wird der Grund als
        Fehlermeldung geliefert, damit der Aufrufer eine Log-Meldung ausgeben
        kann, statt still auf den DB-Stand zurueckzufallen. Die UI-Fenster
        (SymbolsWindow) rufen diese Methode seit Nachtrag 3 NICHT mehr auf –
        sie lesen ausschliesslich die gespeicherte Liste (get_symbols()).

        Returns:
            (symbols, status, error)
            - symbols: immer die DB-Symbol-Liste (Fallback inklusive).
            - status:  "live" bei erfolgreichem MT5-Fetch,
                       "fallback" bei MT5-Ausfall (DB-Stand).
            - error:   Fehlertext (oder None bei Erfolg).
        """
        try:
            import MetaTrader5 as _mt5
        except Exception as exc:
            return self.get_symbols(), "fallback", f"MetaTrader5-Import fehlgeschlagen: {exc}"

        try:
            initialized = bool(_mt5.initialize())
        except Exception as exc:
            return self.get_symbols(), "fallback", f"mt5.initialize() Fehler: {exc}"
        if not initialized:
            return self.get_symbols(), "fallback", "MT5-Terminal nicht verfügbar (initialize() == False)"

        try:
            symbols = _mt5.symbols_get()
        except Exception as exc:
            return self.get_symbols(), "fallback", f"mt5.symbols_get() Fehler: {exc}"
        if not symbols:
            return self.get_symbols(), "fallback", "MT5 liefert keine Symbole (symbols_get() leer)"

        try:
            pairs = [(s.name, getattr(s, "path", "")) for s in symbols]
            self.upsert_from_broker(pairs)
        except Exception as exc:
            return self.get_symbols(), "fallback", f"Upsert in broker_symbols fehlgeschlagen: {exc}"

        return self.get_symbols(), "live", None


# Bequeme Default-Instanz (kapselt app_data.duckdb) – fuer UI-Fenster.
_symbol_repo_default: Optional[SymbolRepository] = None


def get_symbol_repository() -> SymbolRepository:
    """Liefert die app-weite Standard-Instanz (lazy, gebunden an app_data.duckdb)."""
    global _symbol_repo_default
    if _symbol_repo_default is None:
        _symbol_repo_default = SymbolRepository()
    return _symbol_repo_default
