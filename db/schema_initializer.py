# db/schema_initializer.py
"""
db/schema_initializer.py - Pruefen, Anlegen & Migrieren der Kern-Datenbanken.

Ausgelagert aus db_service.py im Rahmen von 18.01.02 (E3): `check_and_init_databases`
legt die drei DuckDB-Dateien (market_data, analytics, app_data) mit ihrem
Schema idempotent an und fuehrt additive Migrationen aus. Importiert die
Basis-Schicht db/db_pool (E4); kein MT5-Import (Lazy-Import-Prinzip).
"""

import os

from db.db_pool import DATA_DIR, DB_ANALYTICS, DB_APP_DATA, DB_MARKET_DATA, DbPool


def check_and_init_databases() -> None:
    """Prüft, initialisiert und migriert die Kern-Datenbanken bei Bedarf."""
    print("🔍 [1/3] Prüfe und initialisiere Ordnerstruktur und Datenbanken...")
    os.makedirs(DATA_DIR, exist_ok=True)

    con_market = DbPool.get(DB_MARKET_DATA)
    con_market.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_bars (
            symbol      VARCHAR NOT NULL,
            timeframe   VARCHAR NOT NULL,
            time        TIMESTAMPTZ NOT NULL,
            open        DOUBLE NOT NULL,
            high        DOUBLE NOT NULL,
            low         DOUBLE NOT NULL,
            close       DOUBLE NOT NULL,
            tick_volume BIGINT,
            spread      INTEGER,
            real_volume BIGINT,
            created_at  TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (symbol, timeframe, time)
        );
    """)

    try:
        col_type_row = con_market.execute("""
            SELECT data_type
            FROM information_schema.columns
            WHERE LOWER(table_name) = 'ohlcv_bars' AND LOWER(column_name) = 'time'
        """).fetchone()

        if col_type_row and col_type_row[0].upper() == "TIMESTAMP":
            print("⚠️ [MIGRATION] Konvertiere 'time' Spalte in ohlcv_bars von TIMESTAMP zu TIMESTAMPTZ...")
            con_market.execute("ALTER TABLE ohlcv_bars ALTER time TYPE TIMESTAMPTZ")
            print("✅ [MIGRATION] Konvertierung erfolgreich abgeschlossen.")
    except Exception as e:
        print(f"⚠️ [MIGRATION WARNUNG] Migration konnte nicht durchgeführt werden: {e}")

    # P1#4 (Optimierung, 17.08.2026): ARTEMIS-Index auf dem PK-Praefix
    # (symbol, timeframe, time) fuer schnelle Punkt-/Bereichs-Lookups der
    # Playground-Zeitraum-Queries. Idempotent: DuckDB legt den Index beim
    # ersten Start nach diesem Update an (~15s bei 20 Mio. Zeilen), danach
    # ist CREATE INDEX IF NOT EXISTS ein reiner Katalog-Check.
    # USER-REQ (17.08.2026): Zeitraum-basiertes Laden (P1#5) nutzt exakt
    # dieses Praefix-Praedikat (symbol+timeframe+time-Range).
    con_market.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_pair_time "
        "ON ohlcv_bars(symbol, timeframe, time)")

    con_analytics = DbPool.get(DB_ANALYTICS)
    # USER-REQ (17.08.2026): analytics.duckdb enthaelt NUR die Persistierung
    # der Algos (algo_results). PyTrader-Fremdtabellen (analytics_metadata,
    # feature_store, grabber_test_results) wurden entfernt (Vorgabe 3+6).
    con_analytics.execute("""
        CREATE TABLE IF NOT EXISTS algo_results (
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

    con_app = DbPool.get(DB_APP_DATA)
    con_app.execute("""
        CREATE TABLE IF NOT EXISTS app_config (
            key VARCHAR PRIMARY KEY,
            value VARCHAR,
            updated_at TIMESTAMP DEFAULT current_timestamp
        );
    """)

    # Phase 15 (15.01): Symbol- & Favoriten-Verwaltung. broker_symbols haelt
    # die Broker-Symbole (aus mt5.symbols_get()) inkl. Favoriten-Flag und
    # dient als Fallback, wenn MT5 nicht verfuegbar ist. Standard-Defaults
    # (SILVER, GOLD, BTCUSD) werden als Favoriten vorbelegt, damit die
    # Favoriten-Dropdowns (ServiceWindow/AnalyticsWindow) nie leer starten.
    con_app.execute("""
        CREATE TABLE IF NOT EXISTS broker_symbols (
            symbol      VARCHAR PRIMARY KEY,
            path        VARCHAR,
            is_favorite BOOLEAN DEFAULT FALSE,
            updated_at  TIMESTAMP DEFAULT current_timestamp
        );
    """)
    con_app.execute("""
        INSERT INTO broker_symbols (symbol, path, is_favorite)
        VALUES ('SILVER', '', TRUE), ('GOLD', '', TRUE), ('BTCUSD', '', TRUE)
        ON CONFLICT (symbol) DO NOTHING;
    """)
    # USER-REQ (17.08.2026): analytics_profiles (PyTrader win_analytics) wurde
    # entfernt – app_data enthaelt nur System-/App-Daten (Vorgabe 1+6).
    print(f"   ✅ Ordner '{DATA_DIR}/' und alle 3 DBs sind einsatzbereit.")
