#!/usr/bin/env python3
"""
Перетранскрибация, классификация и оценка звонков.
Запуск на хосте (подключение к Docker PostgreSQL — одна БД с админкой):
  ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-20 --date-to 2026-03-20
  USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-20 --date-to 2026-03-20
  USE_LLM_EVALUATE=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify ...  # опционально: оценка через LLM (по умолчанию — правила)
  ./run_local.sh python -m call_analytics.retranscribe_reclassify --call-ids 740,741,742
  ./run_local.sh python -m call_analytics.retranscribe_reclassify --classify-only --date-from 2026-03-07 --date-to 2026-03-07
  ./run_local.sh python -m call_analytics.retranscribe_reclassify --reevaluate-only --date-from 2026-04-09 --date-to 2026-04-12
  ./run_local.sh python -m call_analytics.retranscribe_reclassify --reevaluate-only --date-from 2026-04-06 --date-to 2026-04-13
  # Откат классификатора на монолит legacy: VIKINGI_CLASSIFY_LEGACY=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify ...
  ./run_local.sh python -m call_analytics.retranscribe_reclassify --dry-run --date-from 2026-03-01

  Вся база (постранично, без потолка 5000): с широким диапазоном дат или явно:
  USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --all \\
    --date-from 2020-01-01 --date-to 2099-12-31

  Опционально: VIKINGI_TRANSCRIBE_MIN_DURATION_SEC — мин. длительность (с) для STT в этом режиме; по умолчанию 0 (все звонки).

GigaAM для длинных wav (>25 с): ./run_local.sh подставляет GIGAAM_CHUNK_FIRST_SEC=8,
GIGAAM_CHUNK_OVERLAP_SEC=2, GIGAAM_CHUNK_DEDUP_WORDS=12, а также GIGAAM_COMBO_ENABLED=0 и
GIGAAM_SPLIT_ON_TRANSFER_TONES=0 (если не задано в .env). См. transcribe_audio_gigaam.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Перетранскрибация, классификация и оценка звонков")
    parser.add_argument("--date-from", help="Дата начала (YYYY-MM-DD)")
    parser.add_argument("--date-to", help="Дата окончания (YYYY-MM-DD)")
    parser.add_argument("--call-ids", help="ID звонков через запятую (740,741,742)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Все звонки с file_path в диапазоне дат (постраничная выборка из БД, без лимита 5000)",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=3000,
        metavar="N",
        help="Размер страницы при --all (по умолчанию 3000)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50000,
        metavar="N",
        help="Без --all: максимум звонков за один запрос list_calls (по умолчанию 50000)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="Без --all: смещение в list_calls для порции за прогон",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет обработано")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--classify-only",
        action="store_true",
        help="Только классификация по существующим транскриптам (без STT и без пересчёта оценок)",
    )
    mode.add_argument(
        "--reevaluate-only",
        action="store_true",
        help="Классификация по существующим транскриптам + обновление оценок в call_quality_scores (без STT)",
    )
    args = parser.parse_args()

    from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager

    d_from = args.date_from or "2000-01-01"
    d_to = args.date_to or "2099-12-31"

    # Собираем список call_id
    call_ids: list = []
    if args.call_ids:
        call_ids = [int(x.strip()) for x in args.call_ids.split(",") if x.strip()]
    elif args.all:
        offset = 0
        page = max(100, min(args.list_limit, 10000))
        while True:
            rows = CallAnalyticsDB.list_calls(
                date_from=d_from,
                date_to=d_to,
                limit=page,
                offset=offset,
            )
            batch = [r["id"] for r in rows if r.get("file_path")]
            call_ids.extend(batch)
            offset += len(rows)
            if len(rows) < page:
                break
    elif args.date_from or args.date_to:
        rows = CallAnalyticsDB.list_calls(
            date_from=d_from,
            date_to=d_to,
            limit=max(1, args.limit),
            offset=max(0, args.offset),
        )
        call_ids = [r["id"] for r in rows if r.get("file_path")]
    else:
        print("Укажите --date-from/--date-to, --call-ids или --all (с датами или без — тогда весь диапазон)")
        return 1

    if not call_ids:
        print("Звонков не найдено")
        return 0

    print(f"Найдено звонков: {len(call_ids)}")
    if args.dry_run:
        print("Dry-run: выход без обработки")
        return 0

    if args.classify_only:
        return _run_classify_only(call_ids)
    if args.reevaluate_only:
        return _run_reevaluate_only(call_ids)

    # Загрузка STT и оценщика
    use_gigaam = os.environ.get("USE_GIGAAM", "").strip().lower() in ("1", "true", "yes")
    stt_name = "GigaAM" if use_gigaam else "резервный STT"
    print(f"STT: {stt_name}")

    # 0 — транскрибировать все звонки с файлом; раньше было 60 (пропуск коротких).
    MIN_DURATION_SEC = int(os.environ.get("VIKINGI_TRANSCRIBE_MIN_DURATION_SEC", "0"))

    from analyze_call_quality import (
        load_gigaam_model,
        load_stt_model,
        transcribe_audio,
        transcribe_audio_gigaam,
    )
    from call_analytics.classify_by_transcript import classify_auto

    if use_gigaam:
        model = load_gigaam_model()
    else:
        model = load_stt_model()

    base_dir = PROJECT_DIR
    processed = 0
    skipped = 0

    for i, call_id in enumerate(call_ids, 1):
        print(f"\n[{i}/{len(call_ids)}] call_id={call_id} ...", end=" ", flush=True)
        try:
            row = CallAnalyticsDB.get_call_with_details(call_id)
            if not row or not row.get("file_path"):
                print("пропуск (нет записи/пути)")
                skipped += 1
                continue

            dur = row.get("duration_seconds")
            if dur is not None and dur < MIN_DURATION_SEC:
                print(f"пропуск (длительность {dur} с < {MIN_DURATION_SEC})")
                skipped += 1
                continue

            file_path = Path(row["file_path"])
            if not file_path.is_absolute():
                file_path = base_dir / file_path
            if not file_path.exists():
                print("пропуск (файл не найден)")
                skipped += 1
                continue

            # Транскрибация
            if use_gigaam:
                normalized, segments, _ = transcribe_audio_gigaam(file_path, model)
            else:
                normalized, segments, _ = transcribe_audio(file_path, model)

            # Классификация (сначала — чтобы знать, оценивать ли и какими критериями)
            if len((normalized or "").strip()) < 20:
                dept, call_type = "OTHER", "OTHER"
            else:
                dept, call_type = classify_auto(normalized)
            try:
                from text_normalization import augment_sto_in_greeting_if_needed

                normalized = augment_sto_in_greeting_if_needed(normalized, dept, call_type)
            except Exception:
                pass

            # Удаление старых данных
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM call_quality_scores WHERE call_id = %s", (call_id,))
                    cur.execute("DELETE FROM call_transcriptions WHERE call_id = %s", (call_id,))

            # Сохранение транскрипции
            tr_id = CallAnalyticsDB.save_transcription(
                call_id=call_id,
                transcription_text=normalized,
                segments=segments,
                stt_model="gigaam" if use_gigaam else "faster-whisper",
            )

            CallAnalyticsDB.update_call_department_type(call_id, dept, call_type)
            from call_analytics.sync_call_sto_metadata import sync_call_sto_metadata

            sto_meta = sync_call_sto_metadata(call_id, normalized, dept, call_type)

            from call_analytics.quality_evaluation import save_quality_scores_for_call_type

            overall = None
            if call_type in ("OP_IN", "OP_OUT", "STO_IN", "STO_OUT"):
                _, overall, _ = save_quality_scores_for_call_type(
                    call_id=call_id,
                    transcription_id=tr_id,
                    normalized=normalized,
                    call_type=call_type,
                    sto_to_rubric_type=sto_meta.get("sto_to_rubric_type"),
                )
            if dept in ("OP", "STO", "OTHER"):
                try:
                    from analyze_call_quality import apply_auto_manager_for_call

                    extract_dept = dept if dept in ("OP", "STO") else None
                    apply_auto_manager_for_call(call_id, normalized, extract_dept, existing_row=row)
                except Exception:
                    pass
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE calls SET status = %s WHERE id = %s", ("analyzed", call_id))

            rub = sto_meta.get("sto_to_rubric_type") or "—"
            print(
                f"OK {dept} {call_type} ТО-рубрика={rub} балл={overall:.1f}"
                if overall is not None
                else f"OK {dept} {call_type} ТО-рубрика={rub} (без оценки)"
            )
            print("--- Транскрипт ---")
            print(normalized)
            print("---")
            processed += 1
        except Exception as e:
            print(f"ошибка: {e}")
            skipped += 1

    print(f"\nОбработано: {processed}, пропущено: {skipped}")
    return 0


def _run_classify_only(call_ids: list) -> int:
    """Только классификация по существующим транскриптам, без STT и оценки."""
    from database.postgresql_manager import CallAnalyticsDB
    from call_analytics.classify_by_transcript import classify_auto
    from text_normalization import normalize_text

    try:
        from config import USE_LEGACY_CLASSIFY_TRANSCRIPT

        eng = "legacy (запасной)" if USE_LEGACY_CLASSIFY_TRANSCRIPT else "v2 (главный)"
    except ImportError:
        eng = "v2 (главный)"
    print(f"Режим: --classify-only (по существующим транскриптам), классификатор: {eng}")
    processed = 0
    skipped = 0

    for i, call_id in enumerate(call_ids, 1):
        print(f"\n[{i}/{len(call_ids)}] call_id={call_id} ...", end=" ", flush=True)
        try:
            row = CallAnalyticsDB.get_call_with_details(call_id)
            if not row:
                print("пропуск (нет записи)")
                skipped += 1
                continue

            tr = row.get("transcription")
            if not tr or not isinstance(tr, dict):
                print("пропуск (нет транскрипции)")
                skipped += 1
                continue

            text = tr.get("transcription_text") or ""
            if not text or not text.strip():
                print("пропуск (пустая транскрипция)")
                skipped += 1
                continue

            text = normalize_text(text)
            if len((text or "").strip()) < 20:
                dept, call_type = "OTHER", "OTHER"
            else:
                dept, call_type = classify_auto(text)
            CallAnalyticsDB.update_call_department_type(call_id, dept, call_type)
            from call_analytics.sync_call_sto_metadata import sync_call_sto_metadata

            sync_call_sto_metadata(call_id, text, dept, call_type)
            if dept in ("OP", "STO", "OTHER"):
                try:
                    from analyze_call_quality import apply_auto_manager_for_call

                    extract_dept = dept if dept in ("OP", "STO") else None
                    apply_auto_manager_for_call(call_id, text, extract_dept, existing_row=row)
                except Exception:
                    pass
            print(f"OK {dept} {call_type}")
            processed += 1
        except Exception as e:
            print(f"ошибка: {e}")
            skipped += 1

    print(f"\nОбработано: {processed}, пропущено: {skipped}")
    return 0


def _run_reevaluate_only(call_ids: list) -> int:
    """Классификация по текущей транскрипции, затем пересчёт оценок (как после полной обработки)."""
    from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager
    from call_analytics.classify_by_transcript import classify_auto
    from call_analytics.quality_evaluation import save_quality_scores_for_call_type
    from text_normalization import normalize_text

    try:
        from config import USE_LEGACY_CLASSIFY_TRANSCRIPT

        eng = "legacy (запасной)" if USE_LEGACY_CLASSIFY_TRANSCRIPT else "v2 (главный)"
    except ImportError:
        eng = "v2 (главный)"
    print(f"Режим: --reevaluate-only (классификация + оценки по существующим транскриптам), классификатор: {eng}")
    processed = 0
    skipped = 0

    for i, call_id in enumerate(call_ids, 1):
        print(f"\n[{i}/{len(call_ids)}] call_id={call_id} ...", end=" ", flush=True)
        try:
            row = CallAnalyticsDB.get_call_with_details(call_id)
            if not row:
                print("пропуск (нет записи)")
                skipped += 1
                continue

            tr = row.get("transcription")
            if not tr or not isinstance(tr, dict):
                print("пропуск (нет транскрипции)")
                skipped += 1
                continue

            text = tr.get("transcription_text") or ""
            if not text or not text.strip():
                print("пропуск (пустая транскрипция)")
                skipped += 1
                continue

            text = normalize_text(text)
            if len((text or "").strip()) < 20:
                dept, call_type = "OTHER", "OTHER"
            else:
                dept, call_type = classify_auto(text)
            try:
                from text_normalization import augment_sto_in_greeting_if_needed

                text = augment_sto_in_greeting_if_needed(text, dept, call_type)
            except Exception:
                pass

            tr_id = tr.get("id")
            if tr_id and isinstance(tr_id, int):
                CallAnalyticsDB.update_transcription_text(tr_id, text)

            CallAnalyticsDB.update_call_department_type(call_id, dept, call_type)
            from call_analytics.sync_call_sto_metadata import sync_call_sto_metadata

            sto_meta = sync_call_sto_metadata(call_id, text, dept, call_type)
            if dept in ("OP", "STO", "OTHER"):
                try:
                    from analyze_call_quality import apply_auto_manager_for_call

                    extract_dept = dept if dept in ("OP", "STO") else None
                    apply_auto_manager_for_call(call_id, text, extract_dept, existing_row=row)
                except Exception:
                    pass

            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM call_quality_scores WHERE call_id = %s", (call_id,))

            overall = None
            if call_type in ("OP_IN", "OP_OUT", "STO_IN", "STO_OUT"):
                _, overall, _ = save_quality_scores_for_call_type(
                    call_id=call_id,
                    transcription_id=tr_id,
                    normalized=text,
                    call_type=call_type,
                    sto_to_rubric_type=sto_meta.get("sto_to_rubric_type"),
                )
            rub = sto_meta.get("sto_to_rubric_type") or "—"
            print(
                f"OK {dept} {call_type} ТО-рубрика={rub} балл={overall:.1f}"
                if overall is not None
                else f"OK {dept} {call_type} ТО-рубрика={rub} (без оценки)"
            )
            processed += 1
        except Exception as e:
            print(f"ошибка: {e}")
            skipped += 1

    print(f"\nОбработано: {processed}, пропущено: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
