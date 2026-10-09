"""Sonda SignalR al servicio ADIF info.adif.es/InfoStation.

Conecta por websocket, se une al topic PRO-ECM-{estacion}, pide el último
mensaje y vuelca el payload JSON a stdout/fichero. Uso:

    python scripts/probe_adif_signalr.py 60000 [segundos]
"""

import asyncio
import json
import sys
import time

import websockets

RS = "\x1e"
URL = "wss://info.adif.es/InfoStation"


async def capture(station: str, seconds: float = 30, out: str | None = None):
    msgs = []
    inv = 0

    def invoke(target: str, *args):
        nonlocal inv
        inv += 1
        return (
            json.dumps(
                {
                    "arguments": list(args),
                    "invocationId": str(inv),
                    "target": target,
                    "type": 1,
                }
            )
            + RS
        )

    async with websockets.connect(
        URL,
        ping_interval=None,
        additional_headers={
            "Origin": "https://www.adif.es",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36",
        },
    ) as ws:
        # handshake SignalR
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        hs = raw.rstrip(RS)
        print("handshake resp:", hs[:200], file=sys.stderr)

        topic = f"PRO-ECM-{station}"
        await ws.send(invoke("JoinInfo", topic))
        await ws.send(invoke("GetLastMessage", topic))

        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=seconds)
            except TimeoutError:
                break
            for frame in raw.split(RS):
                if not frame:
                    continue
                try:
                    m = json.loads(frame)
                except json.JSONDecodeError:
                    continue
                if m.get("type") == 6:  # ping
                    continue
                msgs.append(m)
    return msgs


def main():
    station = sys.argv[1] if len(sys.argv) > 1 else "60000"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    out = sys.argv[3] if len(sys.argv) > 3 else f"pilot_tmp/adif_{station}.json"
    msgs = asyncio.run(capture(station, seconds))
    print(f"{len(msgs)} mensajes capturados", file=sys.stderr)
    payloads = []
    for m in msgs:
        if m.get("type") == 1 and m.get("target") == "ReceiveMessage":
            for a in m.get("arguments") or []:
                payloads.append(a if not isinstance(a, str) else json.loads(a))
        else:
            payloads.append(m)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payloads, f, ensure_ascii=False, indent=1)
    print(f"guardado en {out} ({len(payloads)} payloads)", file=sys.stderr)


if __name__ == "__main__":
    main()
