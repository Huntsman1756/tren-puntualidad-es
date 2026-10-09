"""Evaluación read-only del gate de estadísticas contra producción.

NO toca `STATS_PUBLIC`: solo informa si los datos pasarían los gates.
A diferencia de una réplica en el script, **reutiliza las funciones reales
de `api.stats`** (`_window`, `_delay_stats`, `_kind_block`,
`representative_days`, `STATS_GATE`, `_eval_gate`, `_line_units`), así la
evaluación no puede divergir de lo que la API haría al publicar.

Requiere las dependencias de api (requirements-dev o api/requirements).

Uso (desde la raíz del repo; el proyecto usa psycopg 3 → esquema
`postgresql+psycopg://`, no `postgresql://` que cargaría psycopg2):
    DATABASE_URL="postgresql+psycopg://readonly:***@localhost:5433/renfe" \
        python scripts/stats_gate_check.py

Idealmente con un usuario de solo lectura; además la sesión se fuerza a
read-only vía `options` del DSN. Salida: JSON en stdout.
"""

import collections
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

DB_URL = os.environ.get("DATABASE_URL")  # sin default: conexión explícita
if not DB_URL:
    sys.exit("DATABASE_URL requerida (apuntar a producción o a su réplica)")

os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("STATS_PUBLIC", "0")

import api.stats as S  # noqa: E402  (importa api.db, que usa DATABASE_URL)

# sesión forzada read-only + redirige el engine del módulo
S.engine = create_engine(
    DB_URL, connect_args={"options": "-c default_transaction_read_only=on"})


def main():
    d0, d1, h0, h1, today = S._window(None, None, None, None, 90)
    out = {
        "note": "informativo: usa las MISMAS funciones que api/stats.py; "
                "la publicación real la decide STATS_PUBLIC",
        "window": {"from": str(d0), "to": str(d1), "today": str(today)},
        "gate": S.STATS_GATE,
        "representativity": S.REPRESENTATIVITY,
    }

    # ---- niveles descriptivos y comparativos (ámbito global, por kind) ----
    r = S._delay_stats(feed=None, nucleo=None, line=None, ccaa=None,
                       provincia=None, station=None, frm=None, to=None,
                       d0=d0, d1=d1, h0=h0, h1=h1, today=today)
    kinds = {}
    for k in S.KINDS:
        blk = S._kind_block(k, r["numer"][k], r["sched_rows"],
                            r["rep"][k], r["caps"], r["feeds_scope"])
        kinds[k] = {
            "source": blk["source"],
            "capture_since": blk["capture_since"],
            "with_data_representative": blk["with_data"],
            "with_data_all": blk["with_data_all"],
            "scheduled_representative": blk["scheduled_representative"],
            "representative_days": blk["representative_days"],
            "excluded_days": blk["excluded_days"],
            "coverage_pct": blk["coverage_pct"],
            "days_observed": blk["days_observed"],
            "gate_descriptive": blk["gate_descriptive"],
            "gate_comparative": blk["gate_comparative"],
        }
    out["kinds"] = kinds

    # ---- gate diario por día: representativo + obs mínimas + cobertura ----
    daily = collections.defaultdict(dict)
    for x in r["sched_rows"]:
        key = (x["feed"], x["day"])
        daily[key]["feed"] = x["feed"]
        daily[key]["scheduled"] = x["n"]
        for k in S.KINDS:
            rep = r["rep"][k].get(key, {})
            daily[key].setdefault(k, {})["representative"] = rep.get("ok", False)
            daily[key][k]["reasons"] = rep.get("reasons", [])
    for k in S.KINDS:
        for row in r["numer"][k]:
            key = (row["feed"], row["service_date"])
            daily[key][k]["obs"] = daily[key][k].get("obs", 0) + 1
    out["by_day"] = [{"day": str(d), **v}
                     for (f, d), v in sorted(daily.items(),
                                             key=lambda kv: kv[0][1])]

    # ---- comparativa: ¿cuántas unidades superan el gate comparativo? ----
    units_out = []
    # unidades = líneas de cada núcleo CER + unidades LD (igual que
    # stats_compare; para cer _line_units requiere nucleo)
    unit_specs = ([(nuc, u) for nuc in S.NUCLEO_BY_SLUG
                   for u in S._line_units(nuc, "cer")]
                  + [(None, u) for u in S._line_units(None, "ld")])
    for nuc, (val, label) in unit_specs:
        u = S._delay_stats(feed="ld" if nuc is None else "cer",
                           nucleo=nuc, line=val, ccaa=None,
                           provincia=None, station=None, frm=None, to=None,
                           d0=d0, d1=d1, h0=h0, h1=h1, today=today)
        b = S._kind_block("reported", u["numer"]["reported"],
                          u["sched_rows"], u["rep"]["reported"],
                          u["caps"], u["feeds_scope"])
        units_out.append({"unit": val, "label": label,
                          "with_data": b["with_data"],
                          "coverage_pct": b["coverage_pct"],
                          "gate_comparative": b["gate_comparative"]["pass"]})
    eligible = [u for u in units_out if u["gate_comparative"]]
    g = S.STATS_GATE["comparative"]
    out["compare"] = {
        "by": "line",
        "enabled": len(eligible) >= g["min_units"],
        "units_checked": len(units_out),
        "units_passing": len(eligible),
        "min_units": g["min_units"],
        "passing_units": [{"unit": u["unit"], "with_data": u["with_data"],
                           "coverage_pct": u["coverage_pct"]}
                          for u in eligible],
    }

    # ---- sanidad: capture_health existe y tiene filas recientes ----
    with S.engine.connect() as c:
        try:
            n = c.execute(text("SELECT count(*) FROM capture_health")).scalar()
            latest = c.execute(
                text("SELECT max(day) FROM capture_health")).scalar()
            out["capture_health"] = {"rows": n, "latest_day": str(latest)}
        except Exception as e:
            out["capture_health"] = {"error": str(e)[:200]}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main())
