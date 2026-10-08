"""Esquema de datos compartido (collector lo crea, api lo lee)."""
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Stop(Base):
    __tablename__ = "stops"
    feed = Column(String(8), primary_key=True)
    stop_id = Column(String(32), primary_key=True)
    name = Column(Text, nullable=False)
    lat = Column(Float)
    lon = Column(Float)


class Route(Base):
    __tablename__ = "routes"
    feed = Column(String(8), primary_key=True)
    route_id = Column(String(64), primary_key=True)
    short_name = Column(Text)
    long_name = Column(Text)
    route_type = Column(Integer)
    color = Column(String(8))
    text_color = Column(String(8))


class Trip(Base):
    __tablename__ = "trips"
    feed = Column(String(8), primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    route_id = Column(String(64))
    service_id = Column(String(64))
    headsign = Column(Text)
    train_number = Column(String(16), index=True)


class StopTime(Base):
    __tablename__ = "stop_times"
    feed = Column(String(8), primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    seq = Column(Integer, primary_key=True)
    stop_id = Column(String(32), index=True)
    arr = Column(Integer)  # segundos desde medianoche local
    dep = Column(Integer)


Index("ix_stop_times_stop", StopTime.feed, StopTime.stop_id, StopTime.dep)


class ServiceDay(Base):
    """Expansión de calendar/calendar_dates: un registro por día activo."""
    __tablename__ = "service_days"
    feed = Column(String(8), primary_key=True)
    service_id = Column(String(64), primary_key=True)
    day = Column(Date, primary_key=True)


Index("ix_service_days_day", ServiceDay.feed, ServiceDay.day)


class RtTrip(Base):
    """Estado actual de un viaje según trip_updates."""
    __tablename__ = "rt_trip"
    feed = Column(String(8), primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    delay = Column(Integer)
    sched_rel = Column(String(24))
    next_stop_id = Column(String(32))
    next_stop_time = Column(BigInteger)   # epoch
    next_stop_delay = Column(Integer)
    updated_at = Column(BigInteger)
    first_seen = Column(BigInteger)


Index("ix_rt_trip_delay", RtTrip.delay)
Index("ix_rt_trip_next_stop", RtTrip.feed, RtTrip.next_stop_id)


class RtStopUpdate(Base):
    __tablename__ = "rt_stop_update"
    feed = Column(String(8), primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    stop_id = Column(String(32), primary_key=True)
    delay = Column(Integer)
    time = Column(BigInteger)
    updated_at = Column(BigInteger)


Index("ix_rt_su_stop", RtStopUpdate.feed, RtStopUpdate.stop_id)


class RtVehicle(Base):
    __tablename__ = "rt_vehicle"
    feed = Column(String(8), primary_key=True)
    vehicle_id = Column(String(64), primary_key=True)
    trip_id = Column(String(64), index=True)
    label = Column(Text)
    platform = Column(String(16))
    lat = Column(Float)
    lon = Column(Float)
    status = Column(String(24))
    stop_id = Column(String(32))
    ts = Column(BigInteger)


class Alert(Base):
    __tablename__ = "alerts"
    feed = Column(String(8), primary_key=True)
    alert_id = Column(String(128), primary_key=True)
    payload = Column(JSONB)
    updated_at = Column(BigInteger)


class Observation(Base):
    """Histórico: cada cambio material de predicción por parada."""
    __tablename__ = "observations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    feed = Column(String(8))
    trip_id = Column(String(64))
    stop_id = Column(String(32))
    delay = Column(Integer)
    time = Column(BigInteger)
    observed_at = Column(BigInteger)


Index("ix_obs_trip", Observation.feed, Observation.trip_id)
Index("ix_obs_time", Observation.observed_at)


class Meta(Base):
    __tablename__ = "meta"
    key = Column(String(64), primary_key=True)
    value = Column(Text)


class TripFlag(Base):
    """Heurística calculada en carga estática: patrón de paradas del viaje.

    `semidirect` = el viaje comparte origen/destino con el patrón canónico
    (modal) de su ruta pero omite >=2 paradas interiores. No es una marca
    oficial de "CIVIS": Renfe no publica ese atributo en GTFS.
    """
    __tablename__ = "trip_flags"
    feed = Column(String(8), primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    n_stops = Column(Integer)
    semidirect = Column(Integer, default=0)   # 0/1
    skipped = Column(Integer, default=0)      # paradas interiores omitidas


Index("ix_trip_flags_semidirect", TripFlag.feed, TripFlag.semidirect)


class PushSub(Base):
    """Suscripción Web Push anónima. Sin cuentas: la clave es el endpoint
    (opaco, controlado por el navegador). Borrado inmediato al darse de baja
    o cuando el push devuelve 404/410."""
    __tablename__ = "push_subs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    endpoint = Column(Text, unique=True, nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    # config: {type: journey|station, from_key, to_key, station_key,
    #          days[], from_time, to_time, threshold_min, min_interval_min}
    config = Column(JSONB, nullable=False)
    last_notify_key = Column(Text)
    last_notify_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RtFleet(Base):
    """Estado observado por tren desde el visor oficial (flota.json)."""
    __tablename__ = "rt_fleet"
    feed = Column(String(8), primary_key=True, default="cer")
    trip_id = Column(String(64), primary_key=True)
    train_number = Column(String(16))
    line = Column(String(16))
    delay_min = Column(Integer)          # retrasoMin observado
    cur_stop_id = Column(String(32))
    next_stop_id = Column(String(32))
    next_eta = Column(BigInteger)        # epoch
    origin_stop_id = Column(String(32))
    dest_stop_id = Column(String(32))
    lat = Column(Float)
    lon = Column(Float)
    platform = Column(String(16))
    next_platform = Column(String(16))
    ts = Column(BigInteger)
