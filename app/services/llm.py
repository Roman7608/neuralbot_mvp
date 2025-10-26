import os
import httpx
import json
import re
from typing import Dict

def _rules_classify(text: str) -> dict:
    """Улучшенные правила для fallback"""
    intent = "НЕОПРЕДЕЛЕННОСТЬ"
    text_low = text.lower()
    
    # Расширенные ключевые слова для продаж
    sales_words = [
        "купить", "авто", "машина", "автомобиль", "седа", "универсал", "внедорожник", 
        "кроссовер", "хэтчбек", "купе", "кабриолет", "пикап", "микроавтобус",
        "ищу", "хочу", "интерес", "покажите", "вариант", "выбор",
        "цена", "стоимость", "рублей", "миллион", "тысяч", "кредит", "рассрочка",
        "полноприводный", "автомат", "механика", "дизель", "бензин", "гибрид",
        "электромобиль", "электро", "новый", "пробег", "б/у", "с пробегом"
    ]
    
    # Бренды (включая не наши)
    brands = ["chery", "jetour", "lada", "haval", "geely", "toyota", "bmw", "mercedes", 
              "audi", "volkswagen", "skoda", "hyundai", "kia", "nissan", "honda", "mazda"]
    
    # СТО
    service_words = ["ремонт", "сто", "слесар", "кузов", "кузовной", "диагностика", "то", "техосмотр",
                     "замена", "масло", "фильтр", "тормоз", "амортизатор", "починить", "восстановить", "отремонтировать"]
    
    # Запчасти
    parts_words = ["запчаст", "детал", "аксессуар", "фильтр", "колодк", "амортизатор",
                   "свеч", "свеча", "прокладк", "сальник", "подшипник"]
    
    # Проверяем в порядке приоритета: сначала сервис, потом продажи
    if any(word in text_low for word in service_words):
        intent = "СТО"
    elif any(word in text_low for word in parts_words):
        intent = "ЗАПАСНЫЕ"
    elif any(word in text_low for word in sales_words + brands):
        intent = "ПРОДАЖИ_АВТО"
    
    # Извлекаем бренд
    brand = None
    for b in brands:
        if b in text_low:
            brand = b.upper()
            break
    
    return {"intent": intent, "brand": brand, "confidence": 0.6}

