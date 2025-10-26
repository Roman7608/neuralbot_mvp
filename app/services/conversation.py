"""
Сервис диалогов с провайдером GigaChat.
"""

import os
import httpx
import json
from typing import AsyncIterator, List, Dict
import uuid
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


# ===== ПРОМПТ ДЛЯ КЛАССИФИКАЦИИ =====
CLASSIFICATION_PROMPT = """Ты классификатор намерений для автосалона. Анализируй запросы клиентов и определяй их намерения.

ПРАВИЛА КЛАССИФИКАЦИИ:
1. ПРОДАЖИ_АВТО - любые запросы о покупке автомобиля:
   - 'мне нужен седан', 'хочу универсал', 'ищу внедорожник'
   - 'до 2,5 млн рублей', 'в кредит', 'рассрочка'
   - 'покажите машину', 'какие есть варианты', 'помогите выбрать'
   - упоминание брендов: Chery, Jetour, Lada, Haval, Geely, Toyota, BMW и др.
   - типы кузова: седан, хэтчбек, универсал, внедорожник, кроссовер, купе
   - характеристики: полноприводный, автомат, механика, дизель, бензин

2. СТО - ремонт и обслуживание:
   - 'нужен ремонт', 'слесарный ремонт', 'кузовной ремонт'
   - 'ТО', 'техосмотр', 'диагностика', 'замена масла'
   - 'починить', 'отремонтировать', 'восстановить'
   - 'кузов', 'кузовной', 'покраска', 'жестянка'

3. ЗАПАСНЫЕ - запчасти и аксессуары:
   - 'нужны запчасти', 'ищу детали', 'аксессуары'
   - 'фильтры', 'тормозные колодки', 'амортизаторы'

4. НЕОПРЕДЕЛЕННОСТЬ - общие вопросы, приветствие, неясные запросы

ИЗВЛЕКАЙ ИНФОРМАЦИЮ:
- brand: извлекай бренд автомобиля (Chery, Jetour, Lada, Haval, Geely, Toyota и др.)
- city: Тольятти, Самара (если упоминается)
- sale_type: новые, с_пробегом, оба, не_знаю

ОТВЕЧАЙ СТРОГО В JSON:
{"intent":"ПРОДАЖИ_АВТО|СТО|ЗАПАСНЫЕ|НЕОПРЕДЕЛЕННОСТЬ","brand":"извлеченный_бренд|null","city":"Тольятти|Самара|null","sale_type":"новые|с_пробегом|оба|не_знаю|null","confidence":0.95}

Запрос клиента: {text}"""

# ===== ПРОМПТ ДЛЯ ГОЛОСОВОГО БОТА =====
SYSTEM_PROMPT = """Ты голосовой ассистент автосалона, который продает автомобили Chery и Jetour в Тольятти и Самаре.

ТВОЯ РОЛЬ:
- Дружелюбный и профессиональный менеджер по продажам
- Помогаешь клиентам выбрать автомобиль
- Отвечаешь на вопросы о моделях, ценах, характеристиках
- Записываешь клиентов на тест-драйв

ПРАВИЛА ОБЩЕНИЯ:
1. Говори КРАТКО - это телефонный разговор (макс 2-3 предложения)
2. Задавай УТОЧНЯЮЩИЕ вопросы для понимания потребностей
3. Будь ЕСТЕСТВЕННЫМ - избегай формальностей
4. Используй ПРОСТОЙ язык - без технического жаргона
5. Если не знаешь точной информации - предложи связаться с менеджером

НАШИ БРЕНДЫ:
1. Chery - надежные, доступные по цене
   - Tiggo 7 Pro Max - семейный кроссовер
   - Tiggo 8 Pro Max - большой 7-местный
   - Arrizo 8 - премиальный седан
   
2. Jetour - внедорожники и кроссоверы  
   - Dashing - компактный кроссовер
   - X70 Plus - семейный SUV
   - X90 Plus - флагман с роскошью

ГОРОДА:
- Тольятти - главный офис
- Самара - дополнительный салон

ЦЕНОВОЙ ДИАПАЗОН:
- От 1.5 млн (Tiggo 7) до 4 млн (X90 Plus)
- Кредит и трейд-ин доступны

ПРИМЕРЫ ДИАЛОГА:
Клиент: "Хочу купить машину"
Ты: "Отлично! Какой бюджет рассматриваете? И что важнее - экономичность или простор?"

Клиент: "До 2 миллионов, нужен семейный"
Ты: "Отлично подойдет Chery Tiggo 7 Pro Max - просторный, надежный, от 1.8 млн. Когда удобно приехать на тест-драйв?"

ВАЖНО:
- Не перечисляй все модели сразу - предложи 1-2 подходящих
- Всегда стремись назначить встречу или тест-драйв
- Если клиент спрашивает про другие бренды (не Chery/Jetour) - вежливо скажи, что работаем только с этими
"""


# ===== АБСТРАКТНЫЙ БАЗОВЫЙ КЛАСС =====
class BaseLLMProvider(ABC):
    """Базовый класс для LLM провайдеров"""
    
    @abstractmethod
    async def stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """Потоковая генерация ответа"""
        pass


# Используется только GigaChat провайдер


