#!/bin/bash
# Генерация WAV-файлов голосового бота через контейнер TTS (torch + Silero).
# Запускать из корня проекта VikingiAll.

set -e
cd "$(dirname "$0")"

echo "Запуск генерации WAV через контейнер tts-service..."
docker compose run --rm \
  -v "$(pwd):/work" \
  -w /work \
  tts-service \
  python3 generate_all_voice_wavs.py

echo ""
echo "Готово. Файлы в audio_responses/"
