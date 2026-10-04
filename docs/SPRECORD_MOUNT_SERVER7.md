# SpRecord на server7: монтирование `/mnt/sprecord`

После **перезагрузки** server7 каталог `/mnt/sprecord` часто остаётся **пустой локальной папкой** — SMB не подключён, пайплайн `daily_sprecord_pipeline.sh` и `call_analytics/autofetch_sprecord.py` не видят файлов.

### Автоматическая настройка на server7 (fstab + systemd timer)

Один раз из каталога проекта под **root**:

```bash
cd /home/vikingi/VikingiAll
sudo bash scripts/setup_sprecord_cifs_server7.sh
```

Скрипт: резервная копия `fstab`, убирает ошибочную строку `sprecord_DB` → `/mnt/sprecord`, добавляет **`sprecord_arch`** → `/mnt/sprecord`, создаёт `/root/.smbcredentials-sprecord` при отсутствии, ставит и включает **timer** `sprecord-mount-retry` (файлы в `scripts/systemd/`).

---

## Две шары на `192.168.0.5` — не перепутать

| Шара | Содержимое | Куда монтировать для пайплайна |
|------|------------|--------------------------------|
| **`sprecord_arch`** | Записи звонков **`*.wav`** (и др.) | **`/mnt/sprecord`** — **это нужно cron и автозабору** |
| **`sprecord_DB`** | В основном **`Records.dat`** (индекс/БД SpRecord), без деревьев `wav` | Опционально во вторую точку, например **`/mnt/sprecord_db`**, если нужен доступ к `Records.dat` |

`autofetch_sprecord.py` обходит **`/mnt/sprecord`** и ищет аудио за календарный день по **`mtime` и/или префиксу имени `YYYY_MM_DD_`** (как список в админке). Если смонтировать только `sprecord_DB`, в шаре не окажется `wav` — импорт будет пустым.

---

## Важно: учётные данные

Старый комментарий в коде (`nissan-tlt` как **SMB-логин**) **не подходит**: сервер отвечает `NT_STATUS_LOGON_FAILURE`.

**Рабочая схема** (история команд на server7):

| Параметр | Значение |
|----------|-----------|
| Сервер | `192.168.0.5` (server5) |
| Домен | `NISSAN-TLT` |
| Пользователь SMB | `bot7` (часто без разницы `BOT7` / `bot7`) |
| Пароль | `Kalina816` (храните в `/root/.smbcredentials-sprecord`, не в чатах) |
| Версия SMB | `vers=3.0` |

---

## 1. Пакеты

```bash
sudo apt-get update
sudo apt-get install -y cifs-utils smbclient
```

---

## 2. Список шар

```bash
smbclient -L //192.168.0.5 -U 'NISSAN-TLT/bot7%Kalina816'
```

Запасной вариант:

```bash
smbclient -L //192.168.0.5 -U 'BOT7%Kalina816'
```

---

## 3. Файл учётных данных (для `mount` и `fstab`)

```bash
sudo tee /root/.smbcredentials-sprecord <<'EOF'
username=bot7
password=Kalina816
domain=NISSAN-TLT
EOF
sudo chmod 600 /root/.smbcredentials-sprecord
```

При смене пароля на server5 обновите этот файл.

---

## 4. Ручное монтирование (основное — архив)

**Записи для автозабора** (`sprecord_arch` → `/mnt/sprecord`):

```bash
sudo mkdir -p /mnt/sprecord
sudo mount -t cifs //192.168.0.5/sprecord_arch /mnt/sprecord -o vers=3.0,credentials=/root/.smbcredentials-sprecord,iocharset=utf8,file_mode=0644,dir_mode=0755,uid=$(id -u),gid=$(id -g),_netdev
```

Проверка:

```bash
findmnt /mnt/sprecord
find /mnt/sprecord -maxdepth 2 -type f -iname '*.wav' 2>/dev/null | head
```

**Опционально — только база** (`sprecord_DB` → отдельная папка, не подменяйте ею `/mnt/sprecord` для пайплайна):

