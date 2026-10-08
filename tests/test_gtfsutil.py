from datetime import date

from collector.gtfsutil import (
    expand_service_days,
    extract_platform,
    extract_train_number,
    hms_to_secs,
    parse_gtfs_date,
    secs_to_hms,
)


class TestHmsToSecs:
    def test_normal(self):
        assert hms_to_secs("08:35:00") == 8 * 3600 + 35 * 60

    def test_unpadded(self):
        assert hms_to_secs("8:30:00") == 30600

    def test_past_midnight_gtfs(self):
        # GTFS admite horas >24 para servicios que cruzan medianoche
        assert hms_to_secs("25:10:00") == 25 * 3600 + 600

    def test_empty(self):
        assert hms_to_secs("") is None
        assert hms_to_secs(None) is None

    def test_invalid(self):
        assert hms_to_secs("n/a") is None

    def test_roundtrip(self):
        assert secs_to_hms(30600) == "08:30:00"


class TestParseGtfsDate:
    def test_ok(self):
        assert parse_gtfs_date("20261008") == date(2026, 10, 8)

    def test_bad(self):
        assert parse_gtfs_date("") is None
        assert parse_gtfs_date("2026-10-08") is None  # formato con guiones no es GTFS


class TestTrainNumber:
    def test_cer_c_suffix(self):
        assert extract_train_number("cer", "3079J23558C1") == "23558"

    def test_cer_r_line(self):
        assert extract_train_number("cer", "5179J25544R2S") == "25544"

    def test_cer_lowercase_suffix(self):
        assert extract_train_number("cer", "1079J20507C4b") == "20507"

    def test_ld_date_trip(self):
        assert extract_train_number("ld", "0393212026-10-07") == "03932"

    def test_short_name_priority(self):
        assert extract_train_number("ld", "0019012026-10-08", "00190") == "00190"

    def test_unknown(self):
        assert extract_train_number("cer", "XXXX") is None


class TestPlatform:
    def test_label(self):
        assert extract_platform("C1-23558-PLATF.(2)") == "2"

    def test_none(self):
        assert extract_platform(None) is None
        assert extract_platform("C1-23558") is None


class TestServiceDays:
    def test_weekday_expansion(self):
        cal = [{"service_id": "S1", "monday": "1", "tuesday": "0", "wednesday": "0",
                "thursday": "0", "friday": "0", "saturday": "0", "sunday": "0",
                "start_date": "20261005", "end_date": "20261011"}]
        days = expand_service_days(cal, [], date(2026, 10, 5))
        # solo lunes 5 dentro del rango visible
        assert days["S1"] == {date(2026, 10, 5)}

    def test_calendar_dates_add_remove(self):
        cal = [{"service_id": "S1", "monday": "1", "tuesday": "0", "wednesday": "0",
                "thursday": "0", "friday": "0", "saturday": "0", "sunday": "0",
                "start_date": "20261005", "end_date": "20261005"}]
        dates = [{"service_id": "S1", "date": "20261005", "exception_type": "2"},
                 {"service_id": "S1", "date": "20261008", "exception_type": "1"}]
        days = expand_service_days(cal, dates, date(2026, 10, 5))
        assert days["S1"] == {date(2026, 10, 8)}

    def test_empty(self):
        assert expand_service_days([], [], date(2026, 10, 8)) == {}
