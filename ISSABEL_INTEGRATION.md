# Интеграция с Issabel PBX

## Что такое Issabel?
- **Бесплатный форк FreePBX**
- Полностью open-source
- Включает CRM, факсы, email
- Проще в установке
- Меньше сообщество, но активное развитие

## Отличия от FreePBX:

| Параметр | FreePBX | Issabel |
|----------|---------|---------|
| Цена | Бесплатно | Бесплатно |
| Интерфейс | Современный | Чуть старее |
| CRM | Платный модуль | Встроен |
| Факсы | Платный модуль | Встроен |
| Обновления | Частые | Реже |
| Сообщество | Большое | Среднее |

## Установка Issabel

### Вариант 1: ISO образ (самый простой)

1. **Скачайте ISO:**
   - https://www.issabel.org/downloads/
   - Версия: Issabel 4.0.0 (базируется на CentOS 7)

2. **Установите на сервер:**
   - Минимум: 2 CPU, 2 GB RAM, 20 GB HDD
   - Рекомендуется: 4 CPU, 4 GB RAM, 40 GB HDD

3. **После установки:**
   - Веб-интерфейс: `https://your-server-ip`
   - Логин: `admin`
   - Пароль: установили при установке

### Вариант 2: Установка на существующий CentOS

```bash
# На CentOS 7
cd /usr/src
wget -O - https://raw.githubusercontent.com/IssabelFoundation/issabelPBX/master/install_issabel4.sh | bash
```

## Настройка AMI в Issabel

1. **Откройте веб-интерфейс:**
   `https://your-issabel-ip`

2. **PBX → PBX Configuration → Manager Users**
   - Add Manager:
     - Username: `admin`
     - Secret: `amp111`
     - Deny: `0.0.0.0/0.0.0.0`
     - Permit: `0.0.0.0/0.0.0.0`
     - Read: `all`
     - Write: `all`

3. **Apply Config** (красная кнопка сверху)

## Настройка SIP-транка в Issabel

### Подключение Zadarma:

1. **PBX → PBX Configuration → Trunks**

2. **Add SIP (chan_pjsip) Trunk**

3. **General Settings:**
   - Trunk Name: `Zadarma`
   - Outbound CallerID: ваш номер

4. **pjsip Settings:**
   - Username: ваш_логин@zadarma
   - Secret: ваш_пароль
   - SIP Server: `sip.zadarma.com`
   - SIP Server Port: `5060`
   - Context: `from-trunk`

5. **Submit → Apply Config**

## Настройка входящих маршрутов

1. **PBX → Inbound Routes**

2. **Add Incoming Route:**
   - Description: "Главная линия"
   - DID Number: ваш виртуальный номер
   - Set Destination: 
     - Custom App
     - Return: `api-router,s,1`

3. **Создайте Custom Context** в `/etc/asterisk/extensions_custom.conf`:

```ini
[api-router]
exten => s,1,NoOp(Маршрутизация через NeuralBot API)
exten => s,n,Set(CALLERID(num)=${CALLERID(num)})
exten => s,n,Set(CALLERID(name)=Звонящий ${CALLERID(num)})
exten => s,n,Set(DEPARTMENT=${CURL(http://your-api:8000/internal/asterisk/route?caller=${CALLERID(num)})})
exten => s,n,GotoIf($["${DEPARTMENT}" = ""]?fallback:route)
exten => s,n(route),Dial(SIP/${DEPARTMENT})
exten => s,n,Hangup()
exten => s,n(fallback),Dial(SIP/100)  ; Консультант по умолчанию
exten => s,n,Hangup()
```

## Интеграция с вашим ботом

### Добавьте endpoint для маршрутизации:

В `app/routers/internal.py`:

```python
@router.get("/asterisk/route")
async def asterisk_route(caller: str):
    """Определение отдела для входящего звонка"""
    db = SessionLocal()
    
    # Ищем существующего клиента
    existing_lead = db.query(Lead).filter(
        Lead.phone == caller
    ).order_by(Lead.created_at.desc()).first()
    
    if existing_lead:
        department_code = existing_lead.department_code
    else:
        department_code = "consultant_fallback"
    
    # Находим номер extension отдела
    departments = get_departments_config().get("departments", [])
    dept = next((d for d in departments if d["code"] == department_code), None)
    
    db.close()
    
    if dept and dept.get("ext"):
        return {"extension": dept["ext"]}
    
    return {"extension": "100"}  # fallback
```

## Стоимость Issabel решения

### Минимальная конфигурация:
- VPS (Selectel): ₽500/мес
- Виртуальный номер: ₽400/мес
- **Итого: ₽900/мес**

### Плюсы Issabel:
✅ Полностью бесплатно
✅ Встроенный CRM
✅ Факсы из коробки
✅ Более простая установка
✅ Всё в одном пакете

### Минусы:
❌ Меньше модулей
❌ Меньше сообщество
❌ Базируется на старом CentOS 7

## Рекомендация:
- **Для старта**: Issabel (проще установить)
- **Для масштабирования**: FreePBX (больше возможностей)











