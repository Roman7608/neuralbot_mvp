"""
Извлечение имени клиента из транскрипта.
"""

import difflib
import logging
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Каталог имён — модуль в корне репозитория (как analyze_call_quality)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from russian_first_names_catalog import get_canonical_first_names


class NameExtractor:
    """Извлекает имя клиента из транскрипта речи."""
    
    # Паттерны для извлечения имени (в порядке приоритета)
    NAME_PATTERNS = [
        r"меня\s+зовут\s+([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)",  # "Меня зовут Иван"
        r"я\s+([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)",  # "Я Иван"
        r"это\s+([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)",  # "Это Иван"
        r"([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)\s+меня\s+зовут",  # "Иван меня зовут"
        r"мо[её]\s+имя\s+([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)",  # "Моё имя Иван"
        r"зовут\s+([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)",  # "Зовут Иван"
        # Паттерны для имени в начале предложения
        r"^([А-ЯЁа-яё]+)\s+я\b",  # "Катков я"
        r"^([А-ЯЁа-яё]+)\.",  # "Владимир." (с точкой)
        r"^([А-ЯЁа-яё]+)\s+([А-ЯЁа-яё]+)\s+([А-ЯЁа-яё]+)",  # "Иванов Иван Иванович"
        r"^([А-ЯЁа-яё]+)\s+([А-ЯЁа-яё]+)",  # "Владимир Катков"
        r"^([А-ЯЁа-яё]+)\b",  # Просто имя в начале (общий случай)
    ]
    
    # Стоп-слова (не являются именами)
    STOP_WORDS = {
        "добрый", "день", "вечер", "утро", "здравствуйте", "привет",
        "алло", "слушаю", "да", "нет", "хорошо", "спасибо", "пожалуйста",
        "хочу", "нужно", "интересует", "можно", "помогите", "помочь",
        "то", "пройти", "сделать", "записаться", "записать", "обслужить",
        "ремонт", "обслуживание", "замена", "покраска",
        "мне", "меня", "тебе", "вас", "имя", "зовут", "фамилия",
        "ниссан", "тойота", "хёндай", "чери", "тэнет", "тенет",
        "мерседес", "бмв", "ауди", "форд", "киа", "мазда", "субару",
        "пока", "до", "свидания", "прощай", "прощайте", "увидимся",
    }
    
    def __init__(self):
        # Компилируем паттерны один раз
        self.compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.NAME_PATTERNS]
        # Единый справочник: data/russian_first_names.txt (+ опционально data/russian_male_names.txt)
        self.common_names: list[str] = list(get_canonical_first_names())
        logger.info("NameExtractor инициализирован (имён в каталоге: %d)", len(self.common_names))
    
    def extract_name(self, transcript: str) -> Optional[str]:
        """Извлекает имя клиента с учетом возможных опечаток."""
        if not transcript: return None
        text = transcript.strip()
        simple_text = re.sub(r"[^\wа-яёА-ЯЁ\- ]+", " ", text, flags=re.UNICODE).strip()
        
        # 1. Поиск по паттернам
        found_name = None
        surname_endings = ('ов', 'ев', 'ин', 'ын', 'ий', 'ый', 'ая', 'яя', 'их', 'ых', 'енко')
        
        two_words_pattern = re.compile(r"([А-ЯЁа-яё]+)\s+([А-ЯЁа-яё]+)", re.IGNORECASE)
        match_two = two_words_pattern.search(text)
        if match_two:
            word1, word2 = match_two.group(1), match_two.group(2)
            if word1.lower() != "я" and word2.lower() != "я":
                if word1.lower().endswith(surname_endings) and not word2.lower().endswith(surname_endings):
                    found_name = word2.capitalize()
                elif word2.lower().endswith(surname_endings) and not word1.lower().endswith(surname_endings):
                    found_name = word1.capitalize()
        
        common_names = self.common_names
        name_fixes = {
            "аман": "Роман",
            "проман": "Роман",
            "романн": "Роман",
            "романнн": "Роман",
            "романннн": "Роман",
            "сюдер": "Сидор",  # типичная ошибка STT «Сидор» → «Сюдер»
            "машка": "Мария",
            "мохаммед": "Мухаммад",
            "мухаммед": "Мухаммад",
            # Не трогаем имя «Алик» — оставляем как есть, не нормализуем в «Али»
            "алик": "Алик",
            # «Карен» (арм.) в справочнике рядом с «Карина»; при cutoff=0.72 difflib путал с «Кариной».
            "карен": "Карен",
        }

        def _normalize_name(candidate: str) -> Optional[str]:
            if not candidate:
                return None
            cand = candidate.strip()
            if not cand:
                return None
            low = cand.lower()
            if low in name_fixes:
                return name_fixes[low]
            matches = difflib.get_close_matches(cand.capitalize(), common_names, n=1, cutoff=0.72)
            if matches:
                if matches[0].lower() != low:
                    logger.info(f"Имя '{candidate}' нормализовано как '{matches[0]}'")
                return matches[0]
            # Если подходящего имени не нашли и кандидат очень короткий (например, "Ох") — считаем, что это не имя
            if len(cand) < 3:
                return None
            return cand.capitalize()

        # Короткий ответ из одного-двух слов часто приходит в нижнем регистре от STT.
        if not found_name and simple_text:
            simple_words = [w for w in simple_text.split() if w and w.lower() not in self.STOP_WORDS]
            if 1 <= len(simple_words) <= 2:
                candidate = _normalize_name(simple_words[0])
                if candidate and candidate.lower() not in self.STOP_WORDS:
                    logger.info(f"Короткий ответ '{transcript}' интерпретирован как имя '{candidate}'")
                    found_name = candidate

        if not found_name:
            for pattern in self.compiled_patterns:
                match = pattern.search(text)
                if match:
                    name_part = match.group(1).strip()
                    name_words = name_part.split()
                    if name_words and name_words[0].lower() not in self.STOP_WORDS:
                        found_name = _normalize_name(name_words[0])
                        break
        
        # 2. Если имя найдено, нормализуем его
        if found_name:
            found_name = _normalize_name(found_name)
            return found_name

        # 3. Если имя не найдено, ищем в расширенном списке частых имен
        words = text.replace(".", " ").replace(",", " ").split()
        for w in words:
            w_clean = w.capitalize()
            if len(w_clean) < 4: continue
            matches = difflib.get_close_matches(w_clean, common_names, n=1, cutoff=0.8)
            if matches:
                logger.info(f"Имя '{w}' распознано как '{matches[0]}'")
                return matches[0]
        
        return None
    
    def extract_fio(self, transcript: str) -> Optional[dict]:
        """Извлекает ФИО (Фамилия Имя Отчество)."""
        if not transcript or not transcript.strip():
            return None
        
        # Ищем 2-4 слова с большой буквы в начале или середине
        fio_pattern = r"([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?(?:\s+([А-ЯЁ][а-яё]+))?"
        match = re.search(fio_pattern, transcript)
        
        if match:
            surname = match.group(1)
            name = match.group(2)
            patronymic_part = match.group(3) or ""
            patronymic_tail = match.group(4) or ""
            patronymic = f"{patronymic_part} {patronymic_tail}".strip()
            
            # Проверка на стоп-слова
            if surname.lower() in self.STOP_WORDS or name.lower() in self.STOP_WORDS:
                return None

            # Нормализация отчества
            if patronymic:
                patronymic = self._normalize_patronymic(patronymic)
                
            return {
                "surname": surname,
                "name": name,
                "patronymic": patronymic
            }
        return None

    def _normalize_patronymic(self, patronymic: str) -> str:
        """Нормализует отчество с учетом опечаток."""
        import difflib
        if not patronymic:
            return ""
        raw = patronymic.strip()
        if not raw:
            return ""
        low = raw.lower().replace(" ", "")

        patronymic_fixes = {
            "валерийявич": "Валериевич",
            "валерийевич": "Валериевич",
            "сергеевич": "Сергеевич",
            "александрович": "Александрович",
            "андреевич": "Андреевич",
            "николаевич": "Николаевич",
            "михайлович": "Михайлович",
            "иванович": "Иванович",
            "петрович": "Петрович",
            "игоревич": "Игоревич",
        }
        if low in patronymic_fixes:
            return patronymic_fixes[low]

        # Если отчество указано двумя словами, пробуем склеить по правилам
        parts = patronymic.strip().split()
        if len(parts) == 2:
            first = parts[0].lower()
            second = parts[1].lower()
            if second.endswith(("ич", "вич", "евич", "ович", "явич")):
                if first.endswith("ий"):
                    return (first[:-2] + "иевич").capitalize()
                if first.endswith("ей"):
                    return (first[:-2] + "еевич").capitalize()
                if first.endswith("ай"):
                    return (first[:-2] + "аевич").capitalize()
                if first.endswith("ь"):
                    return (first[:-1] + "евич").capitalize()

        common_patronymics = [
            "Александрович", "Алексеевич", "Андреевич", "Антонович", "Анатольевич",
            "Борисович", "Валериевич", "Валентинович", "Васильевич", "Викторович",
            "Геннадиевич", "Георгиевич", "Григорьевич", "Дмитриевич", "Евгеньевич",
            "Иванович", "Игоревич", "Леонидович", "Михайлович", "Николаевич",
            "Олегович", "Павлович", "Петрович", "Романович", "Сергеевич",
            "Фёдорович", "Юрьевич",
        ]
        matches = difflib.get_close_matches(raw.capitalize(), common_patronymics, n=1, cutoff=0.75)
        if matches:
            logger.info(f"Отчество '{patronymic}' нормализовано как '{matches[0]}'")
            return matches[0]

        return raw.capitalize()

    def extract_phone(self, transcript: str) -> Optional[str]:
        """Извлекает номер телефона."""
        if not transcript: return None
        nums = re.sub(r"\D", "", transcript)
        if not nums:
            # Пробуем извлечь номер из слов (например: "плюс семь девятьсот два тридцать семь три ноль восемь ноль восемь")
            words = re.split(r"[\s,\.]+", transcript.lower())
            ones = {
                "ноль": "0", "нуль": "0",
                "один": "1", "одна": "1", "одну": "1",
                "два": "2", "две": "2",
                "три": "3",
                "четыре": "4",
                "пять": "5",
                "шесть": "6",
                "семь": "7",
                "восемь": "8",
                "девять": "9",
            }
            tens = {
                "десять": "10",
                "одиннадцать": "11",
                "двенадцать": "12",
                "тринадцать": "13",
                "четырнадцать": "14",
                "пятнадцать": "15",
                "шестнадцать": "16",
                "семнадцать": "17",
                "восемнадцать": "18",
                "девятнадцать": "19",
                "двадцать": "20",
                "тридцать": "30",
                "сорок": "40",
                "пятьдесят": "50",
                "шестьдесят": "60",
                "семьдесят": "70",
                "восемьдесят": "80",
                "девяносто": "90",
            }
            hundreds = {
                "сто": "100",
                "двести": "200",
                "триста": "300",
                "четыреста": "400",
                "пятьсот": "500",
                "шестьсот": "600",
                "семьсот": "700",
                "восемьсот": "800",
                "девятьсот": "900",
            }
            digits = []
            for w in words:
                if w in ("плюс", "+"):
                    continue
                if w in hundreds:
                    digits.append(hundreds[w])
                    continue
                if w in tens:
                    digits.append(tens[w])
                    continue
                if w in ones:
                    digits.append(ones[w])
                    continue
            nums = "".join(digits)
        # Разрешаем номера от 7 до 11 цифр (для гибкости)
        if 7 <= len(nums) <= 12:
            if len(nums) == 10: return "+7" + nums
            if nums.startswith("8") and len(nums) == 11: return "+7" + nums[1:]
            if nums.startswith("7") and len(nums) == 11: return "+" + nums
            # Для 9 цифр (как в примере пользователя) или других форматов
            if len(nums) == 9: return "+7" + nums
            return "+" + nums
        return None
