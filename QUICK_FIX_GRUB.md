# Быстрое восстановление GRUB

## Запуск скрипта

Скрипт находится в директории проекта. Выполните:

```bash
cd ~/Vikingi
./fix_grub_boot.sh
```

Или с полным путем:

```bash
~/Vikingi/fix_grub_boot.sh
```

## Ручное восстановление (если скрипт не работает)

Выполните команды по порядку:

```bash
# 1. Восстановить GRUB в EFI раздел
sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=Ubuntu

# 2. Обновить конфигурацию GRUB
sudo update-grub

# 3. Проверить записи загрузки UEFI
sudo efibootmgr -v
```

## После выполнения

1. Перезагрузите компьютер БЕЗ флешки
2. При загрузке нажмите F12 (меню загрузки)
3. Выберите "Ubuntu"
4. Если Ubuntu не появляется - настройте приоритет в BIOS/UEFI
