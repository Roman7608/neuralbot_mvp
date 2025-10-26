from datetime import datetime, time, date
import pytz
import yaml
from pathlib import Path

CONFIG_PATH = Path("config/departments.yml")

def _parse_time(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))

def within_working_time(now: datetime, dept_code: str | None = None) -> bool:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tzname = cfg.get("timezone", "Europe/Samara")
    tz = pytz.timezone(tzname)
    now_local = now.astimezone(tz)
    wd = str(now_local.weekday())
    # exceptions
    for exc in (cfg.get("work_exceptions") or []):
        if exc.get("date") == now_local.strftime("%Y-%m-%d"):
            if exc.get("is_closed"):
                return False
            st = exc.get("start_time"); et = exc.get("end_time")
            if st and et:
                return _parse_time(st) <= now_local.time() <= _parse_time(et)
    # default hours
    weekday_hours = (((cfg.get("working_hours") or {}).get("default") or {}).get("weekday_hours") or {})
    span = weekday_hours.get(wd)
    if not span:
        return False
    start_s, end_s = span.split("-")
    return _parse_time(start_s) <= now_local.time() <= _parse_time(end_s)


















