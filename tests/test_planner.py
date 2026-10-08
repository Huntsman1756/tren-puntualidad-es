"""Planificador: fechas futuras, día completo, medianoche, cobertura y
estados diferenciados (sin directos / fuera de cobertura / transbordo)."""
from datetime import timedelta

import pytest
from dbfix import TODAY, epoch

pytestmark = pytest.mark.integration

D1 = TODAY + timedelta(days=1)
D2 = TODAY + timedelta(days=2)


def plan(client, **p):
    p.setdefault("from", "cer:17000")
    p.setdefault("to", "cer:18000")
    r = client.get("/api/v1/journeys/plan", params=p)
    assert r.status_code == 200, r.text
    return r.json()


def test_tomorrow_without_time_is_whole_day(client):
    j = plan(client, date=str(D1))
    assert j["status"] == "ok" and j["scheduled_only"] is True
    trips = [i["trip_id"] for i in j["items"]]
    # 06:00 y 07:15 aunque "ahora" sea más tarde: no se arrastra la hora actual
    assert trips[:2] == ["MAD_C1_0600", "MAD_C4A_0700"]
    assert all(i["realtime"] is False for i in j["items"])
    assert j["window"]["from_secs"] == 0


def test_midnight_crossing_service_listed_on_wall_clock_day(client):
    j = plan(client, date=str(D1), time="23:30", hours=2)
    it = next(i for i in j["items"] if i["trip_id"] == "MAD_C1_2350")
    assert it["service_date"] == str(D1)
    assert it["dep_scheduled"] == epoch(D1, 23 * 3600 + 3000)
    assert it["arr_scheduled"] == epoch(D1, 24 * 3600 + 600)  # 00:10 de D1+1
    # el mismo servicio aparece al consultar D2 de madrugada como servicio de D1
    j2 = plan(client, **{"from": "cer:18000", "to": "cer:17000"}, date=str(D2))
    assert j2["status"] == "needs_transfer"   # sentido inverso no existe


def test_no_direct_in_window_vs_needs_transfer(client):
    j = plan(client, date=str(D1), time="13:00", hours=2)
    assert j["status"] == "no_direct_window"
    j = plan(client, **{"from": "cer:15211", "to": "cer:17000"}, date=str(D1))
    assert j["status"] == "needs_transfer"
    assert j["transfers_supported"] is False
    assert "no inventamos" in j["message"]


def test_different_networks(client):
    j = plan(client, **{"from": "cer:17000", "to": "ld:71801"}, date=str(D1))
    assert j["status"] == "different_networks"


def test_out_of_coverage(client):
    far = TODAY + timedelta(days=30)
    j = plan(client, date=str(far))
    assert j["status"] == "out_of_coverage"
    assert j["coverage_gap"]["last_day"] == str(TODAY + timedelta(days=7))
    past = TODAY - timedelta(days=3)
    assert plan(client, date=str(past))["status"] == "out_of_coverage"


def test_coverage_reports_valid_range_per_feed(client):
    cov = client.get("/api/v1/meta/coverage").json()
    assert cov["today"] == str(TODAY)
    assert cov["feeds"]["cer"]["first_day"] == str(TODAY)
    assert cov["feeds"]["cer"]["last_day"] == str(TODAY + timedelta(days=7))


def test_train_future_instance_is_scheduled_only(client, scenario):
    import time

    from sqlalchemy import text
    with scenario.begin() as c:
        c.execute(text("""INSERT INTO rt_trip (feed, trip_id, delay, next_stop_id,
            next_stop_time, updated_at) VALUES ('cer','MAD_C1_0600',900,'18000',:f,:n)"""),
            {"f": int(time.time()) + 600, "n": int(time.time())})
    today = client.get("/api/v1/trains/cer/MAD_C1_0600").json()
    assert today["scheduled_only"] is False and today["rt"]["delay"] == 900
    fut = client.get("/api/v1/trains/cer/MAD_C1_0600", params={"date": str(D1)}).json()
    assert fut["scheduled_only"] is True and fut["rt"] is None
    assert fut["service_date"] == str(D1) and fut["runs_on_date"] is True
    assert fut["stops"][0]["dep_scheduled"] == epoch(D1, 6 * 3600)
    assert all(s["delay_sec"] is None for s in fut["stops"])
    # instancia que no circula ese día
    no = client.get("/api/v1/trains/cer/MAD_C4AX_0710", params={"date": str(D1)}).json()
    assert no["runs_on_date"] is False
    yes = client.get("/api/v1/trains/cer/MAD_C4AX_0710", params={"date": str(D2)}).json()
    assert yes["runs_on_date"] is True


def test_board_future_date_keeps_date_and_no_rt(client):
    b = client.get("/api/v1/stations/board",
                   params={"stops": "cer:17000", "date": str(D1)}).json()
    assert b["scheduled_only"] is True and b["date"] == str(D1)
    assert {i["service_date"] for i in b["items"]} <= {str(D1), str(TODAY)}
    assert all(i["delay_sec"] is None for i in b["items"])
