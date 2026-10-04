"""
Единый справочник русских имён (именительный падеж, каноническое написание).

Источники (объединяются без дубликатов, регистр ключа — нижний):
- data/russian_first_names.txt — основной список в репозитории;
- data/russian_male_names.txt — опционально, для обратной совместимости с NameExtractor.

Использование: analyze_call_quality.COMMON_NAMES, dialog/name_extractor.NameExtractor.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DATA = _ROOT / "data"
_PRIMARY_NAMES_FILE = _DATA / "russian_first_names.txt"
_LEGACY_MALE_FILE = _DATA / "russian_male_names.txt"


def _read_name_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


@lru_cache(maxsize=1)
def get_canonical_first_names() -> tuple[str, ...]:
    """
    Уникальные имена в каноническом виде (для difflib и отображения).
    Порядок: по алфавиту (нижний регистр).
    """
    by_lower: dict[str, str] = {}
    for path in (_PRIMARY_NAMES_FILE, _LEGACY_MALE_FILE):
        for line in _read_name_lines(path):
            low = line.lower()
            if low not in by_lower:
                by_lower[low] = line
    if not by_lower:
        raise RuntimeError(
            "Справочник имён пуст: создайте data/russian_first_names.txt "
            "(см. репозиторий VikingiAll)."
        )
    return tuple(sorted(by_lower.values(), key=lambda x: x.lower()))


@lru_cache(maxsize=1)
def get_common_names_lower() -> frozenset[str]:
    """Множество имён в нижнем регистре — для подстрочных проверок в аналитике."""
    return frozenset(n.lower() for n in get_canonical_first_names())


# Алиас для analyze_call_quality (прежнее имя константы)
COMMON_NAMES = get_common_names_lower()
