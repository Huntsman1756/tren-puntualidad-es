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
    shape_id = Column(String(64))


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
    next_stop_time = Column(BigInteger)  # epoch
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
    """Histórico de observaciones por parada.

    Tipología explícita (v0.3.3):
      - kind='prediction'  cambio en trip_updates (hora estimada del feed)
      - kind='reported'    estado notificado por flota (retraso informado
                           en la parada actual — NO llegada efectiva)
      - kind='legacy'      registros previos sin tipología recuperable
      - source: trip_update | fleet | legacy
    service_date = día de servicio GTFS (feed,trip_id,service_date) =
    identidad de circulación. NULL en registros legacy.
    provider_ts = timestamp declarado por el feed (feed timestamp);
    observed_at = hora de recogida por nosotros.
    """

    __tablename__ = "observations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    feed = Column(String(8))
    trip_id = Column(String(64))
    service_date = Column(Date)  # NULL = legacy
    stop_id = Column(String(32))
    delay = Column(Integer)
    time = Column(BigInteger)
    source = Column(String(16), default="legacy")
    kind = Column(String(16), default="legacy")
    provider_ts = Column(BigInteger)
    observed_at = Column(BigInteger)


Index("ix_obs_instance", Observation.feed, Observation.trip_id, Observation.service_date)


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
    semidirect = Column(Integer, default=0)  # 0/1
    skipped = Column(Integer, default=0)  # paradas interiores omitidas


Index("ix_trip_flags_semidirect", TripFlag.feed, TripFlag.semidirect)


class SchedCapture(Base):
    """Un registro por (feed, día de servicio) ya volcado al histórico.

    closed=1: el día ya pasó — inmutable aunque el GTFS se recargue.
    late=1: la primera captura se hizo después del día (denominadores
    reconstruidos desde un GTFS posterior, marcado por honestidad).
    """

    __tablename__ = "sched_capture"
    feed = Column(String(8), primary_key=True)
    day = Column(Date, primary_key=True)
    captured_at = Column(BigInteger)
    closed = Column(Integer, default=0)
    late = Column(Integer, default=0)


