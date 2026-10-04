#!/usr/bin/env python3
"""
Основной процесс: GigaAM (не Whisper, не гибрид), полный транскрипт, таблица оценки.
Файлы Analytic/SALE/*.wav → транскрибация GigaAM, нормализация, оценка по критериям 3–16 → quality.xlsx.

Запуск (только правила, без LLM; ~1 мин на 10 файлов):
  cd /home/romandemo/Vikingi && .venv/bin/python run_sale_gigaam_llm_norm.py
  # только первые 10 файлов:
  MAX_FILES=10 .venv/bin/python run_sale_gigaam_llm_norm.py

Запуск с LLM (нормализация и/или оценка):
  USE_LLM_NORMALIZE=1 USE_LLM_EVALUATE=1 LLM_MODEL_PATH=/path/to/model.gguf \\
  .venv/bin/python run_sale_gigaam_llm_norm.py
"""

import os
import sys
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SALE_DIR = PROJECT_DIR / "Analytic" / "SALE"
OUTPUT_XLSX = SALE_DIR / "quality.xlsx"

# Короткие подписи колонок (умещаются в ячейку без расширения)
SCORE_HEADERS_SHORT = [
    ("p3_intro", "3.ПК"),
    ("p4_ask_name_form", "4.Имя"),
    ("p5_name_usage_3plus", "5.≥3"),
    ("p6_car_interest", "6.Авто"),
    ("p7_familiar_with_car", "7.Знак."),
    ("p8_for_whom", "8.Кого"),
    ("p9_purchase_timing", "9.Сроки"),
    ("p10_payment_form", "10.Оплата"),
    ("p11_current_car", "11.Тек."),
    ("p12_invite_to_dc", "12.ДЦ"),
    ("p13_test_drive", "13.Т-д"),
    ("p14_ask_contacts", "14.Конт."),
    ("p15_send_contacts", "15.Отпр."),
    ("p16_thanks", "16.Благ."),
    ("total_score", "Балл"),
]

from analyze_op_mono import extract_both_channels_mono, get_tonal_speech_segments
from analyze_call_quality import (
    load_gigaam_model,
    transcribe_audio_gigaam,
    evaluate_call_auto,
    build_evaluation_explanations,
    extract_manager_name_from_full_transcript,
    extract_customer_name_from_full_transcript,
    EVALUATION_CRITERIA,
)
from text_normalization import normalize_transcript

import soundfile as sf
import tempfile


def _audio_files(sale_dir: Path):
    out = list(sale_dir.glob("*.wav"))

    def _key(p: Path):
        try:
            return (0, int(p.stem), p.name)
        except ValueError:
            return (1, 0, p.name)

    return sorted(out, key=_key)


def _transcribe_segments_gigaam(audio, sr, speech_intervals, model):
    pieces = []
    temp_paths = []
    try:
        for start, end in speech_intervals:
            seg = audio[start:end]
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            temp_paths.append(Path(path))
            sf.write(path, seg, sr)
            txt, _, _ = transcribe_audio_gigaam(Path(path), model, normalize_with_llm=False)
            if txt and txt.strip():
                pieces.append(txt.strip())
            else:
                pieces.append("")
        return " ".join(p for p in pieces if p) if pieces else ""
    finally:
        for p in temp_paths:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


