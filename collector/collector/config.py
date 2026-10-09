import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://renfe:renfe@localhost:5432/renfe"
)

GTFS_STATIC = {
    "cer": "https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip",
    "ld": "https://ssl.renfe.com/gtransit/Fichero_AV_LD/google_transit.zip",
}

RT_TRIP_UPDATES = {
    "cer": "https://gtfsrt.renfe.com/trip_updates.json",
    "ld": "https://gtfsrt.renfe.com/trip_updates_LD.json",
}
RT_VEHICLE_POSITIONS = {
    "cer": "https://gtfsrt.renfe.com/vehicle_positions.json",
    "ld": "https://gtfsrt.renfe.com/vehicle_positions_LD.json",
}
RT_ALERTS = {
    "cer": "https://gtfsrt.renfe.com/alerts.json",
    "ld": None,
}

POLL_TRIP_UPDATES = int(os.environ.get("POLL_TRIP_UPDATES", "25"))
POLL_VEHICLE_POSITIONS = int(os.environ.get("POLL_VEHICLE_POSITIONS", "30"))
POLL_ALERTS = int(os.environ.get("POLL_ALERTS", "60"))
POLL_STATIC = int(os.environ.get("POLL_STATIC", "3600"))

TZ = "Europe/Madrid"

FLOTA_URL = "https://tiempo-real.renfe.com/renfe-visor/flota.json"
SALIDAS_URL = "https://tiempo-real.renfe.com/renfe-json-cutter/write/salidas/estacion/{code}.json"
STATIONS_GEOJSON = "https://tiempo-real.renfe.com/data/estaciones.geojson"
POLL_FLOTA = int(os.environ.get("POLL_FLOTA", "45"))

POLL_PUSH = int(os.environ.get("POLL_PUSH", "60"))
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

# Avisos oficiales (WhatsApp Channels de Renfe, pegados a mano o vía WAHA)
# WAHA desactivado si WAHA_URL está vacío.
WAHA_URL = os.environ.get("WAHA_URL", "").strip().rstrip("/")
WAHA_API_KEY = os.environ.get("WAHA_API_KEY", "")
WAHA_SESSION = os.environ.get("WAHA_SESSION", "default")
# lista "invite_code:channel_slug:nucleo_code" separada por comas
WAHA_CHANNELS = os.environ.get("WAHA_CHANNELS", "")
POLL_WAHA = int(os.environ.get("POLL_WAHA", "120"))
# hilo abierto sin novedades durante este tiempo -> sin_actualizar (no resuelto)
NOTICE_STALE_SEC = int(os.environ.get("NOTICE_STALE_SEC", str(6 * 3600)))
# un aviso se une a un hilo si el último del hilo tiene menos de esta antigüedad
NOTICE_THREAD_SEC = int(os.environ.get("NOTICE_THREAD_SEC", str(12 * 3600)))
