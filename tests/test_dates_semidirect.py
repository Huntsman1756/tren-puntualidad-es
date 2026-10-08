"""Consultas por fecha, cruce de medianoche y detección de semidirectos."""
import datetime

import pytest
from api.main import _haversine_m, _parse_date, _parse_time, _window
from collector.static_load import classify_patterns


class TestParseDateTime:
    def test_date_ok(self):
        assert _parse_date("2026-10-29") == datetime.date(2026, 10, 29)

    def test_date_bad(self):
        with pytest.raises(Exception):
            _parse_date("29/10/2026")
        with pytest.raises(Exception):
            _parse_date("nonsense")

    def test_time_ok(self):
        assert _parse_time("08:30") == 30600

    def test_time_bad(self):
        with pytest.raises(Exception):
            _parse_time("25:99")


class TestWindow:
    def test_defaults_today(self):
        day, lo, hi, is_today = _window(None, None, 60)
        _, secs_now, today = __import__("api.main", fromlist=["_now"])._now()
        assert day == today and is_today
        assert lo == secs_now - 300 and hi == secs_now - 300 + 3600

    def test_future_day_scheduled_only(self):
        day, lo, hi, is_today = _window("2099-01-15", "08:00", 60)
        assert day == datetime.date(2099, 1, 15) and not is_today
        assert lo == 28800 and hi == 28800 + 3600

    def test_future_day_no_time_starts_at_midnight(self):
        _, lo, _, _ = _window("2099-01-15", None, 60)
        assert lo == 0

    def test_past_day_no_rt(self):
        _, _, _, is_today = _window("2020-01-01", None, 60)
        assert not is_today


class TestHaversine:
    def test_same_point(self):
        assert _haversine_m(40.4, -3.68, 40.4, -3.68) == 0

    def test_known_distance(self):
        # Atocha <-> Chamartín ≈ 6,7 km
        d = _haversine_m(40.4066, -3.6894, 40.4720, -3.6823)
        assert 6000 < d < 8000

    def test_none_coords_never_merge(self):
        # sin coords: distancia desconocida => NUNCA fusionar homónimos
        assert _haversine_m(None, None, 40.4, -3.68) is None
        assert _haversine_m(40.4, -3.68, None, -3.68) is None


class TestSemidirect:
    CANON = ("A", "B", "C", "D", "E", "F")

    def _map(self):
        return {
            self.CANON: ["t1", "t2", "t3", "t4", "t5"],          # modal
            ("A", "B", "C", "E", "F"): ["t6"],                   # omite D (1)
            ("A", "C", "E", "F"): ["t7"],                        # omite B,D (2)
            ("A", "F", "B", "C", "D", "E"): ["t9"],              # reordenado
        }

    def test_classification(self):
        r = classify_patterns(self._map())
        assert r["t1"] == (0, 0)                      # modal
        assert r["t6"] == (0, 1)                      # omite 1 -> no es semidirecto
        assert r["t7"] == (1, 2)                      # omite 2 -> semidirecto
        assert r["t9"] == (0, 0)                      # orden distinto -> no subsecuencia

    def test_single_pattern_never_semi(self):
        r = classify_patterns({self.CANON: ["t1"]})
        assert r["t1"] == (0, 0)

    def test_modal_wins_over_longer(self):
        # patrón largo pero raro no debe ganar al modal
        r = classify_patterns({
            self.CANON: ["a", "b", "c", "d"],
            ("A", "X", "B", "C", "D", "E", "F"): ["raro"],
        })
        # "raro" incluye X que no está en el canónico -> no subsecuencia -> 0
        assert r["raro"] == (0, 0)
