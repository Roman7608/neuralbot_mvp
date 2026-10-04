# Патчи GigaAM

## gigaam_modeling_vad_overlap.patch

Изменения в `modeling_gigaam.py` для режима long-form с VAD:
- **min_duration_off** — порог тишины из env `GIGAAM_VAD_MIN_DURATION_OFF` (по умолчанию 1.0 с)
- **overlap_sec** — перекрытие сегментов (1 с)
- **transcribe_longform** — передача `**kwargs` в segment_audio_file

### Применение

```bash
# Автоматически при загрузке
python download_gigaam.py

# Вручную после повторной загрузки
python patches/apply_gigaam_vad_patch.py

# Откатить
python patches/apply_gigaam_vad_patch.py --revert
```

### Варианты загрузки

```bash
# По умолчанию: с патчем (long-form VAD)
python download_gigaam.py

# Без патча (старый чанковый режим)
python download_gigaam.py --no-apply-patch
```
