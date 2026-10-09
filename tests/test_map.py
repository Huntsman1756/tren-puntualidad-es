"""Mapa: solo geometrías validadas; posiciones con antigüedad, sin inventar."""
import time

import pytest
from collector.shapes import evaluate_shape
from sqlalchemy import text


def test_evaluate_shape_valid_partial_unused():
    line = [(40.0 + i * 0.001, -3.7) for i in range(200)]      # ~22 km N-S
    st_on = [(40.0 + i * 0.02, -3.7) for i in range(10)]
    assert evaluate_shape(line, st_on)["status"] == "valid"
    # estaciones que la shape no alcanza (shape incompleta)
    st_far = st_on + [(40.5, -3.7), (40.6, -3.7)]
    ev = evaluate_shape(line, st_far)
    assert ev["status"] == "partial" and ev["near"] == 10
    assert evaluate_shape(line, [])["status"] == "unused"
    assert evaluate_shape([(40, -3)], st_on)["status"] == "invalid"


@pytest.mark.integration
def test_map_endpoint_draws_only_valid_shapes_and_hides_stale(client, scenario):
    from collector.shapes import compute_shape_quality
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(text("UPDATE trips SET shape_id='10_C1' WHERE route_id='10T0001C1'"))
        c.execute(text("UPDATE trips SET shape_id='10_C4a' WHERE route_id LIKE '10T%C4%'"))
        # 10_C1: une Atocha (40.406,-3.690) y Chamartín (40.472,-3.682)
        c.execute(text("INSERT INTO shapes VALUES (:f,:s,:q,:la,:lo)"), [
            {"f": "cer", "s": "10_C1", "q": i, "la": 40.406 + i * 0.0066,
             "lo": -3.690 + i * 0.0008} for i in range(11)])
        # 10_C4a: solo cubre Getafe, no llega a Atocha/Chamartín -> parcial
        c.execute(text("INSERT INTO shapes VALUES (:f,:s,:q,:la,:lo)"), [
            {"f": "cer", "s": "10_C4a", "q": i, "la": 40.300 + i * 0.001,
             "lo": -3.732} for i in range(10)])
        compute_shape_quality(c, "cer")
        c.execute(text("""INSERT INTO rt_fleet (feed, trip_id, train_number, delay_min,
            lat, lon, ts) VALUES ('cer','MAD_C1_0600','21000',3,40.43,-3.69,:fresh),
                                 ('cer','MAD_C4A_0700','22000',0,40.35,-3.71,:old)"""),
                  {"fresh": now - 30, "old": now - 3600})
    from dbfix import reset_api_caches
    reset_api_caches()
    m = client.get("/api/v1/mapa/madrid").json()
    assert [s["shape_id"] for s in m["shapes"]] == ["10_C1"]
    assert {e["shape_id"]: e["status"] for e in m["excluded_shapes"]} == {"10_C4a": "partial"}
    assert [t["trip_id"] for t in m["trains"]] == ["MAD_C1_0600"]
    assert m["trains"][0]["freshness"] == "fresh" and m["trains"][0]["source"] == "visor"
    assert m["stale_hidden"] == 1
    assert client.get("/api/v1/mapa/narnia").status_code == 404
