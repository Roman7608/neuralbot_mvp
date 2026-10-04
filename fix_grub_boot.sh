#!/bin/bash
# Скрипт для восстановления GRUB и настройки загрузки

echo "=========================================="
echo "Восстановление GRUB загрузчика"
echo "=========================================="

echo ""
echo "1. Проверка текущего состояния GRUB..."
if [ -f /boot/grub/grub.cfg ]; then
    echo "✅ GRUB конфигурация найдена"
else
    echo "❌ GRUB конфигурация не найдена"
fi

echo ""
echo "2. Проверка EFI раздела..."
if [ -d /boot/efi ]; then
    echo "✅ EFI раздел смонтирован: $(df -h /boot/efi | tail -1 | awk '{print $1}')"
else
    echo "❌ EFI раздел не найден"
    exit 1
fi

echo ""
echo "3. Установка/обновление GRUB в EFI раздел..."
sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=Ubuntu

if [ $? -eq 0 ]; then
    echo "✅ GRUB установлен в EFI раздел"
else
    echo "❌ Ошибка установки GRUB"
    exit 1
fi

echo ""
echo "4. Обновление конфигурации GRUB..."
sudo update-grub

if [ $? -eq 0 ]; then
    echo "✅ Конфигурация GRUB обновлена"
else
    echo "❌ Ошибка обновления конфигурации"
    exit 1
fi

echo ""
echo "5. Проверка записей загрузки UEFI..."
echo "Текущие записи загрузки:"
sudo efibootmgr -v

echo ""
echo "=========================================="
echo "Готово!"
echo "=========================================="
echo ""
echo "Следующие шаги:"
echo "1. Перезагрузите компьютер БЕЗ флешки"
echo "2. При загрузке нажмите F12 (или другую клавишу для меню загрузки)"
echo "3. Выберите 'Ubuntu' из списка"
echo "4. Если Ubuntu не появляется в меню:"
echo "   - Войдите в BIOS/UEFI (обычно F2 или Del)"
echo "   - Найдите раздел 'Boot' или 'Загрузка'"
echo "   - Установите 'Ubuntu' первым в списке приоритета загрузки"
echo "   - Сохраните и перезагрузитесь"
echo ""
