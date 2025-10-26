import os
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import httpx
from ..db import SessionLocal
from ..models import CallLog, Lead, LeadSource, LeadStatus
from .llm import classify_text
from .leads import get_departments_config

logger = logging.getLogger(__name__)

class AsteriskAMI:
    """Клиент для работы с Asterisk AMI"""
    
    def __init__(self):
        self.host = os.getenv("ASTERISK_HOST", "localhost")
        self.port = int(os.getenv("ASTERISK_PORT", "5038"))
        self.username = os.getenv("ASTERISK_USERNAME", "admin")
        self.password = os.getenv("ASTERISK_PASSWORD", "amp111")
        self.connected = False
        self.reader = None
        self.writer = None
        self.listening_task = None
        self.should_listen = False
        self._read_lock = asyncio.Lock()
        
    async def connect(self):
        """Подключение к Asterisk AMI"""
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            
            # Читаем приветствие от Asterisk
            greeting = await self._read_response()
            logger.info(f"Asterisk greeting: {greeting}")
            
            # Отправляем команду Login
            login_cmd = f"Action: Login\r\nUsername: {self.username}\r\nSecret: {self.password}\r\n\r\n"
            self.writer.write(login_cmd.encode())
            await self.writer.drain()
            
            # Читаем ответ на Login
            response = ""
            success = False
            while True:
                line = await self._read_response()
                response += line + "\n"
                
                # Проверяем успешность логина
                if "Response: Success" in line or "Authentication accepted" in line:
                    success = True
                
                # Конец ответа - пустая строка
                if line == "":
                    break
            
            logger.info(f"Login response: {response}")
            
            if success:
                self.connected = True
                logger.info("Подключен к Asterisk AMI")
                # Включаем отправку всех событий AMI
                try:
                    await self._send_command("Action: Events")
                    await self._send_command("EventMask: on")
                    await self._send_command("")
                except Exception as e:
                    logger.error(f"Не удалось включить события AMI: {e}")
                return True
            else:
                logger.error(f"Ошибка подключения к Asterisk: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка подключения к Asterisk AMI: {e}")
            return False
    
    async def disconnect(self):
        """Отключение от Asterisk AMI"""
        if self.writer:
            await self._send_command("Action: Logoff")
            self.writer.close()
            await self.writer.wait_closed()
        self.connected = False
        logger.info("Отключен от Asterisk AMI")
    
    async def _send_command(self, command: str):
        """Отправка команды в AMI"""
        if self.writer:
            self.writer.write(f"{command}\r\n".encode())
            await self.writer.drain()
    
    async def _read_response(self) -> str:
        """Чтение ответа от AMI"""
        if self.reader:
            async with self._read_lock:
                response = await self.reader.readline()
                return response.decode().strip()
        return ""
    
    async def originate_call(self, caller_id: str, extension: str, context: str = "from-internal") -> bool:
        """Инициация звонка"""
        try:
            await self._send_command("Action: Originate")
            await self._send_command(f"Channel: SIP/{caller_id}")
            await self._send_command(f"Exten: {extension}")
            await self._send_command(f"Context: {context}")
            await self._send_command("Priority: 1")
            await self._send_command("")
            
            response = await self._read_response()
            return "Success" in response
            
        except Exception as e:
            logger.error(f"Ошибка инициации звонка: {e}")
            return False
    
    async def stop_recording(self, channel: str) -> bool:
        """Остановить запись звонка"""
        try:
            await self._send_command("Action: StopMonitor")
            await self._send_command(f"Channel: {channel}")
            await self._send_command("")
            
            response = await self._read_response()
            return "Success" in response
            
        except Exception as e:
            logger.error(f"Ошибка остановки записи: {e}")
            return False
    
    async def check_file_exists(self, filepath: str) -> bool:
        """Проверка существования файла"""
        try:
            # Используем команду ListFiles вместо CoreShowFile
            await self._send_command("Action: ListFiles")
            await self._send_command(f"Directory: {filepath}")
            await self._send_command("")
            
            response = await self._read_response()
            return "Success" in response
            
        except Exception as e:
            logger.error(f"Ошибка проверки файла: {e}")
            return False
    
    async def playback_file(self, channel: str, filename: str) -> bool:
        """Воспроизвести файл на канале"""
        try:
            await self._send_command("Action: Playback")
            await self._send_command(f"Channel: {channel}")
            await self._send_command(f"Filename: {filename}")
            await self._send_command("")
            
            response = await self._read_response()
            return "Success" in response
            
        except Exception as e:
            logger.error(f"Ошибка воспроизведения файла: {e}")
            return False
    
    async def _parse_ami_event(self) -> Optional[Dict[str, Any]]:
        """Парсинг события AMI"""
        try:
            event = {}
            while True:
                line = await self._read_response()
                
                if not line:
                    # Конец события
                    if event:
                        return event
                    continue
                
                if ": " in line:
                    key, value = line.split(": ", 1)
                    event[key] = value
                    
        except Exception as e:
            logger.error(f"Ошибка парсинга события AMI: {e}")
            return None
    
    async def listen_events(self):
        """Слушать события от Asterisk AMI в цикле"""
        print("Начинаем слушать события AMI...")
        logger.info("Начинаем слушать события AMI...")
        self.should_listen = True
        
        try:
            while self.should_listen and self.connected:
                print(f"Цикл слушания: should_listen={self.should_listen}, connected={self.connected}")
                event = await self._parse_ami_event()
                print(f"Получено событие: {event}")
                
                if event and "Event" in event:
                    event_type = event.get("Event")
                    print(f"✅ Получено событие AMI: {event_type}")
                    logger.info(f"Получено событие AMI: {event_type}")
                    
                    # Обрабатываем событие
                    try:
                        print(f"🔍 Обрабатываем событие: {event_type}")
                        result = await handle_asterisk_event(event)
                        print(f"✅ Событие {event_type} обработано: {result}")
                    except Exception as e:
                        print(f"❌ Ошибка обработки события {event_type}: {e}")
                        logger.error(f"Ошибка обработки события {event_type}: {e}")
                
                # Небольшая задержка, чтобы не нагружать CPU
                await asyncio.sleep(0.1)
                
        except Exception as e:
            print(f"❌ Ошибка в цикле прослушивания событий: {e}")
            logger.error(f"Ошибка в цикле прослушивания событий: {e}")
        finally:
            self.should_listen = False
            print("Прослушивание событий AMI остановлено")
            logger.info("Прослушивание событий AMI остановлено")
    
    async def start_listening(self):
        """Запустить фоновую задачу слушания событий"""
        if not self.listening_task or self.listening_task.done():
            loop = asyncio.get_event_loop()
            self.listening_task = loop.create_task(self.listen_events())
            logger.info("Фоновая задача слушания событий запущена")
    
    async def stop_listening(self):
        """Остановить слушание событий"""
        self.should_listen = False
        if self.listening_task:
            self.listening_task.cancel()
            try:
                await self.listening_task
            except asyncio.CancelledError:
                pass
        logger.info("Слушание событий остановлено")

