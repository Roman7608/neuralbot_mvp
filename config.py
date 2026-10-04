import os
import pathlib

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Включать ли локальную LLM (через RAG/интенты сначала, потом LLM при необходимости)
# Можно управлять через переменную окружения VIKINGI_USE_LLM=true/false
_use_llm_env = os.getenv("VIKINGI_USE_LLM", "").lower()
USE_LLM: bool = _use_llm_env in ("1", "true", "yes", "y", "on")

# Включать ли RAG с предзаписанными WAV-ответами
USE_RAG: bool = True

# Путь к папке с предзаписанными аудиоответами
AUDIO_RESPONSES_DIR: pathlib.Path = pathlib.Path("audio_responses")

# Пути к Excel-файлам
CLIENTS_BASE_PATH: pathlib.Path = pathlib.Path("clientsbase.xlsx")
NORMA_PATH: pathlib.Path = pathlib.Path("norma.xlsx")
SLOT_PATH: pathlib.Path = pathlib.Path("slot.xlsx")
LEADS_PATH: pathlib.Path = pathlib.Path("test_leads.xlsx")

# Сводная таблица ТО Chery/Tenet (server2 → /mnt/sto_to_table на server7)
STO_TO_TABLE_PATH: pathlib.Path = pathlib.Path(
    os.getenv(
        "STO_TO_TABLE_PATH",
        "/mnt/sto_to_table/_СТО/! ТО Сводная таблица.xlsx",
    )
)
STO_TO_TABLE_MOUNT_DIR: pathlib.Path = pathlib.Path(
    os.getenv("STO_TO_TABLE_MOUNT_DIR", "/mnt/sto_to_table")
)

# Резервный STT для демо/скриптов (пути Hugging Face или локальные каталоги)
WHISPER_MODEL_PATH_VOICE: str = "Systran/faster-whisper-medium"
WHISPER_MODEL_PATH: str = str(
    pathlib.Path(__file__).resolve().parent / "models" / "whisper" / "faster-whisper-large-v3"
)
# Устройство для резервного STT: "cuda" или "cpu"
WHISPER_DEVICE: str = "cuda"

# GigaAM-v3 STT — ai-sage/GigaAM-v3. Локально: Vikingi/models/gigaam-v3/
# Запустить download_gigaam.py один раз при наличии интернета, затем работает офлайн
GIGAAM_MODEL_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parent / "models" / "gigaam-v3"
# Ревизия модели: e2e_ctc (пунктуация+нормализация), e2e_rnnt, ctc, rnnt
GIGAAM_REVISION: str = "e2e_ctc"

# GigaAM для аналитики — e2e_ctc (та же папка, что и голосовой бот). Пауза прогрева: GIGAAM_WARMUP_SEC=1 в transcribe_audio_gigaam
# Прод: GIGAAM_COMBO_ENABLED=0, GIGAAM_SPLIT_ON_TRANSFER_TONES=0 — см. .env / docker-compose admin-panel (приветствие после гудков).
# Длинные записи (>25 с): чанки — GIGAAM_CHUNK_FIRST_SEC (8), GIGAAM_CHUNK_OVERLAP_SEC (2), GIGAAM_CHUNK_DEDUP_WORDS (12);
# задаются в env; run_local.sh и docker-compose admin-panel подставляют те же дефолты.
GIGAAM_ANALYTICS_MODEL_PATH: pathlib.Path = GIGAAM_MODEL_PATH
GIGAAM_ANALYTICS_REVISION: str = GIGAAM_REVISION

# LLM для нормализации и оценки (llama-cpp-python, .gguf). Переопределить через LLM_MODEL_PATH в .env
LLM_MODEL_PATH: str = os.getenv("LLM_MODEL_PATH", str(pathlib.Path.home() / "models" / "qwen" / "model-q4_K.gguf"))
# Использовать streaming STT (обработка в реальном времени)
USE_STREAMING_STT: bool = True

