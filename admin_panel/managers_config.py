"""
Конфигурация менеджеров для фильтра Аналитики.
ОП: 5 менеджеров (Чери/Тенет). СТО: 3 диспетчера + стажер Дарья.
Кузовной цех: отдельный справочник — используется только в связке с признаком «кузовн…» в тексте.
Справочник для добавления/замены: docs/managers_reference.md

В БД и в фильтрах админки используется строка display (Фамилия Имя), чтобы совпадать
с выпадающим списком; manager_name в записях списка — короткий ключ для кода/классификации.
"""

from typing import List, Optional, FrozenSet

import re

# ОП — 5 менеджеров отдела продаж Чери/Тенет. Справочник: docs/managers_reference.md
OP_MANAGERS = [
    {"manager_name": "Евдокимов", "display": "Евдокимов Евгений"},
    {"manager_name": "Захаров", "display": "Захаров Илья"},
    {"manager_name": "Краснощекова", "display": "Краснощекова Анастасия"},
    {"manager_name": "Щеголев", "display": "Щеголев Андрей"},
    {"manager_name": "Калаев", "display": "Калаев Андрей"},
]

# Строка матрицы аналитики, когда в БД только «Андрей» без фамилии (два Андрея в справочнике).
OP_AMBIGUOUS_ANDREY_LABEL = "Андрей (без фамилии)"
OP_AMBIGUOUS_ANDREY_LABEL_LEGACY = "Андрей (неясно, Калаев или Щеголев)"

# СТО — диспетчеры сервиса + ассистенты + стажер Дарья. Справочник: docs/managers_reference.md
STO_MANAGERS = [
    {"manager_name": "Плаксина", "display": "Плаксина Александра"},
    {"manager_name": "Андреева", "display": "Андреева Юлия"},
    {"manager_name": "Гринкина", "display": "Гринкина Юлия"},
    {"manager_name": "Мария", "display": "Мария"},
    {"manager_name": "Лилия", "display": "Лилия"},
    {"manager_name": "Дарья", "display": "Дарья"},
]

# Кузовной цех — не ОП/не диспетчеры СТО; имена/фамилии для правил только при «кузовн…» в транскрипте.
# (Захаров Олег ≠ Захаров Илья ОП — различие по контексту кузовного.)
KUZOV_BODY_MANAGERS = [
    {"manager_name": "Горбушин", "display": "Горбушин Дмитрий"},
    {"manager_name": "Алексеев", "display": "Алексеев Павел"},
    {"manager_name": "Захаров", "display": "Захаров Олег"},
    {"manager_name": "Белоногов", "display": "Белоногов Олег"},
]

_KUZOV_BODY_WORD_TOKENS_CACHE: Optional[FrozenSet[str]] = None


def get_kuzov_body_word_tokens() -> FrozenSet[str]:
    """
    Слова из KUZOV_BODY_MANAGERS (имена и фамилии), lower-case.
    Использовать только вместе с признаком кузовного цеха в тексте.
    """
    global _KUZOV_BODY_WORD_TOKENS_CACHE
    if _KUZOV_BODY_WORD_TOKENS_CACHE is not None:
        return _KUZOV_BODY_WORD_TOKENS_CACHE
    words: set[str] = set()
    for m in KUZOV_BODY_MANAGERS:
        for key in ("manager_name", "display"):
            s = (m.get(key) or "").strip()
            for part in re.split(r"[\s,.-]+", s.lower()):
                w = part.strip(".,;:!?\"«»")
                if len(w) >= 2:
                    words.add(w)
    _KUZOV_BODY_WORD_TOKENS_CACHE = frozenset(words)
    return _KUZOV_BODY_WORD_TOKENS_CACHE


def _all_manager_rows():
    return OP_MANAGERS + STO_MANAGERS


def manager_filter_db_variants(selected: str) -> List[str]:
    """
    Варианты значения calls.manager_name в БД для одного человека (ключ и display).
    Нужно для фильтра: в ячейке может быть «Захаров Илья», в старых записях — «Захаров».
    """
    s = (selected or "").strip()
    if not s:
        return []
    sl = s.lower()
    if (
        s == OP_AMBIGUOUS_ANDREY_LABEL
        or s == OP_AMBIGUOUS_ANDREY_LABEL_LEGACY
        or sl == "андрей"
    ):
        return list(dict.fromkeys([OP_AMBIGUOUS_ANDREY_LABEL, OP_AMBIGUOUS_ANDREY_LABEL_LEGACY, "Андрей"]))
    for m in _all_manager_rows():
        mn = (m.get("manager_name") or "").strip()
        disp = (m.get("display") or mn).strip()
        if not mn:
            continue
        if sl == mn.lower() or sl == disp.lower():
            out = [mn, disp] if mn.lower() != disp.lower() else [mn]
            parts = disp.split()
            if len(parts) >= 2:
                reversed_name = f"{parts[1]} {parts[0]}"
                if reversed_name.lower() != disp.lower():
                    out.append(reversed_name)
            return list(dict.fromkeys(out))
    for m in OP_MANAGERS:
        disp = (m.get("display") or "").strip()
        if sl == disp.lower():
            mn = (m.get("manager_name") or "").strip()
            out = [disp, mn] if mn else [disp]
            parts = disp.split()
            if len(parts) >= 2:
                out.append(f"{parts[1]} {parts[0]}")
            return list(dict.fromkeys([x for x in out if x]))
    return [s]


