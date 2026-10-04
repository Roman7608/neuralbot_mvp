#!/bin/bash
# Скрипт для обновления паролей в postgresql_config.py

echo "=========================================="
echo "Обновление паролей в postgresql_config.py"
echo "=========================================="

echo ""
echo "Введите пароли для обновления конфигурации:"
echo ""
read -sp "Пароль для analytics_user: " ANALYTICS_PASSWORD
echo ""
read -sp "Пароль для ics_user: " ICS_PASSWORD
echo ""

if [ -z "$ANALYTICS_PASSWORD" ] || [ -z "$ICS_PASSWORD" ]; then
    echo "❌ Ошибка: пароли не могут быть пустыми!"
    exit 1
fi

# Создание резервной копии
cp postgresql_config.py postgresql_config.py.backup

# Обновление паролей в файле
python3 <<EOF
import re

# Читаем файл
with open('postgresql_config.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Заменяем пароли
content = re.sub(
    r'POSTGRESQL_ANALYTICS_PASSWORD: str = "[^"]*"',
    f'POSTGRESQL_ANALYTICS_PASSWORD: str = "{ANALYTICS_PASSWORD}"',
    content
)

content = re.sub(
    r'POSTGRESQL_ICS_PASSWORD: str = "[^"]*"',
    f'POSTGRESQL_ICS_PASSWORD: str = "{ICS_PASSWORD}"',
    content
)

# Записываем обратно
with open('postgresql_config.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Пароли обновлены в postgresql_config.py")
EOF

echo ""
echo "=========================================="
echo "Готово!"
echo "=========================================="
echo ""
echo "⚠️ ВАЖНО: Убедитесь, что файл postgresql_config.py не попадает в Git!"
echo "Добавьте в .gitignore:"
echo "  postgresql_config.py"
echo ""
