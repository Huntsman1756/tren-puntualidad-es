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
    """Hoy: si el RT estima buffer < slack se etiqueta 'risky', no se oculta.
    Requiere RT en AMBOS tramos; con RT solo en uno el estado es 'unknown'."""
    now = int(time.time())
    with scenario.begin() as c:
        # MAD_C4B_0755 llega est. 08:55 (+2400 s); LD_03110 sale de 60000
        # est. 09:01 (+60 s): buffer_rt ~6 min < slack 15 min
        c.execute(
            text("""INSERT INTO rt_stop_update
            (feed,trip_id,stop_id,delay,time,updated_at)
            VALUES('cer','MAD_C4B_0755','17000',2400,:t,:t),
                  ('ld','LD_03110','60000',60,:t,:t)"""),
            {"t": now},
        )
    j = plan(client, date=str(TODAY), time="07:00", hours=2)
    xr = [
        i
        for i in j["transfers"]
        if (i["leg1"] or {}).get("trip_id") == "MAD_C4B_0755"
        and (i["leg2"] or {}).get("trip_id") == "LD_03110"
    ]
    assert xr, "debe seguir ofreciendo la conexión"
    assert all(i["risk"] == "risky" for i in xr)
    assert xr[0]["transfer"]["buffer_rt_sec"] < xr[0]["transfer"]["slack_sec"]


def test_unknown_when_rt_missing_on_a_leg(client, scenario):
    """Hoy sin RT en el segundo tramo: el riesgo es 'unknown', nunca 'ok'."""
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(
            text("""INSERT INTO rt_stop_update
            (feed,trip_id,stop_id,delay,time,updated_at)
            VALUES('cer','MAD_C4B_0755','17000',0,:t,:t)"""),
            {"t": now},
        )
    j = plan(client, date=str(TODAY), time="07:00", hours=2)
    xr = [i for i in j["transfers"] if (i["leg1"] or {}).get("trip_id") == "MAD_C4B_0755" and i["leg2"]]
    assert xr
    # sin RT del segundo tramo no podemos afirmar margen
    assert all(i["risk"] == "unknown" for i in xr)


def test_transfer_next_day_leg2(client, scenario):
    """T1 nocturno + T2 de la madrugada siguiente: el día del tramo 2 puede
    ser day+1 y el dep_eff lo refleja."""
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


def _gt(conn, sql_rows):
    conn.execute(
        text(
            "INSERT INTO gtfs_transfer(feed,from_stop_id,to_stop_id,"
            "from_route_id,to_route_id,from_trip_id,to_trip_id,"
            "transfer_type,min_transfer_time) VALUES " + sql_rows
        )
    )


def test_gtfs_min_transfer_time_raises_slack(client, scenario):
    """transfers.txt type 2 (mínimo obligatorio) eleva el slack: una
    conexión válida por defecto deja de ofrecerse si el GTFS exige más."""
    with scenario.begin() as c:
        # regla a nivel parada: 17000→17000 exige 1 h de cambio
        _gt(c, "('cer','17000','17000','','','','',2,3600)")
    # el C4b llega 08:15 y el C2 sale 09:00 -> 45 min < 60 min: fuera
    j = plan(client, **{"from": "cer:18000", "to": "cer:15211"}, date=str(D1))
    assert all((i["leg2"] or {}).get("trip_id") != "MAD_C2_0900" for i in j["transfers"])


def test_gtfs_type3_forbids_even_with_manual_edge(client, scenario):
    """transfer_type=3 en el nivel aplicable prohíbe la conexión; ningún
    enlace (manual, misma parada ni gtfs) la rescata."""
    with scenario.begin() as c:
        # prohibición route-scoped para el T1 del fixture
        _gt(c, "('cer','17000','17000','10T0013C4a','','','',3,NULL)")
    j = plan(client, **{"from": "cer:18000", "to": "cer:15211"}, date=str(D1))
    assert all((i["leg2"] or {}).get("trip_id") != "MAD_C2_0900" for i in j["transfers"])


def test_gtfs_precedence_route_beats_stop(client, scenario):
    """La regla route-scoped gana a la de parada: un tipo 0/1 con route
    permite lo que una type 2 a nivel parada restringiría."""
    with scenario.begin() as c:
        _gt(c, "('cer','17000','17000','','','','',2,3600)")  # 1 h
        _gt(c, "('cer','17000','17000','10T0013C4a','','','',0,NULL)")  # permite
    j = plan(client, **{"from": "cer:18000", "to": "cer:15211"}, date=str(D1))
    assert any((i["leg2"] or {}).get("trip_id") == "MAD_C2_0900" for i in j["transfers"]), (
        "la regla route-scoped debe prevalecer sobre la de parada"
    )


def test_gtfs_type3_not_applicable_other_route(client, scenario):
    """Un type 3 con from_route ajeno a T1 no prohibe nada."""
    with scenario.begin() as c:
        _gt(c, "('cer','17000','17000','60T0009C3','','','',3,NULL)")
    j = plan(client, **{"from": "cer:18000", "to": "cer:15211"}, date=str(D1))
    assert any((i["leg2"] or {}).get("trip_id") == "MAD_C2_0900" for i in j["transfers"])


