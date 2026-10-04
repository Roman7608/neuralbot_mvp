#!/usr/bin/env python3
"""
Эксперимент: анализ ОП и СТО по каналу сотрудника (моно).
  Без аргументов: ex.wav, STO/2.wav -> перезапись experiment.txt
  python run_mono_experiment.py op.wav sto.wav -> добавить оба в experiment.txt
  python run_mono_experiment.py op.wav -> добавить только ОП в experiment.txt
  python run_mono_experiment.py Analytic/STO/3.wav -> добавить только СТО (по пути с STO)
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from analyze_op_mono import run_op_mono
from analyze_sto_mono import run_sto_mono, STO_CRITERIA

OP_FILE = PROJECT_DIR / "Analytic" / "ex.wav"
STO_FILE = PROJECT_DIR / "Analytic" / "STO" / "2.wav"
OUTPUT_FILE = PROJECT_DIR / "Analytic" / "experiment.txt"


def format_op_result(result: dict) -> str:
    if "error" in result:
        return f"[ОП] Ошибка: {result['error']}\n"
    lines = [
        "=" * 60,
        "ОТДЕЛ ПРОДАЖ (ОП) — канал сотрудника",
        f"Файл: {result['file']}",
        f"Менеджер: {result['manager_name']}, Клиент: {result['customer_name']}",
        "",
        "Оценка по критериям (3–16):",
    ]
    for key, val in result.get("scores", {}).items():
        if key != "total_score":
            lines.append(f"  {key}: {val:.2f}")
    lines.append(f"  Общий балл: {result['scores'].get('total_score', 0):.2f}/5.0")
    lines.append("")
    lines.append("Транскрипт (речь менеджера):")
    lines.append("-" * 40)
    lines.append(result.get("transcript", "(пусто)"))
    lines.append("")
    return "\n".join(lines)


def format_sto_result(result: dict) -> str:
    if "error" in result:
        return f"[СТО] Ошибка: {result['error']}\n"
    lines = [
        "=" * 60,
        "СЕРВИС (СТО) — канал сотрудника",
        f"Файл: {result['file']}",
        f"Сотрудник: {result['employee_name']}",
        "",
        "Оценка по критериям 7–29:",
    ]
    scores = result.get("scores", {})
    for num, desc, _ in STO_CRITERIA:
        val = scores.get(num, 0)
        lines.append(f"  {num}. {desc}: {int(val)}")
    lines.append(f"  Итого: {result.get('total_points', 0):.1f}/100")
    lines.append("")
    lines.append("Транскрипт (речь сотрудника):")
    lines.append("-" * 40)
    lines.append(result.get("transcript", "(пусто)"))
    lines.append("")
    return "\n".join(lines)


def main():
    append_single = len(sys.argv) == 2
    append_both = len(sys.argv) >= 3
    append_mode = append_single or append_both

    if append_single:
        single_path = PROJECT_DIR / sys.argv[1]
        # Путь с STO в имени — сервис, иначе ОП
        is_sto = "sto" in str(single_path).lower()
        if is_sto:
            op_file = None
            sto_file = single_path
            print("=== Добавление СТО в experiment.txt ===\n", flush=True)
        else:
            op_file = single_path
            sto_file = None
            print("=== Добавление ОП в experiment.txt ===\n", flush=True)
    elif append_both:
        op_file = PROJECT_DIR / sys.argv[1]
        sto_file = PROJECT_DIR / sys.argv[2]
        print("=== Добавление в experiment.txt (ОП + СТО) ===\n", flush=True)
    else:
        op_file = OP_FILE
        sto_file = STO_FILE
        print("=== Эксперимент: моно-канал сотрудника ===\n", flush=True)

    # ОП
    op_text = ""
    if op_file is not None:
        print(f"1. Обработка ОП ({op_file.name})...", flush=True)
        op_result = run_op_mono(op_file)
        op_text = format_op_result(op_result)
        if "error" in op_result:
            print(f"   Ошибка: {op_result['error']}", flush=True)
        else:
            print(f"   OK. Балл: {op_result['scores'].get('total_score', 0):.2f}/5.0", flush=True)

    # СТО
    sto_text = ""
    if sto_file is not None:
        print(f"{'2.' if op_text else '1.'} Обработка СТО ({sto_file.name})...", flush=True)
        sto_result = run_sto_mono(sto_file)
        sto_text = format_sto_result(sto_result)
        if "error" in sto_result:
            print(f"   Ошибка: {sto_result['error']}", flush=True)
        else:
            print(f"   OK. Балл: {sto_result.get('total_points', 0):.1f}/100", flush=True)
    else:
        print("", flush=True)

    # Запись
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if append_mode and OUTPUT_FILE.exists():
        existing = OUTPUT_FILE.read_text(encoding="utf-8")
        content = existing.rstrip() + "\n\n" + (op_text or "") + (sto_text or "")
    else:
        parts = []
        if op_file:
            parts.append(f"ОП {op_file}")
        if sto_file:
            parts.append(f"СТО {sto_file}")
        header = [
            "ЭКСПЕРИМЕНТ: Аналитика по каналу сотрудника (без диаризации)",
            "Файлы: " + ", ".join(parts) if parts else "",
            "",
        ]
        content = "\n".join(header) + (op_text or "") + (sto_text or "")
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"\n✅ Результаты записаны в {OUTPUT_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
