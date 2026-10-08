import time
import logging
from sqlalchemy import create_engine, text
from .config import DATABASE_URL
from .models import Base

log = logging.getLogger("collector")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def wait_and_create(retries=30):
    for i in range(retries):
        try:
            with engine.begin() as c:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
            Base.metadata.create_all(engine)
            return
        except Exception as e:
            log.warning("db not ready (%s), retry %d/%d", e, i + 1, retries)
            time.sleep(2)
    raise RuntimeError("database unreachable")


def get_meta(conn, key):
    r = conn.execute(text("SELECT value FROM meta WHERE key=:k"), {"k": key}).scalar()
    return r


def set_meta(conn, key, value):
    conn.execute(
        text("INSERT INTO meta(key,value) VALUES(:k,:v) "
             "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"),
        {"k": key, "v": str(value)},
    )