# Глобальный экземпляр AMI клиента
ami_client = AsteriskAMI()

async def handle_asterisk_event(data: dict) -> dict:
    """Обработка событий от Asterisk"""
    try:
        event_type = data.get("Event")
        
        if event_type == "Newchannel":
            await _handle_new_channel(data)
        elif event_type == "Newexten":
            await _handle_newexten(data)
        elif event_type == "Hangup":
            await _handle_hangup(data)
        elif event_type == "Dial":
            await _handle_dial(data)
        elif event_type == "Bridge":
            await _handle_bridge(data)
            
        return {"ok": True}
        
    except Exception as e:
        logger.error(f"Ошибка обработки события Asterisk: {e}")
        return {"ok": False}

async def _handle_new_channel(data: dict):
    """Обработка нового канала"""
    channel = data.get("Channel", "")
    caller_id = data.get("CallerIDNum", "")
    context = data.get("Context", "")
    exten = data.get("Exten", "")
    
    print(f"🎯 _handle_new_channel вызван!")
    print(f"Новый канал: {channel}, CallerID: {caller_id}, Context: {context}, Exten: {exten}")
    logger.info(f"Новый канал: {channel}, CallerID: {caller_id}, Context: {context}")
    
    # Создаем запись о звонке для всех контекстов
    if caller_id and channel:
        direction = "inbound" if context == "from-pstn" else "internal"
        
        db = SessionLocal()
        call_log = CallLog(
            caller_id=caller_id,
            channel=channel,
            direction=direction,
            status="ringing",
            started_at=datetime.now()
        )
        db.add(call_log)
        db.commit()
        db.close()
        print(f"✅ Создана запись о звонке: {channel}")
        
        # Если звонок на extension 001 - запускаем голосовой диалог через AMI
        if exten == "001" and context == "from-internal":
            print(f"🎯 Запуск голосового диалога для extension 001 через AMI")
            logger.info(f"Запуск голосового диалога для extension 001 через AMI")
            await _start_voice_dialog_ami(channel, caller_id)

