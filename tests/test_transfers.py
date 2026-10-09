"""Planificador con un transbordo: enlaces verificados, slack mínimo,
cambio de día, tiempo real y exclusión de conexiones imposibles."""

import time
from datetime import timedelta

import pytest
from dbfix import TODAY, epoch
from sqlalchemy import text

pytestmark = pytest.mark.integration

D1 = TODAY + timedelta(days=1)
D2 = TODAY + timedelta(days=2)


def plan(client, **p):
    p.setdefault("from", "cer:18000")
    p.setdefault("to", "ld:71801")
    r = client.get("/api/v1/journeys/plan", params=p)
    assert r.status_code == 200, r.text
    return r.json()


def test_cross_network_transfer_same_station(client):
    """cer:18000→ld:71801 no tiene directo; el enlace misma-estación en
    17000 (cer→ld) produce transfer_only con los dos tramos."""
    j = plan(client, date=str(D1))
    assert j["status"] == "transfer_only"
    assert j["transfers_supported"] is True
    x = next(i for i in j["transfers"] if i["leg2"]["trip_id"] == "LD_03100")
    assert x["leg1"]["trip_id"] == "MAD_C4B_0755"
    assert x["transfer"]["at_stop"] == "17000"
    assert x["transfer"]["kind"] == "same_station"
    assert x["transfer"]["slack_sec"] == 900  # cer→ld
    assert x["leg2"]["dep_scheduled"] == epoch(D1, 9 * 3600)
    assert x["leg2"]["arr_scheduled"] == epoch(D1, 12 * 3600)
    assert x["leg1"]["dep_scheduled"] == epoch(D1, 7 * 3600 + 3300)
    # todo programado: nada de RT en fecha ≠ hoy
    assert j["scheduled_only"] is True
    assert x["leg1"]["realtime"] is False and x["leg2"]["realtime"] is False


def test_walking_link_between_stations(client):
    """El enlace curado cer:17000→ld:60000 (test_walk) habilita el AVE
    que sale del edificio AV a las 09:00."""
    j = plan(client, date=str(D1))
    x = next(i for i in j["transfers"] if i["leg2"]["trip_id"] == "LD_03110")
    assert x["transfer"]["to_stop"] == "60000"
    assert x["transfer"]["kind"] == "walk"
    assert x["transfer"]["slack_sec"] == 900
    assert x["leg2"]["arr_scheduled"] == epoch(D1, 12 * 3600)


def test_min_slack_excludes_impossible(client, scenario):
    """Un segundo tramo que sale antes de arr1+slack no se ofrece."""
    from sqlalchemy import text

    with scenario.begin() as c:
        # LD casi imposible: sale de ld:17000 6 min después de que el C4b
        # llegue a cer:17000 (slack misma estación cer→ld = 15 min).
        # Desde el origen cer:18000 no hay enlace a ld:17000, así que este
        # tren es solo alcanzable como tramo 2 -> nunca puede aparecer.
        c.execute(
            text("""INSERT INTO trips VALUES
            ('ld','LD_TIGHT','LD_AVE_MAD_BCN','S_ALL','x','03120',NULL)""")
        )
        c.execute(
            text("""INSERT INTO stop_times VALUES
            ('ld','LD_TIGHT',1,'17000',:d,:d),
            ('ld','LD_TIGHT',2,'71801',:a,:a)"""),
            {"d": 8 * 3600 + 900 + 360, "a": 11 * 3600},
        )
    j = plan(client, date=str(D1))
    assert j["transfers"]  # hay opciones válidas
    assert all((i["leg2"] or {}).get("trip_id") != "LD_TIGHT" for i in j["transfers"])


def test_direction_matters(client):
    """El sentido inverso (Sants→origen) no inventa transbordos."""
    j = plan(client, **{"from": "ld:71801", "to": "cer:18000"}, date=str(D1))
    assert j["status"] in ("needs_transfer", "different_networks")
    assert j["transfers"] == []


