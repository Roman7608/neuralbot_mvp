"""
Обработчик голосовых звонков с интеграцией STT → LLM → TTS
Оптимизирован для минимальных задержек (< 1 сек)
"""

import os
import asyncio
import logging
from typing import AsyncIterator, List, Dict, Optional
from datetime import datetime
from .salute_speech import salute_speech
from .conversation import conversation_service
from .asterisk import ami_client
from ..db import SessionLocal
from ..models import CallLog, Lead, LeadSource, LeadStatus

logger = logging.getLogger(__name__)


class VoiceCallHandler:
    """Обработчик голосовых звонков с real-time диалогом"""
    
    def __init__(self):
        self.active_calls = {}  # channel_id: call_context
    
    async def handle_incoming_call(self, channel: str, caller_id: str) -> Dict:
        """
        Обработка входящего голосового звонка
        
        channel: ID канала Asterisk
        caller_id: номер телефона звонящего
        
        Returns: результат обработки
        """
        logger.info(f"Начинаем обработку голосового звонка: {caller_id} на канале {channel}")
        print(f"📞 Входящий звонок от {caller_id}")
        
        # Инициализируем контекст звонка
        call_context = {
            "channel": channel,
            "caller_id": caller_id,
            "started_at": datetime.now(),
            "messages": [],  # История диалога
            "turn_count": 0,  # Количество реплик
        }
        self.active_calls[channel] = call_context
        
        # Создаем запись в БД
        db = SessionLocal()
        call_log = CallLog(
            caller_id=caller_id,
            channel=channel,
            direction="inbound",
            status="in_progress",
            started_at=call_context["started_at"]
        )
        db.add(call_log)
        db.commit()
        call_log_id = call_log.id
        db.close()
        
        try:
            # Начинаем диалог
            await self._run_dialog(call_context, call_log_id)
            
            return {"ok": True, "message": "Звонок обработан"}
            
        except Exception as e:
            logger.error(f"Ошибка обработки звонка {channel}: {e}")
            return {"ok": False, "error": str(e)}
        
        finally:
            # Удаляем из активных
            if channel in self.active_calls:
                del self.active_calls[channel]
    
    async def _run_dialog(self, call_context: Dict, call_log_id: int):
        """Запуск диалога с клиентом"""
        
        channel = call_context["channel"]
        caller_id = call_context["caller_id"]
        
        # Приветствие
        greeting = "Здравствуйте! Автосалон Chery и Jetour. Чем могу помочь?"
        await self._speak_to_caller(channel, greeting)
        call_context["messages"].append({
            "role": "assistant",
            "content": greeting
        })
        
        # Основной цикл диалога (максимум 10 реплик)
        max_turns = 10
        
        for turn in range(max_turns):
            call_context["turn_count"] = turn + 1
            
            # 1. Слушаем пользователя (STT)
            user_text = await self._listen_to_caller(channel)
            
            if not user_text or user_text.strip() == "":
                # Тишина - переспрашиваем
                await self._speak_to_caller(channel, "Извините, не расслышал. Повторите пожалуйста?")
                continue
            
            print(f"👤 Клиент: {user_text}")
            logger.info(f"Клиент сказал: {user_text}")
            
            # Добавляем в контекст
            call_context["messages"].append({
                "role": "user",
                "content": user_text
            })
            
            # Проверяем на завершение разговора
            if self._is_goodbye(user_text):
                await self._speak_to_caller(channel, "До свидания! Рады были помочь.")
                break
            
            # 2. Генерируем ответ (LLM) и озвучиваем (TTS) ПАРАЛЛЕЛЬНО
            bot_response = await self._get_and_speak_response(channel, call_context["messages"])
            
            print(f"🤖 Бот: {bot_response}")
            logger.info(f"Бот ответил: {bot_response}")
            
            # Добавляем в контекст
            call_context["messages"].append({
                "role": "assistant",
                "content": bot_response
            })
            
            # Проверяем, нужно ли переключить на человека
            if self._should_transfer_to_human(bot_response):
                await self._transfer_to_manager(channel, caller_id)
                break
        
        # Обновляем статус звонка
        db = SessionLocal()
        call_log = db.query(CallLog).filter(CallLog.id == call_log_id).first()
        if call_log:
            call_log.status = "completed"
            call_log.ended_at = datetime.now()
            db.commit()
        db.close()
    
    async def _listen_to_caller(self, channel: str, timeout: int = 10) -> str:
        """
        Слушаем речь от звонящего через Asterisk AGI
        
        Интеграция с Asterisk для получения аудио потока
        """
        logger.info(f"Слушаем клиента на канале {channel}...")
        
        try:
            # Получаем аудио от Asterisk через AMI
            if ami_client.connected:
                # Запускаем запись аудио от пользователя
                audio_file = f"/tmp/user_audio_{channel.replace('/', '_')}.wav"
                success = await ami_client.start_recording(channel, audio_file)
                
                if success:
                    # Ждем записи аудио (5 секунд)
                    await asyncio.sleep(5)
                    
                    # Останавливаем запись
                    await ami_client.stop_recording(channel)
                    
                    # Проверяем, что файл создался
                    if os.path.exists(audio_file):
                        # Читаем аудио файл
                        with open(audio_file, 'rb') as f:
                            audio_data = f.read()
                        
                        # Удаляем временный файл
                        os.remove(audio_file)
                        
                        # Распознаем речь через Salute STT
                        if audio_data:
                            # Конвертируем в нужный формат для Salute (16kHz mono)
                            text = await self._convert_and_recognize_audio(audio_data)
                            return text
                
                logger.warning(f"Не удалось получить аудио от канала {channel}")
                return ""
            else:
                logger.error("AMI клиент не подключен")
                return ""
                
        except Exception as e:
            logger.error(f"Ошибка при прослушивании клиента: {e}")
            return ""
    
    async def _convert_and_recognize_audio(self, audio_data: bytes) -> str:
        """
        Конвертируем аудио в формат для Salute STT и распознаем речь
        """
        try:
            # Salute STT ожидает PCM 16-bit LE, mono, 16 kHz
            # Asterisk записывает в формате WAV, нужно конвертировать
            
            # Временный файл для конвертации
            temp_input = "/tmp/temp_input.wav"
            temp_output = "/tmp/temp_output.wav"
            
            # Сохраняем исходное аудио
            with open(temp_input, 'wb') as f:
                f.write(audio_data)
            
            # Конвертируем через sox (если доступен) или используем Python
            try:
                # Пробуем через sox
                import subprocess
                result = subprocess.run([
                    'sox', temp_input, '-r', '16000', '-c', '1', '-b', '16', 
                    '-e', 'signed-integer', temp_output
                ], capture_output=True, timeout=10)
                
                if result.returncode == 0 and os.path.exists(temp_output):
                    # Читаем конвертированное аудио
                    with open(temp_output, 'rb') as f:
                        converted_audio = f.read()
                    
                    # Удаляем временные файлы
                    os.remove(temp_input)
                    os.remove(temp_output)
                    
                    # Распознаем речь
                    text = await salute_speech.stt_recognize_pcm16(converted_audio, 16000)
                    return text or ""
                else:
                    logger.warning("Sox конвертация не удалась, пробуем без конвертации")
                    
            except Exception as conv_error:
                logger.warning(f"Ошибка конвертации через sox: {conv_error}")
            
            # Fallback: пробуем распознать как есть (8kHz)
            text = await salute_speech.stt_recognize_pcm16(audio_data, 8000)
            
            # Очищаем временные файлы
            for temp_file in [temp_input, temp_output]:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            
            return text or ""
            
        except Exception as e:
            logger.error(f"Ошибка конвертации и распознавания аудио: {e}")
            return ""
    
    async def _speak_to_caller(self, channel: str, text: str):
        """
        Произносим текст звонящему через Asterisk
        """
        logger.info(f"Говорим клиенту: {text}")
        print(f"🔊 Бот говорит: {text}")
        
        try:
            # Синтезируем речь (Salute)
            audio_data = await salute_speech.tts_synthesize(text, voice="Bys_8000", sample_rate_hz=8000)
            
            if audio_data:
                # Сохраняем аудио во временный файл
                temp_audio_file = f"/tmp/bot_response_{channel.replace('/', '_')}.wav"
                with open(temp_audio_file, 'wb') as f:
                    f.write(audio_data)
                
                # Воспроизводим через Asterisk AMI
                if ami_client.connected:
                    # Используем Playback для воспроизведения файла
                    success = await ami_client.playback_file(channel, temp_audio_file)
                    if success:
                        logger.info(f"TTS: воспроизведено {len(audio_data)} bytes аудио")
                    else:
                        logger.error("Ошибка воспроизведения аудио через AMI")
                
                # Удаляем временный файл
                if os.path.exists(temp_audio_file):
                    os.remove(temp_audio_file)
            else:
                logger.error("Не удалось синтезировать речь")
                
        except Exception as e:
            logger.error(f"Ошибка при воспроизведении речи: {e}")
    
    async def _get_and_speak_response(self, channel: str, messages: List[Dict]) -> str:
        """
        ОПТИМИЗАЦИЯ: Генерируем ответ и начинаем синтез параллельно
        
        Это ключевая функция для минимальных задержек:
        1. LLM генерирует текст чанками (streaming)
        2. Как только накопилось предложение - сразу синтезируем и играем
        3. Параллельная обработка сокращает задержки
        """
        full_response = ""
        sentence_buffer = ""
        
        # Параллельная обработка: streaming LLM + потоковый TTS
        async for text_chunk in conversation_service.get_bot_response_stream("", messages):
            full_response += text_chunk
            sentence_buffer += text_chunk
            
            # Если накопилось законченное предложение - синтезируем и играем
            if any(punct in sentence_buffer for punct in ['. ', '! ', '? ', '\n']):
                # Находим последнюю законченную фразу
                last_punct = max(
                    sentence_buffer.rfind('. '),
                    sentence_buffer.rfind('! '),
                    sentence_buffer.rfind('? '),
                    -1
                )
                
                if last_punct > 0:
                    sentence = sentence_buffer[:last_punct + 1].strip()
                    sentence_buffer = sentence_buffer[last_punct + 1:].strip()
                    
                    if sentence:
                        # Синтезируем и играем предложение (параллельно с генерацией следующего)
                        asyncio.create_task(self._speak_to_caller(channel, sentence))
        
        # Произносим остаток
        if sentence_buffer.strip():
            await self._speak_to_caller(channel, sentence_buffer.strip())
        
        return full_response
    
    def _is_goodbye(self, text: str) -> bool:
        """Проверка на завершение разговора"""
        goodbye_words = ["пока", "до свидания", "спасибо", "всё", "хватит", "goodbye", "bye"]
        text_lower = text.lower()
        return any(word in text_lower for word in goodbye_words)
    
    def _should_transfer_to_human(self, bot_response: str) -> bool:
        """Проверка, нужно ли переключить на оператора"""
        transfer_phrases = [
            "соединяю с оператором",
            "переключаю на менеджера",
            "свяжу с консультантом",
        ]
        text_lower = bot_response.lower()
        return any(phrase in text_lower for phrase in transfer_phrases)
    
    async def _transfer_to_manager(self, channel: str, caller_id: str):
        """Переключение звонка на менеджера"""
        logger.info(f"Переключаем звонок {caller_id} на менеджера")
        
        # Ищем подходящий отдел на основе контекста разговора
        target_extension = "100"  # fallback консультант
        
        # TODO: Используйте классификатор для выбора отдела
        # department = classify_department_from_context(call_context["messages"])
        
        # Переключаем звонок через AMI
        if ami_client.connected:
            success = await ami_client.originate_call(caller_id, target_extension)
            if success:
                logger.info(f"Звонок переключен на ext {target_extension}")
            else:
                logger.error(f"Ошибка переключения звонка")


