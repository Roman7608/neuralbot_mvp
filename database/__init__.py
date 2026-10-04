"""
Модуль для работы с базой данных PostgreSQL.
"""

from database.postgresql_manager import (
    PostgreSQLManager,
    TelegramLeadsDB,
    CallAnalyticsDB,
)

__all__ = [
    'PostgreSQLManager',
    'TelegramLeadsDB',
    'CallAnalyticsDB',
]
