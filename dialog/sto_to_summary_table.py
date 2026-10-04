"""
Чтение сводной таблицы ТО Chery/Tenet с server2 (SMB → /mnt/sto_to_table на server7).

Источник: \\\\server2\\общие данные\\_СТО\\! ТО Сводная таблица.xlsx
Учётные данные SMB — те же, что для SpRecord (server5), см. docs/STO_TO_TABLE_MOUNT_SERVER7.md.

Форматы листа (шапка в первых строках, автоопределение):
  • server2 «Лист1»: строка 0 пустая/служебная, шапка в строке 1 —
    марка ам | модель | двигатель коробка | Наименование (5 т.км, …) | Итого
  • legacy: Марка | Модель | № ТО | Пробег от/до, км | Время, мин | Стоимость, руб
Длительность слота в файле нет — всегда DEFAULT_LABOR_MINUTES (150).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_STO_TO_FILENAME = "! ТО Сводная таблица.xlsx"
DEFAULT_STO_TO_MOUNT_DIR = Path("/mnt/sto_to_table")
DEFAULT_LABOR_MINUTES = 150

_cache_df: Optional[pd.DataFrame] = None
_cache_path: Optional[Path] = None
_cache_mtime: Optional[float] = None


@dataclass(frozen=True)
class StoToRegulationResult:
    """Строка регламентного ТО по марке, модели и пробегу."""

    found: bool
    brand: str = ""
    model: str = ""
    to_label: str = ""
    to_number: int = 0
    duration_min: int = DEFAULT_LABOR_MINUTES
    price_total: float = 0.0
    mileage_km: int = 0
    source_path: str = ""


def resolve_sto_to_table_path() -> Path:
    """Путь к xlsx: env STO_TO_TABLE_PATH или файл в STO_TO_TABLE_MOUNT_DIR."""
    env_path = (os.getenv("STO_TO_TABLE_PATH") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    mount_dir = Path(os.getenv("STO_TO_TABLE_MOUNT_DIR", str(DEFAULT_STO_TO_MOUNT_DIR)))
    candidates = [
        mount_dir / "_СТО" / DEFAULT_STO_TO_FILENAME,
        mount_dir / DEFAULT_STO_TO_FILENAME,
    ]
    if env_path:
        candidates.insert(0, Path(env_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if env_path else candidates[0]


def _norm_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _parse_int(value: Any) -> Optional[int]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower().replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", s.replace(",", "."))
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower().replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    s = s.replace("руб", "").replace("₽", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _find_column(columns: list[Any], *needles: str) -> Optional[str]:
    for col in columns:
        cl = _norm_text(col)
        if not cl:
            continue
        for needle in needles:
            if needle in cl:
                return str(col)
    return None


def _to_number_from_label(label: str) -> int:
    s = _norm_text(label)
    m = re.search(r"то[\s\-]*(\d+)", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _mileage_threshold_km_from_name_cell(value: Any) -> Optional[int]:
    """«5 т.км» / «10 т.км» в колонке Наименование → пробег порога в км."""
    s = str(value or "").strip().lower().replace("\u00a0", " ")
    if not s:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*т\.?\s*км", s)
    if m:
        try:
            return int(float(m.group(1).replace(",", ".")) * 1000)
        except (TypeError, ValueError):
            return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*тыс", s)
    if m:
        try:
            return int(float(m.group(1).replace(",", ".")) * 1000)
        except (TypeError, ValueError):
            return None
    return _parse_int(value)


def _detect_header_row_index(raw: pd.DataFrame, max_scan: int = 8) -> Optional[int]:
    """Строка, где одновременно есть «марка» и «модель» в ячейках."""
    for i in range(min(max_scan, len(raw))):
        cells = [_norm_text(v) for v in raw.iloc[i].tolist()]
        has_brand = any("марка" in c for c in cells)
        has_model = any("модель" in c for c in cells)
        if has_brand and has_model:
            return i
    return None


def _read_sheet_dataframe(path: Path, sheet: str) -> pd.DataFrame:
    """Читает лист с автоопределением строки заголовка (server2: шапка не в строке 0)."""
    preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=12)
    if preview is None or preview.empty:
        return pd.DataFrame()
    header_row = _detect_header_row_index(preview)
    if header_row is None:
        df = pd.read_excel(path, sheet_name=sheet)
    else:
        df = pd.read_excel(path, sheet_name=sheet, header=header_row)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    # Убрать повтор шапки и полностью пустые строки
    drop_idx: list[int] = []
    for idx, row in df.iterrows():
        brand_probe = _find_column(list(df.columns), "марка", "brand")
        if brand_probe:
            val = _norm_text(row.get(brand_probe, ""))
            if val in ("марка", "марка ам", "brand"):
                drop_idx.append(idx)
                continue
        if all(_norm_text(v) == "" for v in row.tolist()):
            drop_idx.append(idx)
    if drop_idx:
        df = df.drop(index=drop_idx)
    return df.reset_index(drop=True)


def _brand_matches(row_brand: str, query_brand: str) -> bool:
    if not query_brand:
        return False
    rb, qb = _norm_text(row_brand), _norm_text(query_brand)
    if not rb or not qb:
        return False
    return qb in rb or rb in qb or qb.split()[0] == rb.split()[0]


def _model_matches(row_model: str, query_model: str) -> bool:
    if not query_model:
        return False
    rm, qm = _norm_text(row_model), _norm_text(query_model)
    if not rm or not qm:
        return False
    # car_model иногда «Chery Tiggo 7» — сравниваем по токенам
    q_tokens = [t for t in re.split(r"[\s/\-]+", qm) if t and t not in ("chery", "tenet", "чери", "тенет")]
    if q_tokens:
        return all(t in rm for t in q_tokens[:3]) or qm in rm or rm in qm
    return qm in rm or rm in qm


def _load_dataframe(path: Path) -> pd.DataFrame:
    global _cache_df, _cache_path, _cache_mtime
    if not path.is_file():
        logger.warning("Сводная таблица ТО не найдена: %s", path)
        _cache_df = pd.DataFrame()
        _cache_path = path
        _cache_mtime = None
        return _cache_df

    mtime = path.stat().st_mtime
    if _cache_df is not None and _cache_path == path and _cache_mtime == mtime:
        return _cache_df

    frames: list[pd.DataFrame] = []
    try:
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            df = _read_sheet_dataframe(path, sheet)
            if df is not None and not df.empty:
                df["_sheet"] = sheet
                frames.append(df)
    except Exception as exc:
        logger.error("Ошибка чтения сводной таблицы ТО %s: %s", path, exc)
        _cache_df = pd.DataFrame()
        _cache_path = path
        _cache_mtime = mtime
        return _cache_df

    _cache_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    _cache_path = path
    _cache_mtime = mtime
    logger.info(
        "Сводная таблица ТО загружена: %s (%d строк)",
        path,
        len(_cache_df),
    )
    return _cache_df


def lookup_sto_to_regulation(
    brand: Optional[str],
    model: Optional[str],
    mileage_km: Optional[int],
    *,
    path: Optional[Path] = None,
) -> StoToRegulationResult:
    """
    Подбор регламентного ТО по марке, модели и пробегу.

    :returns: StoToRegulationResult; found=False — нет строки или нет файла.
    """
    brand_s = (brand or "").strip()
    model_s = (model or "").strip()
    mileage = int(mileage_km) if mileage_km is not None else None
    table_path = path or resolve_sto_to_table_path()
    empty = StoToRegulationResult(found=False, source_path=str(table_path))

    if not brand_s and not model_s:
        return empty

    df = _load_dataframe(table_path)
    if df.empty:
        return empty

    brand_col = _find_column(list(df.columns), "марка", "brand")
    model_col = _find_column(list(df.columns), "модель", "model")
    to_col = _find_column(list(df.columns), "№ то", "номер то", "то-", " то ", "регламент")
    mile_from_col = _find_column(
        list(df.columns), "пробег от", "от, км", "от км", "mileage from", "км от"
    )
    mile_to_col = _find_column(
        list(df.columns), "пробег до", "до, км", "до км", "mileage to", "км до"
    )
    if mile_from_col and mile_to_col == mile_from_col:
        mile_to_col = _find_column(
            [c for c in df.columns if c != mile_from_col],
            "пробег до",
            "до, км",
            "до км",
            "mileage to",
            "км до",
        )
    duration_col = _find_column(
        list(df.columns), "время", "мин", "длительность", "duration", "нормочас"
    )
    price_col = _find_column(list(df.columns), "стоимость", "цена", "price", "руб", "итого")
    name_col = _find_column(
        list(df.columns), "наименование", "интервал", "регламент", "название"
    )

    if not brand_col and not model_col:
        logger.warning(
            "Сводная ТО %s: не найдены колонки марка/модель (колонки: %s)",
            table_path,
            list(df.columns),
        )
        return empty

    candidates: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rb = str(row.get(brand_col, "") if brand_col else "")
        rm = str(row.get(model_col, "") if model_col else "")
        if brand_col and not _brand_matches(rb, brand_s):
            continue
        if model_col and not _model_matches(rm, model_s):
            continue
        candidates.append(row.to_dict())

    if not candidates:
        return empty

    def _mileage_bounds(row: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        mf = _parse_int(row.get(mile_from_col)) if mile_from_col else None
        mt = _parse_int(row.get(mile_to_col)) if mile_to_col else None
        if mf is None and mt is None and mile_to_col:
            mt = _parse_int(row.get(mile_to_col))
        return mf, mt

    def _row_name_threshold(row: dict[str, Any]) -> Optional[int]:
        if not name_col:
            return None
        return _mileage_threshold_km_from_name_cell(row.get(name_col))

    use_name_thresholds = bool(
        name_col
        and not mile_from_col
        and not mile_to_col
        and any(_row_name_threshold(r) is not None for r in candidates)
    )

    selected: Optional[dict[str, Any]] = None
    if mileage is not None and mileage > 0:
        if use_name_thresholds:
            ranked = sorted(
                ((r, _row_name_threshold(r)) for r in candidates),
                key=lambda x: (x[1] is None, x[1] or 0),
            )
            best_thr = -1
            for row, thr in ranked:
                if thr is None:
                    continue
                if thr <= mileage and thr >= best_thr:
                    best_thr = thr
                    selected = row
            if selected is None and ranked:
                for row, thr in ranked:
                    if thr is not None and thr >= mileage:
                        selected = row
                        break
        else:
            best_from = -1
            for row in candidates:
                mf, mt = _mileage_bounds(row)
                if mf is not None and mileage < mf:
                    continue
                if mt is not None and mileage > mt:
                    continue
                score = mf if mf is not None else 0
                if score >= best_from:
                    best_from = score
                    selected = row
            if selected is None:
                best_to = None
                for row in candidates:
                    _, mt = _mileage_bounds(row)
                    if mt is None or mt < mileage:
                        continue
                    if best_to is None or mt < best_to:
                        best_to = mt
                        selected = row
    if selected is None:
        selected = candidates[0]

    to_label = str(selected.get(to_col, "") if to_col else "").strip()
    if not to_label and name_col:
        to_label = str(selected.get(name_col, "") or "").strip()
    if not to_label:
        to_num = _to_number_from_label(str(selected.get(mile_to_col, "")))
        to_label = f"ТО-{to_num}" if to_num else "ТО"
    else:
        to_num = _to_number_from_label(to_label)
        if to_num <= 0 and use_name_thresholds:
            thr = _row_name_threshold(selected)
            if thr is not None:
                ordered = sorted(
                    t for t in (_row_name_threshold(r) for r in candidates) if t is not None
                )
                if thr in ordered:
                    to_num = ordered.index(thr) + 1
                to_label = f"ТО-{to_num}" if to_num else to_label

    duration = _parse_int(selected.get(duration_col)) if duration_col else None
    price = _parse_float(selected.get(price_col)) if price_col else None

    return StoToRegulationResult(
        found=True,
        brand=brand_s,
        model=model_s,
        to_label=to_label,
        to_number=to_num,
        duration_min=DEFAULT_LABOR_MINUTES if duration is None else max(duration, DEFAULT_LABOR_MINUTES),
        price_total=max(price or 0.0, 0.0),
        mileage_km=mileage or 0,
        source_path=str(table_path),
    )


def get_labor_and_cost_from_table(
    brand: Optional[str] = None,
    model: Optional[str] = None,
    mileage_km: Optional[int] = None,
) -> tuple[int, float, StoToRegulationResult]:
    """
    duration_min и price_total для слотов / озвучивания (цена — только по запросу в сценарии).

    Если марка/модель не в таблице — (150, 0.0, found=False).
    """
    result = lookup_sto_to_regulation(brand, model, mileage_km)
    if not result.found:
        return DEFAULT_LABOR_MINUTES, 0.0, result
    duration = result.duration_min or DEFAULT_LABOR_MINUTES
    return duration, result.price_total, result


def clear_sto_to_table_cache() -> None:
    """Сброс кэша (тесты / после обновления файла на server2)."""
    global _cache_df, _cache_path, _cache_mtime
    _cache_df = None
    _cache_path = None
    _cache_mtime = None


def _engine_gearbox_col(columns: list[Any]) -> Optional[str]:
    return _find_column(
        columns,
        "двигатель коробка",
        "двигатель",
        "коробка",
        "engine",
        "кпп",
    )


@dataclass(frozen=True)
class EngineGearboxSpec:
    raw: str = ""
    volume_l: Optional[float] = None
    transmission: Optional[str] = None  # mt | at
    drive: Optional[str] = None  # fwd | awd


def parse_engine_gearbox_cell(value: Any) -> EngineGearboxSpec:
    """Парсит «1.5 MT», «2.0 CVT», «1.6 AT 4WD» из колонки двигатель/коробка."""
    raw = str(value or "").strip()
    s = _norm_text(raw)
    if not s:
        return EngineGearboxSpec(raw=raw)

    volume_l: Optional[float] = None
    m_vol = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:л|l)?", s)
    if m_vol:
        try:
            volume_l = float(m_vol.group(1).replace(",", "."))
        except (TypeError, ValueError):
            volume_l = None

    transmission: Optional[str] = None
    if re.search(r"\b(?:mt|мех|механик|manual)\b", s):
        transmission = "mt"
    elif re.search(r"\b(?:at|cvt|amt|robot|автомат|вариатор)\b", s):
        transmission = "at"

    drive: Optional[str] = None
    if re.search(r"\b(?:4wd|awd|полн|4x4)\b", s):
        drive = "awd"
    elif re.search(r"\b(?:fwd|передн)\b", s):
        drive = "fwd"

    return EngineGearboxSpec(raw=raw, volume_l=volume_l, transmission=transmission, drive=drive)


def _collect_sto_to_candidates(
    brand: Optional[str],
    model: Optional[str],
    mileage_km: Optional[int],
    *,
    path: Optional[Path] = None,
) -> tuple[list[dict[str, Any]], Path, dict[str, Optional[str]]]:
    """Кандидаты строк Excel после фильтра марка/модель/пробег."""
    brand_s = (brand or "").strip()
    model_s = (model or "").strip()
    mileage = int(mileage_km) if mileage_km is not None else None
    table_path = path or resolve_sto_to_table_path()
    cols: dict[str, Optional[str]] = {}
    if not brand_s and not model_s:
        return [], table_path, cols

    df = _load_dataframe(table_path)
    if df.empty:
        return [], table_path, cols

    brand_col = _find_column(list(df.columns), "марка", "brand")
    model_col = _find_column(list(df.columns), "модель", "model")
    mile_from_col = _find_column(list(df.columns), "пробег от", "от, км", "от км", "mileage from", "км от")
    mile_to_col = _find_column(list(df.columns), "пробег до", "до, км", "до км", "mileage to", "км до")
    if mile_from_col and mile_to_col == mile_from_col:
        mile_to_col = _find_column(
            [c for c in df.columns if c != mile_from_col],
            "пробег до", "до, км", "до км", "mileage to", "км до",
        )
    name_col = _find_column(list(df.columns), "наименование", "интервал", "регламент", "название")
    eg_col = _engine_gearbox_col(list(df.columns))
    cols = {
        "brand_col": brand_col,
        "model_col": model_col,
        "mile_from_col": mile_from_col,
        "mile_to_col": mile_to_col,
        "name_col": name_col,
        "engine_gearbox_col": eg_col,
    }

    candidates: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rb = str(row.get(brand_col, "") if brand_col else "")
        rm = str(row.get(model_col, "") if model_col else "")
        if brand_col and not _brand_matches(rb, brand_s):
            continue
        if model_col and not _model_matches(rm, model_s):
            continue
        candidates.append(row.to_dict())

    if not candidates:
        return [], table_path, cols

    def _mileage_bounds(row: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        mf = _parse_int(row.get(mile_from_col)) if mile_from_col else None
        mt = _parse_int(row.get(mile_to_col)) if mile_to_col else None
        return mf, mt

    def _row_name_threshold(row: dict[str, Any]) -> Optional[int]:
        if not name_col:
            return None
        return _mileage_threshold_km_from_name_cell(row.get(name_col))

    use_name_thresholds = bool(
        name_col and not mile_from_col and not mile_to_col
        and any(_row_name_threshold(r) is not None for r in candidates)
    )

    filtered: list[dict[str, Any]] = []
    if mileage is not None and mileage > 0:
        if use_name_thresholds:
            ranked = sorted(
                ((r, _row_name_threshold(r)) for r in candidates),
                key=lambda x: (x[1] is None, x[1] or 0),
            )
            best_thr = -1
            selected: Optional[dict[str, Any]] = None
            for row, thr in ranked:
                if thr is None:
                    continue
                if thr <= mileage and thr >= best_thr:
                    best_thr = thr
                    selected = row
            if selected is not None:
                filtered = [row for row, thr in ranked if thr == best_thr]
            else:
                fallback_thr: Optional[int] = None
                for row, thr in ranked:
                    if thr is not None and thr >= mileage:
                        fallback_thr = thr
                        break
                if fallback_thr is not None:
                    filtered = [row for row, thr in ranked if thr == fallback_thr]
        else:
            for row in candidates:
                mf, mt = _mileage_bounds(row)
                if mf is not None and mileage < mf:
                    continue
                if mt is not None and mileage > mt:
                    continue
                filtered.append(row)
            if not filtered:
                best_to = None
                best_row = None
                for row in candidates:
                    _, mt = _mileage_bounds(row)
                    if mt is None or mt < mileage:
                        continue
                    if best_to is None or mt < best_to:
                        best_to = mt
                        best_row = row
                if best_row is not None:
                    filtered = [best_row]

    if not filtered:
        filtered = candidates
    return filtered, table_path, cols


def filter_sto_to_candidates_by_spec(
    candidates: list[dict[str, Any]],
    *,
    engine_gearbox_col: Optional[str],
    transmission: Optional[str] = None,
    volume_l: Optional[float] = None,
    drive: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Сужает список строк по ответам клиента (КПП, объём, привод)."""
    if not candidates:
        return []
    out = candidates
    if engine_gearbox_col:
        if transmission:
            tx = transmission.lower()
            out = [
                r for r in out
                if parse_engine_gearbox_cell(r.get(engine_gearbox_col)).transmission == tx
            ]
        if volume_l is not None:
            out = [
                r for r in out
                if parse_engine_gearbox_cell(r.get(engine_gearbox_col)).volume_l == volume_l
                or (
                    parse_engine_gearbox_cell(r.get(engine_gearbox_col)).volume_l is not None
                    and abs(parse_engine_gearbox_cell(r.get(engine_gearbox_col)).volume_l - volume_l) < 0.05
                )
            ]
        if drive:
            dr = drive.lower()
            out = [
                r for r in out
                if parse_engine_gearbox_cell(r.get(engine_gearbox_col)).drive == dr
            ]
    return out if out else candidates


