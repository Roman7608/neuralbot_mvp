"""Конфиг MAX-бота (токен из .env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MAX_BOT_TOKEN: str = (os.environ.get("MAX_BOT_TOKEN") or "").strip()
MAX_API_BASE: str = (os.environ.get("MAX_API_BASE") or "https://platform-api.max.ru").rstrip("/")

# Long polling (см. https://dev.max.ru/docs-api/methods/GET/updates )
MAX_POLL_TIMEOUT: int = int(os.environ.get("MAX_POLL_TIMEOUT", "30"))
MAX_POLL_LIMIT: int = int(os.environ.get("MAX_POLL_LIMIT", "100"))

# Позже: webhook на этом порту (прокси с ideco / nginx → сюда)
MAX_BOT_HTTP_PORT: int = int(os.environ.get("MAX_BOT_HTTP_PORT", "8040"))


def max_service_booking_via_alfa_enabled() -> bool:
    """
    Ветка «запись на ТО» через слоты/1С Альфа в MAX-боте.
    Пока интеграция не готова — по умолчанию выключено (только передача контакта в слесарный цех).
    Включить: MAX_ALFA_SERVICE_BOOKING_ENABLED=1 в окружении или .env.
    """
    v = (os.environ.get("MAX_ALFA_SERVICE_BOOKING_ENABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


_DEFAULT_POLICY_DOCX = "Политика обработки персональных данных Викинги (18.11.22).docx"


def policy_local_file_path() -> Path | None:
    """
    Локальный файл политики на диске бота (server7 / контейнер).
    Приоритет: MAX_POLICY_LOCAL_PATH из окружения, иначе Analytic/<имя по умолчанию>, если файл есть.
    Используется для загрузки в MAX через POST /uploads (без публичного HTTPS).
    """
    env = (os.environ.get("MAX_POLICY_LOCAL_PATH") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    repo = Path(__file__).resolve().parent.parent
    candidates.append(repo / "Analytic" / _DEFAULT_POLICY_DOCX)
    for p in candidates:
        try:
            if p.is_file():
                return p.resolve()
        except OSError:
            continue
    return None


def policy_document_public_url() -> str:
    """
    Полный HTTPS-URL документа политики для кнопки type=link в MAX.
    Задайте MAX_POLICY_DOC_URL или VIKINGI_PUBLIC_BASE_URL (без слэша в конце),
    тогда подставится путь /public/privacy-policy на админке.
    """
    explicit = (os.environ.get("MAX_POLICY_DOC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    base = (os.environ.get("VIKINGI_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/public/privacy-policy"
    return ""
