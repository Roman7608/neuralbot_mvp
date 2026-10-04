#!/usr/bin/env python3
"""
Автозабор записей звонков из SpRecord (server5) на server7.

- По умолчанию берёт "сегодня" (локальная дата на server7).
- Можно указать дату явно: --date 2026-03-17
- Ожидает, что SpRecord уже примонтирован на /mnt/sprecord.
  Команды mount: шара sprecord_arch → /mnt/sprecord (wav); опционально sprecord_DB → /mnt/sprecord_db. bot7, NISSAN-TLT: docs/SPRECORD_MOUNT_SERVER7.md
  (старый комментарий «nissan-tlt» как SMB-логин неверен — см. документ).
- Файлы сохраняются на HDD: /mnt/audio_calls/calls/auto/ (см. config.AUDIO_CALLS_ROOT).
- Отбор за календарный день: **mtime** или **дата из имени** `YYYY_MM_DD_...` (как на CIFS mtime часто «не тот день»).

Пример запуска:

  python3 call_analytics/autofetch_sprecord.py --date 2026-03-17

Пайплайн по cron в 23:55 (`scripts/daily_sprecord_pipeline.sh`): на момент запуска `date +%Y-%m-%d`
ещё тот же календарный день — автозабор забирает все файлы **за этот день** (по mtime и/или префиксу имени).

  55 23 * * * cd /home/vikingi/VikingiAll && bash scripts/daily_sprecord_pipeline.sh
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Добавить корень проекта в path для импорта config
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import psycopg2
import soundfile as sf

from config import AUDIO_CALLS_ROOT
from call_analytics.sprecord_filename import date_from_sprecord_filename

# ПУТИ И ПОДКЛЮЧЕНИЕ — при необходимости адаптируйте под свою среду.

# Точка монтирования SpRecord (server5, 192.168.0.5).
SPRECORD_ROOT = Path("/mnt/sprecord")

# Куда складываем скопированные файлы — только HDD server7
LOCAL_ROOT = Path(AUDIO_CALLS_ROOT) / "auto"

# Не копировать и не регистрировать файлы короче N секунд
MIN_DURATION_SEC = 20

# Источник маппинга "sprecord_id -> internal/external".
# Формат JSON:
# [
#   {"sprecord_id": "8B2", "internal_number": "223", "external_number": "+7..."},
#   ...
# ]
SPRECORD_NUMBER_MAP_FILE = Path(
    os.environ.get(
        "SPRECORD_NUMBER_MAP_FILE",
        str(PROJECT_DIR / "call_analytics" / "sprecord_number_map.json"),
    )
)

# Белые списки (через .env):
#   SPRECORD_WHITELIST_INTERNAL=223,224,225
#   SPRECORD_WHITELIST_EXTERNAL=79261234567,79031234567
SPRECORD_WHITELIST_INTERNAL = os.environ.get("SPRECORD_WHITELIST_INTERNAL", "")
SPRECORD_WHITELIST_EXTERNAL = os.environ.get("SPRECORD_WHITELIST_EXTERNAL", "")
# Переключатель применения whitelist:
# - 1/true/yes/on: фильтровать только номера из списков
# - 0/false/no/off (по умолчанию): whitelist отключён, скачиваем всё
SPRECORD_WHITELIST_ENABLED = os.environ.get("SPRECORD_WHITELIST_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Автопостроение карты из sprecord_DB/Records.dat (без ручных задач).
SPRECORD_AUTO_BUILD_MAP = os.environ.get("SPRECORD_AUTO_BUILD_MAP", "1").strip().lower() not in ("0", "false", "no")
SPRECORD_DB_RECORDS_PATH = Path(os.environ.get("SPRECORD_DB_RECORDS_PATH", "/mnt/sprecord_db/Records.dat"))
SPRECORD_DB_HOST = os.environ.get("SPRECORD_DB_HOST", "192.168.0.5")
SPRECORD_DB_SHARE = os.environ.get("SPRECORD_DB_SHARE", "sprecord_DB")
SPRECORD_DB_DOMAIN = os.environ.get("SPRECORD_DB_DOMAIN", "NISSAN-TLT")
SPRECORD_DB_USER = os.environ.get("SPRECORD_DB_USER", "bot7")
SPRECORD_DB_PASSWORD = os.environ.get("SPRECORD_DB_PASSWORD", "Kalina816")

# Подключение к PostgreSQL с БД аналитики (пароль TOP, POSTGRESQL_ANALYTICS_PASSWORD из .env).
try:
    from postgresql_config import get_analytics_connection_string
    PG_DSN = get_analytics_connection_string()
except ImportError:
    PG_DSN = "dbname=vikingi_analytics user=analytics_user password=TOP host=localhost port=5433"


def _digits_only(s: str) -> str:
    return re.sub(r"\D+", "", (s or "").strip())


def normalize_internal_number(src: str | int | None) -> str | None:
    if src is None:
        return None
    d = _digits_only(str(src))
    return d or None


def normalize_external_phone(src: str | None) -> str | None:
    if not src:
        return None
    d = _digits_only(src)
    if not d:
        return None
    # Для whitelist достаточно последних 10 цифр.
    if len(d) >= 10:
        return d[-10:]
    return d


def _split_csv_env(src: str) -> set[str]:
    return {x.strip() for x in src.split(",") if x.strip()}


def load_whitelists() -> tuple[set[str], set[str]]:
    internal = {
        x for x in (normalize_internal_number(v) for v in _split_csv_env(SPRECORD_WHITELIST_INTERNAL)) if x
    }
    external = {
        x for x in (normalize_external_phone(v) for v in _split_csv_env(SPRECORD_WHITELIST_EXTERNAL)) if x
    }
    return internal, external


def load_sprecord_number_map() -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    # 1) Автокарта из Records.dat (предпочтительно для полной автоматизации).
    if SPRECORD_AUTO_BUILD_MAP:
        try:
            auto_map = build_map_from_records_dat()
            out.update(auto_map)
            if auto_map:
                print(f"✅ Карта из Records.dat: {len(auto_map)} записей")
        except Exception as e:
            print(f"⚠️  Автокарта из Records.dat недоступна: {e}")

    # 2) Локальный JSON (опционально, имеет приоритет и может переопределять авто-карту).
    if SPRECORD_NUMBER_MAP_FILE.exists():
        try:
            raw = json.loads(SPRECORD_NUMBER_MAP_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️  Не удалось прочитать маппинг {SPRECORD_NUMBER_MAP_FILE}: {e}")
            return out
        if not isinstance(raw, list):
            print(f"⚠️  Неверный формат {SPRECORD_NUMBER_MAP_FILE}: ожидается JSON-массив объектов")
            return out

        for row in raw:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("sprecord_id", "")).strip().upper()
            if not sid:
                continue
            out[sid] = {
                "internal_number": normalize_internal_number(row.get("internal_number")),
                "external_number": normalize_external_phone(str(row.get("external_number", ""))),
            }
    return out


def _looks_like_audio_filename(line: str) -> re.Match[str] | None:
    return re.match(
        r"^\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_([0-9A-F]{3,})\.(?:wav|mp3|ogg|gsm)$",
        line.strip(),
        flags=re.IGNORECASE,
    )


def _extract_numbers_from_candidate_pool(nums: list[str]) -> tuple[str | None, str | None]:
    internal = None
    external = None
    for n in nums:
        dn = _digits_only(n)
        if not dn:
            continue
        if external is None and len(dn) >= 10:
            external = normalize_external_phone(dn)
        if internal is None and 3 <= len(dn) <= 6:
            internal = normalize_internal_number(dn)
        if internal and external:
            break
    return internal, external


def parse_records_dat_map(records_dat_path: Path) -> dict[str, dict[str, str | None]]:
    """
    Грубый, но практичный парсер Records.dat:
    - берём строки через `strings -n 6`
    - ловим имя файла YYYY_MM_DD_HH_MM_SS_<HEX>.wav
    - собираем соседние числовые токены (до/после) как кандидаты номеров.
    """
    if not records_dat_path.exists():
        return {}

    cmd = ["strings", "-n", "6", str(records_dat_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")

    recent_nums: collections.deque[str] = collections.deque(maxlen=24)
    pending: list[dict[str, object]] = []
    out: dict[str, dict[str, str | None]] = {}

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.strip()
        if not line:
            continue

        m = _looks_like_audio_filename(line)
        if m:
            sid = m.group(1).upper()
            pending.append({"sid": sid, "remain": 20, "nums": list(recent_nums)})
        else:
            d = _digits_only(line)
            if d:
                recent_nums.append(d)
                for p in pending:
                    cast_nums = p["nums"]
                    if isinstance(cast_nums, list):
                        cast_nums.append(d)

        for p in pending:
            p["remain"] = int(p["remain"]) - 1

        still_pending: list[dict[str, object]] = []
        for p in pending:
            if int(p["remain"]) > 0:
                still_pending.append(p)
                continue
            sid = str(p["sid"])
            nums = p["nums"] if isinstance(p["nums"], list) else []
            internal, external = _extract_numbers_from_candidate_pool(nums)
            if internal or external:
                out[sid] = {
                    "internal_number": internal,
                    "external_number": external,
                }
        pending = still_pending

    proc.wait(timeout=120)
    return out


def _download_records_dat_from_smb(target_path: Path) -> bool:
    auth = f"{SPRECORD_DB_DOMAIN}/{SPRECORD_DB_USER}%{SPRECORD_DB_PASSWORD}"
    remote = f"//{SPRECORD_DB_HOST}/{SPRECORD_DB_SHARE}"
    cmd = ["smbclient", remote, "-U", auth, "-c", f"get Records.dat {target_path}"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"⚠️  smbclient get Records.dat failed: {r.stderr.strip() or r.stdout.strip()}")
        return False
    return target_path.exists() and target_path.stat().st_size > 0


def build_map_from_records_dat() -> dict[str, dict[str, str | None]]:
    # 1) Пробуем локально смонтированную БД.
    if SPRECORD_DB_RECORDS_PATH.exists():
        return parse_records_dat_map(SPRECORD_DB_RECORDS_PATH)

    # 2) Фолбэк: прямое чтение по SMB (без sudo, чтобы ежедневный cron работал сам).
    with tempfile.TemporaryDirectory(prefix="sprecord_db_") as td:
        tmp_file = Path(td) / "Records.dat"
        if not _download_records_dat_from_smb(tmp_file):
            return {}
        return parse_records_dat_map(tmp_file)


def extract_sprecord_id(file_name: str) -> str | None:
    m = re.search(r"_([0-9A-F]{3,})\.[^.]+$", file_name.upper())
    if not m:
        return None
    return m.group(1)


def resolve_numbers_for_file(
    file_path: Path,
    number_map: dict[str, dict[str, str | None]],
) -> tuple[str | None, str | None]:
    """
    Возвращает (internal_number, external_phone10).
    1) Пытается найти sprecord_id в имени и взять значения из JSON-маппинга.
    2) Фолбэк: ищет длинные цифровые последовательности в имени/пути.
    """
    sid = extract_sprecord_id(file_path.name)
    if sid and sid in number_map:
        item = number_map[sid]
        return item.get("internal_number"), item.get("external_number")

    # Фолбэк для других форматов имён/путей (если номер реально присутствует в имени).
    tokens = re.findall(r"\d{3,}", str(file_path))
    internal = None
    external = None
    for tok in tokens:
        if internal is None and 3 <= len(tok) <= 6:
            internal = normalize_internal_number(tok)
        if external is None and len(tok) >= 10:
            external = normalize_external_phone(tok)
    return internal, external


def parse_call_time_from_filename(file_name: str) -> dt.time | None:
    """
    Пытается извлечь время из имени вида YYYY_MM_DD_HH_MM_SS_XXX.wav.
    """
    stem = Path(file_name).stem
    parts = stem.split("_")
    if len(parts) < 6:
        return None
    try:
        hh = int(parts[3])
        mm = int(parts[4])
        ss = int(parts[5])
        return dt.time(hh, mm, ss)
    except (TypeError, ValueError):
        return None


def iter_files_for_date(root: Path, target_date: dt.date):
    """
    Все аудио за календарный target_date: день по mtime **или** по префиксу имени YYYY_MM_DD_
    (совпадает с логикой списка в админке «Из SPRecord»).
    """
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith((".wav", ".mp3", ".ogg", ".gsm")):
                continue
            p = Path(dirpath) / name
            try:
                mtime = dt.date.fromtimestamp(p.stat().st_mtime)
            except FileNotFoundError:
                continue
            name_d = date_from_sprecord_filename(name)
            if mtime == target_date or name_d == target_date:
                yield p


def copy_and_register(
    file_path: Path,
    target_date: dt.date,
    internal_number: int,
    caller_phone: str | None,
    cur,
) -> bool:
    """
    Копирует файл в локальную папку и создаёт запись в calls, если её ещё нет.
    Возвращает True при успехе, False если файл пропущен (например, короткий).
    """
    try:
        info = sf.info(str(file_path))
        dur_sec = info.frames / info.samplerate
        if dur_sec < MIN_DURATION_SEC:
            return False
    except Exception:
        pass

    rel_dir = target_date.strftime("%Y-%m-%d")
    dest_dir = LOCAL_ROOT / rel_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / file_path.name
    shutil.copy2(file_path, dest_path)

    # Абсолютный путь — файлы на HDD /mnt/audio_calls, не в проекте
    file_path_for_db = str(dest_path.resolve())

    try:
        info = sf.info(str(dest_path))
        duration_seconds = int(round(info.frames / info.samplerate))
    except Exception:
        duration_seconds = None

    file_size = dest_path.stat().st_size

    # Минимальный набор метаданных; при необходимости можно дообогатить позже.
    call_date = target_date
    call_time = parse_call_time_from_filename(file_path.name) or dt.time(0, 0, 0)
    internal_number_db = internal_number
    department = "OTHER"          # OP / STO / OTHER
    source_type = "auto"

    cur.execute(
        """
        INSERT INTO calls
            (file_path, file_name, internal_number, caller_phone,
             call_date, call_time,
             duration_seconds, file_size_bytes,
             department, source_type, call_source, status)
        VALUES (%s, %s, %s,
                %s,
                %s, %s,
                %s, %s,
                %s, %s, 'sprecord', 'pending')
        ON CONFLICT (file_path) DO NOTHING
        """,
        (
            file_path_for_db,
            dest_path.name,
            internal_number_db,
            caller_phone,
            call_date,
            call_time,
            duration_seconds,
            file_size,
            department,
            source_type,
        ),
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Автозабор записей из SpRecord (server5) на server7.")
    parser.add_argument(
        "--date",
        help="Дата в формате YYYY-MM-DD (по умолчанию — сегодня по времени server7).",
    )
    args = parser.parse_args()

    if args.date:
        target_date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = dt.date.today()

    print(f"📂 Автозабор записей за {target_date} из {SPRECORD_ROOT}")

    if not SPRECORD_ROOT.exists():
        print(
            f"❌ Папка {SPRECORD_ROOT} не найдена. "
            f"Сначала примонтируйте SpRecord (например, smb://192.168.0.5/...)."
        )
        return 1

    number_map = load_sprecord_number_map()
    wl_internal, wl_external = load_whitelists()
    files = list(iter_files_for_date(SPRECORD_ROOT, target_date))
    if not files:
        print("⚠️  Файлов за эту дату не найдено (ни по mtime, ни по префиксу YYYY_MM_DD_ в имени).")
        return 0

    print(f"Найдено файлов: {len(files)}")
    print(
        "Whitelist: enabled=%s, internal=%d, external=%d, map=%d"
        % ("yes" if SPRECORD_WHITELIST_ENABLED else "no", len(wl_internal), len(wl_external), len(number_map))
    )

    conn = psycopg2.connect(PG_DSN)
    imported = 0
    skipped = 0
    skipped_by_whitelist = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for p in files:
                    try:
                        internal_raw, external_raw = resolve_numbers_for_file(p, number_map)
                        internal_norm = normalize_internal_number(internal_raw)
                        external_norm = normalize_external_phone(external_raw)

                        internal_allowed = bool(internal_norm and internal_norm in wl_internal)
                        external_allowed = bool(external_norm and external_norm in wl_external)
                        # При включённом whitelist пропускаем только совпавшие номера.
                        if SPRECORD_WHITELIST_ENABLED and not (internal_allowed or external_allowed):
                            skipped += 1
                            skipped_by_whitelist += 1
                            print(
                                f"  ! skip whitelist {p.name}: internal={internal_norm or '-'} external={external_norm or '-'}"
                            )
                            continue

                        print(
                            f"  → {p} (internal={internal_norm or '-'}, external={external_norm or '-'})"
                        )
                        internal_db = int(internal_norm) if internal_norm else 0
                        caller_phone_db = external_norm
                        if copy_and_register(
                            p,
                            target_date,
                            internal_db,
                            caller_phone_db,
                            cur,
                        ):
                            imported += 1
                        else:
                            skipped += 1
                            print(f"  ! пропуск {p}: длительность < {MIN_DURATION_SEC} с")
                    except OSError as e:
                        skipped += 1
                        print(f"  ! пропуск {p}: {e}")
                    except Exception as e:
                        skipped += 1
                        print(f"  ! ошибка {p}: {e}")
        print(
            f"✅ Импорт завершён. Успешно: {imported}, пропущено: {skipped}, "
            f"из них whitelist: {skipped_by_whitelist}"
        )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