def test_plan_dst_boundary_date_no_crash(client):
    """Cambio de hora Europe/Madrid: una fecha fuera de cobertura responde
    limpio; las horas GTFS son nominales (segundos desde medianoche local)
    y el slack se evalúa siempre en ese mismo reloj."""
    j = plan(client, date="2026-10-25")  # domingo DST Europe/Madrid (fall back)
    assert j["status"] == "out_of_coverage"
    j = plan(client, date="2026-03-29")  # DST spring forward
    assert j["status"] == "out_of_coverage"


def test_gtfs_edge_creates_inter_stop_transfer(client, scenario):
    """Un enlace GTFS entre paradas distintas (tipo 0/1/2) genera la
    conexión: T1 llega a 17000, enlace oficial 17000->18000 y T2 sale de
    18000. Sin la arista GTFS ese transbordo no existe."""
    with scenario.begin() as c:
        _gt(c, "('cer','17000','18000','','','','',2,480)")
    j = plan(client, **{"from": "cer:18000", "to": "cer:10000"}, date=str(D1))
    xs = [i for i in j["transfers"] if (i["leg2"] or {}).get("trip_id") == "MAD_C5_0930"]
    assert xs, "el enlace oficial 17000->18000 debe crear la opción"
    assert xs[0]["transfer"]["to_stop"] == "18000"
    assert xs[0]["transfer"]["slack_sec"] == 480


def test_prohibition_is_scoped_to_to_stop(client, scenario):
    """Un type 3 X->18000 no contamina X->15410: misma parada origen,
    destinos distintos, la prohibición solo cierra su propio enlace."""

    with scenario.begin() as c:
        _gt(c, "('cer','17000','18000','','','','',2,480)")
        _gt(c, "('cer','17000','15410','','','','',0,NULL)")
        _gt(c, "('cer','17000','18000','10T0013C4a','','','',3,NULL)")
    # 17000->18000 prohibido para el T1 (regla route-scoped): sin MAD_C5
    j = plan(client, **{"from": "cer:18000", "to": "cer:10000"}, date=str(D1))
    assert all(
        (i["leg2"] or {}).get("trip_id") != "MAD_C5_0930" for i in j["transfers"]
    ), "la prohibición route-scoped a 18000 debe cerrar ese enlace"
    # pero 17000->15410 sigue abierto para el mismo T1
    j = plan(client, **{"from": "cer:18000", "to": "cer:99998"}, date=str(D1))
    assert any(
        (i["leg2"] or {}).get("trip_id") == "MAD_C9_0935" for i in j["transfers"]
    ), "la prohibición X->18000 no debe contaminar X->15410"


def test_walk_origin_respects_requested_time(client):
    """Leg0: la caminata debe empezar dentro de la ventana pedida.
    LD_03110 sale 09:00 con slack 15 min -> caminar desde las 08:45."""
    early = plan(
        client,
        **{"from": "cer:18000", "to": "ld:71801"},
        date=str(TODAY),
        time="08:00",
        hours=2,
    )
    late = plan(
        client,
        **{"from": "cer:18000", "to": "ld:71801"},
        date=str(TODAY),
        time="08:50",
        hours=2,
    )
    leg0_early = [i for i in early["transfers"] if i["leg1"] is None]
    leg0_late = [i for i in late["transfers"] if i["leg1"] is None]
    assert leg0_early, "a las 08:00 debe ofrecerse caminar a las 08:45"
    assert not leg0_late, "a las 08:50 ya no da tiempo: no debe ofrecerse"


def test_order_and_names_on_full_page(client, scenario):
    """Más de 20 candidatos: los 20 devueltos van ordenados por llegada y
    TODOS llevan nombres (enriquecer tras ordenar, nunca antes)."""
    from sqlalchemy import text

    with scenario.begin() as c:
        for i in range(26):  # 26 trenes LD desde 60000, cada 20 min
            tid = f"LD_05{i:02d}"
            dep = 9 * 3600 + i * 1200
            c.execute(
                text(
                    "INSERT INTO trips (feed,trip_id,route_id,service_id,"
                    "train_number) VALUES('ld',:t,'LD_AVE_MAD_BCN','S_ALL',:n)"
                ),
                {"t": tid, "n": f"05{i:02d}"},
            )
            c.execute(
                text(
                    "INSERT INTO stop_times (feed,trip_id,seq,stop_id,arr,dep)"
                    " VALUES('ld',:t,1,'60000',:d,:d),"
                    "('ld',:t,2,'71801',:a,:a)"
                ),
                {"t": tid, "d": dep, "a": dep + 9000},
            )
    j = plan(
        client,
        **{"from": "cer:18000", "to": "ld:71801"},
        date=str(D1),
        hours=12,
    )
    xs = j["transfers"]
    assert len(xs) == 20
    arrs = [i["arr_epoch"] for i in xs]
    assert arrs == sorted(arrs), "ordenados por llegada efectiva"
    assert all(i["transfer"]["to_name"] for i in xs), "todos con nombre"
    # el más rápido es el que llega antes, no el primero generado
    assert xs[0]["arr_epoch"] == min(arrs)
