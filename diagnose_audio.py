#!/usr/bin/env python3
"""
Скрипт диагностики проблем с аудио в голосовом боте
Проверяет все компоненты: Asterisk, Docker, STT/TTS, сеть
"""

import os
import sys
import asyncio
import subprocess
import base64
import httpx
import json
from pathlib import Path

# Добавляем путь к приложению
sys.path.append('app')

async def check_docker_network():
    """Проверяем Docker сеть и доступность сервисов"""
    print("🔍 Проверяем Docker сеть...")
    
    try:
        # Проверяем, что контейнеры запущены
        result = subprocess.run(['docker', 'ps'], capture_output=True, text=True)
        if result.returncode != 0:
            print("❌ Docker не доступен")
            return False
        
        containers = result.stdout
        if 'asterisk' in containers and 'api' in containers:
            print("✅ Контейнеры Asterisk и API запущены")
        else:
            print("❌ Не все контейнеры запущены")
            print(containers)
            return False
        
        # Проверяем доступность API
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get("http://localhost:8000/internal/bot/status")
                if response.status_code == 200:
                    print("✅ API доступен")
                    data = response.json()
                    print(f"   LLM: {data.get('llm_provider_name', 'unknown')}")
                    print(f"   Salute настроен: {data.get('salute_configured', False)}")
                else:
                    print(f"❌ API недоступен: {response.status_code}")
                    return False
        except Exception as e:
            print(f"❌ Ошибка подключения к API: {e}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка проверки Docker: {e}")
        return False

async def check_asterisk_ami():
    """Проверяем подключение к Asterisk AMI"""
    print("\n🔍 Проверяем Asterisk AMI...")
    
    try:
        from app.services.asterisk import ami_client
        
        # Проверяем подключение
        if await ami_client.connect():
            print("✅ Подключение к Asterisk AMI успешно")
            
            # Проверяем статус
            status = {
                "connected": ami_client.connected,
                "host": ami_client.host,
                "port": ami_client.port,
                "username": ami_client.username
            }
            print(f"   Статус: {status}")
            
            await ami_client.disconnect()
            return True
        else:
            print("❌ Не удалось подключиться к Asterisk AMI")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка проверки AMI: {e}")
        return False

async def check_stt_tts():
    """Проверяем STT и TTS сервисы"""
    print("\n🔍 Проверяем STT/TTS сервисы...")
    
    try:
        from app.services.salute_speech import salute_speech
        
        # Проверяем настройки
        if not salute_speech.client_id or not salute_speech.auth_key:
            print("❌ Salute Speech не настроен (отсутствуют креды)")
            return False
        
        print("✅ Salute Speech креды настроены")
        
        # Тестируем TTS
        print("   Тестируем TTS...")
        audio_data = await salute_speech.tts_synthesize("Тест", voice="Bys_8000", sample_rate_hz=8000)
        
        if audio_data:
            print(f"✅ TTS работает, сгенерировано {len(audio_data)} bytes")
            
            # Сохраняем тестовый файл
            with open("test_tts_output.wav", "wb") as f:
                f.write(audio_data)
            print("   Тестовый файл сохранен: test_tts_output.wav")
        else:
            print("❌ TTS не работает")
            return False
        
        # Тестируем STT (если есть тестовый файл)
        test_audio_file = "test_audio.wav"
        if os.path.exists(test_audio_file):
            print(f"   Тестируем STT с файлом {test_audio_file}...")
            
            with open(test_audio_file, "rb") as f:
                audio_bytes = f.read()
            
            text = await salute_speech.stt_recognize_pcm16(audio_bytes, 8000)
            if text:
                print(f"✅ STT работает, распознано: '{text}'")
            else:
                print("❌ STT не распознал речь")
        else:
            print(f"⚠️ Тестовый аудио файл {test_audio_file} не найден")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка проверки STT/TTS: {e}")
        return False

async def check_llm():
    """Проверяем LLM сервис"""
    print("\n🔍 Проверяем LLM сервис...")
    
    try:
        from app.services.conversation import conversation_service
        
        # Тестируем простой запрос
        response = await conversation_service.get_bot_response("Привет")
        
        if response:
            print(f"✅ LLM работает, ответ: '{response[:50]}...'")
            print(f"   Провайдер: {conversation_service.provider_name}")
        else:
            print("❌ LLM не отвечает")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка проверки LLM: {e}")
        return False

