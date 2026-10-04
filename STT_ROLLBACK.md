# Резервный снимок цепочки STT в git

Состояние части проекта «СТТ + аналитика по правилам» зафиксировано в теге **`stt-whisper-backup`** (историческое имя тега).

## Текущая конфигурация

- **По умолчанию**: GigaAM v3 для аналитики и голосового контура (см. `GIGAAM_SETUP.md`).
- **Предзагрузка модели** (один раз при наличии интернета): `python download_gigaam.py`

## Полный откат к снимку до текущей схемы (git)

```bash
git checkout stt-whisper-backup -- analyze_call_quality.py config.py text_normalization.py recalculate_quality_from_quality2.py transcribe_with_diarization.py compare_llm_vs_rules.py
```

Или полностью переключиться на это состояние:

```bash
git checkout stt-whisper-backup
```

(Внимание: detached HEAD; для работы лучше ветка: `git checkout -b restore-legacy-stt stt-whisper-backup`)

## Что входит в снимок

- **analyze_call_quality.py** — транскрибация, `evaluate_call_by_rules`, `merge_transcription_with_diarization`
- **config.py** — пути моделей и устройство STT, `GIGAAM_MODEL_PATH`, `GIGAAM_REVISION`
- **text_normalization.py** — `normalize_transcript` для выхода STT
- **recalculate_quality_from_quality2.py** — пересчёт оценок по правилам
- **transcribe_with_diarization.py** — транскрибация с диаризацией
- **compare_llm_vs_rules.py** — сравнение LLM и правил

## Дата фиксации

Тег создан перед переходом на GigaAM v3.
