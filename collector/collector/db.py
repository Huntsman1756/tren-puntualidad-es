import logging
import time

from sqlalchemy import create_engine, text

from collector.config import DATABASE_URL
from collector.models import Base

log = logging.getLogger("collector")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def _migrate(c):
    """ALTERs idempotentes para columnas añadidas tras create_all."""
    for stmt in (
        "ALTER TABLE observations ADD COLUMN IF NOT EXISTS service_date DATE",
        "ALTER TABLE observations ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'legacy'",
        "ALTER TABLE observations ADD COLUMN IF NOT EXISTS kind VARCHAR(16) DEFAULT 'legacy'",
        "ALTER TABLE observations ADD COLUMN IF NOT EXISTS provider_ts BIGINT",
        "CREATE INDEX IF NOT EXISTS ix_obs_instance ON observations(feed, trip_id, service_date)",
        "CREATE INDEX IF NOT EXISTS ix_obs_stop_day ON observations(feed, stop_id, service_date)",
        "ALTER TABLE geo_station ADD COLUMN IF NOT EXISTS nucleo_code VARCHAR(4)",
        "ALTER TABLE geo_station ADD COLUMN IF NOT EXISTS nucleo VARCHAR(40)",
        "ALTER TABLE geo_station ADD COLUMN IF NOT EXISTS lineas TEXT",
        "UPDATE observations SET source='legacy', kind='legacy' WHERE source IS NULL",
        "ALTER TABLE trips ADD COLUMN IF NOT EXISTS shape_id VARCHAR(64)",
        # inicio efectivo de la captura tipificada: para despliegues con
        # datos v0.3.3 previos, se siembra desde la primera observación
        # tipificada existente por (feed, fuente)
        """INSERT INTO meta(key, value)
           SELECT 'capture_start_' || feed || '_' || source,
                  min(observed_at)::text
           FROM observations
           WHERE kind IN ('prediction','reported')
           GROUP BY feed, source
           ON CONFLICT DO NOTHING""",
    ):
        c.execute(text(stmt))
    migrate_push_v2(c)


def migrate_push_v2(c):
    """push_subs (una config por endpoint) -> push_devices + push_rules.

    Idempotente: solo copia endpoints aún no migrados y vacía push_subs.
    Los dispositivos migrados quedan con token_hash NULL hasta que su
    navegador los reclame presentando el endpoint (la credencial que ya
    usaba v0.3.x); a partir de ahí solo vale el token."""
    c.execute(text("""
        INSERT INTO push_devices (id, endpoint, p256dh, auth, token_hash,
                                  created_at)
        SELECT gen_random_uuid()::text, s.endpoint, s.p256dh, s.auth, NULL,
               s.created_at
        FROM push_subs s
        WHERE NOT EXISTS (SELECT 1 FROM push_devices d
                          WHERE d.endpoint = s.endpoint)"""))
    c.execute(text("""
        INSERT INTO push_rules (id, device_id, config, enabled,
                                last_notify_key, last_notify_at, created_at,
                                updated_at)
        SELECT gen_random_uuid()::text, d.id, s.config, 1,
               s.last_notify_key, s.last_notify_at, s.created_at, now()
        FROM push_subs s JOIN push_devices d ON d.endpoint = s.endpoint
        WHERE NOT EXISTS (SELECT 1 FROM push_rules r WHERE r.device_id = d.id)"""))
    c.execute(text("DELETE FROM push_subs"))


def wait_and_create(retries=30):
    for i in range(retries):
        try:
            with engine.begin() as c:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
            Base.metadata.create_all(engine)
            with engine.begin() as c2:
                _migrate(c2)
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
