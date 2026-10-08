"""Incidencias oficiales: alcance, clasificación, periodos y salud de fuente."""
import json
import time

import pytest
from api.incidents import classify, period_status, pick_lang, translations
from sqlalchemy import text

NOW = 1_800_000_000


def cats(effect=None, cause=None, body=""):
    return {c["category"] for c in classify(effect, cause, body)}


def test_classify_text_rules_real_renfe_texts():
    c8 = ("#MadC8b Por obras de mejora en la infraestructura, del 10 al 12 de "
          "octubre, los trenes no prestan servicio entre Villalba de Guadarrama "
          "y Cercedilla. Se dispone de un servicio especial de autobús con "
          "parada en estaciones intermedias.")
    assert cats(body=c8) == {"obras", "interrumpido", "alternativo"}
    assert cats(body="Ascensor fuera de servicio en el andén 2") == {"accesibilidad"}
    assert cats(body="Los trenes recuperan sus frecuencias de paso habituales") == {"otras"}


def test_classify_official_fields_win_and_are_explained():
    out = classify("ACCESSIBILITY_ISSUE", "CONSTRUCTION", "texto neutro")
    reasons = {c["category"]: c["reason"] for c in out}
    assert reasons == {"accesibilidad": "effect=ACCESSIBILITY_ISSUE",
                       "obras": "cause=CONSTRUCTION"}
    assert cats("NO_SERVICE") == {"interrumpido"}


def test_no_false_accessibility_from_substring():
    # 'rampa' no debe saltar dentro de otra palabra
    assert "accesibilidad" not in cats(body="La estación de Trampas cerrada")


def test_period_status():
    assert period_status([], NOW) == "active"            # sin periodo = vigente
    assert period_status([{"start": NOW - 10, "end": None}], NOW) == "active"
    assert period_status([{"start": NOW + 3600, "end": None}], NOW) == "upcoming"
    assert period_status([{"start": NOW - 7200, "end": NOW - 3600}], NOW) == "expired"
    assert period_status([{"start": NOW - 7200, "end": NOW - 3600},
                          {"start": NOW + 60, "end": NOW + 120}], NOW) == "upcoming"


def test_translations_language_preference():
    trs = translations({"translation": [{"text": "Avís", "language": "ca"},
                                        {"text": "Aviso", "language": "es"},
                                        {"text": "  ", "language": "en"}]})
    assert len(trs) == 2
    assert pick_lang(trs)["text"] == "Aviso"


# ---------- integración ----------

def _alert(aid, entities, text_="Aviso", periods=None, **extra):
    a = {"informedEntity": entities,
         "descriptionText": {"translation": [{"text": text_, "language": "es"}]},
         "activePeriod": periods if periods is not None else [{"start": "1"}]}
    a.update(extra)
    return (aid, a)


def _load(engine, alerts, ok_ts=None, err_ts=None):
    now = int(time.time())
    with engine.begin() as c:
        c.execute(text("DELETE FROM alerts"))
        for aid, p in alerts:
            c.execute(text("""INSERT INTO alerts (feed, alert_id, payload, updated_at)
                VALUES ('cer', :a, CAST(:p AS jsonb), :t)"""),
                {"a": aid, "p": json.dumps(p), "t": now})
        for k, v in (("alerts_fetch_ok_cer", ok_ts if ok_ts is not None else now),
                     ("alerts_fetch_err_cer", err_ts),
                     ("alerts_count_cer", len(alerts))):
            if v is not None:
                c.execute(text("INSERT INTO meta VALUES (:k,:v) ON CONFLICT (key)"
                               " DO UPDATE SET value=EXCLUDED.value"),
                          {"k": k, "v": str(v)})


@pytest.mark.integration
def test_route_alert_is_line_scope_and_stop_alert_is_station_scope(client, scenario):
    _load(scenario, [
        _alert("LINE_C1_MAD", [{"routeId": "10T0001C1"}], "Retrasos en la línea"),
        _alert("ST_GETAFE", [{"stopId": "10000"}], "Ascensor averiado en Getafe"),
        _alert("C1_AT_ATOCHA", [{"routeId": "10T0001C1", "stopId": "17000"}],
               "Cambio de vía en Atocha"),
    ])
    items = {a["id"]: a for a in client.get("/api/v1/incidencias").json()["items"]}
    assert items["LINE_C1_MAD"]["scope"] == "line"
    assert [li["label"] for li in items["LINE_C1_MAD"]["lines"]] == ["C1 · Madrid"]
    assert items["ST_GETAFE"]["scope"] == "station"
    assert items["ST_GETAFE"]["lines"] == []           # nunca se infla a línea
    assert items["ST_GETAFE"]["stations"][0]["name"] == "Getafe Centro"
    assert items["C1_AT_ATOCHA"]["scope"] == "route_at_stop"
    assert items["ST_GETAFE"]["categories"][0]["category"] == "accesibilidad"


@pytest.mark.integration
def test_station_view_labels_relevance(client, scenario):
    _load(scenario, [
        _alert("LINE_C1_MAD", [{"routeId": "10T0001C1"}]),
        _alert("ST_GETAFE", [{"stopId": "10000"}]),
        _alert("LINE_C1_AST", [{"routeId": "20T0001C1"}]),
    ])
    r = client.get("/api/v1/incidencias", params={"estacion": "cer:17000"}).json()
    rel = {a["id"]: a["relevance"] for a in r["items"]}
    # Atocha: aviso de línea C1 Madrid (línea que para aquí); ni el aviso de
    # una estación vecina ni la C1 homónima de Asturias
    assert rel == {"LINE_C1_MAD": "line"}
    g = client.get("/api/v1/incidencias", params={"estacion": "cer:10000"}).json()
    assert {a["id"]: a["relevance"] for a in g["items"]} == {"ST_GETAFE": "station"}


