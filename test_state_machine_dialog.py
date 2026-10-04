#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы системы стейтов в режиме текстового диалога.
"""

import asyncio
import logging
import os
import sys
import threading
import time
import concurrent.futures
import re
import queue
from pathlib import Path
from datetime import datetime

# Попытка импорта библиотек звука
try:
    import torch
    import numpy as np
    import soundfile as sf
    import sounddevice as sd
except ImportError as e:
    print(f"⚠️ Ошибка импорта библиотек звука: {e}")

# Добавляем путь к проектам
sys.path.insert(0, str(Path(__file__).parent / "Asterisk" / "media_sockets"))

from src.conversation_state import ConversationStateMachine, ConversationState, ClientNeed
from src.name_extractor import NameExtractor
from src.date_parser import DateParser
from src.database_manager import DatabaseManager
from src.leads_manager import LeadsManager
from src.rag_system_states import RAGSystemStates, WAV_FILE_TEXTS

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Глобальные списки синонимов
POSITIVE_WORDS = ["да", "верно", "правильно", "ага", "конечно", "точно", "именно", "подтверждаю", "согласен"]
NEGATIVE_WORDS = ["нет", "неверно", "ошиблись", "не то", "другой", "неправильно", "никак", "отнюдь", "ничуть"]

def num_to_text(text):
    """Преобразует цифры, марки и госномера для Silero TTS."""
    if not isinstance(text, str): text = str(text)
    text = text.replace("nan", "").replace("NaN", "")
    
    # 1. Расширение госномеров
    def expand_plate(m):
        plate = m.group(0)
        alphabet = {'А': 'А ', 'В': 'Вэ ', 'Е': 'Е ', 'К': 'Ка ', 'М': 'Эм ', 'Н': 'Эн ', 'О': 'О ', 'Р': 'Эр ', 'С': 'Эс ', 'Т': 'Тэ ', 'У': 'У ', 'Х': 'Ха '}
        digits = {'0': 'ноль ', '1': 'один ', '2': 'два ', '3': 'три ', '4': 'четыре ', '5': 'пять ', '6': 'шесть ', '7': 'семь ', '8': 'восемь ', '9': 'девять '}
        return "".join([alphabet.get(c.upper(), digits.get(c, c + " ")) for c in plate])
    
    text = re.sub(r'[А-ЯЁA-Z]\d{3}[А-ЯЁA-Z]{2}\d{2,3}', expand_plate, text)

    # 2. Словарь ударений (ВОССТАНОВЛЕН ПОЛНЫЙ СПИСОК)
    accents = {
        "NISSAN": "Нисс+ан", "PATHFINDER": "Патф+айндер", "QASHQAI": "Кашк+ай", "X-TRAIL": "Икстр+ейл", "TERRANO": "Терр+ано",
        "CHERY": "Ч+ери", "TIGGO": "Т+игго", "DASHING": "Д+эшинг",
        "TOYOTA": "Той+ота", "CAMRY": "К+эмри", "COROLLA": "Кор+олла", "RAV4": "Рав чет+ыре", "LAND CRUISER": "Ленд Круз+ер",
        "HYUNDAI": "Хёнд+ай", "SOLARIS": "Сол+ярис", "CRETA": "Кр+ета", "TUCSON": "Тусс+ан", "SANTA FE": "Санта Ф+е",
        "PEUGEOT": "Пеж+о", "CITROEN": "Ситро+ен", "RENAULT": "Рен+о", "LOGAN": "Лог+ан", "DUSTER": "Д+астер",
        "KIA": "К+иа", "RIO": "Р+ио", "SPORTAGE": "Спорт+ейдж", "SKODA": "Шк+ода", "OCTAVIA": "Окт+авия",
        "AUDI": "Ауди", "Q7": "Ку семь", "Q5": "Ку пять", "BMW": "Бээмв+э", "LADA": "Л+ада",
        "Викинги": "В+икинги", "автосалон": "автосал+он",
        "поняла": "понял+а", "записала": "запис+ала", "перевожу": "перевож+у"
    }

    for word, replacement in accents.items():
        text = re.sub(rf"\b{word}\b", replacement, text, flags=re.IGNORECASE)

    # 3. Даты
    text = text.replace("20 января", "двадц+атого январ+я").replace("26 января", "двадцать шест+ого январ+я").replace("25 января", "двадцать п+ятого январ+я")
    text = text.replace("10:00", "д+есять н+оль н+оль")
    
    # Улучшение интонации вопроса
    text = text.replace("Вас запис+ать?", "Вас... запис+ать??")
    text = text.replace("Всё верно?", "Всё... в+ерно??")
    
    return re.sub(r'\s+', ' ', text).strip()

class MockAudioHandler:
    def __init__(self, enable_tts: bool = True):
        self.tts_model = None
        self.tts_initialized = False
        self.enable_tts = enable_tts
        self.sample_rate = 48000
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self._stop_event = threading.Event()
        # Запускаем единый поток-воркер для аудио
        self.worker_thread = threading.Thread(target=self._audio_worker, daemon=True)
        self.worker_thread.start()
    
    async def _init_tts_async(self):
        if self.tts_initialized: return
        print("⏳ Загрузка модели голоса xenia...")
        def load_model():
            from torch import package
            d_dir = Path.home() / "Загрузки"
            paths = [d_dir / "SileroTTS-model-v5_1_ru" / "data" / "v5_1_ru.pt", d_dir / "v5_ru.pt"]
            for p in paths:
                if p.exists(): return package.PackageImporter(str(p)).load_pickle("tts_models", "model")
            raise RuntimeError("Модель TTS не найдена!")
        loop = asyncio.get_event_loop()
        self.tts_model = await loop.run_in_executor(None, load_model)
        self.tts_initialized = True
        print("✅ Голос готов!")
    
    def _audio_worker(self):
        """Единый поток для воспроизведения, чтобы избежать конфликтов ALSA."""
        while not self._stop_event.is_set():
            try:
                item = self.audio_queue.get(timeout=0.1)
                self.is_playing = True
                data, sr = item
                try:
                    if data.dtype != np.float32: data = data.astype(np.float32)
                    if len(data.shape) == 1: data = data.reshape(-1, 1)
                    with sd.OutputStream(samplerate=sr, channels=data.shape[1], dtype='float32') as stream:
                        stream.write(data)
                except Exception as e: logger.error(f"Ошибка в OutputStream: {e}")
                finally:
                    self.is_playing = False
                    self.audio_queue.task_done()
            except queue.Empty: continue

    async def speak_text(self, text: str):
        if not self.enable_tts or not self.tts_initialized: return
        text_s = num_to_text(text)
        try:
            if hasattr(self.tts_model, 'apply_tts'):
                audio = self.tts_model.apply_tts(text=text_s, speaker='xenia', sample_rate=self.sample_rate)
            else: audio = self.tts_model(text_s, speaker='xenia')
            print(f"🔊 (Голос): {text}")
            self.audio_queue.put((audio.cpu().numpy(), self.sample_rate))
        except Exception as e: logger.error(f"Ошибка TTS: {e}")

    async def play_wav_file(self, wav_path: str):
        wav_name = Path(wav_path).name
        text = WAV_FILE_TEXTS.get(wav_name, f"[WAV: {wav_name}]")
        if not Path(wav_path).exists():
            await self.speak_text(text)
            return
        try:
            audio_data, fs = sf.read(wav_path)
            print(f"🔊 БОТ [WAV]: {text}")
            self.audio_queue.put((audio_data, fs))
        except Exception as e: logger.error(f"Ошибка WAV: {e}")

    async def wait_until_finished(self):
        while self.is_playing or not self.audio_queue.empty():
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)

class DialogTester:
    def __init__(self):
        self.session_uuid = "test-session-001"
        self.state_machine = ConversationStateMachine(session_uuid=self.session_uuid)
        self.name_extractor = NameExtractor()
        self.date_parser = DateParser()
        self.audio_handler = MockAudioHandler()
        self.db_manager = DatabaseManager(Path("clientsbase.xlsx"), Path("norma.xlsx"), Path("slot.xlsx"))
        self.leads_manager = LeadsManager(Path("test_leads.xlsx"))
        self.rag_system = RAGSystemStates(Path("Asterisk/media_sockets/audio_responses"), 0.85)
        self.silence_count = 0
        self.last_bot_phrase = ("01_greeting.wav", None)
        self.pending_need = None 
        self.car_confirm_attempts = 0 
        
        print("=" * 70)
        print(" ТЕСТОВЫЙ РЕЖИМ ДИАЛОГА (ВЕРСИЯ 2.1: СТАБИЛЬНЫЙ ЗВУК) ")
        print("=" * 70)

    async def process_client_message(self, text: str) -> None:
        if not text: return
        print(f"\n👤 КЛИЕНТ: {text}")
        self.silence_count = 0 
        await self._process_with_state_machine(text)
    
    async def _process_with_state_machine(self, text: str) -> None:
        state = self.state_machine.state
        if state == ConversationState.INITIAL: await self._handle_initial_state(text)
        elif state == ConversationState.ASKING_NAME: await self._handle_asking_name_state(text)
        elif state == ConversationState.NEED_IDENTIFIED: await self._handle_need_identified_state(text)
        elif state == ConversationState.SERVICE_DATA_COLLECTION: await self._handle_service_data_collection_state(text)
        elif state == ConversationState.SERVICE_SLOT_SELECTION: await self._handle_service_slot_selection_state(text)
        else: await self._handle_default_state(text)
    
    async def _handle_initial_state(self, text: str) -> None:
        name = None
        if not self.state_machine.client_name: name = self.name_extractor.extract_name(text)
        need = self._identify_need(text)
        if name:
            self.state_machine.set_name(name)
            if need: self.pending_need = need 
            await self._play_wav_or_text(None, f"Я правильно поняла, Вас зовут {name}?")
            self.state_machine.transition_to(ConversationState.ASKING_NAME)
            return
        if need:
            self.state_machine.set_identified_need(need)
            self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
            await self._confirm_need(need)
        else:
            self.state_machine.increment_need_attempt()
            if self.state_machine.should_transfer_to_consultant_need():
                await self._play_wav_or_text("03_transfer_consultant.wav", None)
                self.state_machine.transition_to(ConversationState.TRANSFERRING)
            else: await self._play_wav_or_text("02_ask_name.wav", None)
    
    async def _handle_asking_name_state(self, text: str) -> None:
        t = text.lower()
        if self.state_machine.client_name and any(w in t for w in POSITIVE_WORDS):
            final_need = self.pending_need or self._identify_need(text)
            self.pending_need = None 
            if final_need:
                self.state_machine.set_identified_need(final_need)
                self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                await self._confirm_need(final_need)
            else:
                await self._play_wav_or_text(None, f"Очень приятно, {self.state_machine.client_name}. Чем я могу Вам помочь?")
                self.state_machine.state = ConversationState.INITIAL
            return
        if any(w in t for w in NEGATIVE_WORDS): self.state_machine.client_name = None 
        name = self.name_extractor.extract_name(text)
        if name:
            self.state_machine.set_name(name)
            await self._play_wav_or_text(None, f"Я правильно поняла, Вас зовут {name}?")
        else:
            self.state_machine.increment_name_attempt()
            if self.state_machine.should_transfer_to_consultant_name():
                await self._play_wav_or_text("03_transfer_consultant.wav", None)
                self.state_machine.transition_to(ConversationState.TRANSFERRING)
            else: await self._play_wav_or_text("02_ask_name.wav", None)

    async def _handle_need_identified_state(self, text: str) -> None:
        t = text.lower()
        if any(w in t for w in NEGATIVE_WORDS):
            self.state_machine.increment_need_attempt()
            if self.state_machine.should_transfer_to_consultant_need():
                await self._play_wav_or_text("03_transfer_consultant.wav", None)
                self.state_machine.transition_to(ConversationState.TRANSFERRING)
                return
            self.state_machine.state = ConversationState.INITIAL
            await self._play_wav_or_text(None, "Извините, я Вас не совсем поняла. Попробуйте еще раз сказать, что Вас интересует?")
        elif any(w in t for w in POSITIVE_WORDS) or self._identify_need(text):
            self.state_machine.confirm_need()
            await self._handle_confirmed_need()
        else: await self._play_wav_or_text(None, "Я Вас не совсем поняла. Скажите, пожалуйста, «да» или «нет».")

    async def _handle_confirmed_need(self) -> None:
        need = self.state_machine.identified_need
        if not need: return
        if need == ClientNeed.SERVICE:
            await self._play_wav_or_text("14_ask_client_data.wav", None)
            self.state_machine.transition_to(ConversationState.SERVICE_DATA_COLLECTION)
        elif need == ClientNeed.USED_CARS:
            await self._play_wav_or_text("04_transfer_used_cars.wav", None)
            self.state_machine.transition_to(ConversationState.TRANSFERRING)
        else:
            await self._play_wav_or_text("03_transfer_consultant.wav", None)
            self.state_machine.transition_to(ConversationState.TRANSFERRING)

    async def _handle_service_data_collection_state(self, text: str) -> None:
        sd = self.state_machine.service_data
        t_low = text.lower()
        new_fio_dict = self.name_extractor.extract_fio(text)
        new_phone = self.name_extractor.extract_phone(text)
        new_date = self.date_parser.parse_date(text) if self.date_parser else None
        mileage_match = re.search(r"(\d+(?:\s+\d{3})*)", text)
        works_found = any(w in t_low for w in ["то", "ремонт", "замена", "обслуживание", "техобслуживание"])

        if new_fio_dict: sd["fio"] = f"{new_fio_dict['surname']} {new_fio_dict['name']} {new_fio_dict['patronymic']}".strip()
        if new_phone: sd["phone"] = new_phone
        if new_date: sd["desired_date"] = new_date.strftime("%Y-%m-%d")
        if mileage_match: 
            m = mileage_match.group(1).replace(" ", "")
            if len(m) >= 3: sd["mileage"] = m
        if works_found: sd["work_list"] = text

        if sd.get("fio") and sd.get("phone") and not sd.get("car_confirmed"):
            if not sd.get("client_found_in_db"):
                client = await self.db_manager.find_client_async(fio=sd["fio"], phone=sd["phone"])
                if client:
                    car_raw = str(client.get("Автомобиль", "")).replace("nan", "")
                    words = car_raw.split()
                    unique_words = []
                    for w in words:
                        if w.lower() not in [u.lower() for u in unique_words] and w.lower() not in ["vin", "№"]: unique_words.append(w)
                        elif w.lower() == "vin": break
                    car_info = " ".join(unique_words)
                    sd.update({"client_found_in_db": True, "car_model": car_info})
                    await self._play_wav_or_text(None, f"{self.state_machine.client_name}, у Вас автомобиль {car_info}?")
                    return
                else:
                    sd["client_found_in_db"] = False
                    sd["car_confirmed"] = True 

            if any(w in t_low for w in ["какой", "повтори", "как назвала"]):
                await self._play_wav_or_text(None, f"Я назвала автомобиль {sd.get('car_model')}. Всё верно?")
                return
            if any(w in t_low for w in POSITIVE_WORDS): sd["car_confirmed"] = True
            elif any(w in t_low for w in NEGATIVE_WORDS):
                sd["car_confirmed"] = False
                sd["client_found_in_db"] = False
                await self._play_wav_or_text("08_ask_car_service_data.wav", None)
                return
            else:
                self.car_confirm_attempts += 1
                if self.car_confirm_attempts >= 2:
                    await self._play_wav_or_text("03_transfer_consultant.wav", None)
                    self.state_machine.transition_to(ConversationState.TRANSFERRING)
                else: await self._play_wav_or_text(None, "Уточните, пожалуйста, я правильно назвала Ваш автомобиль?")
                return

        if not sd.get("fio") or not sd.get("phone"):
            if not sd.get("fio"): await self._play_wav_or_text(None, "Назовите, пожалуйста, Ваши фамилию, имя и отчество полностью.")
            else: await self._play_wav_or_text(None, "Назовите Ваш номер телефона для связи.")
            return

        missing = []
        if not sd.get("desired_date"): missing.append("желаемую дату обслуживания")
        if not sd.get("mileage"): missing.append("текущий пробег")
        if not sd.get("work_list"): missing.append("список необходимых работ")

        if not missing: await self._process_service_booking()
        else: await self._play_wav_or_text(None, "Уточните, пожалуйста, " + " и ".join(missing))

    async def _handle_service_slot_selection_state(self, text: str) -> None:
        if any(w in text.lower() for w in POSITIVE_WORDS):
            sd_data = self.state_machine.service_data
            success = self.db_manager.add_booking(
                date=sd_data.get("desired_date"), time="10:00", fio=sd_data.get("fio"),
                car=sd_data.get('car_model', ""), work_list=sd_data.get("work_list", "")
            )
            if success:
                await self._play_wav_or_text(None, f"{self.state_machine.client_name}, я Вас записала. Ждем Вас!")
                self.state_machine.transition_to(ConversationState.SERVICE_BOOKED)
            else: await self._play_wav_or_text(None, "Извините, возникла ошибка. Перевожу на специалиста.")
        else: await self._play_wav_or_text(None, "Давайте выберем другое время.")

    async def _handle_default_state(self, text: str) -> None:
        if self.rag_system:
            res = self.rag_system.find_best_response(text)
            if res: await self.audio_handler.play_wav_file(res[0])
            else: await self._play_wav_or_text(None, "Минутку, я Вас соединяю со специалистом.")

    def _identify_need(self, text: str) -> ClientNeed | None:
        t = text.lower()
        if re.search(r"\bто\b", t) or any(w in t for w in ["сервис", "ремонт", "обслуживание", "техобслуживание"]): return ClientNeed.SERVICE
        if any(w in t for w in ["запчаст", "детал"]): return ClientNeed.PARTS
        if any(w in t for w in ["продать", "выкуп", "купите мой"]): return ClientNeed.USED_CARS
        return None

    async def _confirm_need(self, need: ClientNeed) -> None:
        name = self.state_machine.client_name or "клиент"
        mapping = {ClientNeed.SERVICE: "запись на сервис", ClientNeed.USED_CARS: "отдел автомобилей с пробегом"}
        n_text = mapping.get(need, "консультация")
        await self._play_wav_or_text(None, f"{name}, я правильно поняла, Вас интересует {n_text}?")

    async def _play_wav_or_text(self, wav_file: str | None, text: str | None) -> None:
        self.last_bot_phrase = (wav_file, text)
        if wav_file:
            path = Path("Asterisk/media_sockets/audio_responses") / wav_file
            if path.exists():
                await self.audio_handler.play_wav_file(str(path))
                return
            text = WAV_FILE_TEXTS.get(wav_file, text)
        if text: await self.audio_handler.speak_text(text)

    async def _process_service_booking(self) -> None:
        sd = self.state_machine.service_data
        date_str = sd.get("desired_date", "2026-01-20")
        work_list = sd.get("work_list", "")
        
        # Считаем реальные часы
        hours = self.db_manager.calculate_work_hours(work_list)
        logger.info(f"Трудоемкость: {hours} ч.")
        
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        months = ["", "январ+я", "феврал+я", "м+арта", "апр+еля", "м+ая", "и+юня", "июл+я", "августа", "сентябр+я", "октябр+я", "ноябр+я", "декабр+я"]
        days_txt = {20: "двадц+атого", 26: "двадцать шест+ого", 25: "двадцать п+ятого", 24: "двадцать четв+ертого"}
        day_txt = days_txt.get(dt.day, str(dt.day))
        
        await self._play_wav_or_text(None, f"{self.state_machine.client_name}, на {day_txt} {months[dt.month]} в д+есять н+оль н+оль есть свободное время. Вас, запис+ать?")
        self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)

async def input_with_timeout(timeout: float):
    import select
    loop = asyncio.get_event_loop()
    def _read():
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.readline() if ready else None
    return await loop.run_in_executor(None, _read)

async def main():
    tester = DialogTester()
    await tester.audio_handler._init_tts_async()
    await tester._play_wav_or_text("01_greeting.wav", None)
    while True:
        try:
            await tester.audio_handler.wait_until_finished()
            u_input = await input_with_timeout(30.0)
            if u_input is None:
                tester.silence_count += 1
                if tester.silence_count == 1:
                    print("\n⏳ (Таймаут 30с: клиент молчит, переспрашиваем)")
                    wav, text = tester.last_bot_phrase
                    await tester._play_wav_or_text(wav, text)
                else:
                    await tester.audio_handler.speak_text("Прошу прощения, я Вас не слышу. До свидания!")
                    await tester.audio_handler.wait_until_finished()
                    break
                continue
            u_input = u_input.strip()
            if not u_input: continue
            if u_input.lower() in ["quit", "exit"]: break
            await tester.process_client_message(u_input)
        except (KeyboardInterrupt, EOFError): break

if __name__ == "__main__":
    asyncio.run(main())