async def classify_text(text: str) -> dict:
    # GigaChat provider first (if configured)
    llm_provider = os.getenv("LLM_PROVIDER")
    if llm_provider == "gigachat":
        try:
            from .conversation import GigaChatProvider
            provider = GigaChatProvider()
            result = await provider.classify_intent(text)
            # Проверяем, что результат валидный
            if result.get("intent") and result.get("intent") != "НЕОПРЕДЕЛЕННОСТЬ":
                return _normalize_output(result)
            else:
                print(f"GigaChat вернул неопределенность, используем правила: {result}")
                pass  # fall back to local/heuristics
        except Exception as e:
            print(f"Ошибка GigaChat классификации: {e}")
            pass  # fall back to local/heuristics
    
    # Cloud provider (if configured)
    url = os.getenv("LLM_PROVIDER_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    if url and api_key:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json={"model": model, "input": text}, headers={"Authorization": f"Bearer {api_key}"})
                resp.raise_for_status()
                data = resp.json()
                return _normalize_output(data)
        except Exception:
            pass  # fall back to local/heuristics

    # Local Ollama (if available)
    ollama = os.getenv("OLLAMA_ENDPOINT") or ""
    if ollama:
        try:
            prompt = (
                "Ты классификатор намерений для автосалона. Анализируй запросы клиентов и определяй их намерения.\n\n"
                "ПРАВИЛА КЛАССИФИКАЦИИ:\n"
                "1. ПРОДАЖИ_АВТО - любые запросы о покупке автомобиля:\n"
                "   - 'мне нужен седан', 'хочу универсал', 'ищу внедорожник'\n"
                "   - 'до 2,5 млн рублей', 'в кредит', 'рассрочка'\n"
                "   - 'покажите машину', 'какие есть варианты', 'помогите выбрать'\n"
                "   - упоминание брендов: Chery, Jetour, Lada, Haval, Geely, Toyota, BMW и др.\n"
                "   - типы кузова: седан, хэтчбек, универсал, внедорожник, кроссовер, купе\n"
                "   - характеристики: полноприводный, автомат, механика, дизель, бензин\n\n"
                "2. СТО - ремонт и обслуживание:\n"
                "   - 'нужен ремонт', 'слесарный ремонт', 'кузовной ремонт'\n"
                "   - 'ТО', 'техосмотр', 'диагностика', 'замена масла'\n"
                "   - 'починить', 'отремонтировать', 'восстановить'\n"
                "   - 'кузов', 'кузовной', 'покраска', 'жестянка'\n\n"
                "3. ЗАПАСНЫЕ - запчасти и аксессуары:\n"
                "   - 'нужны запчасти', 'ищу детали', 'аксессуары'\n"
                "   - 'фильтры', 'тормозные колодки', 'амортизаторы'\n\n"
                "4. НЕОПРЕДЕЛЕННОСТЬ - общие вопросы, приветствие, неясные запросы\n\n"
                "ИЗВЛЕКАЙ ИНФОРМАЦИЮ:\n"
                "- brand: извлекай бренд автомобиля (Chery, Jetour, Lada, Haval, Geely, Toyota и др.)\n"
                "- city: Тольятти, Самара (если упоминается)\n"
                "- sale_type: новые, с_пробегом, оба, не_знаю\n\n"
                "ОТВЕЧАЙ СТРОГО В JSON:\n"
                '{"intent":"ПРОДАЖИ_АВТО|СТО|ЗАПАСНЫЕ|НЕОПРЕДЕЛЕННОСТЬ",'
                '"brand":"извлеченный_бренд|null",'
                '"city":"Тольятти|Самара|null",'
                '"sale_type":"новые|с_пробегом|оба|не_знаю|null",'
                '"confidence":0.95}\n\n'
                f"Запрос клиента: {text}"
            )
            payload = {
                "model": os.getenv("LLM_MODEL", "qwen2.5:7b-instruct-q4_K_M"),
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1},
                "format": "json"
            }
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(ollama.rstrip("/") + "/api/generate", json=payload)
                r.raise_for_status()
                data = r.json()
                raw = data.get("response", "").strip()
                # 1) Пробуем распарсить весь ответ как JSON
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and parsed.get("intent"):
                        return _normalize_output(parsed)
                except Exception:
                    pass
                # 2) Вырезаем первый JSON-блок {...} из текста и парсим его
                m = re.search(r"\{[\s\S]*\}", raw)
                if m:
                    try:
                        parsed2 = json.loads(m.group(0))
                        if isinstance(parsed2, dict) and parsed2.get("intent"):
                            return _normalize_output(parsed2)
                    except Exception:
                        pass
                # 3) Хак: если модель вернула псевдо-поля вида {BRAND:CHERY}{...} в строке, пытаемся собрать словарь
                pseudo = {}
                for key in ("BRAND", "CITY", "TARGET_DEPT_CODE", "NEXT_QUESTION", "CONFIDENCE"):
                    mm = re.search(r"\{" + key + r":([^\}]+)\}", raw, flags=re.I)
                    if mm:
                        val = mm.group(1).strip()
                        if val.upper() == "NULL":
                            val = None
                        pseudo[key.lower()] = val
                if pseudo:
                    intent_guess = "UNDEFINED"
                    if re.search(r"ПРОДАЖ|PRODAZH|SALES", raw, flags=re.I): intent_guess = "PRODAZHI_AVTO"
                    elif re.search(r"СТО|STO|SERVICE", raw, flags=re.I): intent_guess = "STO"
                    elif re.search(r"ЗАПАСН|SPARE|PART", raw, flags=re.I): intent_guess = "ZAPASNYE"
                    out = {
                        "intent": intent_guess,
                        "brand": pseudo.get("brand"),
                        "city": pseudo.get("city"),
                        "target_dept_code": pseudo.get("target_dept_code"),
                        "next_question": pseudo.get("next_question"),
                        "confidence": float(pseudo.get("confidence") or 0.0),
                    }
                    return _normalize_output(out)
        except Exception:
            pass  # fall back to rules

    # Heuristic rules fallback
    print("Используем правила классификации")
    rules_result = _rules_classify(text)
    print(f"Правила вернули: {rules_result}")
    return _normalize_output(rules_result)

def _normalize_output(data: Dict) -> Dict:
    print(f"_normalize_output input: {data}")
    intent_map = {
        "ПРОДАЖИ_АВТО": "PRODAZHI_AVTO",
        "СТО": "STO",
        "ЗАПАСНЫЕ": "ZAPASNYE",
        "НЕОПРЕДЕЛЕННОСТЬ": "UNDEFINED",
        "HUMAN_DEPTS": "HUMAN_DEPTS",
        # Добавляем уже нормализованные значения
        "PRODAZHI_AVTO": "PRODAZHI_AVTO",
        "STO": "STO",
        "ZAPASNYE": "ZAPASNYE",
        "UNDEFINED": "UNDEFINED",
    }
    brand_map = {
        "Chery": "CHERY",
        "Jetour": "JETOUR",
        "Lada": "LADA",
        "Haval": "HAVAL",
        None: None,
        "null": None,
    }
    city_map = {
        "Тольятти": "TOLYATTI",
        "Самара": "SAMARA",
        None: None,
        "null": None,
    }
    out = dict(data)
    intent = out.get("intent")
    if intent in intent_map:
        out["intent"] = intent_map[intent]
    else:
        out["intent"] = (str(intent) or "UNDEFINED").upper()
    if "brand" in out:
        out["brand"] = brand_map.get(out["brand"], (str(out["brand"]).upper() if out["brand"] else None))
    if "city" in out:
        out["city"] = city_map.get(out["city"], (str(out["city"]).upper() if out["city"] else None))
    print(f"_normalize_output output: {out}")
    return out



