"""Reglas de notificación push: ventanas, dedup y solo dato confirmado."""
from datetime import datetime
from unittest.mock import MagicMock

from collector.gtfsutil import TZINFO
from collector.push import in_window, pick_alert

NOW = datetime(2026, 10, 8, 8, 15, tzinfo=TZINFO)  # jueves 8:15


class TestInWindow:
    def test_inside(self):
        cfg = {"days": [1, 2, 3, 4, 5], "from_time": "07:00", "to_time": "09:30"}
        assert in_window(cfg, NOW)

    def test_outside_time(self):
        cfg = {"from_time": "18:00", "to_time": "20:00"}
        assert not in_window(cfg, NOW)

    def test_weekend_excluded(self):
        cfg = {"days": [0, 6]}  # solo dom y sáb
        assert not in_window(cfg, NOW)

    def test_no_days_all_days(self):
        assert in_window({"from_time": "00:00", "to_time": "23:59"}, NOW)

    def test_sunday_is_zero(self):
        sun = datetime(2026, 10, 11, 10, 0, tzinfo=TZINFO)  # domingo
        assert in_window({"days": [0]}, sun)
        assert not in_window({"days": [6]}, sun)


def _fake_conn(trains):
    conn = MagicMock()
    res = MagicMock()
    res.mappings.return_value.all.return_value = [
        {"trip_id": t["trip_id"], "train_number": t.get("train", "1"),
         "line": t.get("line", "C2"), "dep_secs": t["dep"],
         "dest": t.get("dest", "X"), "st_delay": t.get("delay"),
         "trip_delay": None, "fleet_delay": None}
        for t in trains]
    conn.execute.return_value = res
    return conn


def _station_sub(trains, last_key=None, **cfg):
    base = {"type": "station", "station_key": "cer:18000", "threshold_min": 5}
    return {"conn": _fake_conn(trains),
            "config": {**base, **cfg},
            "last_notify_key": last_key}


class TestPickAlert:

    def _sub(self, trains, last_key=None, cfg=None):
        return _station_sub(trains, last_key, **(cfg or {}))

    def test_delayed_train_notifies(self):
        dep = NOW.hour * 3600 + NOW.minute * 60 + 1200  # en 20 min
        a = pick_alert(self._sub([{"trip_id": "T1", "dep": dep,
                                   "delay": 600}]), NOW)
        assert a and "+10 min" in a["title"]

    def test_scheduled_only_never_notifies(self):
        # delay=None -> sin dato RT -> jamás notificar
        dep = NOW.hour * 3600 + NOW.minute * 60 + 1200
        a = pick_alert(self._sub([{"trip_id": "T1", "dep": dep,
                                   "delay": None}]), NOW)
        assert a is None

    def test_below_threshold(self):
        dep = NOW.hour * 3600 + NOW.minute * 60 + 1200
        a = pick_alert(self._sub([{"trip_id": "T1", "dep": dep,
                                   "delay": 180}]), NOW)  # +3 < umbral 5
        assert a is None

    def test_dedup_same_key(self):
        dep = NOW.hour * 3600 + NOW.minute * 60 + 1200
        key = f"T1:{NOW.date().isoformat()}:2"  # 600s//300 = 2
        a = pick_alert(self._sub([{"trip_id": "T1", "dep": dep,
                                   "delay": 600}], last_key=key), NOW)
        assert a is None

    def test_delay_step_renotifies(self):
        dep = NOW.hour * 3600 + NOW.minute * 60 + 1200
        old = f"T1:{NOW.date().isoformat()}:1"  # escalón anterior
        a = pick_alert(self._sub([{"trip_id": "T1", "dep": dep,
                                   "delay": 600}], last_key=old), NOW)
        assert a is not None  # el retraso creció un escalón

    def test_past_departure_ignored(self):
        dep = NOW.hour * 3600 + NOW.minute * 60 - 60  # ya salió
        a = pick_alert(self._sub([{"trip_id": "T1", "dep": dep,
                                   "delay": 1800}]), NOW)
        assert a is None

    def test_bad_config_none(self):
        sub = {"conn": MagicMock(), "config": {"type": "journey"},
               "last_notify_key": None}
        assert pick_alert(sub, NOW) is None
