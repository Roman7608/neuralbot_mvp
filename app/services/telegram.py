import os
import httpx
import json
import redis
from typing import Optional
from ..db import SessionLocal
from ..models import Lead, LeadSource, LeadStatus
from ..services.leads import get_departments_config
from .llm import classify_text

async def send_message(chat_id: int, text: str, parse_mode: str = "HTML", message_thread_id: int = None) -> bool:
    """Отправить сообщение в Telegram чат"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN не настроен")
        return False
    
    # Логируем для диагностики
    token_mask = f"{token[:6]}...{token[-4:]}" if len(token) > 10 else token[:6]
    print(f"Telegram sendMessage: token={token_mask}, chat_id={chat_id}, thread_id={message_thread_id}")
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    
    # Добавляем thread_id если указан
    if message_thread_id:
        data["message_thread_id"] = message_thread_id
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=data)
            result = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"text": response.text}
            print(f"Telegram response: status={response.status_code}, body={result}")
            
            if response.status_code != 200:
                try:
                    print(f"Telegram sendMessage non-200: {response.status_code} body={result}")
                except Exception:
                    pass
            return response.status_code == 200
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")
        return False

async def notify_department_about_lead(lead: Lead) -> bool:
    """Уведомить отдел о новом лиде"""
    departments = get_departments_config().get("departments", [])
    dept = next((d for d in departments if d["code"] == lead.department_code), None)
    
    if not dept or not dept.get("tg_chat_id"):
        print(f"Не найден tg_chat_id для отдела {lead.department_code}")
        return False
    
    chat_id = int(dept["tg_chat_id"])
    
    # Формируем сообщение
    message = f"""🆕 <b>Новый лид #{lead.id}</b>

👤 <b>Клиент:</b> {lead.name or 'Не указано'}
📞 <b>Телефон:</b> {lead.phone}
🏙️ <b>Город:</b> {lead.city or 'Не указан'}
🚗 <b>Бренд:</b> {lead.brand or 'Не указан'}
🏢 <b>Отдел:</b> {dept['name']}
📱 <b>Источник:</b> {lead.source}
💬 <b>Комментарий:</b> {lead.comment or 'Нет'}
⏰ <b>Время:</b> {lead.created_at.strftime('%d.%m.%Y %H:%M')}

<a href="http://localhost:8000/admin/">Открыть админку</a>"""
    
    return await send_message(chat_id, message)

async def handle_telegram_webhook(data: dict) -> dict:
    """Обработка webhook от Telegram"""
    try:
        message = data.get("message", {})
        if not message:
            return {"ok": True}
        
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "").strip()
        user = message.get("from", {})
        username = user.get("username", "")
        first_name = user.get("first_name", "")
        
        if not chat_id or not text:
            return {"ok": True}
        
        # Приветствие
        if text.lower() in ["/start", "start", "привет", "здравствуйте"]:
            # Сбрасываем счетчик уточняющих вопросов при /start
            try:
                redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
                clarification_key = f"clarification_count:{chat_id}"
                redis_client.delete(clarification_key)
                print(f"Reset clarification count for {chat_id} after /start")
            except Exception as e:
                print(f"Redis reset error: {e}")
            
            welcome_msg = """Здравствуйте! Чем я могу Вам помочь?

Выберите интересующий вас отдел:
/продажи - Продажа автомобилей
/сервис - Сервис и ремонт  
/запчасти - Продажа запасных частей
/прочее - Прочее