def next_sto_to_disambiguation_field(
    candidates: list[dict[str, Any]],
    engine_gearbox_col: Optional[str],
    chosen: dict[str, Any],
) -> Optional[str]:
    """
    Следующее поле для уточнения: transmission | volume_l | drive.
    chosen — уже собранные ответы (ключи как в filter_sto_to_*).
    """
    if not engine_gearbox_col or len(candidates) <= 1:
        return None

    def _distinct(field: str) -> set[Any]:
        vals: set[Any] = set()
        for row in candidates:
            spec = parse_engine_gearbox_cell(row.get(engine_gearbox_col))
            if field == "transmission":
                vals.add(spec.transmission)
            elif field == "volume_l":
                vals.add(spec.volume_l)
            elif field == "drive":
                vals.add(spec.drive)
        vals.discard(None)
        return vals

    for field in ("transmission", "volume_l", "drive"):
        if field in chosen and chosen[field] is not None:
            continue
        if len(_distinct(field)) > 1:
            return field
    return None


def lookup_sto_to_regulation_with_filters(
    brand: Optional[str],
    model: Optional[str],
    mileage_km: Optional[int],
    *,
    transmission: Optional[str] = None,
    volume_l: Optional[float] = None,
    drive: Optional[str] = None,
    path: Optional[Path] = None,
) -> StoToRegulationResult:
    """Подбор ТО с учётом уточнений по двигателю/КПП."""
    candidates, table_path, cols = _collect_sto_to_candidates(
        brand, model, mileage_km, path=path
    )
    empty = StoToRegulationResult(found=False, source_path=str(table_path))
    if not candidates:
        return empty

    eg_col = cols.get("engine_gearbox_col")
    filtered = filter_sto_to_candidates_by_spec(
        candidates,
        engine_gearbox_col=eg_col,
        transmission=transmission,
        volume_l=volume_l,
        drive=drive,
    )
    if len(filtered) > 1 and eg_col:
        field = next_sto_to_disambiguation_field(filtered, eg_col, {
            "transmission": transmission,
            "volume_l": volume_l,
            "drive": drive,
        })
        if field:
            return empty

    selected = filtered[0]
    to_col = _find_column(list(_load_dataframe(table_path).columns), "№ то", "номер то", "то-", " то ", "регламент")
    name_col = cols.get("name_col")
    price_col = _find_column(list(_load_dataframe(table_path).columns), "стоимость", "цена", "price", "руб", "итого")
    duration_col = _find_column(list(_load_dataframe(table_path).columns), "время", "мин", "длительность", "duration")

    to_label = str(selected.get(to_col, "") if to_col else "").strip()
    if not to_label and name_col:
        to_label = str(selected.get(name_col, "") or "").strip()
    to_num = _to_number_from_label(to_label)
    if not to_label:
        to_label = f"ТО-{to_num}" if to_num else "ТО"

    duration = _parse_int(selected.get(duration_col)) if duration_col else None
    price = _parse_float(selected.get(price_col)) if price_col else None

    return StoToRegulationResult(
        found=True,
        brand=(brand or "").strip(),
        model=(model or "").strip(),
        to_label=to_label,
        to_number=to_num,
        duration_min=DEFAULT_LABOR_MINUTES if duration is None else max(duration, DEFAULT_LABOR_MINUTES),
        price_total=max(price or 0.0, 0.0),
        mileage_km=int(mileage_km or 0),
        source_path=str(table_path),
    )
