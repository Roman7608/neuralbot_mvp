"""
Voice Bot Server — точка входа.

Запускает:
1. FastAPI — health-чеки и метрики на порту 8003
2. AudioSocket TCP на порту 9092 — единый сценарий для всех входящих DID (см. docs/VOICE_BOT_UNIFIED_SCENARIO.txt)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI

# Добавляем корень проекта в sys.path для импорта dialog/, config.py и т.д.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.voice.audiosocket import read_frame, FrameType, parse_uuid, hex32_to_uuid_display, build_hangup_frame
from services.voice.session import CallSession
from services.voice.manager import SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("voice-bot")

from licensing.checker import verify_license, start_periodic_check, license_info
verify_license("voice_bot")
start_periodic_check("voice_bot")

from services.voice.voice_phrases import get_voice_scenario

logger.info(
    "VOICE_SCENARIO=%s (legacy — резерв; v2 — короткое приветствие без имени, см. voice_phrases)",
    get_voice_scenario(),
)

AUDIOSOCKET_HOST = os.getenv("AUDIOSOCKET_HOST", "0.0.0.0")
AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "9092"))
STT_SERVICE_URL = os.getenv("STT_SERVICE_URL", "http://stt-service:8001")
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://tts-service:8002")
MAX_CONCURRENT_CALLS = int(os.getenv("MAX_CONCURRENT_CALLS", "5"))

AUDIO_RESPONSES_DIR = Path(os.getenv("AUDIO_RESPONSES_DIR", str(PROJECT_ROOT / "audio_responses")))
CLIENTS_BASE_PATH = Path(os.getenv("CLIENTS_BASE_PATH", str(PROJECT_ROOT / "clientsbase.xlsx")))
NORMA_PATH = Path(os.getenv("NORMA_PATH", str(PROJECT_ROOT / "norma.xlsx")))
SLOT_PATH = Path(os.getenv("SLOT_PATH", str(PROJECT_ROOT / "slot.xlsx")))
LEADS_PATH = Path(os.getenv("LEADS_PATH", str(PROJECT_ROOT / "test_leads.xlsx")))

session_manager = SessionManager(max_concurrent=MAX_CONCURRENT_CALLS)
_wav_cache: dict[str, tuple[np.ndarray, int]] = {}
_tcp_servers: list[asyncio.AbstractServer] = []


def _preload_wav_cache() -> None:
    """Load all WAV files from audio_responses into memory."""
    import soundfile as sf
    _wav_cache.clear()
    if not AUDIO_RESPONSES_DIR.exists():
        logger.warning("audio_responses dir not found: %s", AUDIO_RESPONSES_DIR)
        return
    for path in sorted(AUDIO_RESPONSES_DIR.glob("*.wav")):
        try:
            data, fs = sf.read(path, dtype="float32")
            _wav_cache[path.name] = (data.astype(np.float32), int(fs))
        except Exception as e:
            logger.warning("Cannot load %s: %s", path.name, e)
    logger.info("WAV cache loaded: %d files", len(_wav_cache))


# ------------------------------------------------------------------
# AudioSocket TCP server
# ------------------------------------------------------------------

def _make_audiosocket_handler(greeting_type: str):
    """Возвращает обработчик с привязкой greeting_type."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("AudioSocket connection from %s (greeting_type=%s)", peer, greeting_type)

        first_frame = await read_frame(reader)
        if first_frame is None or first_frame.type != FrameType.UUID:
            logger.warning("Expected UUID frame, got %s from %s", first_frame, peer)
            writer.close()
            return

        ast_db_key = parse_uuid(first_frame)
        call_uuid = hex32_to_uuid_display(ast_db_key)
        logger.info("Call UUID (display=%s, ast_db_key=%s) from %s", call_uuid, ast_db_key, peer)

        if not session_manager.can_accept():
            logger.warning("Rejecting call %s: at capacity", call_uuid)
            writer.write(build_hangup_frame())
            await writer.drain()
            writer.close()
            return

        session = CallSession(
            call_uuid=call_uuid,
            asterisk_astdb_key=ast_db_key,
            reader=reader,
            writer=writer,
            stt_url=STT_SERVICE_URL,
            tts_url=TTS_SERVICE_URL,
            wav_cache=_wav_cache,
            audio_responses_dir=AUDIO_RESPONSES_DIR,
            clients_base_path=CLIENTS_BASE_PATH,
            norma_path=NORMA_PATH,
            slot_path=SLOT_PATH,
            leads_path=LEADS_PATH,
            greeting_type=greeting_type,
        )

        await session_manager.start_session(call_uuid, session.run())

    return handler


async def _start_tcp_servers() -> list[asyncio.AbstractServer]:
    """Один TCP AudioSocket — единый сценарий (greeting_type=chery_tenet для совместимости с WAV/аналитикой)."""
    servers = []
    handler = _make_audiosocket_handler("chery_tenet")
    server = await asyncio.start_server(handler, AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
    servers.append(server)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    logger.info("AudioSocket TCP server listening on %s (unified scenario)", addrs)
    return servers


# ------------------------------------------------------------------
# FastAPI app (health + metrics)
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tcp_servers
    _preload_wav_cache()
    _tcp_servers = await _start_tcp_servers()
    logger.info("Voice bot ready: max %d concurrent calls", MAX_CONCURRENT_CALLS)
    yield
    logger.info("Shutting down voice bot...")
    await session_manager.shutdown()
    for srv in _tcp_servers:
        srv.close()
        await srv.wait_closed()


app = FastAPI(title="Vikingi Voice Bot", lifespan=lifespan)


@app.get("/health")
def health():
    stats = session_manager.get_stats()
    return {
        "status": "ok",
        "wav_cache_size": len(_wav_cache),
        **stats,
    }