# Silero TTS v5 (путь к .pt или директории модели)
SILERO_V5_PATH: pathlib.Path = (
    pathlib.Path.home() / "Загрузки" / "SileroTTS-model-v5_1_ru" / "data" / "v5_1_ru.pt"
)
SILERO_SPEAKER: str = "xenia"
SILERO_SAMPLE_RATE: int = 48000
SILERO_SPEED: float = 1.1  # Скорость речи (1.0 = нормальная, 1.1 = на 10% быстрее)

# Хранилище аудиозаписей звонков — только на HDD server7 (/mnt/audio_calls)
# Используется: автозабор из SpRecord, ручная загрузка через админку
AUDIO_CALLS_ROOT: pathlib.Path = pathlib.Path(
    os.getenv("AUDIO_CALLS_ROOT", "/mnt/audio_calls/calls")
)

# Режим сигнала перевода после реплики бота:
# - sip (по умолчанию): обратный новый SIP INVITE инициирует Инфолада.
# - ami: исторический режим через Asterisk AMI Redirect (оставлен для обратной совместимости).
ASTERISK_TRANSFER_MODE: str = os.getenv("ASTERISK_TRANSFER_MODE", "sip").strip().lower()

# Разрешённые цели перевода (whitelist для Dial с бота на Инфоладу).
# 697070 админ/ОП Чери; 696157 трейд-ин; 697777 СТО; 695873 кузовной; 695874 запчасти.
# Вход на бота (DID, единый сценарий): 697070, 695875, 697777, 695870, 707755 — см. docs/VOICE_BOT_UNIFIED_SCENARIO.txt
# 773399 на бота не маршрутизируют (прямой вызов по схеме Инфолады/Заказчика).
# 695875 и 695870 — вход клиентов и исход менеджеров; в Dial с бота не подставляем.
INFOLADA_ALLOWED_TARGETS: str = os.getenv(
    "INFOLADA_ALLOWED_TARGETS",
    "697070,696157,697777,695873,695874",
)

# Маппинг WAV -> целевой 6-значный номер (по умолчанию можно переопределить env).
# Если не хотите дефолтные значения, задайте их явно в окружении.
ASTERISK_TRANSFER_TARGET_ADMIN: str = os.getenv("ASTERISK_TRANSFER_TARGET_ADMIN", "697070")
ASTERISK_TRANSFER_TARGET_USED_CARS: str = os.getenv("ASTERISK_TRANSFER_TARGET_USED_CARS", "696157")
ASTERISK_TRANSFER_TARGET_CHERY_TENET: str = os.getenv("ASTERISK_TRANSFER_TARGET_CHERY_TENET", "697070")
ASTERISK_TRANSFER_TARGET_SERVICE: str = os.getenv("ASTERISK_TRANSFER_TARGET_SERVICE", "697777")
ASTERISK_TRANSFER_TARGET_SERVICE_ASSISTANT: str = os.getenv("ASTERISK_TRANSFER_TARGET_SERVICE_ASSISTANT", "697777")
ASTERISK_TRANSFER_TARGET_BODY_REPAIR: str = os.getenv("ASTERISK_TRANSFER_TARGET_BODY_REPAIR", "695873")
ASTERISK_TRANSFER_TARGET_PARTS: str = os.getenv("ASTERISK_TRANSFER_TARGET_PARTS", "695874")
ASTERISK_TRANSFER_TARGET_DEFAULT: str = os.getenv("ASTERISK_TRANSFER_TARGET_DEFAULT", ASTERISK_TRANSFER_TARGET_ADMIN)

# Asterisk AMI — для локального управления переводами на server7
ASTERISK_AMI_HOST: str = os.getenv("ASTERISK_AMI_HOST", "asterisk")
ASTERISK_AMI_PORT: int = int(os.getenv("ASTERISK_AMI_PORT", "5038"))
ASTERISK_AMI_USER: str = os.getenv("ASTERISK_AMI_USER", "vikingi-bot")
ASTERISK_AMI_SECRET: str = os.getenv("ASTERISK_AMI_SECRET", "bot-internal-password")
ASTERISK_TRANSFER_CONTEXT: str = os.getenv("ASTERISK_TRANSFER_CONTEXT", "transfer-to-operator")
ASTERISK_TRANSFER_EXTEN: str = os.getenv("ASTERISK_TRANSFER_EXTEN", "100")
# Ассистент сервиса (слесарный цех) — отдельный номер 333
ASTERISK_TRANSFER_EXTEN_SERVICE: str = os.getenv("ASTERISK_TRANSFER_EXTEN_SERVICE", "333")