class Circulation(Base):
    """Versión mínima de la programación histórica: una circulación
    programada (feed, trip_id, day) tal como se capturó ese día.

    La recarga del GTFS NO reescribe días cerrados: los denominadores
    históricos no cambian retrospectivamente."""

    __tablename__ = "circulation"
    feed = Column(String(8), primary_key=True)
    day = Column(Date, primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    route_id = Column(String(64))
    train_number = Column(String(16))
    first_stop = Column(String(32))
    last_stop = Column(String(32))
    dep_secs = Column(Integer)  # primera salida programada (s desde medianoche)
    arr_secs = Column(Integer)  # última llegada programada
    n_stops = Column(Integer)


Index("ix_circ_day", Circulation.feed, Circulation.day)
Index("ix_circ_route", Circulation.feed, Circulation.route_id, Circulation.day)


class CirculationStop(Base):
    """Parada programada de una circulación capturada (denominadores por
    estación/trayecto/franja independientes de recargas del GTFS)."""

    __tablename__ = "circulation_stop"
    feed = Column(String(8), primary_key=True)
    day = Column(Date, primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    seq = Column(Integer, primary_key=True)
    stop_id = Column(String(32))
    arr = Column(Integer)
    dep = Column(Integer)


Index("ix_cstop_stop", CirculationStop.feed, CirculationStop.stop_id, CirculationStop.day)


class GeoStation(Base):
    """Adscripción territorial persistente de cada parada.

    No se borra en la recarga diaria del GTFS: si una parada desaparece se
    marca active=0 y se conserva su clasificación. `source` documenta la
    procedencia: 'catalogo' (join exacto CODIGO oficial) o 'geo_inferida'
    (vecino catalogado cercano, auditable por dist_m/matched_code).
    """

    __tablename__ = "geo_station"
    feed = Column(String(8), primary_key=True)
    stop_id = Column(String(32), primary_key=True)
    cpro = Column(String(2))  # CPRO INE o None
    provincia = Column(String(40))  # nombre oficial INE
    ccaa_code = Column(String(2))  # CAUTO INE
    ccaa = Column(String(40))
    poblacion = Column(String(80))  # POBLACION del catálogo (núcleo)
    source = Column(String(20), nullable=False)  # catalogo | geo_inferida
    matched_code = Column(String(12))  # CÓDIGO del catálogo usado
    dist_m = Column(Float)  # distancia de la inferencia
    active = Column(Integer, default=1)
    # Núcleo de Cercanías oficial (geojson del visor Renfe, join por
    # CODIGO_ESTACION). NULL si la estación no consta en esa fuente.
    nucleo_code = Column(String(4))
    nucleo = Column(String(40))
    lineas = Column(Text)  # LINEAS oficiales que paran aquí


class RouteCore(Base):
    """Núcleo de Cercanías verificable de cada ruta GTFS.

    Se deriva del núcleo oficial de las paradas de la ruta (geojson del
    visor Renfe por CODIGO_ESTACION). Solo se asigna si las paradas
    clasificadas mayoritan el mismo núcleo; `share`/`matched`/`total`
    documentan la evidencia."""

    __tablename__ = "route_core"
    feed = Column(String(8), primary_key=True)
    route_id = Column(String(64), primary_key=True)
    nucleo_code = Column(String(4))
    nucleo = Column(String(40))  # NULL si no verificable
    share = Column(Float)  # paradas clasificadas en el núcleo
    matched = Column(Integer)
    total = Column(Integer)
    updated_at = Column(BigInteger)


Index("ix_route_core_nucleo", RouteCore.feed, RouteCore.nucleo)


class AnomalyEpisode(Base):
    """Episodio de posible incidencia INFERIDA en una línea (núcleo+familia).

    status: observacion (señal aún no persistente) | confirmada (persiste
    >= ANOMALY_CONFIRM_SEC) | resuelta (sin señal durante ANOMALY_CLEAR_SEC
    con datos RT frescos). Nunca se resuelve por falta de datos RT: si el
    feed no es fresco el episodio queda igual y se marca rt_gap_since."""

    __tablename__ = "anomaly_episode"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    nucleo_code = Column(String(4), nullable=False)
    family_slug = Column(String(16), nullable=False)
    status = Column(String(12), nullable=False)
    opened_at = Column(BigInteger, nullable=False)
    confirmed_at = Column(BigInteger)
    last_signal_at = Column(BigInteger)
    last_eval_at = Column(BigInteger)
    resolved_at = Column(BigInteger)
    rt_gap_since = Column(BigInteger)
    evaluations = Column(Integer, default=0)
    signal_evaluations = Column(Integer, default=0)
    peak_delayed_15 = Column(Integer, default=0)
    peak_share = Column(Float)
    peak_max_delay_sec = Column(Integer)
    last_metrics = Column(JSONB)


Index("ix_anomaly_open", AnomalyEpisode.status, AnomalyEpisode.nucleo_code, AnomalyEpisode.family_slug)


class AnomalySample(Base):
    """Métricas por línea en cada evaluación de un episodio (evolución)."""

    __tablename__ = "anomaly_sample"
    episode_id = Column(BigInteger, primary_key=True)
    ts = Column(BigInteger, primary_key=True)
    monitored = Column(Integer)
    scheduled_now = Column(Integer)
    delayed_15 = Column(Integer)
    delayed_30 = Column(Integer)
    share = Column(Float)
    max_delay_sec = Column(Integer)
    median_delay_sec = Column(Integer)
    signal = Column(Integer)


class OfficialNotice(Base):
    """Aviso oficial de Renfe publicado fuera del GTFS-RT (canal de WhatsApp
    o pegado a mano por el administrador). Texto íntegro + interpretación.

    status: activa | en_recuperacion (incidencia subsanada, frecuencias
    recuperándose) | normalizada (Renfe declara normalidad) | sin_actualizar
    (sin novedades en NOTICE_STALE_SEC; no se presume resuelta).
    thread_id agrupa un aviso y sus actualizaciones (misma línea + estación/
    problema)."""

    __tablename__ = "official_notice"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(String(16), nullable=False)  # whatsapp | manual
    channel = Column(String(64), nullable=False)  # p. ej. cercanias-madrid
    external_id = Column(String(128))  # id del mensaje en origen
    posted_at = Column(BigInteger, nullable=False)
    received_at = Column(BigInteger, nullable=False)
    text = Column(Text, nullable=False)
    nucleo_code = Column(String(4))
    lines = Column(JSONB)  # ["C5", "C4b"]
    stations = Column(JSONB)  # [{"name","stop_id"}]
    kind = Column(String(24))  # averia_infraestructura | averia_tren | ...
    status = Column(String(16), nullable=False)
    is_update = Column(Integer, default=0)
    thread_id = Column(BigInteger)
    parse = Column(JSONB)  # evidencia del parser


Index(
    "ux_notice_external",
    OfficialNotice.source,
    OfficialNotice.channel,
    OfficialNotice.external_id,
    unique=True,
)


class CaptureHealth(Base):
    """Salud de la captura RT por (feed, fuente, día local Europe/Madrid).

    Lo escribe el collector en cada sondeo correcto: nº de sondeos, primer y
    último sondeo y mayor hueco entre sondeos consecutivos. Las estadísticas
    lo usan para excluir días de captura parcial (arranque, caídas)."""

    __tablename__ = "capture_health"
    feed = Column(String(8), primary_key=True)
    source = Column(String(16), primary_key=True)  # trip_update | fleet
    day = Column(Date, primary_key=True)
    polls = Column(Integer, default=0)
    first_ts = Column(BigInteger)
    last_ts = Column(BigInteger)
    max_gap_sec = Column(Integer, default=0)


class Shape(Base):
    """Puntos de shapes.txt (solo CER publica geometrías)."""

    __tablename__ = "shapes"
    feed = Column(String(8), primary_key=True)
    shape_id = Column(String(64), primary_key=True)
    seq = Column(Integer, primary_key=True)
    lat = Column(Float)
    lon = Column(Float)


class ShapeQuality(Base):
    """Control de calidad de cada shape (ver collector/shapes.py).
    status: valid | partial | unused | invalid. Solo 'valid' se dibuja."""

    __tablename__ = "shape_quality"
    feed = Column(String(8), primary_key=True)
    shape_id = Column(String(64), primary_key=True)
    n_points = Column(Integer)
    stations = Column(Integer)
    stations_near = Column(Integer)
    share = Column(Float)
    length_m = Column(Integer)
    status = Column(String(12))


class StationNucleo(Base):
    """Contraste oficial: núcleo de Cercanías por CODIGO_ESTACION según el
    geojson del visor Renfe. Se sustituye solo si la descarga tiene datos."""

    __tablename__ = "station_nucleo"
    code = Column(String(12), primary_key=True)  # CODIGO_ESTACION (5 díg.)
    nucleo_code = Column(String(4))
    lineas = Column(Text)  # LINEAS oficiales
    fetched_at = Column(BigInteger)


class LineRoute(Base):
    """Identidad canónica de cada route_id GTFS (ver collector/lines.py).

    nucleo_code NULL = no asignable de forma verificable (status explica
    por qué). line_code es el código comercial con grafía oficial;
    family_code agrupa variantes (C4a, C4b -> C4)."""

    __tablename__ = "line_route"
    feed = Column(String(8), primary_key=True)
    route_id = Column(String(64), primary_key=True)
    nucleo_code = Column(String(4))
    line_code = Column(String(16))
    line_slug = Column(String(16))
    family_code = Column(String(16))
    family_slug = Column(String(16))
    mode = Column(String(8))  # tren | bus
    status = Column(String(16))  # verified|prefix_only|conflict|unmapped|not_applicable
    evidence = Column(JSONB)
    long_name = Column(Text)
    color = Column(String(8))
    text_color = Column(String(8))
    n_trips = Column(Integer, default=0)  # viajes en el GTFS vigente
    updated_at = Column(BigInteger)


Index("ix_line_route_line", LineRoute.nucleo_code, LineRoute.family_slug)


class TripSpan(Base):
    """Primera salida / última llegada programada de cada viaje."""

    __tablename__ = "trip_span"
    feed = Column(String(8), primary_key=True)
    trip_id = Column(String(64), primary_key=True)
    first_dep = Column(Integer)
    last_arr = Column(Integer)
    n_stops = Column(Integer)


class AlertSeen(Base):
    """Histórico de avisos oficiales: cada alert_id con la última versión
    del payload y cuándo se vio por primera y última vez en el feed."""

    __tablename__ = "alerts_seen"
    feed = Column(String(8), primary_key=True)
    alert_id = Column(String(128), primary_key=True)
    payload = Column(JSONB)
    first_seen = Column(BigInteger)
    last_seen = Column(BigInteger)


Index("ix_alerts_seen_last", AlertSeen.last_seen)


class PushDevice(Base):
    """Un navegador/dispositivo suscrito a Web Push.

    La credencial de posesión es un token aleatorio entregado una sola vez
    al registrar el dispositivo; solo se guarda su SHA-256. token_hash NULL
    = dispositivo migrado de push_subs (v0.3.x) aún no reclamado."""

    __tablename__ = "push_devices"
    id = Column(String(36), primary_key=True)
    endpoint = Column(Text, unique=True, nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    token_hash = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True))


class PushRule(Base):
    """Regla de aviso independiente de un dispositivo (trayecto o estación).
    Borrar una regla no afecta a las demás ni al dispositivo."""

    __tablename__ = "push_rules"
    id = Column(String(36), primary_key=True)
    device_id = Column(String(36), nullable=False, index=True)
    config = Column(JSONB, nullable=False)
    enabled = Column(Integer, default=1)
    last_notify_key = Column(Text)
    last_notify_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PushSub(Base):
    """LEGACY (v0.3.0-0.3.3): una fila por endpoint, una sola config.
    Se migra a push_devices/push_rules al arrancar y ya no se escribe.

    Suscripción Web Push anónima. Sin cuentas: la clave es el endpoint
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
    delay_min = Column(Integer)  # retrasoMin observado
    cur_stop_id = Column(String(32))
    next_stop_id = Column(String(32))
    next_eta = Column(BigInteger)  # epoch
    origin_stop_id = Column(String(32))
    dest_stop_id = Column(String(32))
    lat = Column(Float)
    lon = Column(Float)
    platform = Column(String(16))
    next_platform = Column(String(16))
    ts = Column(BigInteger)


class GtfsTransfer(Base):
    """transfers.txt del GTFS, tal cual (tipo 2 = mínimo obligatorio).

    Solo existe en el feed CER. Las reglas route/trip solo aplican cuando
    coinciden los extremos; las filas sin route/trip son de nivel parada.
    """

    __tablename__ = "gtfs_transfer"
    feed = Column(String(8), primary_key=True)
    from_stop_id = Column(String(32), primary_key=True)
    to_stop_id = Column(String(32), primary_key=True)
    from_route_id = Column(String(64), primary_key=True, default="")
    to_route_id = Column(String(64), primary_key=True, default="")
    from_trip_id = Column(String(64), primary_key=True, default="")
    to_trip_id = Column(String(64), primary_key=True, default="")
    transfer_type = Column(Integer)  # 0 recomendado, 1 posible, 2 mínimo, 3 prohibido
    min_transfer_time = Column(Integer)  # segundos


Index("ix_gtfs_transfer_from", GtfsTransfer.feed, GtfsTransfer.from_stop_id)


class TransferLink(Base):
    """Enlace peatonal/intercambiador verificado entre dos paradas del GTFS
    (posiblemente de feeds distintos). Catálogo curado — NUNCA inferido por
    proximidad geográfica. min_secs es el tiempo mínimo de intercambio en
    ese sentido (los enlaces son dirigidos: ida y vuelta pueden diferir)."""

    __tablename__ = "transfer_link"
    link_id = Column(String(64), primary_key=True)
    from_feed = Column(String(8), nullable=False)
    from_stop_id = Column(String(32), nullable=False)
    to_feed = Column(String(8), nullable=False)
    to_stop_id = Column(String(32), nullable=False)
    min_secs = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)  # complex | walk
    label = Column(Text)
    source = Column(Text)  # evidencia que justifica el enlace


Index("ix_transfer_link_from", TransferLink.from_feed, TransferLink.from_stop_id)


class RtExtLd(Base):
    """Enriquecimiento opcional de LD desde RadarDeTrenes (fuente externa).

    Identidad estricta: (train_number, service_date) — nunca solo número.
    Todo lo que publique la API debe etiquetarse source='radar' y mostrar
    provider_ts para que el usuario distinga su procedencia."""

    __tablename__ = "rt_ext_ld"
    train_number = Column(String(16), primary_key=True)
    service_date = Column(Date, primary_key=True)
    platform = Column(String(16))
    rolling_stock = Column(JSONB)
    next_stop_id = Column(String(32))
    next_eta = Column(BigInteger)  # epoch
    delay_min = Column(Integer)
    product = Column(String(32))
    provider_ts = Column(BigInteger)  # timestamp declarado por radar
    observed_at = Column(BigInteger)  # nuestra hora de recogida
    source = Column(String(16), default="radar")
    # cómo se eligió service_date: 'launching' (declarada por el
    # proveedor), 'coverage' (único día en service_days), 'coverage_multi'
    # (varios días posibles — instancia ambigua) o 'civil' (sin verificar)
    identity_src = Column(String(16))


Index("ix_rt_ext_ld_obs", RtExtLd.observed_at)
