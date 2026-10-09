"""Catálogo curado de enlaces de transbordo entre estaciones físicas
distintas (o entre feeds en la misma estación con identidad separada).

Reglas de edición:
- Solo enlaces verificados con fuente real (plano de la estación, recorrido
  peatonal existente o política de conexión documentada). Nunca se infieren
  por proximidad geográfica: la distancia es contexto, no evidencia.
- Cada enlace es dirigido: ida y vuelta pueden tener min_secs distintos
  (entrar a un AVE con control de acceso exige más margen que bajar de él).
- `kind`: 'complex' = mismo complejo/intercambiador techado;
          'walk' = a pie por la calle.
- `source`: evidencia textual auditable (qué se verificó y cuándo).
"""

from sqlalchemy import text

# (link_id, from, to, min_secs, kind, label, source)
# from/to = (feed, stop_id)
LINKS = [
    # --- Núcleo Atocha: Cercanías (18000) y Puerta de Atocha AV (60000)
    #     son edificios contiguos del mismo complejo (124 m entre puntos
    #     GTFS). El acceso AV exige control: más margen al subir al AVE.
    (
        "atocha_cer_av",
        ("cer", "18000"),
        ("ld", "60000"),
        15 * 60,
        "complex",
        "Atocha Cercanías → Puerta de Atocha (control de acceso AV)",
        "Complejo Atocha: comunicación interior documentada; control AV",
    ),
    (
        "atocha_av_cer",
        ("ld", "60000"),
        ("cer", "18000"),
        10 * 60,
        "complex",
        "Puerta de Atocha → Atocha Cercanías",
        "Complejo Atocha: comunicación interior documentada",
    ),
    # Delicias (Cercanías Madrid) queda ~690 m de Puerta de Atocha
    (
        "delicias_av",
        ("cer", "18004"),
        ("ld", "60000"),
        15 * 60,
        "walk",
        "Delicias → Puerta de Atocha (a pie ~700 m + control AV)",
        "Estimación: aceras continuas por Pza. del Emperador Carlos V",
    ),
    (
        "av_delicias",
        ("ld", "60000"),
        ("cer", "18004"),
        12 * 60,
        "walk",
        "Puerta de Atocha → Delicias (a pie ~700 m)",
        "Estimación: aceras continuas por Pza. del Emperador Carlos V",
    ),
    # Embajadores ↔ Puerta de Atocha (~1 km; el GTFS CER ya une
    # Embajadores↔Atocha Cercanías con 19 min en transfers.txt)
    (
        "embajadores_av",
        ("cer", "35609"),
        ("ld", "60000"),
        18 * 60,
        "walk",
        "Embajadores → Puerta de Atocha (a pie ~1 km + control AV)",
        "Estimación: recorrido por Rda. de Atocha; coherente con transfers.txt GTFS (1140 s)",
    ),
    (
        "av_embajadores",
        ("ld", "60000"),
        ("cer", "35609"),
        15 * 60,
        "walk",
        "Puerta de Atocha → Embajadores (a pie ~1 km)",
        "Estimación: recorrido por Rda. de Atocha; coherente con transfers.txt GTFS (1140 s)",
    ),
    # --- Málaga: M.Z. Cercanías (54500) y María Zambrano AV (54413) son
    #     vestíbulos contiguos del mismo edificio (134 m entre puntos GTFS)
    (
        "malaga_cer_av",
        ("cer", "54500"),
        ("ld", "54413"),
        15 * 60,
        "complex",
        "M.Z. Cercanías → María Zambrano (control de acceso AV)",
        "Mismo edificio intermodal Málaga-María Zambrano",
    ),
    (
        "malaga_av_cer",
        ("ld", "54413"),
        ("cer", "54500"),
        10 * 60,
        "complex",
        "María Zambrano → M.Z. Cercanías",
        "Mismo edificio intermodal Málaga-María Zambrano",
    ),
    # --- Valencia: Nord (65000) ↔ Joaquín Sorolla AV (03216), ~1 km a pie
    #     (ADIF/Renfe operan además lanzadera; el enlace peatonal existe)
    (
        "valencia_nord_js",
        ("ld", "65000"),
        ("ld", "03216"),
        18 * 60,
        "walk",
        "València Nord → Joaquín Sorolla (a pie ~1 km + control AV)",
        "Lanzadera/peatonal señalizado oficial Renfe-ADIF",
    ),
    (
        "valencia_js_nord",
        ("ld", "03216"),
        ("ld", "65000"),
        15 * 60,
        "walk",
        "Joaquín Sorolla → València Nord (a pie ~1 km)",
        "Lanzadera/peatonal señalizado oficial Renfe-ADIF",
    ),
    # la parada Nord existe también en feed cer (mismo stop_id)
    (
        "valencia_cer_js",
        ("cer", "65000"),
        ("ld", "03216"),
        18 * 60,
        "walk",
        "València Nord → Joaquín Sorolla (a pie ~1 km + control AV)",
        "Lanzadera/peatonal señalizado oficial Renfe-ADIF",
    ),
    (
        "valencia_js_cer",
        ("ld", "03216"),
        ("cer", "65000"),
        15 * 60,
        "walk",
        "Joaquín Sorolla → València Nord (a pie ~1 km)",
        "Lanzadera/peatonal señalizado oficial Renfe-ADIF",
    ),
    # --- Barcelona: Pl. Catalunya (Rodalies, 78805) ↔ Passeig de Gràcia
    #     (MD/LD, 71802), ~590 m por Rambla de Catalunya
    (
        "bcn_cat_pg",
        ("cer", "78805"),
        ("ld", "71802"),
        12 * 60,
        "walk",
        "Plaça de Catalunya → Passeig de Gràcia (a pie ~600 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    (
        "bcn_pg_cat",
        ("ld", "71802"),
        ("cer", "78805"),
        9 * 60,
        "walk",
        "Passeig de Gràcia → Plaça de Catalunya (a pie ~600 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    # Estació de França (79400) ↔ Arc de Triomf (78804), ~900 m
    (
        "bcn_franca_arc",
        ("ld", "79400"),
        ("cer", "78804"),
        13 * 60,
        "walk",
        "Estació de França → Arc de Triomf (a pie ~900 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    (
        "bcn_arc_franca",
        ("cer", "78804"),
        ("ld", "79400"),
        15 * 60,
        "walk",
        "Arc de Triomf → Estació de França (a pie ~900 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    # --- Figueres: estación urbana (79309) ↔ Vilafant AV (04307), ~2 km
    (
        "figueres_vilafant",
        ("cer", "79309"),
        ("ld", "04307"),
        30 * 60,
        "walk",
        "Figueres → Figueres-Vilafant (a pie ~2 km + control AV)",
        "Estimación: ~2 km peatonal; alternativa habitual bus/taxi",
    ),
    (
        "vilafant_figueres",
        ("ld", "04307"),
        ("cer", "79309"),
        27 * 60,
        "walk",
        "Figueres-Vilafant → Figueres (a pie ~2 km)",
        "Estimación: ~2 km peatonal; alternativa habitual bus/taxi",
    ),
    # --- Bilbao: Abando Intermodal (ld 13200) ↔ La Concordia (cer 05451),
    #     176 m enfrente — desde 2025 ambas en el complejo intermodal
    (
        "abando_concordia",
        ("ld", "13200"),
        ("cer", "05451"),
        6 * 60,
        "walk",
        "Abando → La Concordia (a pie ~200 m)",
        "Estaciones enfrentadas en el complejo intermodal",
    ),
    (
        "concordia_abando",
        ("cer", "05451"),
        ("ld", "13200"),
        8 * 60,
        "walk",
        "La Concordia → Abando (a pie ~200 m)",
        "Estaciones enfrentadas en el complejo intermodal",
    ),
    # Zabalburu (cer 13205) ↔ Abando (ld 13200), ~480 m
    (
        "zabalburu_abando",
        ("cer", "13205"),
        ("ld", "13200"),
        9 * 60,
        "walk",
        "Zabalburu → Abando (a pie ~500 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    (
        "abando_zabalburu",
        ("ld", "13200"),
        ("cer", "13205"),
        7 * 60,
        "walk",
        "Abando → Zabalburu (a pie ~500 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    # --- León (15100) ↔ Feve-León (05778): EXCLUIDO 2026-10-09.
    #     La terminal de vía estrecha se integró en la estación intermodal
    #     de León en 2025; el enlace a pie ya no tiene justificación
    #     suficiente. Reevaluar solo si el GTFS sigue sirviendo 05778 y se
    #     verifica la disposición actual del complejo.
    # --- Oviedo: estación principal (ld 15211) ↔ Vallobín (cer 05300),
    #     ~820 m (antigua terminal de vía estrecha)
    (
        "oviedo_vallobin",
        ("ld", "15211"),
        ("cer", "05300"),
        14 * 60,
        "walk",
        "Oviedo → Vallobín (a pie ~850 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    (
        "vallobin_oviedo",
        ("cer", "05300"),
        ("ld", "15211"),
        15 * 60,
        "walk",
        "Vallobín → Oviedo (a pie ~850 m)",
        "Estimación: recorrido peatonal urbano",
    ),
    # --- Vigo: Urzaiz (ld 08223) ↔ Guixar (ld 22308), ~600 m por túnel
    (
        "vigo_urzaiz_guixar",
        ("ld", "08223"),
        ("ld", "22308"),
        12 * 60,
        "walk",
        "Vigo-Urzaiz → Vigo-Guixar (a pie ~600 m)",
        "Estimación: ~600 m entre las dos terminales de Vigo",
    ),
    (
        "vigo_guixar_urzaiz",
        ("ld", "22308"),
        ("ld", "08223"),
        12 * 60,
        "walk",
        "Vigo-Guixar → Vigo-Urzaiz (a pie ~600 m)",
        "Estimación: ~600 m entre las dos terminales de Vigo",
    ),
]


def seed_transfer_links(conn) -> int:
    """Inserta/actualiza el catálogo (idempotente). No borra enlaces que
    hayan desaparecido del catálogo: la retirada se hace explícita."""
    n = 0
    for lid, (ff, fs), (tf, ts), secs, kind, label, source in LINKS:
        conn.execute(
            text("""
            INSERT INTO transfer_link(link_id,from_feed,from_stop_id,to_feed,
                                      to_stop_id,min_secs,kind,label,source)
            VALUES(:i,:ff,:fs,:tf,:ts,:s,:k,:l,:src)
            ON CONFLICT(link_id) DO UPDATE SET
              from_feed=EXCLUDED.from_feed, from_stop_id=EXCLUDED.from_stop_id,
              to_feed=EXCLUDED.to_feed, to_stop_id=EXCLUDED.to_stop_id,
              min_secs=EXCLUDED.min_secs, kind=EXCLUDED.kind,
              label=EXCLUDED.label, source=EXCLUDED.source
        """),
            {
                "i": lid,
                "ff": ff,
                "fs": fs,
                "tf": tf,
                "ts": ts,
                "s": secs,
                "k": kind,
                "l": label,
                "src": source,
            },
        )
        n += 1
    return n
