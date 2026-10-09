"""Avisos oficiales de Renfe (WhatsApp Channels): parser, hilos e ingesta.

- parse_notice(): función pura. Extrae líneas, estaciones con problema,
  origen/destino de trenes, tipo, efectos, estado y evidencia (regla que
  disparó cada campo). Sin BD.
- process_pending(): interpreta avisos pendientes, resuelve estaciones a
  stop_id del feed CER y los agrupa en hilos.
- run_whatsapp_cycle(): sondeo de canales vía WAHA (desactivado sin WAHA_URL).

Timestamps: epoch en segundos.
"""
import json
import logging
import re
import time
import unicodedata
from urllib.parse import quote

import httpx
from sqlalchemy import text as sql_text

from collector import config as cfg

log = logging.getLogger("collector.notices")

# ---------------------------------------------------------------- parser

KINDS = ("averia_infraestructura", "averia_tren", "incidencia_tercero",
         "meteorologia", "obras", "huelga", "servicio_alterado",
         "reajuste_servicio", "otra")

# orden = prioridad: la causa explícita manda sobre los efectos
_KIND_RULES = [
    ("incidencia_tercero", [
        r"\barrollad\w*", r"\barrollamiento", r"\batropell\w*",
        r"\bpersona (en|sobre) (la )?via", r"\bobjeto (en|sobre) (la )?via",
        r"\binvasion (de )?(la )?via", r"\bvandal\w*", r"\brobo\b",
        r"\bagresi\w*", r"\bintrusi\w*"]),
    ("averia_tren", [
        r"\baveria[^\n]{0,40}\btren\b", r"\btren averiad\w*"]),
    ("averia_infraestructura", [
        r"\b(?:averia|incidencia)[^\n]{0,40}\binfraestructura\b",
        r"\baveria\b"]),
    ("meteorologia", [
        r"\bmeteorolog\w*", r"\bviento", r"\bnieve\b", r"\btemporal\b",
        r"\blluvia", r"\btormenta", r"\bgranizo", r"\binundaci\w*",
        r"\bniebla"]),
    ("huelga", [r"\bhuelga\w*"]),
    ("obras", [r"\bobras\b", r"\btrabajos de (mantenimiento|renovacion|obra|reparacion)"]),
    ("reajuste_servicio", [r"\breajust\w*", r"\brefuerzo", r"\breforzad\w*"]),
    ("servicio_alterado", [
        r"\bservicio (alterado|modificado|reducido)",
        r"\balteracion(es)? (en|del) (el )?servicio",
        r"\bsuprimid\w*", r"\bsupresion\w*"]),
]

_EFFECT_RULES = [
    ("demoras", [r"\bdemoras?\b", r"\bretrasos?\b"]),
    ("detenciones", [r"\bdetencion\w*", r"\bdetenid\w*"]),
    ("recorrido_modificado", [
        r"\brecorrido[^\n]{0,20}\bmodificad\w*",
        r"\bmodificad\w* su recorrido", r"\bmodificar\w* (su )?recorrido"]),
    ("supresiones", [r"\bsuprimid\w*", r"\bsupresion\w*"]),
]

_NORMAL_RE = [
    r"\bcirculacion (ya )?normalizad\w*", r"\bservicio (ya )?normalizad\w*",
    r"\bcon normalidad\b", r"\bnormalidad en la circulacion"]
# subsanada/solucionada/restablecida NO equivale a normalizada: la
# recuperación de frecuencias es gradual
_RECOV_RE = [
    r"\bsubsanad\w*", r"\bsolucionad\w*", r"\brestablecid\w*", r"\bresuelt\w*",
    r"\brecuperando\b", r"\bpaulatin\w*", r"\bprogresiv\w*"]

_PROBLEM_LINE_RE = re.compile(
    r"averia|incidencia|obras|vandal|arrollad|arrollamiento|persona (en|sobre)"
    r"|suprimid|huelga")