def main() -> int:
    if not SALE_DIR.exists():
        print(f"Папка не найдена: {SALE_DIR}")
        return 1

    files = _audio_files(SALE_DIR)
    if not files:
        print(f"В {SALE_DIR} нет .wav")
        return 1
    max_files = os.environ.get("MAX_FILES", "")
    if max_files.isdigit():
        files = files[: int(max_files)]
        print(f"Обработка первых {len(files)} файлов", flush=True)

    use_llm_norm = os.environ.get("USE_LLM_NORMALIZE", "").strip().lower() in ("1", "true", "yes")
    use_llm_eval = os.environ.get("USE_LLM_EVALUATE", "").strip().lower() in ("1", "true", "yes")
    print(f"Режим: GigaAM + нормализация (правила" + (" + LLM)" if use_llm_norm else "") + ") + оценка (" + ("LLM" if use_llm_eval else "правила") + ")", flush=True)
    print("Загрузка GigaAM-v3...", flush=True)
    model = load_gigaam_model()

    def _write_results(rows: list, out_path: Path) -> None:
        SALE_DIR.mkdir(parents=True, exist_ok=True)
        # Таблица оценок: Файл, Имя клиента, Имя менеджера + короткие подписи (умещаются в ячейку)
        table_cols = ["Файл", "Имя клиента", "Имя менеджера"] + [s for _, s in SCORE_HEADERS_SHORT]
        table_data = []
        for r in rows:
            table_data.append({
                "Файл": r["Файл"],
                "Имя клиента": r.get("Имя клиента", ""),
                "Имя менеджера": r.get("Имя менеджера", ""),
                **{short: r.get(key, "") for key, short in SCORE_HEADERS_SHORT},
            })
        df_eval = pd.DataFrame(table_data, columns=table_cols)
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df_eval.to_excel(writer, sheet_name="Оценка", index=False)
            ws = writer.sheets["Оценка"]
            ws.column_dimensions["A"].width = 10
            ws.column_dimensions["B"].width = 12
            ws.column_dimensions["C"].width = 14
            for col_letter in "DEFGHIJKLMNOPQR":
                ws.column_dimensions[col_letter].width = 8
            # Под таблицей — критерии оценки (сколько за что)
            criteria_start = len(rows) + 3
            ws.cell(row=criteria_start, column=1, value="Критерии оценки (сколько за что):")
            for i, (key, meta) in enumerate(EVALUATION_CRITERIA.items(), start=1):
                ws.cell(
                    row=criteria_start + i,
                    column=1,
                    value=f"{meta['name']} — 0–1 (вес 1/14). {meta['description']}.",
                )
            ws.cell(
                row=criteria_start + len(EVALUATION_CRITERIA) + 1,
                column=1,
                value="Общий балл = (сумма по критериям 3–16) × (5/14), шкала 0–5.",
            )
            # Лист «Пояснения»: по каждому файлу — объяснение оценки по каждому пункту
            expl_rows = [["Файл", "Критерий", "Пояснение"]]
            for r in rows:
                expl = r.get("_explanations") or {}
                for key, meta in EVALUATION_CRITERIA.items():
                    name = meta["name"]
                    expl_rows.append([r["Файл"], name, expl.get(key, "—")])
                expl_rows.append([r["Файл"], "Общий балл", expl.get("total_score", "—")])
            pd.DataFrame(expl_rows[1:], columns=expl_rows[0]).to_excel(
                writer, sheet_name="Пояснения", index=False
            )
            ws_ex = writer.sheets["Пояснения"]
            ws_ex.column_dimensions["A"].width = 10
            ws_ex.column_dimensions["B"].width = 42
            ws_ex.column_dimensions["C"].width = 50
            # Листы 2–11: полный транскрипт по файлам; перенос текста в ячейке — весь текст виден
            for idx, r in enumerate(rows):
                sheet_name = str(idx + 1)
                transcript = (r.get("Транскрипт") or "").strip() or "(пусто)"
                tx_df = pd.DataFrame([{"Файл": r["Файл"], "Транскрипт": transcript}])
                tx_df.to_excel(writer, sheet_name=sheet_name, index=False)
                tx_ws = writer.sheets[sheet_name]
                tx_ws.column_dimensions["A"].width = 10
                tx_ws.column_dimensions["B"].width = 80
                cell_b2 = tx_ws.cell(row=2, column=2, value=transcript)
                cell_b2.alignment = Alignment(wrap_text=True, vertical="top")
                tx_ws.cell(row=2, column=1, value=r["Файл"])

    rows = []
    for i, wav_path in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {wav_path.name}", flush=True)
        mono_path = None
        try:
            print("  🔊 Моно (оба канала)...", flush=True)
            mono_path = extract_both_channels_mono(wav_path)
            audio, sr, speech_intervals = get_tonal_speech_segments(mono_path)
            print(f"  ✂️ Сегментов: {len(speech_intervals)}", flush=True)
            print("  📝 GigaAM...", flush=True)
            text_gigaam = _transcribe_segments_gigaam(audio, sr, speech_intervals, model)
            text_gigaam = (text_gigaam or "(пусто)").strip()
            print("  📐 Нормализация...", flush=True)
            text_gigaam = normalize_transcript(text_gigaam)
            print("  📊 Оценка качества...", flush=True)
            scores = evaluate_call_auto(text_gigaam)
            manager_name = extract_manager_name_from_full_transcript(text_gigaam) or ""
            customer_name = extract_customer_name_from_full_transcript(text_gigaam, manager_name or None) or ""
            explanations = build_evaluation_explanations(text_gigaam, scores)
            row = {
                "Файл": wav_path.name,
                "Имя клиента": customer_name,
                "Имя менеджера": manager_name,
                "Транскрипт": text_gigaam,
                "_explanations": explanations,
                "p3_intro": f"{scores['p3_intro']:.2f}",
                "p4_ask_name_form": f"{scores['p4_ask_name_form']:.2f}",
                "p5_name_usage_3plus": f"{scores['p5_name_usage_3plus']:.2f}",
                "p6_car_interest": f"{scores['p6_car_interest']:.2f}",
                "p7_familiar_with_car": f"{scores['p7_familiar_with_car']:.2f}",
                "p8_for_whom": f"{scores['p8_for_whom']:.2f}",
                "p9_purchase_timing": f"{scores['p9_purchase_timing']:.2f}",
                "p10_payment_form": f"{scores['p10_payment_form']:.2f}",
                "p11_current_car": f"{scores['p11_current_car']:.2f}",
                "p12_invite_to_dc": f"{scores['p12_invite_to_dc']:.2f}",
                "p13_test_drive": f"{scores['p13_test_drive']:.2f}",
                "p14_ask_contacts": f"{scores['p14_ask_contacts']:.2f}",
                "p15_send_contacts": f"{scores['p15_send_contacts']:.2f}",
                "p16_thanks": f"{scores['p16_thanks']:.2f}",
                "total_score": f"{scores['total_score']:.2f}/5.0",
            }
            rows.append(row)
        except Exception as e:
            print(f"  ❌ Ошибка: {e}", flush=True)
            row = {
                "Файл": wav_path.name,
                "Имя клиента": "",
                "Имя менеджера": "",
                "Транскрипт": f"(ошибка: {e})",
                "_explanations": {},
            }
            for key, _ in SCORE_HEADERS_SHORT:
                row[key] = ""
            rows.append(row)
        finally:
            if mono_path and mono_path != wav_path and mono_path.exists():
                try:
                    mono_path.unlink()
                except Exception:
                    pass
        _write_results(rows, OUTPUT_XLSX)

    print(f"\nРезультат записан в {OUTPUT_XLSX} (полный транскрипт + таблица оценки)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
