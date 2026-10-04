# GigaAM-v3: настройка и офлайн-работа

## Быстрый старт

1. **Установить зависимости** (один раз):
   ```bash
   pip install -r requirements-gigaam.txt
   ```

2. **Предзагрузить модель** (один раз, при наличии интернета):
   ```bash
   python download_gigaam.py
   ```

3. **Запускать анализ** (GigaAM):
   ```bash
   python analyze_call_quality.py
   ```

По умолчанию для аналитики используется GigaAM. После предзагрузки всё работает локально, без доступа в интернет.

## Пути

- **Голосовой бот** (services/stt): `models/gigaam-v3/` — e2e_ctc
- **Аналитика** (analyze_call_quality, retranscribe_reclassify, админка): `models/gigaam-v3-e2e_rnnt/` — e2e_rnnt (лучше WER)
- При отсутствии e2e_rnnt аналитика использует fallback `models/gigaam-v3`

## Переключение STT

- **GigaAM-v3** (по умолчанию): `python analyze_call_quality.py`
- Резервный снимок прежней цепочки STT в git: см. [STT_ROLLBACK.md](STT_ROLLBACK.md).

## Режимы транскрипции для длинных файлов (> 25 с)

| Режим | Переменная | Описание |
|-------|------------|----------|
| **По умолчанию** | `USE_GIGAAM_LONGFORM_VAD=1` | Long-form с VAD (pyannote), overlap 1 с. Требует патч. |
| **Опционально** | `USE_GIGAAM_LONGFORM_VAD=0` | Старый чанковый режим (фиксированные окна), без VAD. Патч не нужен. |

### Патч VAD+overlap (режим по умолчанию)

Патч `patches/gigaam_modeling_vad_overlap.patch` вносит в `modeling_gigaam.py`:
- `min_duration_off` из env (порог тишины, по умолчанию 1.0 с)
- `overlap_sec` между сегментами (1 с)

**Применение:** см. [patches/README.md](patches/README.md)

- `download_gigaam.py` применяет патч автоматически после загрузки e2e_rnnt
- При повторной загрузке: `python patches/apply_gigaam_vad_patch.py`
- Без патча: `python download_gigaam.py --no-apply-patch` (старый режим)

**Переменные:**
- **GIGAAM_VAD_MIN_DURATION_OFF** — порог тишины (сек), по умолчанию 1.0
- **GIGAAM_FIRST_CHUNK_MAX_SEC** — макс. длина первого чанка (сек). 6 по умолчанию — лучше распознавание приветствия. 0 — отключить
- **GIGAAM_LONGFORM_OVERLAP_SEC** — overlap между чанками, по умолчанию 1.0

## Фильтр по длительности

- **Автозабор** (`autofetch_sprecord.py`): файлы < 60 с не копируются и не регистрируются в БД.
- **Перетранскрибация** (`retranscribe_reclassify`): звонки с `duration_seconds < 60` пропускаются.
