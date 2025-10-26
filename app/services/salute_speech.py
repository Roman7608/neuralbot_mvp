import os
import uuid
import base64
import logging
from typing import Optional, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class SaluteSpeech:
    """SaluteSpeech (Сбер) STT/TTS client.

    MVP: реализуем TTS (wav, 8kHz) для использования с Asterisk.
    STT добавим после проверки TTS.
    """

    def __init__(self) -> None:
        self.client_id = os.getenv("SALUTE_CLIENT_ID")
        self.auth_key = os.getenv("SALUTE_AUTH_KEY")
        self.scope = os.getenv("SALUTE_SCOPE", "SALUTE_SPEECH_PERS")
        self.access_token: Optional[str] = None

        if not self.client_id or not self.auth_key:
            logger.warning("SaluteSpeech креды не настроены (SALUTE_CLIENT_ID/SALUTE_AUTH_KEY)")

    async def _get_access_token(self) -> Optional[str]:
        if self.access_token:
            return self.access_token

        url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

        # Поддерживаем два формата SALUTE_AUTH_KEY:
        # 1) Уже base64(client_id:secret)
        # 2) Сырой secret (GUID) — тогда кодируем client_id:secret
        auth_header: str
        try:
            decoded = base64.b64decode(self.auth_key).decode(errors="ignore")
            if ":" in decoded:
                # Ключ уже в формате base64(client_id:secret)
                auth_header = self.auth_key
            else:
                raise ValueError("Not a pre-encoded basic token")
        except Exception:
            # Обычный случай: формируем client_id:secret и кодируем
            auth_string = f"{self.client_id}:{self.auth_key}"
            auth_header = base64.b64encode(auth_string.encode()).decode()

        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
        }

        data = {"scope": self.scope}

        try:
            # NOTE: у NGW может быть кастомное CA, в некоторых окружениях требуется verify=False
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                resp = await client.post(url, headers=headers, data=data)
                if resp.status_code != 200:
                    logger.error(f"SaluteSpeech OAuth error {resp.status_code}: {resp.text}")
                    return None
                token_data = resp.json()
                self.access_token = token_data.get("access_token")
                if not self.access_token:
                    logger.error("SaluteSpeech OAuth: access_token отсутствует в ответе")
                    return None
                logger.info("SaluteSpeech access token получен")
                return self.access_token
        except Exception as e:
            logger.error(f"Ошибка получения access token SaluteSpeech: {e}")
            return None

    async def tts_synthesize(self, text: str, voice: str = "Bys_8000", sample_rate_hz: int = 8000) -> Optional[bytes]:
        """Синтез речи (wav, 8kHz) для Asterisk.

        voice: популярные варианты Bys, Aleksei, Elena и др. Зависит от тарифа.
        """
        if not text or not self.client_id or not self.auth_key:
            logger.error("SaluteSpeech TTS: отсутствуют текст или креды")
            return None

        token = await self._get_access_token()
        if not token:
            return None

        url = "https://smartspeech.sber.ru/rest/v1/text:synthesize"

        # Нормализуем имя голоса под допустимые значения API
        # Если голос без суффикса, добавим _8000 или _24000 в зависимости от sample_rate_hz
        normalized_voice = voice
        if not (voice.endswith("_8000") or voice.endswith("_24000")):
            normalized_voice = f"{voice}_{'8000' if sample_rate_hz <= 8000 else '24000'}"

        # Базовые заголовки и параметры для WAV (совместимо с нашим эндпоинтом)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "audio/wav",
            "Content-Type": "application/text",
            "RqUID": str(uuid.uuid4()),
        }

        params = {
            "voice": normalized_voice,
            "format": "wav16",
            "sample_rate": str(sample_rate_hz),
        }

        def pcm16_to_wav(pcm_data: bytes, sample_rate: int, num_channels: int = 1) -> bytes:
            data_size = len(pcm_data)
            byte_rate = sample_rate * num_channels * 2  # 16-bit = 2 bytes per sample
            block_align = num_channels * 2
            # WAV RIFF header (44 bytes)
            header = bytearray()
            header.extend(b"RIFF")
            header.extend((36 + data_size).to_bytes(4, byteorder="little", signed=False))
            header.extend(b"WAVE")
            header.extend(b"fmt ")
            header.extend((16).to_bytes(4, byteorder="little", signed=False))  # Subchunk1Size for PCM
            header.extend((1).to_bytes(2, byteorder="little", signed=False))   # AudioFormat PCM = 1
            header.extend((num_channels).to_bytes(2, byteorder="little", signed=False))
            header.extend((sample_rate).to_bytes(4, byteorder="little", signed=False))
            header.extend((byte_rate).to_bytes(4, byteorder="little", signed=False))
            header.extend((block_align).to_bytes(2, byteorder="little", signed=False))
            header.extend((16).to_bytes(2, byteorder="little", signed=False))  # BitsPerSample
            header.extend(b"data")
            header.extend((data_size).to_bytes(4, byteorder="little", signed=False))
            return bytes(header) + pcm_data

        async def do_request(curr_token: str) -> httpx.Response:
            local_headers = dict(headers)
            local_headers["Authorization"] = f"Bearer {curr_token}"
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                return await client.post(
                    url,
                    headers=local_headers,
                    params=params,
                    content=text.encode("utf-8"),
                )

        try:
            # Первая попытка
            resp = await do_request(token)
            if resp.status_code == 401:
                # Токен мог протухнуть — обновим и повторим 1 раз
                logger.warning("SaluteSpeech TTS: 401 Unauthorized, пробую обновить токен и повторить")
                self.access_token = None
                token2 = await self._get_access_token()
                if not token2:
                    return None
                resp = await do_request(token2)

            if resp.status_code != 200:
                # Если формат не принят, попробуем fallback на pcm16
                txt = resp.text
                logger.error(f"SaluteSpeech TTS error {resp.status_code}: {txt}")
                if resp.status_code == 400 and ("invalid format" in txt or "format" in txt):
                    logger.info("SaluteSpeech TTS: пробую fallback format=pcm16 + octet-stream")
                    headers_pcm = dict(headers)
                    headers_pcm["Accept"] = "application/octet-stream"
                    params_pcm = dict(params)
                    params_pcm["format"] = "pcm16"
                    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                        resp2 = await client.post(
                            url,
                            headers=headers_pcm,
                            params=params_pcm,
                            content=text.encode("utf-8"),
                        )
                    if resp2.status_code == 200:
                        # Оборачиваем PCM16 в WAV (mono, sample_rate_hz)
                        try:
                            return pcm16_to_wav(resp2.content, sample_rate_hz, 1)
                        except Exception as conv_err:
                            logger.error(f"SaluteSpeech TTS: ошибка обёртки PCM->WAV: {conv_err}")
                            return resp2.content
                    logger.error(f"SaluteSpeech TTS fallback error {resp2.status_code}: {resp2.text}")
                return None

            return resp.content
        except Exception as e:
            logger.error(f"SaluteSpeech TTS исключение: {e}")
            return None

    # ======== STT (Speech-to-Text) ========
    async def stt_recognize_pcm16(self, audio_bytes: bytes, sample_rate_hz: int = 8000, language: str = "ru-RU") -> Optional[str]:
        """Распознавание речи из PCM16/WAV16 байтов. Возвращает распознанный текст или None."""
        if not audio_bytes or not self.client_id or not self.auth_key:
            logger.error("SaluteSpeech STT: отсутствуют данные или креды")
            return None
        token = await self._get_access_token()
        if not token:
            return None
        url = "https://smartspeech.sber.ru/rest/v1/speech:recognize"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": f"audio/x-pcm;bit=16;rate={sample_rate_hz}",
            "RqUID": str(uuid.uuid4()),
        }
        params = {
            "format": "pcm16",
            "sample_rate": str(sample_rate_hz),
            "language": language,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.post(url, headers=headers, params=params, content=audio_bytes)
                if resp.status_code != 200:
                    logger.error(f"SaluteSpeech STT error {resp.status_code}: {resp.text}")
                    return None
                data = resp.json()
                logger.info(f"SaluteSpeech STT response data type: {type(data)}, content: {data}")
                # Ожидаемый формат: {"result": [{"alternatives": [{"text": "..."}]}]}
                if isinstance(data, dict):
                    result = data.get("result") or []
                    if result and isinstance(result, list) and len(result) > 0:
                        alts = result[0].get("alternatives") or []
                        if alts and isinstance(alts, list) and len(alts) > 0:
                            return alts[0].get("text") or None
                return None
        except Exception as e:
            logger.error(f"SaluteSpeech STT исключение: {e}")
            return None


salute_speech = SaluteSpeech()
