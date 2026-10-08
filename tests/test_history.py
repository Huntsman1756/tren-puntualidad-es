"""Tests del modelo histórico v0.3.3: service_date, dedup, instancias."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

from collector.gtfsutil import TZINFO
from collector.realtime import _service_date


def _epoch(dt: str) -> int:
    return int(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=TZINFO).timestamp())


class TestServiceDate:
    """Identidad de circulación = (feed, trip_id, service_date).

    service_date = día en que el servicio GTFS está activo (calendar),
    no el día calendario de la parada — los servicios que cruzan
    medianoche mantienen el service-date de origen."""

    def test_normal_trip(self):
        # dep 10:00 -> service_date = ese día
        assert _service_date(_epoch("2026-10-09 10:00:00"), 36000) == "2026-10-09"

    def test_past_midnight_gtfs(self):
        # dep = 25:30 (=91800s) del día 09 -> parada real el día 10 01:30,
        # pero el service-day sigue siendo el 09
        assert _service_date(_epoch("2026-10-10 01:30:00"), 91800) == "2026-10-09"

    def test_past_midnight_two_days(self):
        # dep = 49:00 -> dos días después del service-day
        assert _service_date(_epoch("2026-10-11 01:00:00"),
                             49 * 3600) == "2026-10-09"

    def test_same_trip_id_two_days_are_distinct(self):
        # mismo trip_id CER en días distintos -> instancias distintas
        d1 = _service_date(_epoch("2026-10-09 08:15:00"), 8 * 3600 + 900)
        d2 = _service_date(_epoch("2026-10-10 08:15:00"), 8 * 3600 + 900)
        assert d1 == "2026-10-09" and d2 == "2026-10-10" and d1 != d2

    def test_missing_dep_is_honest(self):
        # sin dato programado no se inventa el service_date
        assert _service_date(_epoch("2026-10-09 08:00:00"), None) is None
        assert _service_date(None, 36000) is None

    def test_dst_spring_forward(self):
        # 2026-03-29: a las 02:00 -> 03:00 en Madrid (reloj "atrás")
        # un dep 02:30 no existe en realidad; el GTFS usa segundos puros.
        # Comprobamos que no explota y devuelve un día coherente.
        sd = _service_date(_epoch("2026-03-29 03:30:00"), 9000)
        assert sd in ("2026-03-29", "2026-03-28")


class TestFleetDedup:
    """poll_fleet solo escribe si el delay cambia respecto a la última
    observación de la misma instancia — esto impide que un tren quieto
    infle el histórico cada ~45 s."""
    def test_dedup_logic_documented(self):
        # la dedup ocurre en poll_fleet con `last.get(...) != dm*60`
        import inspect

        from collector.realtime import poll_fleet
        src = inspect.getsource(poll_fleet)
        assert "kind='reported'" in src or 'kind="reported"' in src
        assert "service_date" in src
        assert "DISTINCT ON" in src  # hay dedup real por instancia
