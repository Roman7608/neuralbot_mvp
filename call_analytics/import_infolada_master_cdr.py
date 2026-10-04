#!/usr/bin/env python3
"""
Импорт недозвонов из выгрузки Asterisk CDR Инфолады (cdr-custom/Master.csv).

Стандартный CSV: колонки вроде calldate, src, dst, disposition, billsec, duration, uniqueid
(регистр и лишние кавычки в заголовках допускаются).

Строки, похожие на успешное соединение с ботом (ANSWERED и billsec >= порога), пропускаются,
чтобы не дублировать лиды, уже созданные голосовым ботом.

Запись в telegram_leads: source=infolada_cdr, cdr_uniqueid для ON CONFLICT DO NOTHING.

Пример:
  python3 call_analytics/import_infolada_master_cdr.py /path/to/Master.csv
  python3 call_analytics/import_infolada_master_cdr.py /path/to/Master.csv --dry-run

Переменные окружения:
  INFOLADA_CDR_ANSWERED_SKIP_SEC=3   — ANSWERED с billsec >= этого не импортировать
  INFOLADA_CDR_FILTER_DIDS=1       — только звонки на известные DID Викингов (см. DEFAULT_DIDS; не только «на бота»)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# DID Викингов для фильтра выгрузки CDR (известные входящие линии; не путать
# со списком только «на бота» — 773399 в CDR есть, на бота не маршрутизируют).
DEFAULT_DIDS = frozenset(
    {
        "697070",
        "695875",
        "697777",
        "695870",
        "695869",
        "695877",
        "773399",
        "707755",
    }
)


def _looks_like_asterisk_cdr_data_row(cells: list) -> bool:
    """У выгрузки Master.csv часто нет строки заголовка — первая строка уже CDR."""
    if len(cells) < 17:
        return False
    uid = (cells[16] or "").strip()
    return bool(re.match(r"^\d+\.\d+$", uid))


def _is_named_header_row(cells: list) -> bool:
    joined = " ".join(cells).lower()
    if "uniqueid" in joined:
        return True
    if cells and (cells[0] or "").strip().lower() in ("accountcode", "src", "clid"):
        return True
    return False


def _row_from_positional(cells: list) -> Optional[Dict[str, str]]:
    """Asterisk cdr-custom CSV без заголовка (типично 19 полей). См. порядок колонок cdr_csv."""
    if len(cells) < 17:
        return None
    return {
        "src": (cells[1] or "").strip(),
        "dst": (cells[2] or "").strip(),
        "calldate": (cells[8] or "").strip(),
        "duration": (cells[11] or "").strip(),
        "billsec": (cells[12] or "").strip(),
        "disposition": (cells[13] or "").strip(),
        "uniqueid": (cells[16] or "").strip(),
    }


def _norm_header(h: str) -> str:
    return (h or "").strip().strip('"').lower()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _dst_matches_vikingi(dst_raw: str, filter_dids: bool) -> bool:
    if not filter_dids:
        return True
    d = _digits(dst_raw)
    if not d:
        return False
    tail6 = d[-6:] if len(d) >= 6 else d
    tail7 = d[-7:] if len(d) >= 7 else ""
    if tail6 in DEFAULT_DIDS or tail7 in DEFAULT_DIDS:
        return True
    return any(d.endswith(x) for x in DEFAULT_DIDS)


def _parse_billsec(row: Dict[str, str]) -> float:
    for k in ("billsec", "billableseconds", "duration"):
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(str(v).strip().replace(",", "."))
        except ValueError:
            continue
    return 0.0


def _parse_duration(row: Dict[str, str]) -> float:
    v = row.get("duration")
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return 0.0


def _should_import_row(
    disposition: str,
    billsec: float,
    answered_skip_sec: float,
) -> Tuple[bool, str]:
    d = (disposition or "").strip().upper()
    if d in ("NO ANSWER", "NOANSWER", "BUSY", "FAILED", "CANCEL", "CONGESTION", "CHANUNAVAIL"):
        return True, d or "UNKNOWN"
    if d == "ANSWERED":
        if billsec >= answered_skip_sec:
            return False, "skip_answered_long"
        return True, "ANSWERED_SHORT"
    return True, d or "OTHER"


def _normalize_phone_for_lead(src: str) -> str:
    d = _digits(src)
    if len(d) == 11 and d[0] in "78" and d[1] == "9":
        return "+7" + d[-10:]
    if len(d) == 10 and d[0] == "9":
        return "+7" + d
    if src and src.strip().startswith("+"):
        return "+" + _digits(src)
    return src.strip() or "—"


def _pick(row: Dict[str, Any], *header_names: str) -> str:
    want = {_norm_header(n) for n in header_names}
    for k, v in row.items():
        if _norm_header(str(k)) in want:
            if isinstance(v, str):
                return v.strip()
            return str(v or "").strip()
    return ""


def _build_need_text(
    calldate: str,
    dst: str,
    disposition: str,
    billsec: float,
    duration: float,
    uniqueid: str,
) -> str:
    return (
        f"CDR Инфолада: {calldate or '—'} | на {dst or '—'} | {disposition or '—'} | "
        f"billsec={billsec:.0f}s duration={duration:.0f}s | uniqueid={uniqueid or '—'}"
    )


def _process_one_row(
    *,
    calldate: str,
    src: str,
    dst: str,
    disposition: str,
    uniqueid: str,
    billsec: float,
    duration: float,
    dry_run: bool,
    answered_skip_sec: float,
    filter_dids: bool,
) -> Tuple[str, str]:
    """
    Returns: (action, detail) where action is 'import'|'skip'|'noop'|'dry'
    """
    if not (uniqueid or "").strip():
        return "skip", "empty_uid"
    if not _dst_matches_vikingi(dst, filter_dids):
        return "skip", "did"
    ok, reason = _should_import_row(disposition, billsec, answered_skip_sec)
    if not ok:
        return "skip", reason or "filter"
    need_text = _build_need_text(calldate, dst, disposition or reason, billsec, duration, uniqueid)
    phone = _normalize_phone_for_lead(src)
    if dry_run:
        return "dry", need_text
    from database.postgresql_manager import TelegramLeadsDB

    lead_id = TelegramLeadsDB.save_lead(
        telegram_user_id=0,
        client_fio="—",
        client_phone=phone,
        need_type="secretary",
        need_text=need_text,
        department="secretary",
        group_id=0,
        working_hours=True,
        response_message=need_text,
        source="infolada_cdr",
        voice_contact_outcome=None,
        cdr_uniqueid=uniqueid.strip()[:128],
    )
    if lead_id:
        return "import", str(lead_id)
    return "noop", "dup_or_error"


def import_master_csv(
    path: Path,
    *,
    dry_run: bool = False,
    filter_dids: bool = True,
    answered_skip_sec: float = 3.0,
) -> Tuple[int, int, int]:
    """
    Returns: (inserted, skipped, duplicates_or_none)
    """
    inserted = 0
    skipped = 0
    noop = 0
    preview = 0

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect=dialect)
        first = next(reader, None)
        if not first:
            print("Пустой CSV", file=sys.stderr)
            return 0, 0, 0

        if _looks_like_asterisk_cdr_data_row(first):
            use_positional = True
        elif _is_named_header_row(first):
            use_positional = False
        else:
            use_positional = len(first) >= 17

        if use_positional:

            def all_rows():
                yield first
                for row in reader:
                    yield row

            for cells in all_rows():
                pr = _row_from_positional(cells)
                if not pr:
                    skipped += 1
                    continue
                billsec = float(pr["billsec"] or 0) if pr["billsec"] else 0.0
                try:
                    duration = float(pr["duration"] or 0) if pr["duration"] else 0.0
                except ValueError:
                    duration = 0.0
                action, _ = _process_one_row(
                    calldate=pr["calldate"],
                    src=pr["src"],
                    dst=pr["dst"],
                    disposition=pr["disposition"],
                    uniqueid=pr["uniqueid"],
                    billsec=billsec,
                    duration=duration,
                    dry_run=dry_run,
                    answered_skip_sec=answered_skip_sec,
                    filter_dids=filter_dids,
                )
                if action == "dry":
                    preview += 1
                elif action == "import":
                    inserted += 1
                elif action == "noop":
                    noop += 1
                else:
                    skipped += 1
        else:
            f.seek(0)
            reader2 = csv.DictReader(f, dialect=dialect)
            if not reader2.fieldnames:
                print("Пустой CSV или нет заголовка", file=sys.stderr)
                return 0, 0, 0
            for raw in reader2:
                row = {str(k): v for k, v in raw.items()}
                calldate = _pick(row, "calldate", "start", "cdrdate")
                src = _pick(row, "src", "clid", "source")
                dst = _pick(row, "dst", "destination", "called")
                disposition = _pick(row, "disposition", "status")
                uniqueid = _pick(row, "uniqueid", "linkedid", "sequence")
                if not uniqueid.strip():
                    skipped += 1
                    continue
                nrow = {_norm_header(str(k)): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                billsec = _parse_billsec(nrow)
                duration = _parse_duration(nrow)
                action, _ = _process_one_row(
                    calldate=calldate,
                    src=src,
                    dst=dst,
                    disposition=disposition,
                    uniqueid=uniqueid,
                    billsec=billsec,
                    duration=duration,
                    dry_run=dry_run,
                    answered_skip_sec=answered_skip_sec,
                    filter_dids=filter_dids,
                )
                if action == "dry":
                    preview += 1
                elif action == "import":
                    inserted += 1
                elif action == "noop":
                    noop += 1
                else:
                    skipped += 1

    if dry_run:
        return preview, skipped, 0
    return inserted, skipped, noop


def main() -> int:
    ap = argparse.ArgumentParser(description="Импорт Master.csv CDR Инфолады в лиды.")
    ap.add_argument("csv_path", type=Path, help="Путь к Master.csv")
    ap.add_argument("--dry-run", action="store_true", help="Только показать строки, без БД")
    ap.add_argument(
        "--no-did-filter",
        action="store_true",
        help="Не фильтровать по DID Викингов (все строки файла)",
    )
    args = ap.parse_args()

    if not args.csv_path.is_file():
        print(f"Файл не найден: {args.csv_path}", file=sys.stderr)
        return 1

    answered_skip = float(os.environ.get("INFOLADA_CDR_ANSWERED_SKIP_SEC", "3"))
    filter_dids = os.environ.get("INFOLADA_CDR_FILTER_DIDS", "1") not in ("0", "false", "no")
    if args.no_did_filter:
        filter_dids = False

    ins, sk, noop = import_master_csv(
        args.csv_path,
        dry_run=args.dry_run,
        filter_dids=filter_dids,
        answered_skip_sec=answered_skip,
    )
    if args.dry_run:
        print(f"DRY-RUN: было бы импортировано={ins}, пропущено={sk}")
    else:
        print(f"Готово: импортировано={ins}, пропущено={sk}, дубликаты/пусто={noop}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
