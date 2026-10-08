"""Identidad canónica de líneas: núcleo verificable por route_id."""
import pytest
from collector.lines import (
    NUCLEOS,
    classify_route,
    family_of,
    nucleo_from_route_id,
    slug_of,
)
from sqlalchemy import text


def test_nucleos_tables_in_sync():
    from api.common import NUCLEOS as API_NUCLEOS
    assert API_NUCLEOS == NUCLEOS


@pytest.mark.parametrize("rid,expected", [
    ("10T0011C4", "10"), ("20T0001C1", "20"), ("51T0001R1", "50"),
    ("62T0001C1", "62"), ("99T0001C1", None), ("C1", None), ("", None),
    ("7420060000AV006", None),
])
def test_nucleo_from_route_id(rid, expected):
    assert nucleo_from_route_id(rid) == expected


@pytest.mark.parametrize("code,fam", [
    ("C4a", "C4"), ("C4A", "C4"), ("R2N", "R2"), ("RG1", "RG1"),
    ("C10", "C10"), ("BUS", "BUS"), ("T1", "T1"), (None, None)])
def test_family(code, fam):
    assert family_of(code) == fam


def test_slug():
    assert slug_of("C4a") == "c4a"
    assert slug_of("R2N") == "r2n"


def test_classify_verified_and_conflict():
    assert classify_route("10T0001C1", {"10": 12, None: 3}) == ("10", "verified")
    assert classify_route("10T0001C1", {}) == ("10", "prefix_only")
    assert classify_route("10T0001C1", {None: 4}) == ("10", "prefix_only")
    # paradas mayoritariamente en otro núcleo: NO se asigna
    assert classify_route("60T0009C3", {"47": 9, "60": 2}) == (None, "conflict")
    assert classify_route("88T0001C1", {"10": 5}) == (None, "unmapped")
    # un ramal que pisa dos núcleos conserva el suyo si es mayoritario
    assert classify_route("60T0001C1", {"60": 6, "47": 4}) == ("60", "verified")


def test_rodalies_prefix_51_maps_to_official_50():
    assert classify_route("51T0001R1", {"50": 27}) == ("50", "verified")


# ---------- integración (BD) ----------

@pytest.mark.integration
def test_homonymous_lines_are_distinct(scenario):
    with scenario.connect() as c:
        rows = {r[0]: r for r in c.execute(text(
            "SELECT route_id, nucleo_code, line_code, line_slug, status"
            " FROM line_route WHERE feed='cer'"))}
    assert rows["10T0001C1"][1:] == ("10", "C1", "c1", "verified")
    assert rows["20T0001C1"][1:] == ("20", "C1", "c1", "verified")
    # misma línea comercial, grafía unificada a la oficial del visor
    assert rows["10T0099C4A"][2] == "C4a"
    assert rows["51T0001R1"][1] == "50"
    assert rows["60T0009C3"][1] is None and rows["60T0009C3"][4] == "conflict"


@pytest.mark.integration
def test_api_line_pages_distinguish_nucleos(client):
    mad = client.get("/api/v1/lineas/madrid/c1").json()
    ast = client.get("/api/v1/lineas/asturias/c1").json()
    assert mad["nucleo"]["slug"] == "madrid" and ast["nucleo"]["slug"] == "asturias"
    assert {r["route_id"] for r in mad["routes"]} == {"10T0001C1"}
    assert {r["route_id"] for r in ast["routes"]} == {"20T0001C1"}
    assert mad["line"]["label"] == "C1 · Madrid"
    # la familia C4 agrupa las variantes C4a (ambas grafías)
    fam = client.get("/api/v1/lineas/madrid/c4").json()
    assert fam["line"]["is_family"]
    assert {r["route_id"] for r in fam["routes"]} == {"10T0013C4a", "10T0099C4A"}
    assert client.get("/api/v1/lineas/sevilla/c1").status_code == 404
    assert client.get("/api/v1/lineas/narnia/c1").status_code == 404


@pytest.mark.integration
def test_conflicting_route_not_shown_as_line(client):
    nucs = {n["slug"]: n for n in client.get("/api/v1/nucleos").json()}
    assert "bilbao" not in nucs           # su única ruta está en conflicto
    assert "rodalies-catalunya" in nucs
    audit = client.get("/api/v1/lineas-audit", params={"status": "conflict"}).json()
    assert [i["route_id"] for i in audit["items"]] == ["60T0009C3"]


@pytest.mark.integration
def test_board_and_journeys_carry_line_identity(client):
    b = client.get("/api/v1/stations/board",
                   params={"stops": "cer:17000", "date": "2099-01-01"})
    assert b.status_code == 200
    j = client.get("/api/v1/journeys/plan", params={
        "from": "cer:17000", "to": "cer:18000",
        "date": str(__import__("dbfix").TODAY), "time": "05:00"}).json()
    labels = {i["line_info"]["label"] for i in j["items"]}
    assert "C1 · Madrid" in labels and "C4a · Madrid" in labels
    st = client.get("/api/v1/stations/cer/15211").json()
    assert [li["label"] for li in st["lines"]] == ["C1 · Asturias"]


@pytest.mark.integration
def test_ranking_filters_by_nucleo_and_line(client, scenario):
    import time
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(text("""INSERT INTO rt_trip (feed, trip_id, delay, next_stop_id,
            next_stop_time, updated_at) VALUES
            ('cer','MAD_C1_0600',600,'18000',:f,:n),
            ('cer','AST_C1_0600',900,'15410',:f,:n),
            ('cer','MAD_C4A_0700',60,'17000',:f,:n)"""), {"f": now + 600, "n": now})
    allr = client.get("/api/v1/delays/ranking", params={"min_delay": 0}).json()
    assert len(allr) == 3
    mad = client.get("/api/v1/delays/ranking",
                     params={"nucleo": "madrid", "min_delay": 0}).json()
    assert {r["trip_id"] for r in mad} == {"MAD_C1_0600", "MAD_C4A_0700"}
    c1 = client.get("/api/v1/delays/ranking",
                    params={"nucleo": "madrid", "linea": "c1", "min_delay": 0}).json()
    assert [r["trip_id"] for r in c1] == ["MAD_C1_0600"]
    assert c1[0]["line_info"]["label"] == "C1 · Madrid"
    agg = client.get("/api/v1/delays/lines", params={"min_delay": 300}).json()
    by = {(i["nucleo"]["slug"], i["line"]["slug"]): i for i in agg["items"]}
    assert by[("asturias", "c1")]["delayed"] == 1
    assert by[("asturias", "c1")]["max_delay_sec"] == 900
    assert by[("madrid", "c4")]["delayed"] == 0      # 60 s < umbral
    assert by[("madrid", "c4")]["monitored"] == 1
    assert agg["semantics"] == "snapshot_reported_delay"


@pytest.mark.integration
def test_implausible_fleet_delay_ignored(client, scenario):
    import time
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(text("""INSERT INTO rt_trip (feed, trip_id, delay, next_stop_id,
            next_stop_time, updated_at)
            VALUES ('cer','MAD_C1_0600',120,'18000',:f,:n)"""),
            {"f": now + 600, "n": now})
        c.execute(text("""INSERT INTO rt_fleet (feed, trip_id, delay_min, ts)
            VALUES ('cer','MAD_C1_0600',-1438,:n)"""), {"n": now})
    r = client.get("/api/v1/delays/ranking", params={"min_delay": 0}).json()
    assert r[0]["delay"] == 120 and r[0]["delay_source"] == "predicted"
