#!/usr/bin/env bash
# ВКЛ/ВЫКЛ ЛЛМ: true / false
export VIKINGI_USE_LLM=false

cd /home/romandemo/Vikingi

# Логи в папке logs/ (не засоряют корень проекта)
mkdir -p logs
if [ -f logs/vikingi_demo.log ]; then
  mv logs/vikingi_demo.log "logs/vikingi_demo_$(date +%Y%m%d_%H%M%S).log"
fi

# Запуск бота с выводом как на экран, так и в лог
# При ошибке терминал останется открытым для просмотра
.venv/bin/python demo_main.py 2>&1 | tee logs/vikingi_demo.log
EXIT_CODE=$?
# Всегда ждём нажатия Enter перед закрытием (чтобы видеть ошибки)
if [ $EXIT_CODE -ne 0 ]; then
  echo ""
  echo "❌ Бот завершился с ошибкой (код: $EXIT_CODE). Проверьте логи выше."
else
  echo ""
  echo "✅ Бот завершил работу."
fi
echo "Нажмите Enter для закрытия терминала..."
read