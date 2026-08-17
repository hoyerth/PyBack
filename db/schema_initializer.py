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

    con_analytics = DbPool.get(DB_ANALYTICS)
    con_analytics.execute("""
        CREATE TABLE IF NOT EXISTS analytics_metadata (
            created_at TIMESTAMP DEFAULT current_timestamp,
            info VARCHAR
        );
    """)

    # Analytics-Tabelle für Feature-/Plugin-Daten
    # 17.01 (E-1, 07.08.2026): 4-Spalten-PK (symbol, timeframe, bar_time,
    # feature_id) – erlaubt die konfliktfreie Speicherung MEHRERER Services auf
    # derselben Kerze (feature_id identifiziert das erzeugende Plugin, Default
    # 'native' fuer den klassischen Feature-Builder-Pfad). Bei bestehenden DBs
    # ist CREATE TABLE IF NOT EXISTS ein No-op; die Migration existierender
    # Tabellen erfolgt ueber test/migrate_pk.py (Table-Rewrite + RENAME, da
    # DuckDB 1.5.5 kein DROP PRIMARY KEY unterstuetzt).
    # 19.02 (Cleanup): Die Legacy-Native-Spalten ema_diff/rsi_14/
    # atr_normalized entfallen im NEUSCHEMA – alle Feature-Werte liegen im
    # feature_data-JSON. Bestehende DB-Dateien (mit den Alt-Spalten) werden
    # durch den Additiv-Pfad (ALTER TABLE ADD COLUMN IF NOT EXISTS) nicht
    # angetastet; der Reader greift nur noch auf feature_data zu.
    con_analytics.execute("""
        CREATE TABLE IF NOT EXISTS feature_store (
            symbol      VARCHAR NOT NULL,
            timeframe   VARCHAR NOT NULL,
            bar_time    TIMESTAMPTZ NOT NULL,
            created_at  TIMESTAMP DEFAULT current_timestamp,
            feature_id  VARCHAR NOT NULL DEFAULT 'native',
            plugin_version VARCHAR,
            feature_data JSON,
            instance_hash VARCHAR NOT NULL DEFAULT '',
            PRIMARY KEY (symbol, timeframe, bar_time, feature_id,
                         instance_hash)
        );
    """)

    # Phase 12 (Hybrid-Schema): Additive Erweiterung des feature_store um die
    # Plugin-Architektur. feature_id identifiziert das erzeugende Plugin
    # (z.B. 'srv_grid_lines'), plugin_version dessen Version und feature_data
    # haelt den vollstaendigen FeatureStorePayload (JSON). Bestehende Spalten
    # und Daten bleiben unangetastet.
    con_analytics.execute("ALTER TABLE feature_store ADD COLUMN IF NOT EXISTS feature_id VARCHAR;")
    con_analytics.execute("ALTER TABLE feature_store ADD COLUMN IF NOT EXISTS plugin_version VARCHAR;")
    con_analytics.execute("ALTER TABLE feature_store ADD COLUMN IF NOT EXISTS feature_data JSON;")
    # 20.04 (Q1/Q9, 09.08.2026): Additive Spalte instance_hash – stabile
    # Identifikation von Parameter-Varianten eines Plugins (8-stelliger
    # SHA256-Short-Hash aus generate_instance_hash, ohne lookback – Q3).
    # Ermoeglicht Multi-Varianten-Statistiken und gezieltes Daten-Purge
    # (purge_instance_data, Q5), ohne die feature_id (plugin_id) anzutasten.
    # Bestehende Rows bleiben NULL; feature_id bleibt plugin_id (Zero-Regression).
    con_analytics.execute("ALTER TABLE feature_store ADD COLUMN IF NOT EXISTS instance_hash VARCHAR;")
    # 11.08.2026 (Bugfix Varianten-Kollision): Der feature_store-PK wird um
    # instance_hash erweitert - (symbol, timeframe, bar_time, feature_id,
    # instance_hash). Damit koexistieren Parameter-Varianten eines Plugins
    # auf derselben Bar (vorher ueberschrieb der letzte Lauf die gemeinsame
    # Row; Kontextmenue-Run + Ausfuehrungsdatum trafen alle Varianten
    # gemeinsam). DuckDB 1.5.5 kann PRIMARY KEY nicht AENDERN - Migration als
    # Table-Rewrite (CREATE TABLE AS + EXCLUDE/COALESCE) + ALTER SET NOT
    # NULL/SET DEFAULT + ALTER ADD PRIMARY KEY + DROP/RENAME. Idempotent:
    # laeuft nur, wenn der aktuelle PK noch KEIN instance_hash enthaelt.
    try:
        _pk_rows = con_analytics.execute(
            "SELECT constraint_column_indexes FROM duckdb_constraints() "
            "WHERE table_name='feature_store' "
            "AND constraint_type='PRIMARY KEY'").fetchall()
        _fs_cols = [r[0].lower() for r in con_analytics.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='feature_store' ORDER BY ordinal_position"
        ).fetchall()]
        # duckdb_constraints liefert pro Zeile ein Tupel (index_list,) -
        # der Spalten-Index liegt in Zeile[0].
        _pk_has_hash = any(
            _fs_cols[i].lower() == "instance_hash"
            for _row in _pk_rows for i in (_row[0] or []))
        if not _pk_has_hash:
            con_analytics.execute("""
                CREATE TABLE feature_store_pk2 AS
                SELECT * EXCLUDE (instance_hash),
                       COALESCE(instance_hash, '') AS instance_hash
                FROM feature_store
            """)
            con_analytics.execute(
                "ALTER TABLE feature_store_pk2 ALTER instance_hash SET NOT NULL")
            con_analytics.execute(
                "ALTER TABLE feature_store_pk2 ALTER instance_hash SET DEFAULT ''")
            con_analytics.execute(
                "ALTER TABLE feature_store_pk2 ADD PRIMARY KEY "
                "(symbol, timeframe, bar_time, feature_id, instance_hash)")
            con_analytics.execute("DROP TABLE feature_store")
            con_analytics.execute(
                "ALTER TABLE feature_store_pk2 RENAME TO feature_store")
            print("MIGRATION: feature_store-PK um instance_hash erweitert.")
    except Exception as e:
        print(f"MIGRATION WARNUNG: feature_store-PK-Migration "
              f"fehlgeschlagen: {e}")
    # Bugfix 07.08.2026 (Phase 17 Bugfix-Runde 2): Der Spalten-DEFAULT von
    # created_at wurde durch die PK-Migration (17.01 E-1, test/migrate_pk.py –
    # Table-Rewrite + RENAME) entfernt. Seitdem bleiben NEUE feature_store-Rows
    # ohne explizites created_at NULL und das 'Datum der letzten Ausfuehrung'
    # (MasterTree, MAX(created_at) je feature_id) zeigt '--.--.--'. Der DEFAULT
    # wird hier idempotent wiederhergestellt (No-op bei korrekter DB).
    try:
        con_analytics.execute(
            "ALTER TABLE feature_store ALTER created_at "
            "SET DEFAULT current_timestamp")
    except Exception as e:
        print(f"⚠️ [MIGRATION WARNUNG] created_at-Default des feature_store "
              f"konnte nicht wiederhergestellt werden: {e}")
    # 22.01 (14.08.2026): Peak-Grabber-Serientests. Die Outcome-Spalten
    # (outcome_status/pnl_r_multiple/max_favorable_exc/max_adverse_exc)
    # sind PENDING/NULL-Platzhalter (Frage 4) – die Exit-/Forward-Evaluation
    # folgt als separates Modul in einem spaeteren Kapitel (peak_outcome.py).
    # Schreibzugriff ausschliesslich ueber repositories/grabber_repository.py
    # (DbPool-Muster, Praeambel 4/6). run_id-Konvention: "LIVE-<YYYYmmdd-HHMMSS>"
    # fuer Live-Laeufe, "BT-<YYYYmmdd-HHMMSS>" fuer Serientests.
    con_analytics.execute("""
        CREATE TABLE IF NOT EXISTS grabber_test_results (
            signal_id           VARCHAR PRIMARY KEY,
            run_id              VARCHAR NOT NULL,
            timestamp           TIMESTAMPTZ NOT NULL,   -- Wanduhr-Epochs (Praemabel 8)
            symbol              VARCHAR NOT NULL,
            timeframe           VARCHAR NOT NULL,
            direction           VARCHAR NOT NULL,
            entry_price         DOUBLE NOT NULL,
            sl_price            DOUBLE NOT NULL,
            peak_price          DOUBLE NOT NULL,
            peak_bar_index      BIGINT NOT NULL,
            is_update           BOOLEAN NOT NULL,
            reversal_pct        FLOAT NOT NULL,
            is_yellow_window    BOOLEAN NOT NULL,
            gate_source         VARCHAR NOT NULL,
            outcome_status      VARCHAR DEFAULT 'PENDING',
            pnl_r_multiple      FLOAT,
            max_favorable_exc   FLOAT,
            max_adverse_exc     FLOAT
        );
    """)
    con_analytics.execute(
        "CREATE INDEX IF NOT EXISTS idx_grabber_run "
        "ON grabber_test_results (run_id);")

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

    # Phase 15 (15.03): Analytics-Profile. analytics_profiles haelt benannte
    # Parametrisierungen der Analytics-UI (Option B – Explicit Save: Slider-/
    # Parametertrends setzen Dirty-Flag, Speichern erst auf [Save]). Das
    # Profil-Payload-JSON (Spalte payload) enthaelt als Pflichtfeld
    # `schema_version` (15.03-Spezifikation: 1). Additiv/idempotent –
    # bestehende Profile bleiben unangetastet.
    con_app.execute("""
        CREATE TABLE IF NOT EXISTS analytics_profiles (
            profile_id  VARCHAR PRIMARY KEY,
            name        VARCHAR NOT NULL,
            description VARCHAR,
            payload     JSON,
            is_active   BOOLEAN DEFAULT FALSE,
            created_at  TIMESTAMP DEFAULT current_timestamp,
            updated_at  TIMESTAMP DEFAULT current_timestamp
        );
    """)
    print(f"   ✅ Ordner '{DATA_DIR}/' und alle 3 DBs sind einsatzbereit.")
