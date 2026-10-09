"""Diagnóstico de conciliación RT↔static en el feed LD.

Cuantifica, por número comercial, cuántos viajes LD con servicio hoy
casan con rt_trip / rt_stop_update por trip_id EXACTO, y clasifica el
resto: ¿existe una fila RT para el mismo train_number con OTRO trip_id
(identidad resoluble por número + fecha de servicio + secuencia)?

Uso:
  DATABASE_URL=postgresql+psycopg://renfe:pass@host:5432/renfe \
      python scripts/diag_ld_rt.py [YYYY-MM-DD]
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

TZ = ZoneInfo("Europe/Madrid")
DSN = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://renfe:renfe@localhost:5432/renfe"
).replace("+psycopg", "")


def main():
    day = (
        date.fromisoformat(sys.argv[1])
        if len(sys.argv) > 1
        else datetime.now(TZ).date()
    )
    conn = psycopg.connect(DSN)

    # estáticos LD con servicio ese día (instancia = trip_id con servicio)
    stat = conn.execute(
        """SELECT DISTINCT t.trip_id, t.train_number, t.service_id
           FROM trips t JOIN service_days sd
             ON sd.feed='ld' AND sd.service_id=t.service_id AND sd.day=%s
           WHERE t.feed='ld'""",
        (day,),
    ).fetchall()
    rt_trip = {
        r[0] for r in conn.execute(
            "SELECT trip_id FROM rt_trip WHERE feed='ld'"
        ).fetchall()
    }
    rt_su = defaultdict(set)
    for tid, sid in conn.execute(
        "SELECT trip_id, stop_id FROM rt_stop_update WHERE feed='ld'"
    ).fetchall():
        rt_su[tid].add(sid)
    rt_ids = rt_trip | set(rt_su)

    # paradas de cada viaje estático (para casar por secuencia)
    seq = defaultdict(list)
    tids = [t for t, _, _ in stat]
    for tid, sid, _arr in conn.execute(
        "SELECT trip_id, stop_id, arr FROM stop_times "
        "WHERE feed='ld' AND trip_id=ANY(%s) ORDER BY trip_id, seq",
        (tids,),
    ).fetchall():
        seq[tid].append(sid)

    by_tn_stat = defaultdict(set)
    svc = {}
    for tid, tn, s in stat:
        by_tn_stat[tn].add(tid)
        svc[tid] = s

    exact = by_tn = unmatched = 0
    detail = defaultdict(lambda: {"stat": set(), "rt": set()})
    for tid, tn, _ in stat:
        detail[tn]["stat"].add(tid)
        if tid in rt_ids:
            exact += 1
            detail[tn]["rt"].add(tid)
    for tn, tids_ in by_tn_stat.items():
        rt_for_tn = {r for r in rt_ids if r[:5] == tn[:5]}
        if rt_for_tn:
            detail[tn]["rt"] |= rt_for_tn
            if tids_ - rt_ids:
                by_tn += len(tids_ - rt_ids)
        unmatched += len(tids_ - rt_ids)

    print(f"fecha: {day}  viajes LD con servicio: {len(stat)}")
    print(f"rt_trip ld: {len(rt_trip)}  rt_stop_update viajes ld: {len(rt_su)}")
    print(f"casan por trip_id exacto: {exact} ({100*exact/max(1,len(stat)):.0f}%)")
    print(f"sin trip_id exacto pero con RT del mismo número: {by_tn}")
    print(f"total sin match directo: {unmatched}\n")
    print("train  estáticos (día)              rt_ids del número            estado")
    for tn in sorted(detail):
        d = detail[tn]
        st = sorted(d["stat"])
        rt = sorted(d["rt"])
        ok = "OK" if st and all(t in rt for t in st) else (
            "parcial" if set(st) & set(rt) else ("solo-rt" if rt else "sin-rt")
        )
        print(f"{tn:>6} {st!s:<34} {rt!s:<28} {ok}")

    # muestra de secuencia de paradas para 3 casos sin match
    print("\nsecuencia (muestra, casos sin match exacto):")
    shown = 0
    for tid, tn, _ in stat:
        if tid not in rt_ids and shown < 3:
            print(f"  {tid} ({tn}): {'->'.join(seq.get(tid, []))}")
            shown += 1


if __name__ == "__main__":
    main()