Или просто напишите, что вас интересует!"""
            
            await send_message(chat_id, welcome_msg)
            return {"ok": True}
        
        # Команды отделов
        if text == "/продажи":
            await send_message(chat_id, "🚗 <b>Продажа автомобилей</b>\n\nМы предлагаем:\n• Chery, Jetour, Lada в Тольятти\n• Haval в Самаре\n\nНапишите, какая марка вас интересует!")
            return {"ok": True}
        
        if text == "/сервис":
            await send_message(chat_id, "🔧 <b>Сервис и ремонт</b>\n\nУ нас есть:\n• Слесарный ремонт (Тольятти, Самара)\n• Кузовной ремонт (Тольятти, Самара)\n\nВ каком городе нужен ремонт?")
            return {"ok": True}
        
        if text == "/запчасти":
            await send_message(chat_id, "🔩 <b>Продажа запасных частей</b>\n\nЗапчасти доступны в:\n• Тольятти\n• Самара\n\nНапишите, какие запчасти нужны!")
            return {"ok": True}
        
        if text == "/прочее":
            await send_message(chat_id, "📞 <b>Прочее</b>\n\nДля решения других вопросов напишите, что вас интересует, и мы направим вас к нужному специалисту!")
            return {"ok": True}
        
        # Нормализация кириллических названий брендов
        brand_mapping = {
            "чери": "chery",
            "черри": "chery", 
            "джетур": "jetour",
            "джейтур": "jetour",
            "лада": "lada",
            "хавал": "haval",
            "хавл": "haval"
        }
        
        # Сохраняем историю сообщений в Redis для анализа контекста
        history_key = f"message_history:{chat_id}"
        redis_client = None
        try:
            redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
            # Добавляем текущее сообщение в историю (максимум 5 сообщений)
            redis_client.lpush(history_key, text)
            redis_client.ltrim(history_key, 0, 4)  # Оставляем только последние 5 сообщений
            redis_client.expire(history_key, 3600)  # TTL 1 час
        except Exception as e:
            print(f"Redis history error: {e}")
        
        # Применяем нормализацию
        text_low = text.lower()
        text_normalized = text_low
        for cyrillic, latin in brand_mapping.items():
            text_normalized = text_normalized.replace(cyrillic, latin)
        
        # Простая классификация для Telegram
        text_low = text_normalized
        department_code = "consultant_fallback"
        
        print(f"Telegram message: '{text}' -> '{text_low}' (normalized)")
        
        # Анализируем историю сообщений для поиска брендов
        context_brands = []
        if redis_client:
            try:
                history = redis_client.lrange(history_key, 0, 4)  # Последние 5 сообщений
                for msg in history:
                    msg_normalized = msg.lower()
                    for cyrillic, latin in brand_mapping.items():
                        msg_normalized = msg_normalized.replace(cyrillic, latin)
                    context_brands.append(msg_normalized)
                print(f"Message history: {context_brands}")
            except Exception as e:
                print(f"Redis history read error: {e}")
                context_brands = [text_low]
        else:
            context_brands = [text_low]
        
        # Улучшенная классификация с учетом брендов и типов сервиса
        service_words = ["ремонт", "сто", "слесар", "кузов", "кузовной", "диагностика", "то", "техосмотр", "техобслуживание", "сервис",
                         "замена", "масло", "фильтр", "тормоз", "амортизатор", "починить", "восстановить", "отремонтировать", "покрасить", "покраска"]
        
        sales_words = ["купить", "авто", "машина", "автомобиль", "седа", "универсал", "внедорожник", "нужен", "хочу", "ищу"]
        
        parts_words = ["запасные части", "запчаст", "запчасти", "детал", "аксессуар", "бампер", "фары", "стекло", 
                       "фильтр", "фильтры", "фильтра", "расходники", "колодки", "диски", "шины", "сальники", 
                       "прокладки", "коврики", "брызговики", "стеклоочистители", "дворники"]
        
        accounting_words = ["бухгалтер", "бухгалтерия", "документ", "документы", "счет", "счета", "оплата"]
        
        # Специальные слова для fallback - убираем конкретные слова
        # unclear_words = []  # Не используем конкретные слова
        
        # Функция для поиска бренда в контексте (включая историю)
        def find_brand_in_context():
            for context_text in context_brands:
                if any(word in context_text for word in ["lada", "лада", "ваз"]):
                    return "lada"
                elif any(word in context_text for word in ["jetour", "джетур"]):
                    return "jetour"
                elif any(word in context_text for word in ["chery", "чери"]):
                    return "chery"
                elif any(word in context_text for word in ["haval", "хавал"]):
                    return "haval"
            return None
        
        # Проверяем, содержит ли сообщение только название бренда без контекста
        brand_only = False
        detected_brand = None
        
        # Проверяем, является ли сообщение только названием бренда
        if (text_low in ["chery", "чери", "черри", "черы"] or 
            text_low in ["lada", "лада", "ваз", "лда"] or 
            text_low in ["jetour", "джетур", "джейтур", "джейтур"]):
            brand_only = True
            if text_low in ["chery", "чери", "черри", "черы"]:
                detected_brand = "chery"
            elif text_low in ["lada", "лада", "ваз", "лда"]:
                detected_brand = "lada"
            elif text_low in ["jetour", "джетур", "джейтур", "джейтур"]:
                detected_brand = "jetour"
        
        # Если сообщение содержит только название бренда - требуем уточнения
        if brand_only:
            department_code = "consultant_fallback"
            print(f"Brand only detected: {detected_brand}, requiring clarification")
        # Определяем тип запроса - сначала проверяем продажи (более специфично)
        elif any(word in text_low for word in sales_words) or any(any(word in ctx for word in sales_words) for ctx in context_brands):
            # Продажи - определяем бренд из контекста
            brand = find_brand_in_context()
            if brand == "lada":
                department_code = "sales_lada_tlt"  # Только Lada продажи
                print(f"Classified as LADA SALES: {department_code}")
            elif brand == "jetour":
                department_code = "sales_jetour_tlt"  # Только Jetour продажи
                print(f"Classified as JETOUR SALES: {department_code}")
            elif brand == "chery":
                department_code = "sales_chery_tlt"  # Только Chery продажи
                print(f"Classified as CHERY SALES: {department_code}")
            elif brand == "haval":
                department_code = "sales_haval_smr"  # Только Haval продажи
                print(f"Classified as HAVAL SALES: {department_code}")
            elif any(word in text_low for word in ["пробег", "б/у", "б у", "вторичн"]) or any(any(word in ctx for word in ["пробег", "б/у", "б у", "вторичн"]) for ctx in context_brands):
                department_code = "used_cars_tlt"  # Автомобили с пробегом
                print(f"Classified as USED CARS: {department_code}")
            else:
                department_code = "sales_chery_tlt"  # По умолчанию Chery (любые другие новые)
                print(f"Classified as DEFAULT SALES: {department_code}")
        elif any(word in text_low for word in service_words) or any(any(word in ctx for word in service_words) for ctx in context_brands):
            # Сервис - определяем тип и бренд из контекста
            brand = find_brand_in_context()
            if any(word in text_low for word in ["кузов", "кузовной", "покрасить", "покраска"]) or any(any(word in ctx for word in ["кузов", "кузовной", "покрасить", "покраска"]) for ctx in context_brands):
                department_code = "service_body_tlt"  # Кузовной цех (любые бренды)
                print(f"Classified as BODY SERVICE: {department_code}")
            else:
                # Слесарный ремонт - определяем бренд из контекста
                if brand == "lada":
                    department_code = "service_mech_lada"  # Только Lada в слесарном
                    print(f"Classified as LADA MECH SERVICE: {department_code}")
                else:
                    department_code = "service_mech_tlt"  # Любые другие в слесарном
                    print(f"Classified as OTHER MECH SERVICE: {department_code}")
        elif any(word in text_low for word in parts_words) or any(any(word in ctx for word in parts_words) for ctx in context_brands):
            # Запчасти - определяем бренд из контекста
            brand = find_brand_in_context()
            if brand == "lada":
                department_code = "spares_lada"  # Только Lada запчасти
                print(f"Classified as LADA SPARES: {department_code}")
            else:
                department_code = "spares_tlt"  # Любые другие запчасти
                print(f"Classified as OTHER SPARES: {department_code}")
        elif any(word in text_low for word in accounting_words):
            department_code = "accounting"  # Бухгалтерия
            print(f"Classified as ACCOUNTING: {department_code}")
        else:
            # Все остальные сообщения требуют уточнения
            department_code = "consultant_fallback"  # Неясные запросы - требуют уточнения
            print(f"Classified as FALLBACK (requiring clarification): {department_code}")
        
        # Если запрос неясный, задаем уточняющий вопрос (максимум 2 раза)
        if department_code == "consultant_fallback":
            # Проверяем количество уточняющих вопросов для этого пользователя
            clarification_key = f"clarification_count:{chat_id}"
            
            # Получаем количество вопросов из Redis или используем 0 по умолчанию
            import redis
            try:
                redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
                count = int(redis_client.get(clarification_key) or 0)
                print(f"Redis count for {chat_id}: {count}")
            except Exception as e:
                print(f"Redis error: {e}")
                count = 0
            
            if count >= 2:
                # После 2 вопросов отправляем в Neuro_Other
                print(f"After 2 questions, creating lead for {chat_id}")
                
                # Создаем лид в Neuro_Other
                db = SessionLocal()
                try:
                    name = first_name or username or "Пользователь Telegram"
                    phone = "Не указан"
                    
                    lead = Lead(
                        name=name,
                        phone=phone,
                        source=LeadSource.telegram,
                        department_code="consultant_fallback",
                        comment=f"Telegram: {text} (неясный запрос после 2 уточнений)",
                        status=LeadStatus.new
                    )
                    
                    db.add(lead)
                    db.commit()
                    db.refresh(lead)
                    
                    await notify_department_about_lead(lead)
                    
                    msg = "Благодарю, передаю Ваш контакт специалисту. С Вами свяжутся через несколько минут."
                    await send_message(chat_id, f"✅ <b>Заявка #{lead.id} создана.</b> {msg}")
                    
                    # Сбрасываем счетчик уточняющих вопросов после создания лида
                    try:
                        redis_client.delete(clarification_key)
                        print(f"Reset clarification count for {chat_id} after lead creation")
                    except Exception as e:
                        print(f"Redis reset error after lead: {e}")
                    
                except Exception as e:
                    print(f"Ошибка создания лида: {e}")
                    await send_message(chat_id, "❌ Произошла ошибка при создании заявки. Попробуйте позже.")
                finally:
                    db.close()
                
                return {"ok": True}
            else:
                # Увеличиваем счетчик вопросов
                try:
                    new_count = count + 1
                    redis_client.set(clarification_key, new_count, ex=3600)  # TTL 1 час
                    print(f"Set Redis count for {chat_id} to {new_count}")
                except Exception as e:
                    print(f"Redis set error: {e}")
                
                # Формируем уточняющий вопрос в зависимости от обнаруженного бренда
                if brand_only and detected_brand:
                    brand_names = {
                        "chery": "Chery",
                        "lada": "Lada", 
                        "jetour": "Jetour",
                        "haval": "Haval"
                    }
                    brand_name = brand_names.get(detected_brand, detected_brand.upper())
                    
                    clarification_msg = f"""🤔 <b>Вас интересует {brand_name}?</b>