# Запись на ТО через голосового бота (14_service_choice, слоты, SERVICE_DATA_COLLECTION) — включено.
SERVICE_BOOKING_ENABLED: bool = True
# Прод-вход для всех обращений в сервис. При 0 сохраняется прежний режим:
# ветка записи открывается только по тестовому слову «Австралия».
VOICE_TO_BOOKING_PROD_ENABLED: bool = (
    os.getenv("VOICE_TO_BOOKING_PROD_ENABLED", "0").strip().lower()
    in ("1", "true", "yes", "on")
)

# Аудио-устройства (при необходимости можно указать имена/индексы)
# USB микрофон: устройство #8 "USB microphone: Audio (hw:4,0)"
# Для использования другого устройства укажите его индекс или None для устройства по умолчанию
INPUT_DEVICE: int | None = 8  # USB микрофон
OUTPUT_DEVICE: str | None = None

# VAD (Voice Activity Detection) параметры
USE_VAD: bool = True  # Использовать VAD для определения конца речи
VAD_SAMPLE_RATE: int = 16000  # Частота дискретизации для VAD
VAD_FRAME_SIZE: int = 512  # Размер фрейма для VAD (в сэмплах)
VAD_SILENCE_THRESHOLD_MS: int = 350  # Тишина в мс для остановки записи (меньше — быстрее ответ)
VAD_SPEECH_THRESHOLD: float = 0.3  # Порог вероятности речи (0.0-1.0)
# Максимальная длина одной записи (сек). Меньше — меньше пауза «Роман» → ответ бота
MAX_RECORD_DURATION_SEC: float = 3.0

# Классификация по транскрипту (call_analytics): по умолчанию главный путь v2, запасной — монолит legacy.
# VIKINGI_CLASSIFY_LEGACY=1 — использовать только прежний classify_by_transcript_legacy (откат).
_vikingi_classify_legacy = os.getenv("VIKINGI_CLASSIFY_LEGACY", "").strip().lower()
USE_LEGACY_CLASSIFY_TRANSCRIPT: bool = _vikingi_classify_legacy in ("1", "true", "yes", "y", "on")

# Оценка качества ОП (чек-лист 3–16):
# по умолчанию бинарная шкала 0/1 (основная).
# Откат на прежнюю мягкую шкалу 0..1 с шагом 0.25:
#   export VIKINGI_OP_EVAL_BINARY=0
_op_eval_binary = os.getenv("VIKINGI_OP_EVAL_BINARY", "1").strip().lower()
VIKINGI_OP_EVAL_BINARY: bool = _op_eval_binary not in ("0", "false", "no", "n", "off")

# call_analytics: ложные «Прочие» для ОП исх. (подстроки «говорили»⊂«договорились», «звонил»⊂«дозвонился»/«звонили»).
# Откат: export VIKINGI_CLASSIFY_REPEAT_FIX=0
_rf = os.getenv("VIKINGI_CLASSIFY_REPEAT_FIX", "1").strip().lower()
VIKINGI_CLASSIFY_REPEAT_FIX: bool = _rf not in ("0", "false", "no", "n", "off")
# Кузов в консультации менеджера ОП по новому авто не уводит в Прочие как «кузовной цех».
# Откат: export VIKINGI_CLASSIFY_KUZOV_OP_EXEMPT=0
_kz = os.getenv("VIKINGI_CLASSIFY_KUZOV_OP_EXEMPT", "1").strip().lower()
VIKINGI_CLASSIFY_KUZOV_OP_EXEMPT: bool = _kz not in ("0", "false", "no", "n", "off")

