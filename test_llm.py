#!/usr/bin/env python3
"""
Тест LLM модели: Saiga Llama3 8B Q4_K_M
Проверка загрузки, генерации и скорости работы
"""

from llama_cpp import Llama
import time
import sys

def test_llm():
    model_path = "/home/romandemo/models/qwen/model-q4_K.gguf"
    
    print("=" * 60)
    print("ТЕСТ LLM: Saiga Llama3 8B Q4_K_M")
    print("=" * 60)
    
    # Загрузка модели
    print("\n[1/4] Загрузка модели...")
    try:
        llm = Llama(
            model_path=model_path,
            n_ctx=2048,  # Размер контекста
            n_gpu_layers=-1,  # Максимально на GPU
            verbose=False,
        )
        print("✅ Модель загружена успешно")
    except Exception as e:
        print(f"❌ Ошибка загрузки модели: {e}")
        return False
    
    # Тест 1: Простое приветствие
    print("\n[2/4] Тест 1: Приветствие и представление")
    prompt1 = "Ты вежливый русскоязычный голосовой ассистент автосервиса. Ответь коротко: привет, представься и спроси, чем помочь."
    print(f"Промпт: {prompt1}")
    
    start = time.time()
    try:
        out1 = llm(prompt1, max_tokens=128, stop=["</s>", "###"], stream=False)
        time1 = time.time() - start
        response1 = out1['choices'][0]['text'].strip()
        tokens1 = len(response1.split())
        
        print(f"Ответ: {response1}")
        print(f"Время генерации: {time1:.2f} сек")
        print(f"Токенов сгенерировано: ~{tokens1}")
        print(f"Скорость: ~{tokens1/time1:.1f} токенов/сек")
    except Exception as e:
        print(f"❌ Ошибка генерации: {e}")
        return False
    
    # Тест 2: Вопрос о записи
    print("\n[3/4] Тест 2: Вопрос о записи на сервис")
    prompt2 = "Клиент спрашивает: 'Можно записаться на завтра?' Ответь коротко и вежливо."
    print(f"Промпт: {prompt2}")
    
    start = time.time()
    try:
        out2 = llm(prompt2, max_tokens=64, stop=["</s>", "###"], stream=False)
        time2 = time.time() - start
        response2 = out2['choices'][0]['text'].strip()
        tokens2 = len(response2.split())
        
        print(f"Ответ: {response2}")
        print(f"Время генерации: {time2:.2f} сек")
        print(f"Токенов сгенерировано: ~{tokens2}")
        print(f"Скорость: ~{tokens2/time2:.1f} токенов/сек")
    except Exception as e:
        print(f"❌ Ошибка генерации: {e}")
        return False
    
    # Тест 3: Информация о модели
    print("\n[4/4] Информация о модели")
    try:
        n_ctx = llm.n_ctx() if callable(getattr(llm, "n_ctx", None)) else llm.n_ctx
        print(f"Контекст: {n_ctx} токенов")
    except Exception:
        print("Контекст: (недоступно)")
    try:
        n_gpu = llm.n_gpu_layers
        print(f"GPU слои: {n_gpu} (все на GPU)")
    except Exception:
        pass
    print(f"Путь к модели: {model_path}")
    
    # Проверка времени ответа
    avg_time = (time1 + time2) / 2
    print(f"\n📊 Среднее время генерации: {avg_time:.2f} сек")
    
    if avg_time < 2.0:
        print("✅ Время ответа < 2 сек - соответствует требованиям!")
    else:
        print("⚠️  Время ответа > 2 сек - требуется оптимизация")
    
    print("\n" + "=" * 60)
    print("✅ ТЕСТ LLM ЗАВЕРШЁН УСПЕШНО")
    print("=" * 60)
    
    return True

if __name__ == "__main__":
    success = test_llm()
    sys.exit(0 if success else 1)

