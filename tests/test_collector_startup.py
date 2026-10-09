"""Arranque del collector y latido de captura (PostgreSQL real).

- db_populated: decide si el arranque puede saltarse la espera inicial.
- record_capture: upsert por (feed, fuente, día local) con mayor hueco.
- poll_trip_updates: escribe el latido de captura junto al resto.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

from collector.gtfsutil import TZINFO  # noqa: E402
from collector.main import db_populated  # noqa: E402
from collector.realtime import poll_trip_updates, record_capture  # noqa: E402
from dbfix import TABLES, epoch  # noqa: E402

TODAY = datetime.now(TZINFO).date()
T0 = epoch(TODAY, 10 * 3600)


def _capture_row(engine, feed, source, day):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT polls, first_ts, last_ts, max_gap_sec FROM capture_health"
            " WHERE feed=:f AND source=:s AND day=:d"),
            {"f": feed, "s": source, "d": day}).first()


class TestDbPopulated:
    def test_empty_db_is_not_populated(self, db_engine):
        with db_engine.begin() as c:
            for t in TABLES:
                c.execute(text(f"DELETE FROM {t}"))
            assert db_populated(c) is False

    def test_populated_after_static_meta(self, scenario):
        with scenario.begin() as c:
            assert db_populated(c) is False  # hay paradas, falta meta de carga
            c.execute(text("INSERT INTO meta(key,value) VALUES"
                           " ('static_loaded_cer','1'),('static_loaded_ld','1')"))
            assert db_populated(c) is True
            c.execute(text("DELETE FROM meta WHERE key='static_loaded_ld'"))
            assert db_populated(c) is False


class TestRecordCapture:
    def test_gap_and_polls_same_day(self, scenario):
        with scenario.begin() as c:
            record_capture(c, "cer", "trip_update", T0)
            record_capture(c, "cer", "trip_update", T0 + 1000)
            record_capture(c, "cer", "trip_update", T0 + 4000)
        polls, first, last, gap = _capture_row(scenario, "cer", "trip_update", TODAY)
        assert polls == 3
        assert first == T0
        assert last == T0 + 4000
        assert gap == 3000

    def test_next_local_day_creates_new_row(self, scenario):
        with scenario.begin() as c:
            record_capture(c, "cer", "trip_update", T0)
            record_capture(c, "cer", "trip_update", T0 + 86400)
        assert _capture_row(scenario, "cer", "trip_update", TODAY)[0] == 1
        polls, _, _, gap = _capture_row(scenario, "cer", "trip_update",
                                        TODAY + timedelta(days=1))
        assert polls == 1
        assert gap == 0


def test_poll_trip_updates_writes_capture_health(scenario):
    data = {"header": {"timestamp": "1"}, "entity": []}
    poll_trip_updates("cer", data=data, now=T0)
    row = _capture_row(scenario, "cer", "trip_update", TODAY)
    assert row is not None
    assert row[0] == 1
