"""
Конфигурация подключения к PostgreSQL для проекта Vikingi.
Переменные окружения (Docker) имеют приоритет над значениями по умолчанию.

Автоопределение для единой БД:
- Внутри Docker: postgres:5432 (если env не задан)
- На хосте: localhost:5433 (порт Docker postgres, см. docker-compose)
"""

import os


def _in_docker() -> bool:
    """Проверка: скрипт выполняется внутри Docker-контейнера."""
    return os.path.exists("/.dockerenv")


def _default_host() -> str:
    if "POSTGRESQL_HOST" in os.environ:
        return os.environ["POSTGRESQL_HOST"]
    return "postgres" if _in_docker() else "localhost"


def _default_port() -> int:
    if "POSTGRESQL_PORT" in os.environ:
        return int(os.environ["POSTGRESQL_PORT"])
    # Docker postgres проброшен на хост как 5433:5432
    return 5432 if _in_docker() else 5433


# Параметры подключения к PostgreSQL
POSTGRESQL_HOST: str = _default_host()
POSTGRESQL_PORT: int = _default_port()
POSTGRESQL_DATABASE: str = os.environ.get("POSTGRESQL_DATABASE", "vikingi_analytics")

# Пользователи
POSTGRESQL_ANALYTICS_USER: str = os.environ.get("POSTGRESQL_ANALYTICS_USER", "analytics_user")
POSTGRESQL_ANALYTICS_PASSWORD: str = os.environ.get("POSTGRESQL_ANALYTICS_PASSWORD", "TOP")

# Часовой пояс сессии БД: CURRENT_TIMESTAMP и сравнения по времени — в «местном» времени дилера.
# Переопределение: POSTGRESQL_SESSION_TIMEZONE или общий TZ (как у контейнеров ботов).
POSTGRESQL_SESSION_TIMEZONE: str = (
    os.environ.get("POSTGRESQL_SESSION_TIMEZONE")
    or os.environ.get("TZ")
    or "Europe/Samara"
)

POSTGRESQL_ICS_USER: str = os.environ.get("POSTGRESQL_ICS_USER", "ics_user")
POSTGRESQL_ICS_PASSWORD: str = os.environ.get("POSTGRESQL_ICS_PASSWORD", "Rop")

# Строка подключения для SQLAlchemy (если используется)
def get_analytics_connection_string() -> str:
    """Возвращает строку подключения для analytics_user."""
    return f"postgresql://{POSTGRESQL_ANALYTICS_USER}:{POSTGRESQL_ANALYTICS_PASSWORD}@{POSTGRESQL_HOST}:{POSTGRESQL_PORT}/{POSTGRESQL_DATABASE}"

def get_ics_connection_string() -> str:
    """Возвращает строку подключения для ics_user."""
    return f"postgresql://{POSTGRESQL_ICS_USER}:{POSTGRESQL_ICS_PASSWORD}@{POSTGRESQL_HOST}:{POSTGRESQL_PORT}/{POSTGRESQL_DATABASE}"

# Параметры пула соединений (для SQLAlchemy)
POSTGRESQL_POOL_SIZE: int = 5
POSTGRESQL_MAX_OVERFLOW: int = 10
POSTGRESQL_POOL_TIMEOUT: int = 30

# SSL (для удаленных подключений)
POSTGRESQL_USE_SSL: bool = False  # True для удаленных подключений
POSTGRESQL_SSL_MODE: str = "require"  # require, prefer, allow, disable
