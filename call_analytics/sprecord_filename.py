"""
Дата звонка из имени файла SpRecord (префикс YYYY_MM_DD_).
Согласовано с отбором в админке и автозаборе (mtime на CIFS ненадёжен).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional


def date_from_sprecord_filename(filename: str) -> Optional[date]:
    m = re.match(r"^(\d{4})_(\d{2})_(\d{2})_", filename)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
