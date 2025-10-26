#!/usr/bin/env python3
"""
Простой AGI скрипт для тестирования голосового бота с LLM
"""

import sys
import os
import asyncio
import json
import requests
from datetime import datetime

# Добавляем путь к модулям приложения
sys.path.append('/app')

def log(message):
    """Логирование в AGI"""
    print(f"VERBOSE \"{message}\" 1", file=sys.stderr)
    sys.stderr.flush()

def agi_command(command):
    """Выполнение AGI команды"""
    print(command)
    sys.stdout.flush()
    return sys.stdin.readline().strip()

async def test_llm_conversation():
    """Тестирование LLM через API"""
    try:
        # Тестовый запрос к LLM
        test_message = "Хочу купить машину до 2 миллионов рублей"
        
        log(f"Тестируем LLM с сообщением: {test_message}")
        
        # Отправляем запрос к API
        response = requests.post(
            "http://api:8000/internal/bot/conversation/test",
            json={
                "message": test_message,
                "context": []
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            bot_response = result.get("response", "Нет ответа")
            log(f"LLM ответил: {bot_response}")
            
            # Воспроизводим ответ через TTS (пока просто логируем)
            log(f"Воспроизводим: {bot_response}")
            
            return True
        else:
            log(f"Ошибка API: {response.status_code}")
            return False
            
    except Exception as e:
        log(f"Ошибка тестирования LLM: {e}")
        return False

def main():
    """Основная функция AGI скрипта"""
    if len(sys.argv) < 3:
        log("Недостаточно аргументов")
        return
    
    channel = sys.argv[1]
    caller_id = sys.argv[2]
    
    log(f"Начинаем тест LLM для канала {channel}, звонящий {caller_id}")
    
    # Получаем информацию о канале
    agi_command(f"GET VARIABLE CHANNEL")
    
    # Отвечаем на звонок
    agi_command(f"ANSWER")
    
    # Ждем немного
    agi_command(f"WAIT 1")
    
    # Проигрываем приветствие
    agi_command(f"STREAM FILE hello-world \"\"")
    
    # Ждем еще немного
    agi_command(f"WAIT 2")
    
    # Тестируем LLM
    log("Запускаем тест LLM...")
    success = asyncio.run(test_llm_conversation())
    
    if success:
        log("LLM тест успешен!")
        # Проигрываем успешное сообщение
        agi_command(f"STREAM FILE beep \"\"")
    else:
        log("LLM тест не удался")
        # Проигрываем ошибку
        agi_command(f"STREAM FILE error \"\"")
    
    # Ждем и завершаем
    agi_command(f"WAIT 2")
    log("Завершаем тест")

if __name__ == "__main__":
    main()



