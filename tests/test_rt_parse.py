"""Tests offline del parsing GTFS-RT / flota (sin red ni BD)."""
from collector.realtime import (
    parse_alerts,
    parse_fleet,
    parse_trip_updates,
    parse_vehicle_positions,
)

NOW = 1791475000


def _tu(trip_id="T1", delay=120, stus=None, rel="SCHEDULED"):
    return {"id": f"e_{trip_id}", "tripUpdate": {
        "trip": {"tripId": trip_id, "scheduleRelationship": rel},
        "stopTimeUpdate": stus if stus is not None else [
            {"arrival": {"delay": 60, "time": str(NOW + 600)}, "stopId": "10001"},
            {"arrival": {"delay": 90, "time": str(NOW + 300)}, "stopId": "10002"},
        ],
        "delay": delay}}


class TestTripUpdates:
    def test_basic_and_next_stop(self):
        ts, trips, stus = parse_trip_updates(
            {"header": {"timestamp": str(NOW)}, "entity": [_tu()]}, "cer", NOW)
        assert ts == NOW
        assert len(trips) == 1 and len(stus) == 2
        t = trips[0]
        assert t["t"] == "T1" and t["delay"] == 120
        # la próxima parada es la de menor tiempo (10002 @ +300, no la primera del array)
        assert t["ns"] == "10002" and t["nst"] == NOW + 300 and t["nsd"] == 90

    def test_duplicate_entities_deduped(self):
        feed = {"header": {"timestamp": str(NOW)}, "entity": [
            _tu("T1", 60, [{"arrival": {"delay": 0, "time": str(NOW)}, "stopId": "1"}]),
            _tu("T1", 300, [{"arrival": {"delay": 5, "time": str(NOW)}, "stopId": "1"}]),
        ]}
        _, trips, stus = parse_trip_updates(feed, "cer", NOW)
        assert len(trips) == 1 and len(stus) == 1
        assert trips[0]["delay"] == 300  # la última entidad gana

    def test_missing_trip_id_skipped(self):
        _, trips, stus = parse_trip_updates(
            {"entity": [_tu(""), _tu("T2", 0, [])]}, "cer", NOW)
        assert [t["t"] for t in trips] == ["T2"]
        assert trips[0]["ns"] is None

    def test_cancelled(self):
        _, trips, _ = parse_trip_updates(
            {"entity": [_tu("T9", 0, [], rel="CANCELED")]}, "cer", NOW)
        assert trips[0]["rel"] == "CANCELED"

    def test_no_header(self):
        ts, trips, _ = parse_trip_updates({"entity": []}, "ld", NOW)
        assert ts == NOW and trips == []


class TestVehiclePositions:
    def test_parse(self):
        feed = {"header": {"timestamp": str(NOW)}, "entity": [{
            "id": "VP_C1-23558",
            "vehicle": {
                "trip": {"tripId": "3079J23558C1"},
                "position": {"latitude": 37.48, "longitude": -5.93},
                "currentStatus": "INCOMING_AT",
                "timestamp": str(NOW - 5),
                "stopId": "50702",
                "vehicle": {"id": "23558", "label": "C1-23558-PLATF.(2)"},
            }}]}
        ts, rows = parse_vehicle_positions(feed, "cer")
        assert ts == NOW and len(rows) == 1
        r = rows[0]
        assert r["vid"] == "23558" and r["t"] == "3079J23558C1"
        assert r["plat"] == "2" and r["status"] == "INCOMING_AT"

    def test_missing_id_skipped(self):
        _, rows = parse_vehicle_positions(
            {"entity": [{"vehicle": {"position": {}}}]}, "cer")
        assert rows == []


class TestAlerts:
    def test_dedupe(self):
        feed = {"header": {"timestamp": str(NOW)}, "entity": [
            {"id": "A1", "alert": {"effect": "SIGNIFICANT_DELAYS"}},
            {"id": "A1", "alert": {"effect": "SIGNIFICANT_DELAYS"}},
            {"id": "A2", "alert": {}},
        ]}
        ts, rows = parse_alerts(feed, "cer")
        assert ts == NOW and {r["a"] for r in rows} == {"A1", "A2"}


class TestFleet:
    def test_parse(self):
        data = {"fechaActualizacion": "2026-10-08T18:55:11", "trenes": [{
            "tripId": "3079J23560C1", "codTren": "23560", "codLinea": "C1",
            "retrasoMin": "5", "codEstAct": "51003", "codEstSig": "51100",
            "horaLlegadaSigEst": "2026-10-08T19:01:58",
            "codEstDest": "51200", "codEstOrig": "50600",
            "latitud": 37.39, "longitud": -5.97, "via": "9", "nextVia": "2",
        }]}
        ts, rows = parse_fleet(data)
        r = rows[0]
        assert r["t"] == "3079J23560C1" and r["dm"] == 5
        assert r["plat"] == "9" and r["nplat"] == "2"
        assert r["ns"] == "51100" and r["dst"] == "51200"
        assert r["eta"] is not None

    def test_bad_delay_string(self):
        data = {"trenes": [{"tripId": "X", "retrasoMin": "", "codEstAct": "1"}]}
        _, rows = parse_fleet(data)
        assert rows[0]["dm"] is None

    def test_empty(self):
        _, rows = parse_fleet({"trenes": []})
        assert rows == []