async def _handle_newexten(data: dict):
    """Обработка события Newexten (переход по шагам диалплана)"""
    try:
        channel = data.get("Channel", "")
        context = data.get("Context", "")
        extension = data.get("Extension", data.get("Exten", ""))
        app = data.get("Application", "")
        caller_id = data.get("CallerIDNum", "")
        
        print(f"🧭 Newexten: channel={channel}, context={context}, extension={extension}, app={app}")
        logger.info(f"Newexten: context={context}, extension={extension}, app={app}")
        
        # Триггерим голосовой диалог, когда достигли extension 001 во внутреннем контексте
        if extension == "001" and context == "from-internal" and caller_id:
            print("🎯 Запуск голосового диалога (по Newexten) для extension 001")
            logger.info("Запуск голосового диалога (по Newexten) для extension 001")
            await _start_voice_dialog_ami(channel, caller_id)
            
    except Exception as e:
        logger.error(f"Ошибка обработки Newexten: {e}")
    
    # Если это входящий звонок из внешнего контекста - обрабатываем с маршрутизацией
    if context == "from-pstn" and caller_id:
        await _process_incoming_call(caller_id, channel)

async def _process_incoming_call(caller_id: str, channel: str):
    """Обработка входящего звонка с LLM маршрутизацией"""
    try:
        # Создаем запись о звонке
        db = SessionLocal()
        call_log = CallLog(
            caller_id=caller_id,
            channel=channel,
            direction="inbound",
            status="ringing",
            started_at=datetime.now()
        )
        db.add(call_log)
        db.commit()
        call_id = call_log.id
        db.close()
        
        # Получаем информацию о звонящем из базы (если есть)
        db = SessionLocal()
        existing_lead = db.query(Lead).filter(
            Lead.phone == caller_id,
            Lead.source == LeadSource.phone
        ).order_by(Lead.created_at.desc()).first()
        db.close()
        
        # Определяем отдел на основе истории клиента или по умолчанию
        department_code = "consultant_fallback"
        
        if existing_lead:
            department_code = existing_lead.department_code
            logger.info(f"Найден существующий клиент: {existing_lead.name}, отдел: {department_code}")
        else:
            # Создаем новый лид для незнакомого номера
            db = SessionLocal()
            new_lead = Lead(
                name=f"Звонящий {caller_id}",
                phone=caller_id,
                source=LeadSource.phone,
                department_code=department_code,
                comment="Входящий звонок",
                status=LeadStatus.new
            )
            db.add(new_lead)
            db.commit()
            db.close()
        
        # Находим номер отдела для переадресации
        departments = get_departments_config().get("departments", [])
        target_dept = next((d for d in departments if d["code"] == department_code), None)
        
        if target_dept and target_dept.get("ext"):
            target_extension = target_dept["ext"]
            logger.info(f"Переадресация звонка {caller_id} в отдел {department_code} (ext: {target_extension})")
            
            # Переадресация звонка
            if await ami_client.originate_call(caller_id, target_extension):
                # Обновляем статус звонка
                db = SessionLocal()
                call_log = db.query(CallLog).filter(CallLog.id == call_id).first()
                if call_log:
                    call_log.status = "routed"
                    call_log.routed_to = target_extension
                    call_log.routed_at = datetime.now()
                    db.commit()
                db.close()
                
                logger.info(f"Звонок {caller_id} успешно переадресован в {target_extension}")
            else:
                logger.error(f"Ошибка переадресации звонка {caller_id}")
        else:
            logger.warning(f"Не найден номер отдела для {department_code}")
            
    except Exception as e:
        logger.error(f"Ошибка обработки входящего звонка: {e}")

