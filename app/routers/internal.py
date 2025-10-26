from fastapi import APIRouter, Request, HTTPException
import os
from pydantic import BaseModel
from typing import Optional, List, Dict
from ..services.llm import classify_text
from ..services.telegram import handle_telegram_webhook
from ..services.asterisk import handle_asterisk_event, start_asterisk_monitoring, stop_asterisk_monitoring, ami_client
from ..services.conversation import conversation_service
from ..services.salute_speech import salute_speech
import logging
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

class ClassifyIn(BaseModel):
    text: str

@router.post("/llm/classify")
async def llm_classify(body: ClassifyIn):
    result = await classify_text(body.text)
    print(f"LLM classify result: {result}")
    return result

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    return await handle_telegram_webhook(data)

@router.post("/asterisk/events")
async def asterisk_events(request: Request):
    data = await request.json()
    return await handle_asterisk_event(data)

@router.post("/asterisk/connect")
async def asterisk_connect():
    """Подключение к Asterisk AMI"""
    success = await start_asterisk_monitoring()
    if success:
        return {"ok": True, "message": "Подключен к Asterisk AMI"}
    else:
        raise HTTPException(status_code=500, detail="Ошибка подключения к Asterisk")

@router.post("/asterisk/disconnect")
async def asterisk_disconnect():
    """Отключение от Asterisk AMI"""
    await stop_asterisk_monitoring()
    return {"ok": True, "message": "Отключен от Asterisk AMI"}

@router.get("/asterisk/status")
async def asterisk_status():
    """Статус подключения к Asterisk"""
    return {
        "connected": ami_client.connected,
        "host": ami_client.host,
        "port": ami_client.port,
        "username": ami_client.username
    }

class OriginateCallIn(BaseModel):
    caller_id: str
    extension: str
    context: Optional[str] = "from-internal"

@router.post("/asterisk/originate")
async def asterisk_originate(body: OriginateCallIn):
    """Инициация звонка через Asterisk"""
    if not ami_client.connected:
        raise HTTPException(status_code=400, detail="Не подключен к Asterisk AMI")
    
    success = await ami_client.originate_call(
        body.caller_id, 
        body.extension, 
        body.context
    )
    
    if success:
        return {"ok": True, "message": f"Звонок инициирован: {body.caller_id} -> {body.extension}"}
    else:
        raise HTTPException(status_code=500, detail="Ошибка инициации звонка")

class StartRecordingIn(BaseModel):
    channel: str
    filename: str

@router.post("/asterisk/recording/start")
async def asterisk_start_recording(body: StartRecordingIn):
    """Начать запись звонка"""
    if not ami_client.connected:
        raise HTTPException(status_code=400, detail="Не подключен к Asterisk AMI")
    
    success = await ami_client.start_recording(body.channel, body.filename)
    
    if success:
        return {"ok": True, "message": f"Запись начата: {body.channel} -> {body.filename}"}
    else:
        raise HTTPException(status_code=500, detail="Ошибка начала записи")

# ===== ГОЛОСОВОЙ БОТ (STT + LLM + TTS) =====

class ChatMessageIn(BaseModel):
    message: str
    context: Optional[List[Dict[str, str]]] = None