def test_no_verified_link_no_transfer(client):
    """Asturias→Madrid: sin enlace verificable no se inventa conexión."""
    j = plan(client, **{"from": "cer:15211", "to": "cer:17000"}, date=str(D1))
    assert j["status"] == "needs_transfer"
    assert j["transfers"] == []


def test_risky_when_rt_breaks_slack(client, scenario):
    """Hoy: si el RT estima buffer < slack se etiqueta 'risky', no se oculta."""
    now = int(time.time())
    with scenario.begin() as c:
        # MAD_C4B_0755 lleva 40 min de retraso estimado al llegar al enlace:
        # arr est. 08:55 vs dep2 09:00 -> buffer_rt 5 min < slack 15 min
        c.execute(
            text("""INSERT INTO rt_stop_update
            (feed,trip_id,stop_id,delay,time,updated_at)
            VALUES('cer','MAD_C4B_0755','17000',2400,:t,:t)"""),
            {"t": now},
        )
    j = plan(client, date=str(TODAY), time="07:00", hours=2)
    xr = [i for i in j["transfers"] if (i["leg1"] or {}).get("trip_id") == "MAD_C4B_0755"]
    assert xr, "debe seguir ofreciendo la conexión"
    assert all(i["risk"] == "risky" for i in xr)
    assert xr[0]["transfer"]["buffer_rt_sec"] < xr[0]["transfer"]["slack_sec"]


def test_transfer_next_day_leg2(client, scenario):
    """T1 nocturno + T2 de la madrugada siguiente: el día del tramo 2 puede
    ser day+1 y el dep_eff lo refleja."""
    from sqlalchemy import text

    with scenario.begin() as c:
        c.execute(
            text("""INSERT INTO trips VALUES
            ('cer','MAD_NIGHT','10T0001C1','S_ALL','x','21005',NULL)""")
        )
        c.execute(
            text("""INSERT INTO stop_times VALUES
            ('cer','MAD_NIGHT',1,'18000',:d,:d),
            ('cer','MAD_NIGHT',2,'17000',:a,:a)"""),
            {"d": 23 * 3600 + 600, "a": 23 * 3600 + 2400},
        )
        c.execute(
            text("""INSERT INTO trips VALUES
            ('ld','LD_MAD_BCN_00','LD_AVE_MAD_BCN','S_ALL','x','03199',NULL)""")
        )
        c.execute(
            text("""INSERT INTO stop_times VALUES
            ('ld','LD_MAD_BCN_00',1,'17000',:d,:d),
            ('ld','LD_MAD_BCN_00',2,'71801',:a,:a)"""),
            {"d": 25 * 3600 + 300, "a": 28 * 3600},
        )  # 01:25 del día de servicio
    j = plan(client, date=str(D1), time="23:00", hours=2)
    x = next(i for i in j["transfers"] if i["leg2"]["trip_id"] == "LD_MAD_BCN_00")
    assert x["leg1"]["dep_scheduled"] == epoch(D1, 23 * 3600 + 600)
    # LD_03110 arr 12:00 del mismo día también aparece; el nocturno llega a las 04:00 D+1
    assert x["leg2"]["arr_scheduled"] == epoch(D1, 28 * 3600)
    assert x["leg2"]["service_date"] == str(D1)


def test_direct_still_wins_and_lists_transfers(client):
    """Con directo disponible, status=ok y los transbordos se adjuntan."""
    j = plan(client, **{"from": "cer:17000", "to": "cer:18000"}, date=str(D1))
    assert j["status"] == "ok"
    assert j["items"], "directo C1 17000→18000"
    # aun así puede haber opciones con transbordo (ninguna mejor aquí)
    assert isinstance(j["transfers"], list)


def test_transfers_disabled_param(client):
    j = plan(client, date=str(D1), transfers="false")
    assert j["transfers"] == []
    assert j["status"] in ("needs_transfer", "different_networks", "no_direct_window")