async def _handle_hangup(data: dict):
    """Обработка завершения звонка"""
    channel = data.get("Channel", "")
    cause = data.get("Cause", "")
    
    print(f"Завершение звонка: {channel}, причина: {cause}")
    logger.info(f"Завершение звонка: {channel}, причина: {cause}")
    
    # Обновляем статус в базе
    db = SessionLocal()
    call_log = db.query(CallLog).filter(CallLog.channel == channel).first()
    if call_log:
        call_log.status = "completed"
        call_log.ended_at = datetime.now()
        call_log.hangup_cause = cause
        db.commit()
        print(f"✅ Обновлен статус звонка: {channel} -> completed")
    else:
        print(f"⚠️ Запись о звонке не найдена: {channel}")
    db.close()

async def _handle_dial(data: dict):
    """Обработка события дозвона"""
    channel = data.get("Channel", "")
    destination = data.get("Destination", "")
    
    logger.info(f"Дозвон: {channel} -> {destination}")

async def _handle_bridge(data: dict):
    """Обработка соединения звонков"""
    channel1 = data.get("Channel1", "")
    channel2 = data.get("Channel2", "")
    
    logger.info(f"Соединение: {channel1} <-> {channel2}")

async def start_asterisk_monitoring():
    """Запуск мониторинга Asterisk"""
    print("start_asterisk_monitoring вызван!")
    if await ami_client.connect():
        # Запускаем фоновое слушание событий
        print("AMI подключен, запускаем слушание событий...")
        await ami_client.start_listening()
        print("Фоновое слушание событий запущено!")
        logger.info("Мониторинг Asterisk запущен с автоматическим слушанием событий")
        return True
    print("Не удалось подключиться к AMI")
    return False

async def stop_asterisk_monitoring():
    """Остановка мониторинга Asterisk"""
    await ami_client.stop_listening()
    await ami_client.disconnect()
    logger.info("Мониторинг Asterisk остановлен")

async def _start_voice_dialog_ami(channel: str, caller_id: str):
    """Запуск голосового диалога через AMI события"""
    try:
        print(f"🎤 Запуск голосового диалога через AMI для канала {channel}")
        logger.info(f"Запуск голосового диалога через AMI для канала {channel}")
        
        # Ждем немного, чтобы диалплан завершил приветствие
        await asyncio.sleep(3)
        
        # Получаем запись звонка из MixMonitor
        call_filename = f"20251023-{datetime.now().strftime('%H%M%S')}-{caller_id}"
        recording_path = f"/var/spool/asterisk/monitor/{call_filename}.wav"
        
        # Ждем, пока файл записи появится
        max_wait = 10  # максимум 10 секунд
        wait_time = 0
        while wait_time < max_wait:
            try:
                # Проверяем, есть ли файл записи
                if await ami_client.check_file_exists(recording_path):
                    print(f"✅ Файл записи найден: {recording_path}")
                    break
            except Exception as e:
                print(f"⚠️ Ошибка проверки файла: {e}")
            
            await asyncio.sleep(1)
            wait_time += 1
        
        if wait_time >= max_wait:
            print(f"⚠️ Файл записи не найден, используем тестовые данные")
            recording_path = None
        
        # Обрабатываем аудио через API
        await _process_voice_interaction_ami(channel, caller_id, recording_path)
        
    except Exception as e:
        print(f"❌ Ошибка запуска голосового диалога через AMI: {e}")
        logger.error(f"Ошибка запуска голосового диалога через AMI: {e}")

