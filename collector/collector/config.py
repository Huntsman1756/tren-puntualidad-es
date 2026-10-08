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
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "https://trenes.h1756.es/fuentes")