# corta un nombre de estación/destino donde empieza otra cláusula
_CUT_RE = re.compile(
    r"[,;:.!()\n]|\s+y\s|\s+(?:a las|a la|y destino|efectu|con destino|por|"
    r"debido|aproximadamente)\b")
_LINE_NORD_RE = re.compile(r"\b(R)-?(\d{1,2})[ -]+(Nord|Sur)\b")
_LINE_RE = re.compile(r"\b(RG|C|R|S|T|A)-(\d{1,2})([ab])?\b")


def _fold(s: str) -> str:
    """Quita acentos conservando la longitud: los índices valen para el original."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in s)


def _seg(raw_line: str, low_line: str, start: int) -> tuple[str, str]:
    """Trozo de texto desde `start` hasta la siguiente cláusula (original, minúsculas)."""
    m = _CUT_RE.search(low_line, start)
    end = m.start() if m else len(low_line)
    return raw_line[start:end].strip(), low_line[start:end].strip()


def _parse_lines(fold: str) -> tuple[list, list]:
    """Códigos normalizados (C-4b->C4b, C-5->C5, R2 Nord->R2N) y fragmentos."""
    codes, ev, spans = [], [], []
    for m in _LINE_NORD_RE.finditer(fold):
        code = f"R{int(m.group(2))}{'N' if m.group(3) == 'Nord' else 'S'}"
        spans.append(m.span())
        codes.append(code)
        ev.append(m.group(0))
    for m in _LINE_RE.finditer(fold):
        if any(a <= m.start() < b for a, b in spans):
            continue
        code = f"{m.group(1)}{int(m.group(2))}{(m.group(3) or '').lower()}"
        codes.append(code)
        ev.append(m.group(0))
    out, seen = [], set()
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out, ev


def _parse_places(raw: str, fold: str) -> tuple[list, list, list, list]:
    """(estaciones con problema, origen/destino, ev_estaciones, ev_mencion)."""
    stations, mentions, ev_st, ev_m = [], [], [], []
    seen_st, seen_m = set(), set()
    for rl, fl in zip(raw.split("\n"), fold.split("\n"), strict=True):
        ll = fl.lower()
        # "estación de X" = problema, salvo "salida de la estación de X" (origen)
        for m in re.finditer(r"estacion de ", ll):
            if re.search(r"salida (de )?(la )?$", ll[:m.start()]):
                continue
            name, nlow = _seg(rl, ll, m.end())
            if nlow and nlow not in seen_st:
                seen_st.add(nlow)
                stations.append(name)
                ev_st.append([name, "estacion de"])
        # "en X" tras avería/incidencia (sin "la/el", que son infraestructura)
        if _PROBLEM_LINE_RE.search(ll):
            m = re.search(r"(?:[Aa]veria|[Ii]ncidencia)\b[^\n]*?\ben ([A-Z][^\n,.;:!()]*)", fl)
            if m:
                name, nlow = _seg(rl, ll, m.start(1))
                if nlow and nlow not in seen_st:
                    seen_st.add(nlow)
                    stations.append(name)
                    ev_st.append([name, "en X tras averia"])
        # origen: "salida de X" (o "salida de la estación de X")
        for m in re.finditer(r"salida de ", ll):
            name, nlow = _seg(rl, ll, m.end())
            if nlow.startswith("la estacion de ") or nlow.startswith("estacion de "):
                cut = nlow.index("de ") + 3
                name, nlow = name[cut:].strip(), nlow[cut:].strip()
            elif nlow.startswith("la estacion") or nlow.startswith("estacion"):
                continue  # "salida de la estación a las 09:57h": no es origen
            if nlow and ("origen", nlow) not in seen_m:
                seen_m.add(("origen", nlow))
                mentions.append({"name": name, "rol": "origen"})
                ev_m.append([name, "salida de"])
        # destino: "destino X"
        for m in re.finditer(r"destino (?:a )?", ll):
            name, nlow = _seg(rl, ll, m.end())
            if nlow and ("destino", nlow) not in seen_m:
                seen_m.add(("destino", nlow))
                mentions.append({"name": name, "rol": "destino"})
                ev_m.append([name, "destino"])
    return stations, mentions, ev_st, ev_m


def _first_match(low: str, patterns: list) -> str | None:
    for p in patterns:
        m = re.search(p, low)
        if m:
            return m.group(0).strip()
    return None


def parse_notice(text: str, nucleo_code: str | None) -> dict:
    """Interpreta un aviso oficial. Pura: no toca la BD.

    Devuelve lines, stations (problema), mentions (origen/destino de trenes),
    kind, effects, salida_hora, is_update, status, nucleo_code y evidence
    (regla o palabra que disparó cada campo)."""
    raw = text or ""
    fold = _fold(raw)
    low = fold.lower()
    ev: dict = {}

    lines, ev["lines"] = _parse_lines(fold)
    stations, mentions, ev["stations"], ev["mentions"] = _parse_places(raw, fold)

    # tipo: primera regla (por prioridad) que casa
    kind, kind_kw = "otra", None
    for k, patterns in _KIND_RULES:
        kw = _first_match(low, patterns)
        if kw:
            kind, kind_kw = k, kw
            break
    ev["kind"] = kind_kw

    # efectos: cada uno con su palabra; salida retrasada con hora
    effects, ev_eff = [], {}
    for code, patterns in _EFFECT_RULES:
        kw = _first_match(low, patterns)
        if kw:
            effects.append(code)
            ev_eff[code] = kw
    salida_hora = None
    m = re.search(r"\bsalida\b[^\n]*?\ba las (\d{1,2}[:.]\d{2}\s*h?)", low)
    if m:
        salida_hora = raw[m.start(1):m.end(1)].replace(" ", "")
        effects.append("salida_retrasada")
        ev_eff["salida_retrasada"] = m.group(0).strip()
    ev["effects"] = ev_eff

    m = re.search(r"\bactualizacion\b", low)
    is_update = m is not None
    ev["is_update"] = m.group(0) if m else None

    # estado: normalizada (explícita) > en_recuperacion > activa
    status, status_kw = "activa", None
    kw = _first_match(low, _NORMAL_RE)
    if kw:
        status, status_kw = "normalizada", kw
    else:
        kw = _first_match(low, _RECOV_RE)
        if kw:
            status, status_kw = "en_recuperacion", kw
    ev["status"] = status_kw

    return {
        "lines": lines,
        "stations": stations,
        "mentions": mentions,
        "kind": kind,
        "effects": effects,
        "salida_hora": salida_hora,
        "is_update": is_update,
        "status": status,
        "nucleo_code": nucleo_code,
        "evidence": ev,
    }


# ------------------------------------------------ resolución e hilos

def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", _fold(s).lower()).strip()


def _nucleo_stops(conn, nucleo: str, cache: dict) -> list:
    """(stop_id, nombre normalizado) de paradas CER servidas por rutas del núcleo."""
    if nucleo not in cache:
        rows = conn.execute(sql_text("""
            SELECT s.stop_id, s.name FROM stops s
            WHERE s.feed = 'cer' AND EXISTS (
                SELECT 1 FROM stop_times st
                JOIN trips t ON t.feed = st.feed AND t.trip_id = st.trip_id
                JOIN line_route lr ON lr.feed = t.feed AND lr.route_id = t.route_id
                WHERE st.feed = s.feed AND st.stop_id = s.stop_id
                  AND lr.nucleo_code = :n)"""), {"n": nucleo}).all()
        cache[nucleo] = [(r.stop_id, _norm_name(r.name)) for r in rows]
    return cache[nucleo]


def _resolve_station(conn, nucleo: str | None, name: str, cache: dict) -> str | None:
    """stop_id si el nombre identifica una única parada del núcleo; si no, None.

    Prioriza igualdad exacta; si no, prefijo en límite de palabra."""
    if not nucleo:
        return None
    target = _norm_name(name)
    stops = _nucleo_stops(conn, nucleo, cache)
    exact = {sid for sid, nm in stops if nm == target}
    if exact:
        return next(iter(exact)) if len(exact) == 1 else None
    pref = re.compile(re.escape(target) + r"(?![a-z0-9])")
    cands = {sid for sid, nm in stops if pref.match(nm)}
    return next(iter(cands)) if len(cands) == 1 else None


def _recent_threads(conn, channel: str, posted_at: int, exclude_id: int) -> list:
    """Hilos del canal con avisos dentro de NOTICE_THREAD_SEC, abiertos o no.

    Se incluyen los cerrados porque un aviso capturado TARDE (posted_at
    anterior al último del hilo) pertenece a ese hilo: no debe abrir una
    incidencia paralela que parezca vigente. Cada hilo lleva el estado y
    la fecha de su mensaje más reciente."""
    rows = conn.execute(sql_text("""
        SELECT id, thread_id, posted_at, status, kind, lines, stations
        FROM official_notice
        WHERE channel = :c AND thread_id IS NOT NULL AND id <> :id
          AND posted_at >= :min
        ORDER BY posted_at DESC, id DESC"""),
        {"c": channel, "id": exclude_id,
         "min": posted_at - cfg.NOTICE_THREAD_SEC}).mappings().all()
    threads: dict = {}
    for r in rows:
        t = threads.get(r["thread_id"])
        if t is None:  # primera fila = aviso más reciente del hilo
            t = threads[r["thread_id"]] = {
                "thread_id": r["thread_id"], "status": r["status"],
                "kind": r["kind"], "newest_posted": r["posted_at"],
                "lines": set(), "stations": set()}
        t["lines"].update(r["lines"] or [])
        t["stations"].update(_norm_name(s.get("name", "")) for s in (r["stations"] or []))
    # sin_actualizar sigue siendo hilo abierto: una novedad tardía se une
    open_st = ("activa", "en_recuperacion", "sin_actualizar")
    for t in threads.values():
        t["open"] = t["status"] in open_st
    return list(threads.values())


def _find_thread(threads: list, lines: list, station_names: list,
                 kind: str, posted_at: int) -> tuple[int | None, bool]:
    """(thread_id, ambiguo) del hilo al que unir el aviso.

    Reglas: comparte línea y estación con problema (o, sin estación, el
    mismo tipo). Un aviso tardío se une aunque el hilo ya esté cerrado
    (posted_at <= último del hilo); si es posterior al cierre es una
    incidencia nueva. Más de un candidato = ambiguo: no se adivina."""
    wanted_st = {_norm_name(n) for n in station_names}
    open_c, closed_c = [], []
    for t in threads:
        if not set(lines) & t["lines"]:
            continue
        if wanted_st:
            if not wanted_st & t["stations"]:
                continue
        elif kind == "otra" or t["kind"] != kind:
            continue
        (open_c if t["open"] else closed_c).append(t)
    if len(open_c) == 1:
        return open_c[0]["thread_id"], False
    if len(open_c) > 1:
        return None, True
    if len(closed_c) == 1 and posted_at <= closed_c[0]["newest_posted"]:
        return closed_c[0]["thread_id"], False
    if len(closed_c) > 1:
        return None, True
    return None, False


def _mark_stale(conn, now: int) -> int:
    """Último aviso de hilo 'activa' sin novedad en NOTICE_STALE_SEC ->
    sin_actualizar (no se presume resuelto)."""
    res = conn.execute(sql_text("""
        UPDATE official_notice o SET status = 'sin_actualizar'
        WHERE o.status = 'activa' AND o.thread_id IS NOT NULL
          AND o.posted_at < :cut
          AND NOT EXISTS (SELECT 1 FROM official_notice n
                          WHERE n.thread_id = o.thread_id
                            AND n.posted_at > o.posted_at)"""),
        {"cut": now - cfg.NOTICE_STALE_SEC})
    return res.rowcount or 0


def process_pending(conn, now: int) -> dict:
    """Interpreta avisos pendientes (manual o WhatsApp sin parse) y los agrupa.

    now: epoch en segundos. Usar dentro de la transacción del llamador."""
    rows = conn.execute(sql_text("""
        SELECT id, channel, posted_at, text, nucleo_code FROM official_notice
        WHERE status = 'pendiente' OR parse IS NULL
        ORDER BY posted_at, id""")).mappings().all()
    cache: dict = {}
    stats = {"processed": 0, "attached": 0, "new_threads": 0, "stale": 0,
             "ambiguous": 0, "unclassified": 0}
    for r in rows:
        p = parse_notice(r["text"], r["nucleo_code"])
        stations = []
        for name in p["stations"]:
            sid = _resolve_station(conn, r["nucleo_code"], name, cache)
            stations.append({"name": name, "stop_id": sid})
        # hilo: se busca entre los recientes del canal (abiertos o no)
        tid, ambiguous = _find_thread(
            _recent_threads(conn, r["channel"], r["posted_at"], r["id"]),
            p["lines"], p["stations"], p["kind"], r["posted_at"])
        if ambiguous:
            p["ambiguous"] = True
        conn.execute(sql_text("""
            UPDATE official_notice SET
                lines = CAST(:lines AS JSONB), stations = CAST(:st AS JSONB),
                kind = :kind, status = :status, is_update = :upd,
                parse = CAST(:parse AS JSONB),
                thread_id = CASE WHEN :amb THEN NULL
                                 ELSE COALESCE(CAST(:tid AS BIGINT), id) END
            WHERE id = :id"""),
            {"id": r["id"], "tid": tid, "amb": ambiguous,
             "lines": _json(p["lines"]),
             "st": _json(stations), "kind": p["kind"], "status": p["status"],
             "upd": 1 if p["is_update"] else 0,
             "parse": _json(p)})
        stats["processed"] += 1
        stats["attached" if tid else "new_threads"] += 1
        stats["ambiguous"] += 1 if ambiguous else 0
        if p["kind"] == "otra" and not p["lines"] and not p["stations"]:
            stats["unclassified"] += 1
    stats["stale"] = _mark_stale(conn, now)
    return stats


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ------------------------------------------------ WAHA (WhatsApp HTTP API)

def _parse_channels(spec: str) -> list:
    """'invite:slug:nucleo,...' -> [(invite, slug, nucleo)]. Ignora entradas mal formadas."""
    out = []
    for item in (spec or "").split(","):
        if not item.strip():
            continue
        parts = [p.strip() for p in item.split(":")]
        if len(parts) != 3 or not all(parts):
            log.warning("WAHA_CHANNELS: entrada mal formada %r", item)
            continue
        out.append(tuple(parts))
    return out


def _messages(payload):
    """(items, error) de la respuesta de WAHA.

    items=None y error='estructura_desconocida' si la forma no es la
    documentada: lista plana o dict con 'messages'/'data' lista. Una lista
    vacía es una respuesta válida (sin mensajes en la ventana), no un error."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        for k in ("messages", "data"):
            if isinstance(payload.get(k), list):
                return payload[k], None
    return None, "estructura_desconocida"


