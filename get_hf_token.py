#!/usr/bin/env python3
"""
Вспомогательный скрипт для получения токена Hugging Face
"""
import requests
import getpass

username = input("Hugging Face username: ")
password = getpass.getpass("Hugging Face password: ")

try:
    # Используем правильный эндпоинт для авторизации
    session = requests.Session()
    login_url = "https://huggingface.co/api/login"
    
    response = session.post(
        login_url,
        json={"username": username, "password": password},
        headers={"Content-Type": "application/json"}
    )
    
    if response.status_code == 200:
        # После успешного логина получаем токен из cookies или API
        token_url = "https://huggingface.co/api/whoami-v2"
        token_response = session.get(token_url)
        
        if token_response.status_code == 200:
            print("\n✅ Авторизация успешна!")
            print("Теперь создайте токен через веб-интерфейс:")
            print("https://hf.co/settings/tokens")
            print("\nИли используйте huggingface_hub для создания токена программно.")
        else:
            print("Авторизация успешна, но получить информацию о токене не удалось.")
    else:
        print(f"Ошибка авторизации: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Ошибка: {e}")