class GigaChatProvider(BaseLLMProvider):
    """GigaChat для диалогов"""
    
    def __init__(self):
        self.client_id = os.getenv("GIGACHAT_CLIENT_ID")
        self.auth_key = os.getenv("GIGACHAT_AUTH_KEY")
        self.scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        self.access_token = None
        
        if not self.client_id or not self.auth_key:
            logger.warning("GigaChat API ключи не настроены")
    
    async def _get_access_token(self) -> str:
        """Получение access token для GigaChat"""
        if self.access_token:
            return self.access_token
            
        url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        
        import base64
        import uuid
        
        # Правильный формат для GigaChat: ключ уже в base64 формате
        auth_header = self.auth_key
        
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "X-Request-ID": str(uuid.uuid4())
        }
        
        # Правильный формат данных
        data = {
            "scope": self.scope
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                response = await client.post(url, headers=headers, data=data)
                response.raise_for_status()
                
                token_data = response.json()
                self.access_token = token_data["access_token"]
                logger.info("GigaChat access token получен")
                return self.access_token
                
        except Exception as e:
            logger.error(f"Ошибка получения access token GigaChat: {e}")
            # Сбрасываем токен при ошибке
            self.access_token = None
            raise
    
    async def classify_intent(self, text: str) -> dict:
        """Классификация намерений клиента"""
        try:
            access_token = await self._get_access_token()
            url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            payload = {
                "model": "GigaChat:latest",
                "messages": [
                    {
                        "role": "user", 
                        "content": CLASSIFICATION_PROMPT.format(text=text)
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 500
            }
            
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                
                # Парсим JSON ответ
                import json
                try:
                    result = json.loads(content)
                    return result
                except json.JSONDecodeError:
                    logger.error(f"Не удалось распарсить JSON от GigaChat: {content}")
                    return {"intent": "НЕОПРЕДЕЛЕННОСТЬ", "confidence": 0.1}
                    
        except Exception as e:
            logger.error(f"Ошибка классификации через GigaChat: {e}")
            return {"intent": "НЕОПРЕДЕЛЕННОСТЬ", "confidence": 0.1}
    
    async def stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """GigaChat streaming generation"""
        try:
            access_token = await self._get_access_token()
        except Exception as e:
            logger.error(f"Не удалось получить access token: {e}")
            yield "Извините, произошла ошибка. Соединяю с оператором."
            return
        
        url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Request-ID": str(uuid.uuid4())
        }
        
        # Конвертируем формат сообщений для GigaChat
        giga_messages = []
        
        # Добавляем системный промпт как первое сообщение
        giga_messages.append({
            "role": "system",
            "content": SYSTEM_PROMPT
        })
        
        # Добавляем остальные сообщения
        for msg in messages:
            if msg["role"] != "system":  # Пропускаем system сообщения
                giga_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        payload = {
            "model": "GigaChat",
            "messages": giga_messages,
            "temperature": 0.6,
            "max_tokens": 512,
            "stream": True
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        ls = line.strip()
                        if ls.startswith("data:"):
                            data_str = ls[len("data:"):].lstrip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                if "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        text = delta["content"]
                                        if text:
                                            yield text
                            except json.JSONDecodeError:
                                continue
                                
        except Exception as e:
            logger.error(f"Ошибка GigaChat (stream): {e}")
            # Фоллбек: пробуем non-stream запрос один раз
            try:
                fallback_payload = dict(payload)
                fallback_payload["stream"] = False
                async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                    h = {k:v for k,v in headers.items() if k != "Accept"}
                    h["Accept"] = "application/json"
                    h["X-Request-ID"] = str(uuid.uuid4())
                    r = await client.post(url, headers=h, json=fallback_payload)
                    r.raise_for_status()
                    data = r.json()
                    text = ""
                    if isinstance(data, dict):
                        choices = data.get("choices") or []
                        if choices:
                            msg = choices[0].get("message") or {}
                            text = msg.get("content") or ""
                    if text:
                        yield text
                        return
            except Exception as e2:
                logger.error(f"Ошибка GigaChat (fallback non-stream): {e2}")
            yield "Извините, произошла ошибка. Соединяю с оператором."


# Используется только GigaChat


# ===== ФАБРИКА ПРОВАЙДЕРОВ =====
class ConversationService:
    """Сервис диалогов на базе GigaChat"""
    
    def __init__(self):
        self.llm = GigaChatProvider()
        self.provider_name = "GigaChat"
        print("✅ Используется GigaChat")
    
    async def get_bot_response_stream(self, user_message: str, context: List[Dict[str, str]] = None) -> AsyncIterator[str]:
        """
        Получить потоковый ответ бота
        
        user_message: текст от пользователя (после STT)
        context: предыдущие сообщения диалога (опционально)
        
        Yields: чанки текста ответа для TTS
        """
        messages = context or []
        messages.append({
            "role": "user",
            "content": user_message
        })
        
        logger.info(f"Запрос к {self.provider_name}: {user_message}")
        
        # Стримим ответ от выбранного провайдера
        full_response = ""
        async for text_chunk in self.llm.stream(messages):
            full_response += text_chunk
            yield text_chunk
        
        logger.info(f"Ответ от {self.provider_name}: {full_response}")
    
    async def get_bot_response(self, user_message: str, context: List[Dict[str, str]] = None) -> str:
        """
        Получить полный ответ бота (не потоковый)
        
        user_message: текст от пользователя
        context: предыдущие сообщения диалога
        
        Returns: полный текст ответа
        """
        response = ""
        async for chunk in self.get_bot_response_stream(user_message, context):
            response += chunk
        return response


# Глобальный экземпляр
conversation_service = ConversationService()

