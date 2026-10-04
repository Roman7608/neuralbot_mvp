# Сводная таблица ТО (server2) — монтирование на server7

Файл регламентов Chery/Tenet для голосового бота и записи на ТО:

`\\server2\общие данные\_СТО\! ТО Сводная таблица.xlsx`

Обновляет диспетчер СТО. Код читает файл через `dialog/sto_to_summary_table.py` (`STO_TO_TABLE_PATH`).

## Учётные данные

**Подтверждено (2026-05-27):** логин и пароль SMB на **server2** — **те же**, что для SpRecord на **server5**.

| Параметр | Значение |
|----------|----------|
| Сервер | `server2` (уточнить IP в сети дилера, при необходимости — `192.168.0.2`) |
| Шара | `общие данные` |
| Путь в шаре | `_СТО\! ТО Сводная таблица.xlsx` |
| Домен | `NISSAN-TLT` |
| Пользователь | `bot7` |
| Credentials | `/root/.smbcredentials-sprecord` (общий с SpRecord) |
| SMB | `vers=3.0` |

Подробности по SpRecord: [SPRECORD_MOUNT_SERVER7.md](./SPRECORD_MOUNT_SERVER7.md).

## Точка монтирования

```text
/mnt/sto_to_table/! ТО Сводная таблица.xlsx
```

## Ручной mount (проверка)

```bash
sudo mkdir -p /mnt/sto_to_table
sudo mount -t cifs '//server2/общие данные' /mnt/sto_to_table \
  -o credentials=/root/.smbcredentials-sprecord,vers=3.0,uid=0,gid=0,file_mode=0444,dir_mode=0555
ls -la '/mnt/sto_to_table/_СТО/! ТО Сводная таблица.xlsx'
```

Если имя шары или путь отличаются — уточнить у админов server2 и поправить `STO_TO_TABLE_PATH`.

## fstab (постоянно)

```fstab
//server2/общие\040данные  /mnt/sto_to_table  cifs  credentials=/root/.smbcredentials-sprecord,vers=3.0,nofail,x-systemd.automount,x-systemd.idle-timeout=60,file_mode=0444,dir_mode=0555  0  0
```

После правки:

```bash
sudo mount -a
```

## Docker voice-bot

В `docker-compose.yml` для `voice-bot`:

```yaml
environment:
  STO_TO_TABLE_PATH: /mnt/sto_to_table/_СТО/! ТО Сводная таблица.xlsx
volumes:
  - /mnt/sto_to_table:/mnt/sto_to_table:ro
```

Пересборка не обязательна — достаточно `docker compose up -d voice-bot` после mount на хосте.

## Проверка из Python

```bash
python3 -c "
from dialog.sto_to_summary_table import resolve_sto_to_table_path, lookup_sto_to_regulation
print(resolve_sto_to_table_path())
print(lookup_sto_to_regulation('Chery', 'Tiggo 7', 15000))
"
```