async def test_full_pipeline():
    """Тестируем полный пайплайн STT → LLM → TTS"""
    print("\n🔍 Тестируем полный пайплайн...")
    
    try:
        # Используем тестовый аудио файл
        test_audio_file = "test_audio.wav"
        if not os.path.exists(test_audio_file):
            print(f"❌ Тестовый файл {test_audio_file} не найден")
            return False
        
        # Читаем аудио
        with open(test_audio_file, "rb") as f:
            audio_bytes = f.read()
        
        # Кодируем в base64
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        # Отправляем запрос к API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://localhost:8000/internal/bot/voice/call",
                json={
                    "audio_data": audio_base64,
                    "caller_id": "test_user",
                    "sample_rate": 8000,
                    "language": "ru-RU"
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                print("✅ Полный пайплайн работает!")
                print(f"   Распознано: '{data.get('user_text', '')}'")
                print(f"   Ответ бота: '{data.get('bot_response', '')[:50]}...'")
                print(f"   Аудио ответ: {'есть' if data.get('audio_response') else 'нет'}")
                
                # Сохраняем ответ
                if data.get('audio_response'):
                    audio_response = base64.b64decode(data['audio_response'])
                    with open("test_pipeline_response.wav", "wb") as f:
                        f.write(audio_response)
                    print("   Ответ сохранен: test_pipeline_response.wav")
                
                return True
            else:
                print(f"❌ Ошибка пайплайна: {response.status_code}")
                print(response.text)
                return False
        
    except Exception as e:
        print(f"❌ Ошибка тестирования пайплайна: {e}")
        return False

def check_asterisk_config():
    """Проверяем конфигурацию Asterisk"""
    print("\n🔍 Проверяем конфигурацию Asterisk...")
    
    config_files = [
        "asterisk-config/extensions.conf",
        "asterisk-config/pjsip.conf",
        "asterisk-config/voice_bot_agi_final.sh"
    ]
    
    for config_file in config_files:
        if os.path.exists(config_file):
            print(f"✅ {config_file} существует")
        else:
            print(f"❌ {config_file} не найден")
    
    # Проверяем MixMonitor в extensions.conf
    try:
        with open("asterisk-config/extensions.conf", "r") as f:
            content = f.read()
            if "MixMonitor" in content:
                print("✅ MixMonitor настроен в extensions.conf")
            else:
                print("❌ MixMonitor не найден в extensions.conf")
    except Exception as e:
        print(f"❌ Ошибка чтения extensions.conf: {e}")
    
    # Проверяем кодеки в pjsip.conf
    try:
        with open("asterisk-config/pjsip.conf", "r") as f:
            content = f.read()
            if "allow=ulaw" in content and "allow=alaw" in content:
                print("✅ Кодеки G.711 настроены в pjsip.conf")
            else:
                print("❌ Кодеки G.711 не найдены в pjsip.conf")
    except Exception as e:
        print(f"❌ Ошибка чтения pjsip.conf: {e}")

async def main():
    """Основная функция диагностики"""
    print("🚀 Диагностика голосового бота")
    print("=" * 50)
    
    # Проверяем конфигурацию
    check_asterisk_config()
    
    # Проверяем компоненты
    docker_ok = await check_docker_network()
    ami_ok = await check_asterisk_ami()
    stt_tts_ok = await check_stt_tts()
    llm_ok = await check_llm()
    
    # Тестируем полный пайплайн
    pipeline_ok = await test_full_pipeline()
    
    # Итоговый отчет
    print("\n" + "=" * 50)
    print("📊 ИТОГОВЫЙ ОТЧЕТ:")
    print(f"   Docker сеть: {'✅' if docker_ok else '❌'}")
    print(f"   Asterisk AMI: {'✅' if ami_ok else '❌'}")
    print(f"   STT/TTS: {'✅' if stt_tts_ok else '❌'}")
    print(f"   LLM: {'✅' if llm_ok else '❌'}")
    print(f"   Полный пайплайн: {'✅' if pipeline_ok else '❌'}")
    
    if all([docker_ok, ami_ok, stt_tts_ok, llm_ok, pipeline_ok]):
        print("\n🎉 Все компоненты работают! Проблема может быть в:")
        print("   - Кодеках MicroSIP (убедитесь, что используется G.711)")
        print("   - Сетевых настройках RTP")
        print("   - Брандмауэре Windows")
        print("   - VPN/TAP адаптерах")
    else:
        print("\n⚠️ Есть проблемы с компонентами. Исправьте их перед тестированием звонков.")
    
    print("\n💡 Рекомендации:")
    print("   1. Убедитесь, что MicroSIP использует кодек G.711 (PCMA/PCMU)")
    print("   2. Отключите VPN и антивирус на время теста")
    print("   3. Проверьте записи звонков в /var/spool/asterisk/monitor/")
    print("   4. Используйте 'asterisk -rvvvvv' для детального логирования")

if __name__ == "__main__":
    asyncio.run(main())
