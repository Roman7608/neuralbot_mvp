#!/usr/bin/env bash
# Настройка на server7: fstab для sprecord_arch → /mnt/sprecord, credentials, systemd timer.
# Запуск: cd /home/vikingi/VikingiAll && sudo bash scripts/setup_sprecord_cifs_server7.sh
#
# Идемпотентно: не дублирует строку fstab, правит sprecord_DB→/mnt/sprecord если была.

set -euo pipefail

CRED_FILE=/root/.smbcredentials-sprecord
FSTAB_LINE='//192.168.0.5/sprecord_arch  /mnt/sprecord  cifs  vers=3.0,credentials=/root/.smbcredentials-sprecord,iocharset=utf8,file_mode=0644,dir_mode=0755,uid=1000,gid=1000,_netdev,nofail  0  0'

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запустите от root: sudo bash $0" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="${REPO_ROOT}/scripts/systemd"

echo "=== Резервная копия /etc/fstab ==="
cp -a /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"

echo "=== Удаление ошибочной строки sprecord_DB → /mnt/sprecord (если есть) ==="
if grep -qE '^//192\.168\.0\.5/sprecord_DB[[:space:]]+/mnt/sprecord' /etc/fstab; then
  tmp="$(mktemp)"
  awk '!/^\/\/192\.168\.0\.5\/sprecord_DB[ \t]+\/mnt\/sprecord/ {print}' /etc/fstab >"$tmp"
  mv "$tmp" /etc/fstab
  echo "Удалена строка sprecord_DB на /mnt/sprecord."
fi

echo "=== Строка fstab для sprecord_arch ==="
if grep -qE '//192\.168\.0\.5/sprecord_arch[[:space:]]+/mnt/sprecord' /etc/fstab; then
  echo "Строка sprecord_arch → /mnt/sprecord уже есть."
else
  echo "$FSTAB_LINE" >> /etc/fstab
  echo "Добавлена строка в /etc/fstab."
fi

echo "=== ${CRED_FILE} ==="
if [[ -f "$CRED_FILE" ]]; then
  echo "Файл уже существует, не перезаписываю."
else
  umask 077
  cat >"$CRED_FILE" <<'EOF'
username=bot7
password=Kalina816
domain=NISSAN-TLT
EOF
  chmod 600 "$CRED_FILE"
  echo "Создан $CRED_FILE (при смене пароля на server5 отредактируйте)."
fi

echo "=== Каталог точки монтирования ==="
mkdir -p /mnt/sprecord

echo "=== Установка systemd timer ==="
install -m 644 "${UNIT_SRC}/sprecord-mount-retry.service" /etc/systemd/system/sprecord-mount-retry.service
install -m 644 "${UNIT_SRC}/sprecord-mount-retry.timer" /etc/systemd/system/sprecord-mount-retry.timer
systemctl daemon-reload
systemctl enable sprecord-mount-retry.timer
systemctl start sprecord-mount-retry.timer

echo "=== Пробуем смонтировать сейчас ==="
if mountpoint -q /mnt/sprecord; then
  echo "/mnt/sprecord уже смонтирован."
else
  mount /mnt/sprecord && echo "Смонтировано." || echo "Не удалось сейчас (server5 недоступен?) — timer повторит."
fi

echo ""
echo "Готово. Проверка:"
echo "  findmnt /mnt/sprecord"
echo "  systemctl list-timers sprecord-mount-retry.timer"
