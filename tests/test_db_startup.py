"""Arranque del collector frente a conexiones huérfanas y locks (BD real)."""
import contextlib
import os

import collector.db as cdb
import psycopg
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

COLUMNS = [
    ("observations", "service_date"),
    ("observations", "source"),
    ("observations", "kind"),
    ("observations", "provider_ts"),
    ("geo_station", "nucleo_code"),
    ("geo_station", "nucleo"),
    ("geo_station", "lineas"),
    ("trips", "shape_id"),
]
INDEXES = ["ix_obs_instance", "ix_obs_stop_day"]


def _raw_conninfo():
    url = os.environ["TEST_DATABASE_URL"]
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _open_raw(app_name):
    return psycopg.connect(_raw_conninfo(), application_name=app_name, autocommit=False)


def _close_quietly(conn):
    with contextlib.suppress(Exception):
        conn.rollback()
    with contextlib.suppress(Exception):
        conn.close()


def _column_exists(conn, table, column):
    return conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = :t AND column_name = :c"),
        {"t": table, "c": column}).first() is not None


def test_migrate_twice_is_noop(db_engine):
    with db_engine.begin() as c:
        cdb._migrate(c)
    with db_engine.begin() as c:
        cdb._migrate(c)  # segunda pasada: sin errores
    with db_engine.begin() as c:
        for table, column in COLUMNS:
            assert _column_exists(c, table, column), f"falta {table}.{column}"
        for name in INDEXES:
            assert c.execute(text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = :n"), {"n": name}).first(), f"falta índice {name}"


def test_terminate_orphans_kills_stale_collector(db_engine):
    raw = _open_raw("trenes-collector")
    try:
        # simula backend huérfano que retiene ACCESS EXCLUSIVE sobre trips
        raw.execute("LOCK TABLE trips IN ACCESS EXCLUSIVE MODE")
        with db_engine.begin() as c:
            n = cdb.terminate_orphans(c)
        assert n >= 1
        # con el huérfano muerto, la migración completa sin esperar el lock
        with db_engine.begin() as c:
            cdb._migrate(c)
    finally:
        _close_quietly(raw)
        db_engine.dispose()


def test_lock_timeout_fails_fast(db_engine, monkeypatch):
    # columna ausente para que _migrate necesite ALTER sobre trips
    with db_engine.begin() as c:
        c.execute(text("ALTER TABLE trips DROP COLUMN IF EXISTS shape_id"))
    raw = _open_raw("other")
    try:
        raw.execute("LOCK TABLE trips IN ACCESS EXCLUSIVE MODE")
        # 1s en vez de 15s para que el test sea rápido; mismo mecanismo
        monkeypatch.setattr(cdb, "LOCK_TIMEOUT", "1s")
        with pytest.raises(Exception, match=r"lock timeout|canceling statement"):
            with db_engine.begin() as c:
                cdb._migrate(c)
    finally:
        _close_quietly(raw)
        with db_engine.begin() as c:
            c.execute(text("ALTER TABLE trips ADD COLUMN IF NOT EXISTS shape_id VARCHAR(64)"))
