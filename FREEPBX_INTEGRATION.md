# Интеграция с FreePBX

## Шаг 1: Установка FreePBX

### Вариант A: VPS сервер
1. Арендуйте VPS (рекомендуется):
   - **Selectel**: от ₽500/мес
   - **Timeweb**: от ₽300/мес  
   - **REG.RU**: от ₽400/мес
   
2. Характеристики VPS:
   - OS: Ubuntu 22.04
   - CPU: 2 cores
   - RAM: 4 GB
   - HDD: 40 GB
   - IP: Статический

3. Установка:
```bash
ssh root@your-vps-ip
wget http://download.freepbx.org/freepbx-installer.sh
bash freepbx-installer.sh
```

### Вариант B: Локальный сервер
- Установите на физический сервер в офисе
- Настройте проброс портов на роутере

## Шаг 2: Настройка AMI в FreePBX

1. **Включите AMI:**
   - Откройте: `http://your-freepbx-ip`
   - Зайдите в: `Settings → Asterisk Manager Users`
   - Создайте пользователя:
     - Username: `admin`
     - Secret: `amp111`
     - Разрешения: Read/Write для всех

2. **Проверьте порты:**
```bash
netstat -tulpn | grep 5038
# Должен показать: 0.0.0.0:5038
```

3. **Откройте порт в firewall:**
```bash
ufw allow 5038/tcp
```

## Шаг 3: Настройка входящих маршрутов

### В FreePBX веб-интерфейсе:

1. **Applications → Misc Applications**
   - Создайте приложение для каждого отдела:
     - Description: "Sales Chery"
     - Feature Code: 001
     - Destination: Extension 101

2. **Connectivity → Inbound Routes**
   - Создайте маршрут:
     - Description: "Главный номер"
     - DID Number: ваш номер телефона
     - Set Destination: Custom Destination
     - Custom: `AGI(agi://your-api-server:8000/internal/asterisk/agi)`

## Шаг 4: Подключение SIP-транка

### Что такое SIP-транк?
SIP-транк - это виртуальная линия связи с телефонным оператором через интернет.

### Российские провайдеры SIP-транков:

#### **1. Zadarma** (Рекомендуется для старта)
- Стоимость: ₽0 абонентской платы
- Входящие звонки: ₽0.50/мин
- Исходящие по России: ₽1.5/мин
- Виртуальный номер: ₽300-500/мес

**Настройка в FreePBX:**
1. `Connectivity → Trunks → Add Trunk → Add SIP (chan_pjsip) Trunk`
2. General:
   - Trunk Name: `Zadarma`
3. pjsip Settings → General:
   - Username: ваш логин от Zadarma
   - Secret: ваш пароль
4. pjsip Settings → Advanced:
   - SIP Server: `sip.zadarma.com`
   - SIP Server Port: `5060`

#### **2. Телфин**
- Абонентская плата: от ₽300/мес
- Входящие: ₽1/мин
- Исходящие по России: ₽1.2/мин

#### **3. МТТ (Мобильные ТелеСистемы)**
- Корпоративное решение
- От ₽1000/мес за номер

#### **4. Ростелеком**
- Виртуальная АТС
- От ₽2000/мес

## Шаг 5: Интеграция с вашим ботом

### Обновите `.env`:
```env
# Если FreePBX на VPS
ASTERISK_HOST=123.45.67.89  # IP вашего VPS
ASTERISK_PORT=5038
ASTERISK_USERNAME=admin
ASTERISK_PASSWORD=amp111

# Если FreePBX локально
ASTERISK_HOST=192.168.1.100  # Локальный IP
ASTERISK_PORT=5038
ASTERISK_USERNAME=admin
ASTERISK_PASSWORD=amp111
```

### Настройка AGI скрипта:

В FreePBX создайте Custom Destination:
```
[from-pstn]
exten => s,1,NoOp(Входящий звонок)
exten => s,n,Set(CALLERID(num)=${CALLERID(num)})
exten => s,n,AGI(agi://your-api-server-ip:8000/agi)
exten => s,n,Dial(SIP/${EXTEN})
exten => s,n,Hangup()
```

## Шаг 6: Тестирование

1. **Подключитесь к AMI:**
```powershell
curl.exe -X POST http://localhost:8000/internal/asterisk/connect
```

2. **Проверьте статус:**
```powershell
curl.exe http://localhost:8000/internal/asterisk/status
```

Должен вернуть: `"connected": true`

3. **Сделайте тестовый звонок** на ваш виртуальный номер

4. **Проверьте лог:**
```powershell
curl.exe http://localhost:8000/admin/call_logs
```

## Стоимость решения

### Минимальная конфигурация:
- VPS (Selectel): ₽500/мес
- Виртуальный номер (Zadarma): ₽400/мес
- Трафик звонков: зависит от объема
- **Итого: от ₽900/мес**

### Средняя конфигурация:
- VPS (более мощный): ₽1500/мес
- 2-3 виртуальных номера: ₽1200/мес
- SIP-транк (Телфин): ₽300/мес
- Трафик: ~₽3000/мес (при 100 звонках/день)
- **Итого: от ₽6000/мес**

## Преимущества FreePBX:
✅ Бесплатная система
✅ Веб-интерфейс
✅ Большое сообщество
✅ Куча модулей
✅ Легко масштабируется

## Недостатки:
❌ Нужен выделенный сервер
❌ Требует настройки
❌ Нужны базовые знания телефонии
















