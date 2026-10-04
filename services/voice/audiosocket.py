"""
Asterisk AudioSocket protocol — parser and writer.
See res_audiosocket.h (Asterisk):
  0x00 — HANGUP
  0x01 — UUID (first frame from Asterisk)
  0x03 — DTMF
  0x10 — AUDIO (slin 8 kHz, 16-bit LE)
  0xFF — ERROR

Frame format: [1 byte type][2 bytes length BE][payload]
"""

import asyncio
import logging
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)

HEADER_SIZE = 3


class FrameType(IntEnum):
    HANGUP = 0x00
    UUID = 0x01
    DTMF = 0x03
    AUDIO = 0x10  # slin 8 kHz
    ERROR = 0xFF


@dataclass
class AudioSocketFrame:
    type: FrameType
    payload: bytes


async def read_frame(reader: asyncio.StreamReader) -> Optional[AudioSocketFrame]:
    """Read one AudioSocket frame. Returns None on EOF / error."""
    try:
        header = await reader.readexactly(HEADER_SIZE)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None

    frame_type = header[0]
    payload_len = struct.unpack("!H", header[1:3])[0]

    if payload_len > 0:
        try:
            payload = await reader.readexactly(payload_len)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
    else:
        payload = b""

    try:
        ft = FrameType(frame_type)
    except ValueError:
        logger.warning("Unknown AudioSocket frame type: 0x%02x", frame_type)
        return AudioSocketFrame(type=frame_type, payload=payload)

    return AudioSocketFrame(type=ft, payload=payload)


def build_audio_frame(pcm_data: bytes) -> bytes:
    """Build an AUDIO frame to send back to Asterisk."""
    header = struct.pack("!BH", FrameType.AUDIO, len(pcm_data))
    return header + pcm_data


def build_hangup_frame() -> bytes:
    """Build HANGUP frame (0x00) to tell Asterisk to hang up."""
    return struct.pack("!BH", FrameType.HANGUP, 0)


def parse_uuid(frame: AudioSocketFrame) -> str:
    """
    Ключ для AstDB / AMI DBGet (должен совпадать с extensions.conf).

    Asterisk (res_audiosocket.c) шлёт 16 байт: uuid_parse(call_uuid_string) → memcpy(uu,16).
    Ключ в диалплане был ${call_uuid} со строкой вида 00000000-0000-4000-8000-...
    str(uuid.UUID(bytes=p)) в Python может НЕ совпасть с этой строкой → пустой DBGet.

    Общий ключ: 32 hex-символа без дефисов = FILTER(0-9a-fA-F,${call_uuid}) в диалплане = p.hex() здесь.
    """
    p = frame.payload
    if len(p) == 16:
        return p.hex()
    s = p.decode("ascii", errors="replace").strip("\x00")
    return "".join(c.lower() for c in s if c in "0123456789abcdefABCDEF")


def hex32_to_uuid_display(hex32: str) -> str:
    """Человекочитаемый UUID для логов и session_uuid (из 32 hex)."""
    import uuid as uuid_mod

    h = (hex32 or "").strip().lower()
    if len(h) != 32:
        return hex32
    try:
        return str(uuid_mod.UUID(hex=h))
    except ValueError:
        return hex32