def _msg_fields(m):
    """(external_id, posted_at epoch s, body, descarte).

    Acepta el envoltorio documentado de vista previa de WAHA
    ({reactions, viewCount, message: {...}}) y el mensaje plano que usa
    /chats/{id}/messages o el evento 'message' ({id, timestamp, body}).
    descarte != None explica por qué no se ingiere: no_objeto,
    envoltorio_invalido, sin_id, sin_texto (multimedia sin pie) o
    sin_timestamp."""
    if not isinstance(m, dict):
        return None, None, None, "no_objeto"
    inner = m.get("message")
    if inner is not None:
        if not isinstance(inner, dict):
            return None, None, None, "envoltorio_invalido"
        m = inner
    key = m.get("key") if isinstance(m.get("key"), dict) else {}
    mid = m.get("id") or key.get("id")
    if isinstance(mid, dict):
        mid = mid.get("_serialized") or mid.get("id")
    if not mid:
        return None, None, None, "sin_id"
    body = m.get("body")
    if body is None:
        body = m.get("text")
    if body is None or not str(body).strip():
        return None, None, None, "sin_texto"
    ts = m.get("timestamp")
    if ts is None:
        ts = m.get("t")
    try:
        ts = int(float(ts))
    except (TypeError, ValueError):
        return None, None, None, "sin_timestamp"
    if ts > 10**11:  # milisegundos
        ts //= 1000
    return str(mid), ts, str(body), None