def display_for_manager_key(key: str, department: str) -> Optional[str]:
    """По ключу manager_name и отделу — строка для БД/фильтра (Фамилия Имя)."""
    rows = OP_MANAGERS if department == "OP" else STO_MANAGERS if department == "STO" else []
    kl = (key or "").strip().lower()
    for m in rows:
        mn = (m.get("manager_name") or "").strip()
        if mn.lower() == kl:
            return (m.get("display") or mn).strip()
    return None


def _token_set_label_match(tokens: List[str]) -> Optional[str]:
    """Совпадение двух слов с display независимо от порядка («Андрей Калаев» / «Калаев Андрей»)."""
    if len(tokens) < 2:
        return None
    pair = frozenset(tokens[:2])
    hits: List[str] = []
    for m in OP_MANAGERS:
        disp = (m.get("display") or "").strip()
        parts = [p.lower() for p in disp.split() if p]
        if len(parts) >= 2 and frozenset(parts[:2]) == pair:
            hits.append(disp)
    if len(hits) == 1:
        return hits[0]
    return None


def resolve_op_manager_roster_label(stored: Optional[str]) -> Optional[str]:
    """
    Значение calls.manager_name → строка строки матрицы ОП (display из справочника
    или OP_AMBIGUOUS_ANDREY_LABEL). None — не привязан ни к одной строке.
    """
    if not stored or not str(stored).strip():
        return None
    s = str(stored).strip()
    if s == OP_AMBIGUOUS_ANDREY_LABEL_LEGACY:
        return OP_AMBIGUOUS_ANDREY_LABEL
    sl = s.lower()
    tokens = [t for t in re.split(r"\s+", sl) if t]

    for m in OP_MANAGERS:
        disp = (m.get("display") or "").strip()
        key = (m.get("manager_name") or "").strip()
        if sl == disp.lower() or sl == key.lower():
            return disp

    by_tokens = _token_set_label_match(tokens)
    if by_tokens:
        return by_tokens

    if len(tokens) == 1:
        t0 = tokens[0]
        surname_hits: List[str] = []
        for m in OP_MANAGERS:
            key = (m.get("manager_name") or "").strip()
            if t0 == key.lower():
                surname_hits.append((m.get("display") or key).strip())
        if len(surname_hits) == 1:
            return surname_hits[0]

        firstname_hits: List[str] = []
        for m in OP_MANAGERS:
            disp = (m.get("display") or "").strip()
            parts = disp.split()
            if len(parts) >= 2 and parts[1].lower() == t0:
                firstname_hits.append(disp)
        if len(firstname_hits) == 1:
            return firstname_hits[0]
        if t0 == "андрей" and len(firstname_hits) >= 2:
            return OP_AMBIGUOUS_ANDREY_LABEL

    return None


def canonical_op_manager_for_db(stored: Optional[str]) -> Optional[str]:
    """
    Каноническое значение для записи в calls.manager_name (ОП):
    display «Фамилия Имя» или короткое «Андрей» при неоднозначности.
    """
    if not stored or not str(stored).strip():
        return None
    label = resolve_op_manager_roster_label(stored)
    if label == OP_AMBIGUOUS_ANDREY_LABEL:
        return "Андрей"
    if label:
        return label
    return str(stored).strip()


def get_op_manager_matrix_labels() -> List[str]:
    """Строки матрицы ОП: справочник + неоднозначный Андрей."""
    labels = [(m.get("display") or m.get("manager_name") or "").strip() for m in OP_MANAGERS]
    labels = [x for x in labels if x]
    labels.append(OP_AMBIGUOUS_ANDREY_LABEL)
    return labels


def stored_manager_to_display(
    stored: Optional[str], department: Optional[str] = None
) -> Optional[str]:
    """Значение из БД → каноническая подпись для списка (предпочтительно Фамилия Имя)."""
    if not stored or not str(stored).strip():
        return None
    s = str(stored).strip()
    if s.lower().startswith("администратор"):
        return s
    dept = (department or "").strip().upper()
    # Одно имя в Прочих/СТО — не подменять на менеджера ОП (админ «Анастасия» ≠ Краснощекова).
    if dept in ("OTHER", "STO") and len(s.split()) == 1:
        return s
    op_label = resolve_op_manager_roster_label(stored)
    if op_label:
        return op_label
    v = manager_filter_db_variants(s)
    if not v:
        return s
    if len(v) >= 2:
        return max(v, key=len)
    return v[0]


def get_managers_for_analytics(department: str) -> list:
    """Список для выпадающих списков: manager_name и display — полная строка (Фамилия Имя)."""
    if department == "OP":
        rows = get_op_manager_matrix_labels()
        return [{"manager_name": label, "display": label} for label in rows]
    elif department == "STO":
        rows = STO_MANAGERS
    else:
        return []
    out = []
    for m in rows:
        if isinstance(m, str):
            label = m.strip()
            out.append({"manager_name": label, "display": label})
            continue
        label = (m.get("display") or m["manager_name"]).strip()
        out.append({"manager_name": label, "display": label})
    return out
