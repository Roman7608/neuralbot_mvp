"""
Парсер дат из русской речи.

Поддерживает:
- Названия месяцев (января, февраль, март и т.д.)
- Дни недели (понедельник, вторник и т.д.)
- Относительные даты (сегодня, завтра, через неделю)
- Разные форматы чисел (20-го, двадцатое, 20)
- Комбинированные форматы (20 января, двадцатое января, 20-го января)
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class DateParser:
    """Парсер дат из русской речи."""
    
    # Названия месяцев в родительном падеже (для "20 января")
    MONTHS_GENITIVE = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    
    # Названия месяцев в именительном падеже (для "январь")
    MONTHS_NOMINATIVE = {
        "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
        "май": 5, "июнь": 6, "июль": 7, "август": 8,
        "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
    }
    
    # Дни недели
    WEEKDAYS = {
        "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
        "пятница": 4, "суббота": 5, "воскресенье": 6,
    }
    
    # Числа прописью (для "двадцатое")
    NUMBER_WORDS = {
        "первое": 1, "второе": 2, "третье": 3, "четвертое": 4, "пятое": 5,
        "шестое": 6, "седьмое": 7, "восьмое": 8, "девятое": 9, "десятое": 10,
        "одиннадцатое": 11, "двенадцатое": 12, "тринадцатое": 13, "четырнадцатое": 14,
        "пятнадцатое": 15, "шестнадцатое": 16, "семнадцатое": 17, "восемнадцатое": 18,
        "девятнадцатое": 19, "двадцатое": 20, "двадцать первое": 21, "двадцать второе": 22,
        "двадцать третье": 23, "двадцать четвертое": 24, "двадцать пятое": 25,
        "двадцать шестое": 26, "двадцать седьмое": 27, "двадцать восьмое": 28,
        "двадцать девятое": 29, "тридцатое": 30, "тридцать первое": 31,
    }

    @classmethod
    def _day_words_all(cls) -> dict[str, int]:
        """Именительный/винительный и родительный падеж («девятнадцатого июня»)."""
        def _to_genitive(word: str) -> Optional[str]:
            if word == "третье":
                return "третьего"
            if word.endswith("ое"):
                return word[:-2] + "ого"
            if word.endswith("ее"):
                return word[:-2] + "его"
            return None

        def _to_feminine(word: str) -> Optional[str]:
            if word == "третье":
                return "третья"
            if word.endswith("ое"):
                return word[:-2] + "ая"
            if word.endswith("ее"):
                return word[:-2] + "яя"
            return None

        def _to_feminine_accusative(word: str) -> Optional[str]:
            if word.endswith("ая"):
                return word[:-2] + "ую"
            if word.endswith("яя"):
                return word[:-2] + "юю"
            return None

        out = dict(cls.NUMBER_WORDS)
        for word, day in cls.NUMBER_WORDS.items():
            genitive = _to_genitive(word)
            if genitive:
                out[genitive] = day
            # Составные формы: «двадцать третьего», «тридцать первого».
            if " " in word:
                parts = word.split()
                tail_gen = _to_genitive(parts[-1])
                if tail_gen:
                    out[" ".join(parts[:-1] + [tail_gen])] = day
            feminine = _to_feminine(word)
            if feminine:
                out[feminine] = day
                feminine_acc = _to_feminine_accusative(feminine)
                if feminine_acc:
                    out[feminine_acc] = day
        return out
    
    # Относительные даты
    RELATIVE_DATES = {
        "сегодня": 0,
        "завтра": 1,
        "послезавтра": 2,
        "через день": 1,
        "через два дня": 2,
        "через три дня": 3,
        "через неделю": 7,
        "через две недели": 14,
        "через месяц": 30,  # Приблизительно
    }
    
    def __init__(self):
        """Инициализирует парсер дат."""
        self._day_words = self._day_words_all()
        # Компилируем паттерны
        self.month_pattern = re.compile(
            r"\b(" + "|".join(self.MONTHS_GENITIVE.keys()) + r"|" + "|".join(self.MONTHS_NOMINATIVE.keys()) + r")\b",
            re.IGNORECASE
        )
        self.weekday_pattern = re.compile(
            r"\b(" + "|".join(self.WEEKDAYS.keys()) + r")\b",
            re.IGNORECASE
        )
        self.number_pattern = re.compile(r"\b(\d{1,2})(?:-го|-е|-ое)?\b", re.IGNORECASE)
        day_word_keys = sorted(self._day_words.keys(), key=len, reverse=True)
        self.number_word_pattern = re.compile(
            r"\b(" + "|".join(re.escape(k) for k in day_word_keys) + r")\b",
            re.IGNORECASE
        )
        logger.info("Инициализирован DateParser")
    
    def parse_date(
        self,
        text: str,
        reference_date: Optional[datetime] = None,
        *,
        slot_day9_hint: bool = False,
        slot_day10_hint: bool = False,
    ) -> Optional[datetime]:
        """
        Парсит дату из текста.
        
        :param text: Текст с датой
        :param reference_date: Опорная дата (по умолчанию - сегодня)
        :return: Объект datetime или None, если дата не найдена
        """
        if not text or not text.strip():
            return None
        
        if reference_date is None:
            from dialog.dealer_time import dealer_local_now_naive

            reference_date = dealer_local_now_naive()
        
        from dialog.service_speech_parse import normalize_stt_booking_date_text

        text_lower = normalize_stt_booking_date_text(
            text,
            slot_day9_hint=slot_day9_hint,
            slot_day10_hint=slot_day10_hint,
        ).lower().replace("ё", "е").strip()
        # Время обрабатывается отдельным парсером слотов. Не позволяем «9:00»
        # стать девятым числом перед названием месяца, добавленным из контекста.
        text_lower = re.sub(r"(?<!\d)\d{1,2}\s*:\s*\d{2}(?!\d)", " ", text_lower)
        text_lower = re.sub(
            r"\b(?:в|на)\s+\d{1,2}\s*\.\s*\d{2}\b",
            " ",
            text_lower,
            flags=re.IGNORECASE,
        )

        # 1. Проверяем относительные даты (сегодня, завтра и т.д.)
        date = self._parse_relative_date(text_lower, reference_date)
        if date:
            return date
        
        # 2. Проверяем явную дату "число месяц" (20 января, двадцатое января).
        # Она приоритетнее дня недели в той же реплике
        # ("понедельник, 17 августа" -> 17 августа).
        date = self._parse_day_month(text_lower, reference_date)
        if date:
            return date

        # 3. Проверяем формат "число.месяц.год" или "число-месяц-год"
        date = self._parse_numeric_date(text_lower)
        if date:
            return date

        # 4. Проверяем дни недели (понедельник, вторник и т.д.).
        # Используем как менее точный сигнал, чем явная календарная дата.
        date = self._parse_weekday(text_lower, reference_date)
        if date:
            return date

        # 5. Проверяем формат "число" без месяца:
        # берём текущий месяц, если число ещё не прошло, иначе следующий.
        date = self._parse_day_without_month(text_lower, reference_date)
        if date:
            return date
        
        logger.debug(
            "Дата не найдена в тексте: '%s'",
            text[:100],
        )
        return None

    def parse_dates(
        self,
        text: str,
        reference_date: Optional[datetime] = None,
        *,
        slot_day9_hint: bool = False,
        slot_day10_hint: bool = False,
    ) -> list[datetime]:
        """
        Все даты из реплики: «18 или 19 июня», «семнадцатое или девятнадцатое июня».
        """
        if not text or not text.strip():
            return []
        if reference_date is None:
            from dialog.dealer_time import dealer_local_now_naive

            reference_date = dealer_local_now_naive()
        text_lower = text.lower().strip()
        parts = re.split(r"\s+или\s+", text_lower)
        if len(parts) <= 1:
            single = self.parse_date(
                text,
                reference_date,
                slot_day9_hint=slot_day9_hint,
                slot_day10_hint=slot_day10_hint,
            )
            return [single] if single else []

        month_match = self.month_pattern.search(text_lower)
        month_suffix = f" {month_match.group(1)}" if month_match else ""
        seen: set[str] = set()
        result: list[datetime] = []
        for part in parts:
            chunk = part.strip()
            if month_suffix and not self.month_pattern.search(chunk):
                chunk = f"{chunk}{month_suffix}"
            parsed = self.parse_date(
                chunk,
                reference_date,
                slot_day9_hint=slot_day9_hint,
                slot_day10_hint=slot_day10_hint,
            )
            if not parsed:
                continue
            key = parsed.strftime("%Y-%m-%d")
            if key in seen:
                continue
            seen.add(key)
            result.append(parsed)
        return result

    def parse_explicit_past_date(
        self,
        text: str,
        reference_date: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Явное «число + месяц», которое уже прошло и не относится к будущему году."""
        if not text or not text.strip():
            return None
        if reference_date is None:
            from dialog.dealer_time import dealer_local_now_naive

            reference_date = dealer_local_now_naive()
        from dialog.service_speech_parse import normalize_stt_booking_date_text

        low = normalize_stt_booking_date_text(text).lower().strip()
        if re.search(r"\bследующ\w*\s+год", low, re.IGNORECASE):
            return None
        year_match = re.search(r"\b(20\d{2})\b", low)
        explicit_year = int(year_match.group(1)) if year_match else reference_date.year
        if explicit_year > reference_date.year:
            return None
        for month_match in self.month_pattern.finditer(low):
            month_name = month_match.group(1).lower()
            month = self.MONTHS_GENITIVE.get(month_name) or self.MONTHS_NOMINATIVE.get(
                month_name
            )
            day = self._day_number_before_month(low[:month_match.start()])
            if month is None or day is None:
                continue
            try:
                candidate = datetime(explicit_year, month, day)
            except ValueError:
                continue
            if candidate.date() < reference_date.date():
                return candidate
        return None

    def _parse_relative_date(self, text: str, reference_date: datetime) -> Optional[datetime]:
        """Парсит относительные даты (сегодня, завтра и т.д.) с учетом опечаток."""
        import difflib
        
        # Сортируем ключевые слова по длине (от длинных к коротким),
        # чтобы "послезавтра" находилось раньше "завтра"
        sorted_keywords = sorted(self.RELATIVE_DATES.items(), key=lambda x: len(x[0]), reverse=True)
        
        words = text.replace(".", " ").replace(",", " ").split()
        for w in words:
            if len(w) < 4: continue
            matches = difflib.get_close_matches(w, [k for k, _ in sorted_keywords], n=1, cutoff=0.7)
            if matches:
                keyword = matches[0]
                days_offset = self.RELATIVE_DATES[keyword]
                result = reference_date + timedelta(days=days_offset)
                logger.info(f"Распознана относительная дата '{w}' как '{keyword}': {result.date()}")
                return result
        
        # Обычный поиск (для фраз)
        for keyword, days_offset in sorted_keywords:
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, text, re.IGNORECASE):
                result = reference_date + timedelta(days=days_offset)
                return result
        
        # Обработка "через N дней/недель"
        match = re.search(r"через\s+(\d+)\s+(день|дня|дней|недел[ию]|неделю|недели|недель)", text)
        if match:
            number = int(match.group(1))
            unit = match.group(2)
            if "недел" in unit:
                days_offset = number * 7
            else:
                days_offset = number
            result = reference_date + timedelta(days=days_offset)
            logger.info(
                "Найдена относительная дата 'через %d %s': %s",
                number,
                unit,
                result.strftime("%Y-%m-%d"),
            )
            return result
        
        return None
    
    def _parse_weekday(self, text: str, reference_date: datetime) -> Optional[datetime]:
        """Парсит день недели как ближайший следующий такой день."""
        from dialog.service_slot_day_part import extract_weekday_index

        target_weekday = extract_weekday_index(text)
        if target_weekday is None:
            return None

        current_weekday = reference_date.weekday()
        days_ahead = target_weekday - current_weekday

        if days_ahead <= 0:
            days_ahead += 7

        result = reference_date + timedelta(days=days_ahead)
        logger.info(
            "Найден день недели (index=%d): %s",
            target_weekday,
            result.strftime("%Y-%m-%d"),
        )
        return result
    
    def _day_number_before_month(self, text_before_month: str) -> Optional[int]:
        """Число дня перед названием месяца (прописью или цифрами)."""
        prefix = text_before_month.rstrip(" \t,.;:!?")
        number_word_match = re.search(
            r"(" + "|".join(
                re.escape(k) for k in sorted(self._day_words.keys(), key=len, reverse=True)
            ) + r")$",
            prefix,
            re.IGNORECASE,
        )
        if number_word_match:
            day = self._day_words.get(number_word_match.group(1).lower())
            if day and 1 <= day <= 31:
                return day
        number_match = re.search(
            r"(?<![\d:])(\d{1,2})(?:-го|-е|-ое)?$",
            prefix,
            re.IGNORECASE,
        )
        if number_match:
            day = int(number_match.group(1))
            if 1 <= day <= 31:
                return day
        return None

    def _parse_day_month(self, text: str, reference_date: datetime) -> Optional[datetime]:
        """Парсит формат 'число месяц' (20 января, двадцатое января)."""
        last_result: Optional[datetime] = None
        for month_match in self.month_pattern.finditer(text):
            month_name = month_match.group(1).lower()
            month = self.MONTHS_GENITIVE.get(month_name) or self.MONTHS_NOMINATIVE.get(month_name)
            if month is None:
                continue
            day = self._day_number_before_month(text[:month_match.start()])
            if day is None:
                continue
            year = reference_date.year
            if month < reference_date.month or (
                month == reference_date.month and day < reference_date.day
            ):
                year += 1
            try:
                last_result = datetime(year, month, day)
                logger.info(
                    "Найдена дата: %d %s %d",
                    day,
                    month_name,
                    year,
                )
            except ValueError:
                continue
        return last_result

    def _parse_day_without_month(
        self,
        text: str,
        reference_date: datetime,
    ) -> Optional[datetime]:
        """Парсит «двадцать восьмое [утро]» без месяца."""
        if self.month_pattern.search(text):
            return None

        day: Optional[int] = None
        word_match = self.number_word_pattern.search(text)
        if word_match:
            day = self._day_words.get(word_match.group(1).lower())
        if day is None:
            digit_match = self.number_pattern.search(text)
            if digit_match:
                candidate = int(digit_match.group(1))
                if 1 <= candidate <= 31:
                    day = candidate
        if day is None:
            return None

        year = reference_date.year
        month = reference_date.month
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            return None

        if candidate.date() < reference_date.date():
            month += 1
            if month > 12:
                month = 1
                year += 1
            try:
                candidate = datetime(year, month, day)
            except ValueError:
                return None

        logger.info(
            "Найдена дата без месяца: %d.%d.%d",
            candidate.day,
            candidate.month,
            candidate.year,
        )
        return candidate
    
    def _parse_numeric_date(self, text: str) -> Optional[datetime]:
        """Парсит числовые форматы дат (20.01.2025, 20-01-2025, 2025-01-20)."""
        # Формат DD.MM.YYYY или DD-MM-YYYY
        match = re.search(r"\b(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})\b", text)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1 <= day <= 31 and 1 <= month <= 12:
                try:
                    result = datetime(year, month, day)
                    logger.info(
                        "Найдена числовая дата: %d.%d.%d",
                        day,
                        month,
                        year,
                    )
                    return result
                except ValueError:
                    pass
        
        # Формат YYYY-MM-DD
        match = re.search(r"\b(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})\b", text)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1 <= day <= 31 and 1 <= month <= 12:
                try:
                    result = datetime(year, month, day)
                    logger.info(
                        "Найдена числовая дата (ISO): %d-%d-%d",
                        year,
                        month,
                        day,
                    )
                    return result
                except ValueError:
                    pass
        
        return None
    
    def format_date_for_speech(self, date: datetime) -> str:
        """
        Форматирует дату для произношения на русском языке.
        
        :param date: Объект datetime
        :return: Строка для произношения (например, "20 января")
        """
        day = date.day
        month_names = [
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря"
        ]
        month_name = month_names[date.month - 1]
        return f"{day} {month_name}"
    
    def extract_date_from_text(self, text: str) -> Optional[str]:
        """
        Извлекает дату из текста и возвращает в формате YYYY-MM-DD.
        
        :param text: Текст с датой
        :return: Дата в формате YYYY-MM-DD или None
        """
        date = self.parse_date(text)
        if date:
            return date.strftime("%Y-%m-%d")
        return None