```bash
sudo mkdir -p /mnt/sprecord_db
sudo mount -t cifs //192.168.0.5/sprecord_DB /mnt/sprecord_db -o vers=3.0,credentials=/root/.smbcredentials-sprecord,iocharset=utf8,file_mode=0644,dir_mode=0755,uid=$(id -u),gid=$(id -g),_netdev
ls /mnt/sprecord_db
```

---

## 5. Автомонтирование после перезагрузки (`/etc/fstab`)

Подставьте **`uid`/`gid`** пользователя `vikingi`: `id vikingi` (часто `1000`).

**Обязательная строка** — архив на точку, которую ждёт автозабор:

```text
//192.168.0.5/sprecord_arch  /mnt/sprecord  cifs  vers=3.0,credentials=/root/.smbcredentials-sprecord,iocharset=utf8,file_mode=0644,dir_mode=0755,uid=1000,gid=1000,_netdev,nofail  0  0
```

**Опционально** — `Records.dat` на отдельную точку:

```text
//192.168.0.5/sprecord_DB  /mnt/sprecord_db  cifs  vers=3.0,credentials=/root/.smbcredentials-sprecord,iocharset=utf8,file_mode=0644,dir_mode=0755,uid=1000,gid=1000,_netdev,nofail  0  0
```

Смысл опций:

- **`_netdev`** — не монтировать до появления сети.
- **`nofail`** — если server5 недоступен при загрузке, **загрузка server7 не зависнет**; шара появится после ручного `mount` или подстраховки ниже.

Проверка после правок `fstab`:

```bash
sudo umount /mnt/sprecord 2>/dev/null || true
sudo mount /mnt/sprecord
findmnt /mnt/sprecord
```

---

## 6. Подстраховка: server5 «догоняет» после включения или сети

`fstab` при неудачном первом монтировании **сам бесконечно не повторяет** попытки. Если server7 загрузился раньше, чем поднялся `192.168.0.5`, или кабель подключили позже — имеет смысл **один** из вариантов (не обязательно оба).

### Вариант A — `cron` после ребута

Открыть `crontab -e` у **root** (`sudo crontab -e`) и добавить, например:

```cron
@reboot sleep 180 && mountpoint -q /mnt/sprecord || mount /mnt/sprecord
```

Через 3 минуты после старта: если `/mnt/sprecord` ещё не смонтирован — выполнится `mount` (подхватится строка из `fstab`).

Дополнительно можно раз в 15 минут дожимать (по желанию):

```cron
*/15 * * * * mountpoint -q /mnt/sprecord || mount /mnt/sprecord
```

### Вариант B — `systemd` timer (то же по смыслу)

Файл **`/etc/systemd/system/sprecord-mount-retry.service`**:

```ini
[Unit]
Description=Ensure SpRecord CIFS is mounted on /mnt/sprecord
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'mountpoint -q /mnt/sprecord || mount /mnt/sprecord'
```

Файл **`/etc/systemd/system/sprecord-mount-retry.timer`**:

```ini
[Unit]
Description=Retry SpRecord mount after boot and periodically

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
```

Команды на server7:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sprecord-mount-retry.timer
systemctl list-timers sprecord-mount-retry.timer
```

---

## 7. Догнать автозабор после простоя

```bash
cd /home/vikingi/VikingiAll
[ -f .env ] && set -a && . .env && set +a

for d in 2026-04-03 2026-04-04 2026-04-05; do
  echo "=== autofetch $d ==="
  python3 call_analytics/autofetch_sprecord.py --date "$d"
done

USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-04-03 --date-to 2026-04-05
```

Даты замените при необходимости.

---

## 8. Лог пайплайна

```bash
tail -100 /home/vikingi/VikingiAll/logs/vikingi_autofetch.log
```

---

## 9. Откуда взято

- Проверка на площадке: `sprecord_DB` содержит `Records.dat`, **`*.wav` на `sprecord_arch`**.
- `/home/vikingi/.bash_history`, снимки `mount` с `bot7`, `NISSAN-TLT`, `vers=3.0`.
