"""Tests de integración: requieren PostgreSQL con datos (TEST_DATABASE_URL).
Ejecutar contra el stack de desarrollo o en CI con servicio postgres cargado."""
import os

import pytest

pytestmark = pytest.mark.integration

URL = os.environ.get("TEST_API_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def client():
    import httpx
    c = httpx.Client(base_url=URL, timeout=30)
    try:
        c.get("/health/ready").raise_for_status()
    except Exception:
        pytest.skip("API no disponible en " + URL)
    return c


def test_health(client):
    assert client.get("/health/live").json()["status"] == "ok"


def test_search_groups_same_name(client):
    r = client.get("/api/v1/stations/search", params={"q": "atocha"})
    assert r.status_code == 200
    groups = r.json()
    atocha = next(g for g in groups if "Atocha" in g["name"])
    feeds = {s["feed"] for s in atocha["stops"]}
    assert "cer" in feeds


def test_board_shape(client):
    r = client.get("/api/v1/stations/cer/18000/board", params={"minutes": 180})
    assert r.status_code == 200
    items = r.json()["items"]
    if items:
        i = items[0]
        for k in ("trip_id", "scheduled", "estimated", "realtime", "delay_source"):
            assert k in i
        assert i["delay_source"] in (None, "observed", "stop", "trip")


def test_combined_board(client):
    r = client.get("/api/v1/stations/board",
                   params={"stops": "cer:18000,ld:18000"})
    assert r.status_code == 200


def test_station_404(client):
    assert client.get("/api/v1/stations/cer/999999").status_code == 404


def test_journeys_param_validation(client):
    assert client.get("/api/v1/journeys",
                      params={"from": "bad", "to": "bad"}).status_code in (400, 422)


def test_rate_limit_shape(client):
    # el middleware no rompe peticiones normales
    assert client.get("/api/v1/meta/status").status_code == 200
