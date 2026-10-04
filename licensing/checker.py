"""
Модуль проверки лицензии Vikingi.

Лицензия — JWT-токен, подписанный RSA-4096.
Приватный ключ хранится только у разработчика.
На сервере — публичный ключ (проверка) и файл лицензии.
"""

import sys
import json
import time
import hashlib
import logging
import threading
from pathlib import Path
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger("licensing")

_BASE_DIR = Path(__file__).resolve().parent.parent
_PUBLIC_KEY_PATH = Path(__file__).resolve().parent / "public_key.pem"
_LICENSE_PATH = _BASE_DIR / "license.key"
_TIMESTAMP_PATH = _BASE_DIR / ".license_last_check"

_GRACE_DAYS = 3


def _load_public_key():
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    return load_pem_public_key(_PUBLIC_KEY_PATH.read_bytes())


def _decode_jwt_rs256(token: str, public_key) -> dict:
    """Декодирование JWT RS256 без внешних JWT-библиотек."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("Неверный формат токена")

    def _b64decode(s: str) -> bytes:
        s += "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s)

    header_b, payload_b, sig_b = parts
    header = json.loads(_b64decode(header_b))
    if header.get("alg") != "RS256":
        raise ValueError(f"Неподдерживаемый алгоритм: {header.get('alg')}")

    signature = _b64decode(sig_b)
    message = f"{header_b}.{payload_b}".encode("ascii")
    public_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())

    return json.loads(_b64decode(payload_b))


def _check_timestamp_monotonic(valid_until_str: str) -> bool:
    """Проверка, что системное время не откатили назад."""
    today_str = date.today().isoformat()
    if _TIMESTAMP_PATH.exists():
        try:
            last = _TIMESTAMP_PATH.read_text().strip()
            if last > today_str:
                logger.error("Обнаружен откат системного времени: last=%s, now=%s", last, today_str)
                return False
        except Exception:
            pass
    try:
        _TIMESTAMP_PATH.write_text(today_str)
    except Exception:
        pass
    return True


def _validate(module: Optional[str] = None) -> tuple[bool, str]:
    """Основная проверка. Возвращает (ok, message)."""
    if not _PUBLIC_KEY_PATH.exists():
        return False, "Отсутствует файл публичного ключа"
    if not _LICENSE_PATH.exists():
        return False, "Отсутствует файл лицензии (license.key)"

    try:
        public_key = _load_public_key()
    except Exception as e:
        return False, f"Ошибка загрузки ключа: {e}"

    try:
        token = _LICENSE_PATH.read_text().strip()
        payload = _decode_jwt_rs256(token, public_key)
    except Exception as e:
        return False, f"Лицензия невалидна: {e}"

    valid_until_str = payload.get("valid_until", "")
    try:
        valid_until = date.fromisoformat(valid_until_str)
    except ValueError:
        return False, f"Некорректная дата в лицензии: {valid_until_str}"

    if not _check_timestamp_monotonic(valid_until_str):
        return False, "Обнаружена манипуляция с системным временем"

    today = date.today()
    if today > valid_until:
        from datetime import timedelta
        grace_end = valid_until + timedelta(days=_GRACE_DAYS)
        # valid_until=28.10: предупреждаем 29–30.10, останавливаем сервисы 31.10.
        if today < grace_end:
            days_over = (today - valid_until).days
            return True, f"Лицензия истекла {valid_until_str}, грейс-период: осталось {_GRACE_DAYS - days_over} дн."
        return False, f"Лицензия истекла {valid_until_str}. Обратитесь к разработчику: +7(902)373-08-08"

    if module and "modules" in payload:
        allowed = payload["modules"]
        if module not in allowed:
            return False, f"Модуль '{module}' не включён в лицензию"

    days_left = (valid_until - today).days
    return True, f"Лицензия OK: {payload.get('client', '?')}, до {valid_until_str} ({days_left} дн.)"


def verify_license(module: Optional[str] = None) -> None:
    """
    Проверить лицензию при старте сервиса.
    При невалидной лицензии — завершает процесс.
    """
    ok, msg = _validate(module)
    if ok:
        logger.info("[LICENSE] %s", msg)
        if "грейс" in msg.lower():
            print(f"⚠ ВНИМАНИЕ: {msg}", flush=True)
    else:
        logger.error("[LICENSE] %s", msg)
        print(f"\n{'='*60}", flush=True)
        print(f"  ЛИЦЕНЗИЯ: {msg}", flush=True)
        print(f"{'='*60}\n", flush=True)
        sys.exit(1)


def start_periodic_check(module: Optional[str] = None, interval_hours: int = 24) -> None:
    """Фоновая проверка лицензии раз в N часов."""
    def _checker():
        while True:
            time.sleep(interval_hours * 3600)
            ok, msg = _validate(module)
            if ok:
                logger.info("[LICENSE] periodic: %s", msg)
            else:
                logger.error("[LICENSE] periodic: %s", msg)
                print(f"\n  ЛИЦЕНЗИЯ ИСТЕКЛА: {msg}\n  Сервис будет остановлен.\n", flush=True)
                import os
                os._exit(1)

    t = threading.Thread(target=_checker, daemon=True, name="license-checker")
    t.start()


def license_info() -> dict:
    """Информация о лицензии для health-эндпоинтов."""
    ok, msg = _validate()
    return {"valid": ok, "message": msg}
