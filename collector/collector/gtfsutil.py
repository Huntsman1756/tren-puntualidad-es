import csv
import io
import re
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from collector.config import TZ

TZINFO = ZoneInfo(TZ)


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_gtfs_csv(zf: zipfile.ZipFile, name: str):
    """Itera filas como dicts, con valores recortados (los ficheros van con padding)."""
    if name not in zf.namelist():
        return
    with zf.open(name) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(text)
        header = [h.strip() for h in next(reader)]
        for row in reader:
            if not any(row):
                continue
            yield {h: (v.strip() if v else "") for h, v in zip(header, row, strict=False)}


def hms_to_secs(s: str):
    """'08:35:00' o '25:10:00' -> segundos desde medianoche."""
    if not s:
        return None
    try:
        h, m, sec = (int(x) for x in s.split(":"))
        return h * 3600 + m * 60 + sec
    except ValueError:
        return None


def secs_to_hms(s):
    if s is None:
        return None
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def parse_gtfs_date(s: str):
    try:
        return datetime.strptime(s.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def extract_train_number(feed: str, trip_id: str, short_name: str = ""):
    """Número de tren comercial a partir del trip_id."""
    if short_name:
        return short_name
    if feed == "cer":
        # CER: 4 dígitos + una letra (J, S, V, M...) + nº comercial + código de línea
        m = re.match(r"^\d{4}[A-Z](\d{4,6})", trip_id)
        if m:
            return m.group(1)
        m = re.search(r"J(\d{3,6})", trip_id)
        if m:
            return m.group(1)
    m = re.match(r"^(\d{5})", trip_id)
    if m:
        return m.group(1)
    return None


def extract_platform(label: str):
    """'C1-23558-PLATF.(2)' -> '2'"""
    if not label:
        return None
    m = re.search(r"PLATF\.?\(?\.?\s*(\w+)\)?", label)
    return m.group(1) if m else None


def expand_service_days(cal_rows, dates_rows, today):
    """Devuelve {service_id: set(date)} acotado a [today-1, today+40]."""
    lo, hi = today - timedelta(days=1), today + timedelta(days=40)
    out = {}
    for r in cal_rows:
        sid = r.get("service_id", "")
        start, end = parse_gtfs_date(r.get("start_date", "")), parse_gtfs_date(r.get("end_date", ""))
        if not sid or not start or not end:
            continue
        flags = [r.get(d, "0") == "1" for d in
                 ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
        d = max(start, lo)
        while d <= min(end, hi):
            if flags[d.weekday()]:
                out.setdefault(sid, set()).add(d)
            d += timedelta(days=1)
    for r in dates_rows:
        sid, d, exc = r.get("service_id", ""), parse_gtfs_date(r.get("date", "")), r.get("exception_type", "")
        if not sid or not d or not (lo <= d <= hi):
            continue
        if exc == "1":
            out.setdefault(sid, set()).add(d)
        elif exc == "2" and sid in out:
            out[sid].discard(d)
    return out
