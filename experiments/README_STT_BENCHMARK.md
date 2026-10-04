# Бенчмарк STT на коротких WAV («да» / «согласна» / «хорошо»)

## Что где

| Скрипт | Назначение |
|--------|------------|
| `stt_compare.sh` | Запросы к **`/transcribe/bytes`** (как у голосового бота), 5 гипотез |
| `stt_compare_whisper_transcribe.sh` | Запросы к **`/transcribe`** (нормальный WAV), `STT_TRANSCRIBE_VARIANT=primary\|backup` |
| `run_whisper_medium_large_benchmark.sh` | Подряд **primary + backup**, общий `.tsv` и сводка (имя файла историческое) |
| `run_gigaam_stt_batch.py` | **GigaAM-v3** локально (вне Docker STT) |
| `run_stt_silero_whisper_small_gigaam.py` | **Silero** (публичный JIT) + **faster-whisper small** + **GigaAM v3** в одном прогоне |

## Silero (публичные модели)

- В актуальном `models.yml` у открытого Silero **нет русского STT** (только en, de, es, ua).
- Скрипт `run_stt_silero_whisper_small_gigaam.py` по умолчанию грузит **`SILERO_STT_LANG=en`** и помечает движок как **`silero_en_not_ru`**: это **не оценка русского ASR**, а проверка пайплайна / латентности.
- JIT, собранные под старый PyTorch, на **torch 2.10** с **украинской** моделью часто падают в `stft` — при необходимости пробуйте другой `SILERO_STT_LANG` или отключите: `SKIP_SILERO=1`.

## Важно про GigaAM

- В контейнере **`stt-service`** основной движок — **GigaAM** (`STT_ENGINE=gigaam`). Запасной конвейер STT подключается опционально через переменные окружения.
- Для GigaAM в `models/gigaam-v3` добавлен **`modeling_gigaam.py`** (копия из HF `transformers_modules`), иначе `from_pretrained` не подхватит код.
- Текущий проектный **venv: torch 2.10 + torchaudio 2.10** — при создании `MelSpectrogram` в GigaAM часто падает с **`meta tensors`**. Пакетный прогон не стартует, пока не понизите torch/torchaudio (например 2.2–2.4) или не появится патч.

## Запуск

```bash
# сравнение primary / backup на /transcribe (рекомендуется)
bash experiments/run_whisper_medium_large_benchmark.sh

# по отдельности
STT_TRANSCRIBE_VARIANT=primary bash experiments/stt_compare_whisper_transcribe.sh
STT_TRANSCRIBE_VARIANT=backup  bash experiments/stt_compare_whisper_transcribe.sh

# GigaAM (после починки окружения)
./venv/bin/python experiments/run_gigaam_stt_batch.py

# Silero (en|de|es|ua) + faster-whisper small + GigaAM (если загрузится)
./venv/bin/python experiments/run_stt_silero_whisper_small_gigaam.py
```

Переменные: `STT_URL` (по умолчанию `http://127.0.0.1:8001`), `STT_WAV_DIR`, `STT_BENCH_OUT` (куда писать `.tsv`/лог).

## «Две версии GigaAM»

На сервере фактически один набор весей в `models/gigaam-v3`. Второй «вариант» (например загрузка с Hugging Face `ai-sage/GigaAM-v3`) даст тот же чекпоинт после стабилизации окружения — имеет смысл только если появится **другая** папка модели или отдельный venv.