def _fetch_channel(cli: httpx.Client, invite: str):
    url = (f"{cfg.WAHA_URL}/api/{quote(cfg.WAHA_SESSION, safe='')}"
           f"/channels/{quote(invite, safe='')}/messages/preview")
    headers = {"X-Api-Key": cfg.WAHA_API_KEY} if cfg.WAHA_API_KEY else {}
    r = cli.get(url, params={"downloadMedia": "false", "limit": 100}, headers=headers)
    r.raise_for_status()
    return r.json()


def _fetch_session_status(cli: httpx.Client) -> str:
    """Estado de la sesión WAHA ('WORKING', …); 'error' si no se puede saber."""
    headers = {"X-Api-Key": cfg.WAHA_API_KEY} if cfg.WAHA_API_KEY else {}
    try:
        r = cli.get(f"{cfg.WAHA_URL}/api/sessions/{quote(cfg.WAHA_SESSION, safe='')}",
                    headers=headers)
        r.raise_for_status()
        data = r.json()
        st = data.get("status") if isinstance(data, dict) else None
        return str(st) if st else "desconocido"
    except (httpx.HTTPError, ValueError):
        return "error"


_last_poll = 0.0  # monotonic; 0 = nunca


def _stats_key(slug: str) -> str:
    return f"whatsapp_stats_{slug}"