@router.post("/bot/chat")
async def bot_chat(body: ChatMessageIn):
    """
    Тестовый эндпоинт для диалога с ботом (текст → текст)
    
    Позволяет протестировать LLM без STT/TTS
    Автоматически использует GigaChat для обработки запросов
    """
    try:
        response = await conversation_service.get_bot_response(
            body.message,
            body.context
        )
        return {
            "ok": True,
            "bot_response": response,
            "provider": conversation_service.provider_name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка бота: {str(e)}")

from fastapi.responses import StreamingResponse

@router.post("/bot/chat/stream")
async def bot_chat_stream(body: ChatMessageIn):
    """
    Потоковый диалог с ботом (текст → текст streaming)
    
    Демонстрирует streaming генерацию ответа
    """
    async def generate():
        try:
            async for chunk in conversation_service.get_bot_response_stream(
                body.message,
                body.context
            ):
                yield chunk
        except Exception as e:
            logger.error(f"Ошибка streaming: {e}")
            yield f"\n\n[Ошибка: {e}]"
    
    return StreamingResponse(generate(), media_type="text/plain")

@router.get("/bot/status")
async def bot_status():
    """Статус голосового бота и настроенных провайдеров"""
    import os
    
    return {
        "llm_provider": "gigachat",
        "llm_provider_name": conversation_service.provider_name,
        "gigachat_configured": bool(os.getenv("GIGACHAT_CLIENT_ID") and os.getenv("GIGACHAT_AUTH_KEY")),
        "salute_configured": bool(os.getenv("SALUTE_CLIENT_ID") and os.getenv("SALUTE_AUTH_KEY")),
        "services": {
            "stt": "SaluteSpeech",
            "llm": conversation_service.provider_name,
            "tts": "SaluteSpeech"
        }
    }

class TestLLMIn(BaseModel):
    message: str
    context: Optional[List[Dict[str, str]]] = None

@router.post("/bot/conversation/test")
async def test_llm_conversation(body: TestLLMIn):
    """
    Тестовый endpoint для проверки LLM без STT/TTS
    
    Принимает текстовое сообщение и возвращает ответ от LLM
    """
    try:
        response = await conversation_service.get_bot_response(
            body.message,
            body.context
        )
        return {
            "ok": True,
            "response": response,
            "provider": conversation_service.provider_name
        }
    except Exception as e:
        logger.error(f"Ошибка тестирования LLM: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка LLM: {str(e)}")

class TestSTTIn(BaseModel):
    audio_data: str  # base64 encoded audio
    sample_rate: int = 8000
    language: str = "ru-RU"

@router.post("/bot/stt/test")
async def test_stt(body: TestSTTIn):
    """
    Тестовый endpoint для проверки STT (Speech-to-Text)
    
    Принимает base64-кодированные аудио данные и возвращает распознанный текст
    """
    try:
        import base64
        from ..services.salute_speech import salute_speech
        
        # Декодируем base64 аудио
        audio_bytes = base64.b64decode(body.audio_data)
        
        # Распознаем речь
        recognized_text = await salute_speech.stt_recognize_pcm16(
            audio_bytes, 
            body.sample_rate, 
            body.language
        )
        
        return {
            "ok": True,
            "recognized_text": recognized_text,
            "provider": "SaluteSpeech"
        }
        
    except Exception as e:
        logger.error(f"Ошибка тестирования STT: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка STT: {str(e)}")

class VoiceCallIn(BaseModel):
    audio_data: str  # base64 encoded audio
    caller_id: str
    sample_rate: int = 8000
    language: str = "ru-RU"
    context: Optional[List[Dict[str, str]]] = None

@router.post("/bot/voice/call")
async def handle_voice_call(body: VoiceCallIn):
    """
    Полный цикл обработки голосового звонка: STT → LLM → TTS
    
    Принимает аудио от пользователя, распознает речь, получает ответ от LLM,
    синтезирует речь и возвращает аудио ответ
    """
    try:
        import base64
        from ..services.salute_speech import salute_speech
        from ..services.conversation import conversation_service
        
        # Шаг 1: STT - распознаем речь пользователя
        audio_bytes = base64.b64decode(body.audio_data)
        user_text = await salute_speech.stt_recognize_pcm16(
            audio_bytes, 
            body.sample_rate, 
            body.language
        )
        
        if not user_text:
            # Если не удалось распознать речь, возвращаем стандартный ответ
            user_text = "Здравствуйте, не расслышал ваш вопрос"
        
        # Шаг 2: LLM - получаем ответ от бота
        bot_response = await conversation_service.get_bot_response(
            user_text,
            body.context
        )
        
        # Шаг 3: TTS - синтезируем речь бота
        audio_response = await salute_speech.tts_synthesize(
            bot_response,
            voice="Bys_8000",
            sample_rate_hz=body.sample_rate
        )
        
        if audio_response:
            # Кодируем аудио ответ в base64
            audio_base64 = base64.b64encode(audio_response).decode('utf-8')
        else:
            audio_base64 = None
        
        return {
            "ok": True,
            "user_text": user_text,
            "bot_response": bot_response,
            "audio_response": audio_base64,
            "caller_id": body.caller_id,
            "processing": {
                "stt_provider": "SaluteSpeech",
                "llm_provider": conversation_service.provider_name,
                "tts_provider": "SaluteSpeech"
            }
        }
        
    except Exception as e:
        logger.error(f"Ошибка обработки голосового звонка: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка голосового звонка: {str(e)}")

class CreateLeadIn(BaseModel):
    phone: str
    name: Optional[str] = None
    comment: str
    source: str = "voice_bot"
    department_code: Optional[str] = None

@router.post("/crm/leads")
async def create_lead(body: CreateLeadIn):
    """
    Создание лида в CRM системе
    
    Сохраняет информацию о потенциальном клиенте в базе данных
    """
    try:
        from ..db import SessionLocal
        from ..models import Lead, LeadSource, LeadStatus
        
        db = SessionLocal()
        
        # Создаем новый лид
        lead = Lead(
            phone=body.phone,
            name=body.name,
            comment=body.comment,
            source=LeadSource.voice_bot,
            status=LeadStatus.new,
            department_code=body.department_code
        )
        
        db.add(lead)
        db.commit()
        lead_id = lead.id
        db.close()
        
        return {
            "ok": True,
            "lead_id": lead_id,
            "message": "Лид успешно создан"
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания лида: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка создания лида: {str(e)}")

@router.get("/crm/leads")
async def get_leads():
    """
    Получение списка лидов
    """
    try:
        from ..db import SessionLocal
        from ..models import Lead
        
        db = SessionLocal()
        leads = db.query(Lead).all()
        db.close()
        
        return {
            "ok": True,
            "leads": [
                {
                    "id": lead.id,
                    "phone": lead.phone,
                    "name": lead.name,
                    "comment": lead.comment,
                    "source": lead.source.value,
                    "status": lead.status.value,
                    "department_code": lead.department_code,
                    "created_at": lead.created_at.isoformat() if lead.created_at else None
                }
                for lead in leads
            ]
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения лидов: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка получения лидов: {str(e)}")


# ===== ГОЛОСОВОЙ БОТ (STT + TTS) =====

class TTSRequest(BaseModel):
    text: str

@router.post("/bot/tts")
async def bot_tts(body: TTSRequest):
    """
    Тестовый эндпоинт для TTS (Text-to-Speech)
    
    Преобразует текст в аудио с помощью Salute Speech
    """
    try:
        audio_data = await salute_speech.tts_synthesize(body.text)
        if audio_data:
            return StreamingResponse(
                iter([audio_data]),
                media_type="audio/wav",
                headers={
                    "Content-Disposition": "attachment; filename=speech.wav"
                }
            )
        else:
            raise HTTPException(status_code=500, detail="Ошибка синтеза речи")
    except Exception as e:
        logger.error(f"Ошибка TTS: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка TTS: {str(e)}")


class STTRequest(BaseModel):
    # base64 аудио PCM16 8kHz
    audio_base64: str
    sample_rate_hz: int | None = 8000

@router.post("/bot/stt")
async def bot_stt(body: STTRequest):
    try:
        import base64
        audio_bytes = base64.b64decode(body.audio_base64)
        text = await salute_speech.stt_recognize_pcm16(audio_bytes, body.sample_rate_hz or 8000)
        if text is None:
            raise HTTPException(status_code=500, detail="Ошибка распознавания речи")
        return {"ok": True, "text": text}
    except Exception as e:
        logger.error(f"Ошибка STT: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка STT: {str(e)}")

class VoiceChatRequest(BaseModel):
    message: str
    context: Optional[List[Dict[str, str]]] = None


@router.post("/bot/voice/chat")
async def bot_voice_chat(body: VoiceChatRequest):
    """
    Полный голосовой диалог: LLM -> TTS
    
    Получает текст, генерирует ответ через LLM, синтезирует речь
    """
    try:
        # 1. Получаем ответ от LLM
        response_chunks = []
        async for chunk in conversation_service.get_bot_response_stream(
            body.message,
            body.context
        ):
            response_chunks.append(chunk)
        
        full_response = "".join(response_chunks)
        
        # 2. Синтезируем речь через SaluteSpeech
        audio_data = None
        if os.getenv("SALUTE_CLIENT_ID") and os.getenv("SALUTE_AUTH_KEY"):
            audio_data = await salute_speech.tts_synthesize(full_response)
        else:
            raise HTTPException(status_code=500, detail="Salute Speech не настроен")
        
        if audio_data:
            return StreamingResponse(
                iter([audio_data]),
                media_type="audio/wav",
                headers={
                    "Content-Disposition": "attachment; filename=bot_response.wav",
                    "X-Bot-Response": full_response.encode('utf-8').decode('latin-1'),
                    "X-Provider": conversation_service.provider_name
                }
            )
        else:
            raise HTTPException(status_code=500, detail="Ошибка синтеза речи")
            
    except Exception as e:
        logger.error(f"Ошибка голосового диалога: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка голосового диалога: {str(e)}")