Пожалуйста, уточните, что именно вас интересует:

🚗 <b>Отдел продаж новых автомобилей</b> {brand_name}
🚙 <b>Автомобили с пробегом</b> {brand_name}
🔩 <b>Запчасти</b> {brand_name}
🔧 <b>Обслуживание и слесарный ремонт</b> {brand_name}
🎨 <b>Цех кузовного ремонта</b>
📊 <b>Бухгалтерия</b>
❓ <b>Прочее</b>

Напишите, что именно вас интересует!"""
                else:
                    clarification_msg = """🤔 <b>Извините, я Вас не понял.</b>

С каким отделом Вас соединить:

🚗 <b>Отдел продаж новых автомобилей</b> Chery, Jetour, Lada
🔧 <b>Слесарным цехом</b> Chery или Lada  
🎨 <b>Кузовным цехом</b>
🔩 <b>Отделом запасных частей</b> Lada или других марок
📊 <b>Бухгалтерией</b>
❓ <b>Иным вариантом</b>

Напишите, что именно вас интересует!"""
                
                await send_message(chat_id, clarification_msg)
                return {"ok": True}
        
        # Создаем лид из сообщения
        db = SessionLocal()
        try:
            # Извлекаем имя и телефон (простая логика)
            name = first_name or username or "Пользователь Telegram"
            phone = "Не указан"
            
            # Ищем номер телефона в тексте
            import re
            phone_match = re.search(r'[\+]?[0-9\s\-\(\)]{10,}', text)
            if phone_match:
                phone = phone_match.group().strip()
            
            lead = Lead(
                name=name,
                phone=phone,
                source=LeadSource.telegram,
                department_code=department_code,
                comment=f"Telegram: {text}",
                status=LeadStatus.new
            )
            
            db.add(lead)
            db.commit()
            db.refresh(lead)
            
            # Уведомляем отдел
            await notify_department_about_lead(lead)
            
            # Подтверждаем пользователю
            msg = "Благодарю, передаю Ваш контакт специалисту. С Вами свяжутся через несколько минут."
            print(f"Sending confirmation to user chat_id: {chat_id}")
            await send_message(chat_id, f"✅ <b>Заявка #{lead.id} создана.</b> {msg}")
            
            # Сбрасываем счетчик уточняющих вопросов после создания лида
            try:
                clarification_key = f"clarification_count:{chat_id}"
                redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
                redis_client.delete(clarification_key)
                print(f"Reset clarification count for {chat_id} after lead creation")
            except Exception as e:
                print(f"Redis reset error after lead: {e}")
            
        except Exception as e:
            print(f"Ошибка создания лида: {e}")
            await send_message(chat_id, "❌ Произошла ошибка при создании заявки. Попробуйте позже.")
        finally:
            db.close()
        
        return {"ok": True}
        
    except Exception as e:
        print(f"Ошибка обработки Telegram webhook: {e}")
        return {"ok": False}

def resolve_department_from_llm(llm: dict):
    """Возвращает (department_code, reply_text) на основе ответа LLM.
    reply_text — вежливая подсказка для пользователя.
    """
    # Парсим intent из формата "СТО|ТО|ЗАПАСНЫЕ|НЕОПРЕДЕЛЕННОСТЬ"
    intent_raw = str(llm.get("intent") or "")
    intent = intent_raw.split("|")[0].upper() if "|" in intent_raw else intent_raw.upper()
    
    # Парсим brand
    brand_raw = llm.get("brand")
    if brand_raw and brand_raw != "null" and brand_raw != "NULL":
        brand = str(brand_raw).upper()
    else:
        brand = None
    
    # Парсим city из формата "ТОЛЬЯТТИ|NULL"
    city_raw = llm.get("city")
    if city_raw and city_raw != "null" and city_raw != "NULL":
        city = str(city_raw).split("|")[0].upper() if "|" in str(city_raw) else str(city_raw).upper()
    else:
        city = None
    target = llm.get("target_dept_code")

    cfg = get_departments_config() or {}
    depts = cfg.get("departments", [])

    def find_sales(brand_name: str):
        items = [d for d in depts if d.get("type") == "sales"]
        if brand_name:
            items = [d for d in items if (d.get("brand") or "").upper() == brand_name]
        if city:
            city_norm = "САМАРА" if city in ("SAMARA", "САМАРА") else ("ТОЛЬЯТТИ" if city in ("TOLYATTI", "ТОЛЬЯТТИ") else None)
            if city_norm:
                items_city = [d for d in items if (d.get("city") or "").upper() == ("САМАРА" if city_norm=="САМАРА" else "Тольятти")]
                if items_city:
                    items = items_city
        return items[0].get("code") if items else None

    if target:
        return target, None

    if intent == "ПРОДАЖИ_АВТО":
        # Наши бренды
        our_brands = {d.get("brand", "").upper() for d in depts if d.get("type") == "sales" and d.get("brand")}
        
        if brand and brand not in our_brands:
            # Бренд не из нашего портфеля
            alt = ", ".join(sorted(our_brands)) or "Chery, Haval, Jetour"
            reply = (
                f"Отличный выбор! К сожалению, мы официально работаем только с брендами "
                f"<b>{alt}</b>. Но можем подобрать Вам достойную альтернативу! "
                f"С каким отделом продаж Вас соединить?"
            )
            return None, reply
        
        # Находим подходящий отдел
        code = find_sales(brand) or find_sales(None)
        if code:
            return code, "Соединю Вас с отделом продаж — подберём идеальный вариант!"
        else:
            return "consultant_fallback", "Направлю к консультанту — он поможет с выбором!"

    if intent == "СТО":
        if city in ("SAMARA", "САМАРА"):
            return "service_mech_smr", "Передам запрос в сервис — решим вопрос быстро!"
        return "service_mech_tlt", "Передам запрос в сервис — решим вопрос быстро!"

    if intent == "ЗАПАСНЫЕ":
        if city in ("SAMARA", "САМАРА"):
            return "spares_smr", "Направлю в отдел запчастей — найдём нужное!"
        return "spares_tlt", "Направлю в отдел запчастей — найдём нужное!"

    return None, "Подскажите, пожалуйста, Вас интересует покупка авто, сервис или запчасти?"



