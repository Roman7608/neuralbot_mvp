"""
Нормализация телефонных номеров.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Строки TEL в vCard (MAX / мессенджеры могут прислать контакт как текст vcf)
_VCARD_TEL_LINE = re.compile(r"(?im)^(?:item\d*\.?)?TEL[^:]*:\s*(.+)$")


def _normalize_phone_scalar(phone: str) -> Optional[str]:
    """
    Нормализация одного номера (не целого vCard).
    """
    if not phone:
        return None

    cleaned = re.sub(r"[\s\-\(\)]", "", phone)

    if cleaned.startswith("+"):
        digits = re.sub(r"\D", "", cleaned[1:])
        if len(digits) >= 10 and digits[0] in ["7", "8", "9"]:
            if digits[0] in ["7", "8"] and len(digits) == 11 and digits[1] == "9":
                return f"+{digits}"
            elif digits[0] == "9" and len(digits) == 10:
                return f"+7{digits}"

    digits_only = re.sub(r"\D", "", cleaned)
    if len(digits_only) == 10 and digits_only[0] == "9":
        return f"+7{digits_only}"

    if len(digits_only) == 11 and digits_only[0] == "8" and digits_only[1] == "9":
        return f"+7{digits_only[1:]}"

    # MAX / контакты: «7902…» без плюса (11 цифр, страна 7 + моб. 9…)
    if len(digits_only) == 11 and digits_only[0] == "7" and digits_only[1] == "9":
        return f"+{digits_only}"

    if "(" in phone and ")" in phone:
        match = re.match(r"\((\d+)\)\s*(\d+[\-\s]*\d+[\-\s]*\d+)", phone)
        if match:
            city_code = match.group(1)
            number = re.sub(r"[\s\-]", "", match.group(2))
            if len(city_code) >= 3 and len(number) >= 6:
                return f"+7{city_code}{number}"

    return None


def extract_phone_from_vcard(text: str) -> Optional[str]:
    """
    Достаёт номер из текста vCard (BEGIN:VCARD … TEL:… END:VCARD).
    Перебирает все поля TEL, возвращает первый успешно нормализованный.
    """
    if not text or "vcard" not in text.lower():
        return None
    # MAX / ez-vcard отдают перевод строки как \r без \n — у (?m)^ якорь «начало строки»
    # срабатывает только после \n, иначе TEL: не находится; в БД уезжал целый vCard.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for m in _VCARD_TEL_LINE.finditer(text):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        n = _normalize_phone_scalar(raw)
        if n:
            return n
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 10:
            n2 = _normalize_phone_scalar(digits)
            if n2:
                return n2
    return None


def normalize_phone(phone: str) -> Optional[str]:
    """
    Нормализует телефонный номер.

    Поддерживаемые форматы:
    - +79023700000 (мобильный с +)
    - 79023700000 (11 цифр, 7 и далее мобильный 9…)
    - 9023700000 (мобильный без +7, начинается с 9, 10 цифр)
    - (8462) 46-00-00 (стационарный)
    - vCard целиком — извлекается поле TEL

    :param phone: Номер телефона в любом формате
    :return: Нормализованный номер или None, если не удалось нормализовать
    """
    if not phone:
        return None

    if "VCARD" in phone.upper():
        from_vcf = extract_phone_from_vcard(phone)
        if from_vcf:
            return from_vcf

    result = _normalize_phone_scalar(phone)
    if result:
        return result
    preview = phone if len(phone) <= 80 else phone[:77] + "..."
    logger.warning("Не удалось нормализовать номер: %s", preview)
    return None


def is_valid_phone(phone: str) -> bool:
    """
    Проверяет, является ли номер телефона валидным.

    :param phone: Номер телефона
    :return: True если номер валиден, False иначе
    """
    normalized = normalize_phone(phone)
    return normalized is not None
