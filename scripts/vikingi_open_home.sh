#!/usr/bin/env bash
# Открывает домашний каталог: несколько способов под xrdp/Xfce, если Thunar из ярлыка молчит.
set -u
HOME_DIR=/home/vikingi
LOG=/tmp/vikingi-open-home.log

{
  echo "---- $(date -Iseconds) ----"
  echo "DISPLAY=${DISPLAY-} XDG_SESSION_TYPE=${XDG_SESSION_TYPE-}"
  echo "USER=$(id -un)"
} >>"$LOG" 2>&1

if [ -z "${DISPLAY:-}" ]; then
  command -v zenity >/dev/null 2>&1 && zenity --error --text="Нет DISPLAY. Откройте терминал на рабочем столе (в окне Remmina), не по SSH без графики." 2>/dev/null || true
  exit 1
fi

# xrdp часто не выставляет XDG_RUNTIME_DIR — без него Thunar сразу завершается
_ensure_xdg_runtime() {
  local uid
  uid="$(id -u)"
  if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "${XDG_RUNTIME_DIR}" ] && [ -w "${XDG_RUNTIME_DIR}" ]; then
    return 0
  fi
  if [ -d "/run/user/${uid}" ] && [ -w "/run/user/${uid}" ]; then
    export XDG_RUNTIME_DIR="/run/user/${uid}"
    return 0
  fi
  export XDG_RUNTIME_DIR="${TMPDIR:-/tmp}/vikingi-runtime-${uid}"
  mkdir -p "${XDG_RUNTIME_DIR}"
  chmod 700 "${XDG_RUNTIME_DIR}"
}
_ensure_xdg_runtime
{
  echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR-}"
} >>"$LOG" 2>&1

cd "$HOME_DIR" 2>>"$LOG" || true

# xrdp + xdg-desktop-portal часто дают таймаут org.freedesktop.secrets — Thunar сразу выходит
export GTK_USE_PORTAL=0
export GIO_USE_VFS=local

thunar_running() { pgrep -x thunar >/dev/null 2>&1; }
pcman_running() { pgrep -x pcmanfm >/dev/null 2>&1; }

# 1) Thunar + dbus-launch (типично лечит «клик — и ничего» в xrdp)
if command -v dbus-launch >/dev/null 2>&1 && [ -x /usr/bin/thunar ]; then
  nohup env GTK_USE_PORTAL=0 GIO_USE_VFS=local "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}" \
    dbus-launch --exit-with-session /usr/bin/thunar "$HOME_DIR" >>"$LOG" 2>&1 &
  sleep 2
  thunar_running && exit 0
fi

# 2) Thunar без привязки к session manager (иногда ломается в RDP)
if [ -x /usr/bin/thunar ]; then
  nohup env GTK_USE_PORTAL=0 GIO_USE_VFS=local XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
    /usr/bin/thunar --sm-client-disable "$HOME_DIR" >>"$LOG" 2>&1 &
  sleep 2
  thunar_running && exit 0
fi

# 2b) Обычный Thunar в фоне
if [ -x /usr/bin/thunar ]; then
  nohup env GTK_USE_PORTAL=0 GIO_USE_VFS=local XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
    /usr/bin/thunar "$HOME_DIR" >>"$LOG" 2>&1 &
  sleep 2
  thunar_running && exit 0
fi

# 3) exo-open (Xfce)
if command -v exo-open >/dev/null 2>&1; then
  nohup env GTK_USE_PORTAL=0 GIO_USE_VFS=local "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}" \
    exo-open --launch FileManager "$HOME_DIR" >>"$LOG" 2>&1 &
  sleep 2
  thunar_running && exit 0
fi

# 4) PCManFM, если установлен
if command -v pcmanfm >/dev/null 2>&1; then
  nohup env GTK_USE_PORTAL=0 GIO_USE_VFS=local "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}" \
    pcmanfm "$HOME_DIR" >>"$LOG" 2>&1 &
  sleep 1
  pcman_running && exit 0
fi

# 5) Запасной вариант — окно терминала со списком файлов
if command -v xfce4-terminal >/dev/null 2>&1; then
  nohup xfce4-terminal --title="Каталог $HOME_DIR" \
    -e "bash -lc 'cd /home/vikingi && ls -la; echo; read -r -p \"Нажмите Enter чтобы закрыть...\" _'" >>"$LOG" 2>&1 &
  exit 0
fi

command -v zenity >/dev/null 2>&1 && zenity --error --text="Не удалось открыть файловый менеджер.\nПодробности: $LOG\n\nВ терминале на рабочем столе выполните:\n  cat $LOG" 2>/dev/null || true
exit 1