@pytest.mark.integration
def test_line_view_does_not_promote_station_alert_to_line(client, scenario):
    _load(scenario, [
        _alert("ST_GETAFE", [{"stopId": "10000"}]),
        _alert("LINE_C4", [{"routeId": "10T0013C4a"}]),
    ])
    r = client.get("/api/v1/incidencias",
                   params={"nucleo": "madrid", "linea": "c4a"}).json()
    rel = {a["id"]: a["relevance"] for a in r["items"]}
    assert rel == {"LINE_C4": "line", "ST_GETAFE": "station_on_line"}
    ast = client.get("/api/v1/incidencias", params={"nucleo": "asturias"}).json()
    assert ast["total"] == 0


@pytest.mark.integration
def test_journey_alerts(client, scenario):
    _load(scenario, [
        _alert("LINE_C1_MAD", [{"routeId": "10T0001C1"}]),
        _alert("ST_CHAMARTIN", [{"stopId": "18000"}]),
        _alert("LINE_R1", [{"routeId": "51T0001R1"}]),
    ])
    r = client.get("/api/v1/incidencias", params={
        "origen": "cer:17000", "destino": "cer:18000"}).json()
    rel = {a["id"]: a["relevance"] for a in r["items"]}
    assert rel == {"LINE_C1_MAD": "line", "ST_CHAMARTIN": "destination"}


@pytest.mark.integration
def test_period_filter_and_unmatched_audit(client, scenario):
    fut = int(time.time()) + 86400
    _load(scenario, [
        _alert("NOW", [{"routeId": "10T0001C1"}]),
        _alert("FUTURE", [{"routeId": "10T0001C1"}], periods=[{"start": str(fut)}]),
        _alert("GHOST", [{"routeId": "10T9999C99"}, {"stopId": "00001"}]),
    ])
    assert {a["id"] for a in client.get(
        "/api/v1/incidencias", params={"periodo": "proximas"}).json()["items"]} == {"FUTURE"}
    au = client.get("/api/v1/incidencias/audit").json()
    assert au["unmatched_route_ids"] == ["10T9999C99"]
    assert au["unmatched_stop_ids"] == ["00001"]
    assert au["published"] == 3
    assert au["fields_present"]["severity"] == 0   # no se inventa


@pytest.mark.integration
def test_source_health_states(client, scenario):
    now = int(time.time())
    _load(scenario, [])
    s = client.get("/api/v1/incidencias").json()["source"]
    assert s["status"] == "ok" and "sin avisos" in s["message"]
    _load(scenario, [], ok_ts=now - 3600, err_ts=now - 30)
    assert client.get("/api/v1/incidencias").json()["source"]["status"] == "down"
    with scenario.begin() as c:
        c.execute(text("DELETE FROM meta WHERE key LIKE 'alerts_fetch_%'"))
    assert client.get("/api/v1/incidencias").json()["source"]["status"] == "unknown"
    ld = client.get("/api/v1/incidencias", params={"feed": "ld"}).json()["source"]
    assert ld["status"] == "not_available"


@pytest.mark.integration
def test_poll_alerts_records_history_and_health(scenario):
    from collector.realtime import poll_alerts
    data = {"header": {"timestamp": "1791358372"}, "entity": [
        {"id": "A1", "alert": {"informedEntity": [{"routeId": "10T0001C1"}]}}]}
    assert poll_alerts("cer", data=data, now=1000) == 1
    assert poll_alerts("cer", data={"header": {}, "entity": []}, now=2000) == 0
    with scenario.connect() as c:
        seen = c.execute(text(
            "SELECT first_seen, last_seen FROM alerts_seen WHERE alert_id='A1'")).first()
        meta = dict(c.execute(text(
            "SELECT key, value FROM meta WHERE key LIKE 'alerts_%'")).all())
    assert tuple(seen) == (1000, 1000)
    assert meta["alerts_fetch_ok_cer"] == "2000"
    assert meta["alerts_count_cer"] == "0"


@pytest.mark.integration
def test_line_alert_naming_one_station_is_not_spread(client, scenario):
    _load(scenario, [
        _alert("LIFT", [{"routeId": "10T0013C4a"}],
               "GETAFE CENTRO: el ascensor de vía 2 está fuera de servicio."),
    ])
    a = client.get("/api/v1/incidencias").json()["items"][0]
    assert a["scope"] == "line"                    # alcance oficial intacto
    assert [m["name"] for m in a["mentioned_stations"]] == ["Getafe Centro"]
    assert a["mentioned_stations"][0]["inferred_from"] == "texto"
    rel = lambda st: client.get("/api/v1/incidencias", params={  # noqa: E731
        "estacion": st}).json()["items"][0]["relevance"]
    assert rel("cer:10000") == "line_mentions_station"
    assert rel("cer:17000") == "line_other_station"


@pytest.mark.integration
def test_route_ids_missing_from_gtfs_resolved_by_format(client, scenario):
    _load(scenario, [_alert("RG", [{"routeId": "51T0245R1"}], "Afectación R1")])
    a = client.get("/api/v1/incidencias").json()["items"][0]
    assert a["unmatched"]["routes"] == ["51T0245R1"]
    assert a["lines"][0]["label"] == "R1 · Rodalies de Catalunya"
    assert a["lines"][0]["status"] == "route_id_format"
    assert a["nucleos"][0]["slug"] == "rodalies-catalunya"