def _merge_stats(conn, slug: str, add: dict) -> dict:
    """Acumula contadores del canal en meta (JSON). Devuelve el total."""
    import json as _j

    from collector import db
    prev = {}
    raw = db.get_meta(conn, _stats_key(slug))
    if raw:
        try:
            prev = _j.loads(raw)
        except ValueError:
            prev = {}
    out = dict(prev)
    for k, v in add.items():
        if isinstance(v, dict):
            out[k] = {kk: int((out.get(k) or {}).get(kk, 0)) + int(vv)
                      for kk, vv in v.items()}
        elif isinstance(v, (int, float)):
            out[k] = int(out.get(k, 0)) + int(v)
        else:
            out[k] = v
    db.set_meta(conn, _stats_key(slug), _j.dumps(out, ensure_ascii=False))
    return out


def run_whatsapp_cycle() -> int:
    """Sondea los canales WAHA, inserta mensajes nuevos y los procesa.

    Devuelve nº de avisos nuevos. Sin WAHA_URL no hace nada (0). Respeta
    POLL_WAHA entre sondeos (el loop del collector llama cada 60 s).
    Nunca lanza: cada problema queda en meta como degradación de ESTA
    fuente, no del sistema."""
    from collector import db  # engine compartido con el collector

    if not cfg.WAHA_URL:
        return 0
    channels = _parse_channels(cfg.WAHA_CHANNELS)
    if not channels:
        return 0
    global _last_poll
    if cfg.POLL_WAHA > 0 and _last_poll and (time.monotonic() - _last_poll) < cfg.POLL_WAHA:
        return 0
    _last_poll = time.monotonic()
    now = int(time.time())
    fetched: dict = {}
    errors: dict = {}
    degraded: dict = {}
    session_status = "error"
    with httpx.Client(timeout=20) as cli:
        session_status = _fetch_session_status(cli)
        for invite, slug, nucleo in channels:
            try:
                payload = _fetch_channel(cli, invite)
                msgs, err = _messages(payload)
                if err:
                    degraded[slug] = err
                    log.warning("WAHA %s: respuesta con estructura no reconocida", slug)
                    continue
                fetched[(slug, nucleo)] = msgs
            except (httpx.HTTPError, ValueError) as e:
                log.warning("WAHA %s: fallo al leer canal (%s)", slug, e)
                errors[slug] = str(e)[:120]

    inserted = 0
    with db.engine.begin() as conn:
        db.set_meta(conn, "whatsapp_session_status", session_status)
        if session_status != "WORKING":
            db.set_meta(conn, "whatsapp_session_degraded", now)
            log.warning("WAHA: sesión %s (estado %s)", cfg.WAHA_SESSION, session_status)
        for slug, err in errors.items():
            db.set_meta(conn, f"whatsapp_fetch_err_{slug}", now)
            db.set_meta(conn, f"whatsapp_fetch_errmsg_{slug}", err)
            _merge_stats(conn, slug, {"fetch_errors": 1})
        for slug, reason in degraded.items():
            db.set_meta(conn, f"whatsapp_fetch_ok_{slug}", now)
            db.set_meta(conn, f"whatsapp_degraded_{slug}", reason)
            _merge_stats(conn, slug, {"degraded": 1})
        for (slug, nucleo), msgs in fetched.items():
            db.set_meta(conn, f"whatsapp_fetch_ok_{slug}", now)
            prev_newest = db.get_meta(conn, f"whatsapp_last_msg_{slug}")
            prev_newest = int(prev_newest) if prev_newest else 0
            stats = {"received": len(msgs), "discarded": {}}
            newest, late = prev_newest, 0
            for m in msgs:
                ext, posted, body, why = _msg_fields(m)
                if why:
                    stats["discarded"][why] = stats["discarded"].get(why, 0) + 1
                    continue
                row = conn.execute(sql_text("""
                    INSERT INTO official_notice (source, channel, external_id,
                        posted_at, received_at, text, nucleo_code, status,
                        is_update, verified)
                    VALUES ('whatsapp', :ch, :ext, :posted, :recv, :text, :nuc,
                            'pendiente', 0, TRUE)
                    ON CONFLICT (source, channel, external_id) DO NOTHING
                    RETURNING id"""),
                    {"ch": slug, "ext": ext, "posted": posted, "recv": now,
                     "text": body, "nuc": nucleo}).first()
                newest = max(newest, posted)
                if row is None:
                    stats["duplicated"] = stats.get("duplicated", 0) + 1
                    continue
                inserted += 1
                stats["inserted"] = stats.get("inserted", 0) + 1
                stats["latency_max"] = max(stats.get("latency_max", 0), now - posted)
                if posted < prev_newest:
                    late += 1
            if late:
                stats["late"] = late
            if newest:
                db.set_meta(conn, f"whatsapp_last_msg_{slug}", newest)
            # HTTP correcto pero nada interpretable = degradación, no captura sana
            usable = stats.get("inserted", 0) + stats.get("duplicated", 0)
            if stats["received"] and not usable:
                db.set_meta(conn, f"whatsapp_degraded_{slug}", "sin_mensajes_interpretables")
                stats["degraded"] = 1
                log.warning("WAHA %s: %d mensajes recibidos, ninguno interpretable",
                            slug, stats["received"])
            _merge_stats(conn, slug, stats)
        res = process_pending(conn, now)
        if res.get("unclassified"):
            _merge_stats(conn, "_all", {"unclassified": res["unclassified"]})
    return inserted