async def _process_voice_interaction_ami(channel: str, caller_id: str, recording_path: str = None):
    """Обработка голосового взаимодействия через AMI"""
    try:
        print(f"🎯 Обработка голосового взаимодействия для канала {channel}")
        
        # Подготавливаем аудио данные
        audio_data = None
        if recording_path:
            try:
                # Читаем файл записи
                with open(recording_path, 'rb') as f:
                    audio_data = f.read()
                print(f"✅ Прочитан файл записи: {len(audio_data)} bytes")
            except Exception as e:
                print(f"⚠️ Ошибка чтения файла записи: {e}")
        
        # Если нет аудио данных, используем тестовые
        if not audio_data:
            audio_data = b"test"  # Тестовые данные
            print(f"⚠️ Используем тестовые аудио данные")
        
        # Кодируем в base64
        import base64
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        # Отправляем запрос к API
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://localhost:8000/internal/bot/voice/call",
                json={
                    "audio_data": audio_base64,
                    "caller_id": caller_id,
                    "sample_rate": 8000,
                    "language": "ru-RU"
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ API ответ получен: {data.get('bot_response', '')[:50]}...")
                
                # Воспроизводим ответ через AMI
                if data.get('audio_response'):
                    await _play_audio_response_ami(channel, data['audio_response'])
                else:
                    # Fallback - используем TTS
                    await _play_text_response_ami(channel, data.get('bot_response', 'Спасибо за звонок!'))
                
            else:
                print(f"❌ Ошибка API: {response.status_code}")
                # Fallback ответ
                await _play_text_response_ami(channel, "Извините, произошла ошибка. Попробуйте позже.")
                
    except Exception as e:
        print(f"❌ Ошибка обработки голосового взаимодействия: {e}")
        logger.error(f"Ошибка обработки голосового взаимодействия: {e}")

async def _play_audio_response_ami(channel: str, audio_base64: str):
    """Воспроизведение аудио ответа через AMI"""
    try:
        # Декодируем аудио
        import base64
        audio_data = base64.b64decode(audio_base64)
        
        # Сохраняем во временный файл
        temp_file = f"/tmp/bot_response_{channel.replace('/', '_')}.wav"
        with open(temp_file, 'wb') as f:
            f.write(audio_data)
        
        # Воспроизводим через AMI
        success = await ami_client.playback_file(channel, temp_file)
        if success:
            print(f"✅ Аудио ответ воспроизведен через AMI")
        else:
            print(f"❌ Ошибка воспроизведения аудио через AMI")
        
        # Удаляем временный файл
        import os
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
    except Exception as e:
        print(f"❌ Ошибка воспроизведения аудио: {e}")

async def _play_text_response_ami(channel: str, text: str):
    """Воспроизведение текстового ответа через TTS и AMI"""
    try:
        # Синтезируем речь
        from .salute_speech import salute_speech
        audio_data = await salute_speech.tts_synthesize(text, voice="Bys_8000", sample_rate_hz=8000)
        
        if audio_data:
            # Сохраняем во временный файл
            temp_file = f"/tmp/bot_tts_{channel.replace('/', '_')}.wav"
            with open(temp_file, 'wb') as f:
                f.write(audio_data)
            
            # Воспроизводим через AMI
            success = await ami_client.playback_file(channel, temp_file)
            if success:
                print(f"✅ TTS ответ воспроизведен через AMI")
            else:
                print(f"❌ Ошибка воспроизведения TTS через AMI")
            
            # Удаляем временный файл
            import os
            if os.path.exists(temp_file):
                os.remove(temp_file)
        else:
            print(f"❌ Ошибка синтеза речи")
            
    except Exception as e:
        print(f"❌ Ошибка воспроизведения текста: {e}")



