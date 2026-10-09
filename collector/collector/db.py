import logging
import time

from sqlalchemy import create_engine, text

from collector.config import DATABASE_URL
from collector.models import Base

log = logging.getLogger("collector")

# nombre con el que etiquetamos nuestras conexiones en pg_stat_activity
APP_NAME = "trenes-collector"
# tiempo máximo de espera de un lock durante las migraciones: si algo más
# retiene un lock, el arranque falla y wait_and_create reintenta
LOCK_TIMEOUT = "15s"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={
        "application_name": APP_NAME,
        # detecta conexiones muertas (p. ej. contenedor matado) en vez de colgarse
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    },
)


def _column_exists(c, table, column) -> bool:
    return c.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = :t AND column_name = :c"),
        {"t": table, "c": column}).first() is not None


def _add_column(c, table, column, ddl_type):
    """ADD COLUMN IF NOT EXISTS sin tomar ACCESS EXCLUSIVE si ya existe."""
    if _column_exists(c, table, column):
        return
    c.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}"))


def _create_index(c, name, target):
    """CREATE INDEX IF NOT EXISTS sin tocar la tabla si el índice ya existe."""
    if c.execute(text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :n"),
            {"n": name}).first():
        return
    c.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {target}"))


def _migrate(c):
    """ALTERs idempotentes para columnas añadidas tras create_all.

    Salta sin locks lo que ya existe, así un arranque normal no necesita
    ACCESS EXCLUSIVE sobre tablas que nada cambia."""
    c.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    _add_column(c, "observations", "service_date", "DATE")
    _add_column(c, "observations", "source", "VARCHAR(16) DEFAULT 'legacy'")
    _add_column(c, "observations", "kind", "VARCHAR(16) DEFAULT 'legacy'")
    _add_column(c, "observations", "provider_ts", "BIGINT")
    _create_index(c, "ix_obs_instance", "observations(feed, trip_id, service_date)")
    _create_index(c, "ix_obs_stop_day", "observations(feed, stop_id, service_date)")
    _add_column(c, "geo_station", "nucleo_code", "VARCHAR(4)")
    _add_column(c, "geo_station", "nucleo", "VARCHAR(40)")
    _add_column(c, "geo_station", "lineas", "TEXT")
    c.execute(text("UPDATE observations SET source='legacy', kind='legacy' WHERE source IS NULL"))
    _add_column(c, "trips", "shape_id", "VARCHAR(64)")
    # inicio efectivo de la captura tipificada: para despliegues con
    # datos v0.3.3 previos, se siembra desde la primera observación
    # tipificada existente por (feed, fuente)
    c.execute(text("""INSERT INTO meta(key, value)
           SELECT 'capture_start_' || feed || '_' || source,
                  min(observed_at)::text
           FROM observations
           WHERE kind IN ('prediction','reported')
           GROUP BY feed, source
           ON CONFLICT DO NOTHING"""))
    migrate_push_v2(c)
    # catálogo curado de enlaces de transbordo entre estaciones (idempotente;
    # create_all ya ha creado la tabla en este arranque)
    _add_column(c, "rt_ext_ld", "identity_src", "VARCHAR(16)")
    # avisos oficiales: procedencia verificada y URL de origen (manual)
    _add_column(c, "official_notice", "verified", "BOOLEAN")
    _add_column(c, "official_notice", "source_url", "TEXT")
    from collector.transfer_links import seed_transfer_links
    seed_transfer_links(c)


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


def terminate_orphans(conn) -> int:
    """Termina backends 'trenes-collector' huérfanos y devuelve cuántos.

    Caso típico: el contenedor se mata a mitad de una carga GTFS (COPY de
    stop_times); el backend viejo sigue vivo reteniendo locks sobre trips
    y stop_times, y el ALTER TABLE del nuevo arranque se queda colgado.

    Supone UNA sola instancia del collector por base de datos: cualquier
    otra conexión con ese application_name se considera huérfana. Excluye
    solo la propia conexión (pg_backend_pid())."""
    rows = conn.execute(text(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE application_name = :app AND pid <> pg_backend_pid() "
        "AND datname = current_database()"),
        {"app": APP_NAME}).scalars().all()
    return sum(1 for ok in rows if ok)


def wait_and_create(retries=30):
    for i in range(retries):
        try:
            # primera conexión: si la BD responde, limpia huérfanos de un
            # collector anterior antes de tocar ninguna tabla
            with engine.begin() as c:
                n = terminate_orphans(c)
            if n:
                log.warning("terminados %d backends huérfanos de %s", n, APP_NAME)
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