# ===== ОПТИМИЗИРОВАННЫЙ PIPELINE =====

async def process_voice_interaction(user_audio_file: str) -> str:
    """
    DEMO: Полный цикл обработки голосового взаимодействия
    
    user_audio_file: путь к файлу с речью пользователя
    Returns: путь к файлу с ответом бота
    """
    import time
    
    start_time = time.time()
    
    # Шаг 1: STT - распознаем речь
    stt_start = time.time()
        # TODO: заменить на Salute STT при готовности
        user_text = ""  # salute_speech.recognize_file(user_audio_file)
    stt_time = time.time() - stt_start
    print(f"⏱️ STT: {stt_time:.2f} сек - Распознано: {user_text}")
    
    if not user_text:
        return ""
    
    # Шаг 2: LLM - генерируем ответ
    llm_start = time.time()
    bot_response = await conversation_service.get_bot_response(user_text)
    llm_time = time.time() - llm_start
    print(f"⏱️ LLM: {llm_time:.2f} сек - Ответ: {bot_response}")
    
    # Шаг 3: TTS - синтезируем речь
    tts_start = time.time()
    output_file = f"/tmp/bot_response_{int(time.time())}.pcm"
    audio = await salute_speech.tts_synthesize(bot_response)
    if audio:
        with open(output_file, "wb") as f:
            f.write(audio)
    tts_time = time.time() - tts_start
    print(f"⏱️ TTS: {tts_time:.2f} сек - Аудио: {output_file}")
    
    total_time = time.time() - start_time
    print(f"⏱️ ИТОГО: {total_time:.2f} сек")
    print(f"  STT: {stt_time:.2f}с | LLM: {llm_time:.2f}с | TTS: {tts_time:.2f}с")
    
    return output_file


# Глобальный экземпляр обработчика
voice_call_handler = VoiceCallHandler()


