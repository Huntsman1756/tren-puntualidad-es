"""Respuestas WAHA anonimizadas (ids y códigos ficticios, textos reales).

Formas observadas/documentadas:
- PREVIEW: GET /api/{s}/channels/{invite}/messages/preview devuelve una lista
  de {reactions, viewCount, message: {...}} — el mensaje real va anidado.
- PLANO: GET /api/{s}/chats/{id}/messages y el evento 'message' devuelven el
  mensaje en el nivel superior ({id, timestamp, body, hasMedia, ...}).
- Los timestamp pueden llegar en segundos o milisegundos.
"""
from fixtures_notices import M2, M3, M4

T_A = 1791505200   # epoch s
T_B = 1791508800


def preview_item(mid, ts, body=None, media=None):
    """Envoltorio documentado de messages/preview (campos opcionales fuera)."""
    msg = {"id": mid, "timestamp": ts}
    if body is not None:
        msg["body"] = body
    if media is not None:
        msg["media"] = media
        msg["hasMedia"] = True
    return {"reactions": {}, "viewCount": 0, "message": msg}


PREVIEW_ANIDADO = [
    preview_item("false_123@newsletter_AAAA", T_A, M2),
    preview_item("false_123@newsletter_BBBB", T_B, M3),
]

PREVIEW_OPCIONALES_AUSENTES = [{"message": {"id": "false_1@newsletter_C",
                                            "timestamp": T_A, "body": M4}}]

PLANO = [
    {"id": "true_999@newsletter_AAAA", "timestamp": T_A,
     "from": "999@newsletter", "fromMe": True, "body": M2,
     "hasMedia": False, "ack": 0, "ackName": "PENDING", "_data": {}},
]

MS = [preview_item("false_1@newsletter_MS", T_A * 1000 + 123, M2)]

MEDIA_SIN_TEXTO = [
    preview_item("false_1@newsletter_IMG", T_A, body=None,
                 media={"mimetype": "image/jpeg", "url": "http://waha/f.jpg"}),
]

SIN_ID = [preview_item(None, T_A, M2)]
SIN_TS = [{"message": {"id": "false_1@newsletter_X", "body": M2}}]

VACIA = []
ESTRUCTURA_RARA = {"ok": True, "result": "unexpected"}
LISTA_CON_BASURA = [None, 42, "texto suelto", {"no": "es un mensaje"}]

LARGO = ("🚊 Línea C-5\n\n🟡 Los trenes con origen y destino en Móstoles, "
         "Alcorcón, Fuenlabrada, Humanes, Leganés y Parla sufren demoras, "
         "detenciones prolongadas y pueden ver modificado su recorrido "
         "habitual por la avería en la infraestructura.\n\n⚠️ Disculpen las "
         "molestias. " * 6)
