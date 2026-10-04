"""
Менеджер для работы с базами данных.

- norma.xlsx — трудоёмкости и цены ТО (постоянный Excel)
- clientsbase.xlsx — база клиентов (Excel-fallback, заменяется 1С Альфа)
- slot.xlsx — расписание слотов (Excel-fallback, заменяется 1С Альфа)

Async-методы (*_async) пробуют 1С API, при недоступности — Excel.
"""

import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Менеджер для работы с Excel базами данных."""
    
    def __init__(
        self,
        clientsbase_path: Path,
        norma_path: Path,
        slot_path: Path,
    ):
        """
        Инициализирует менеджер баз данных.
        
        :param clientsbase_path: Путь к clientsbase.xlsx
        :param norma_path: Путь к norma.xlsx
        :param slot_path: Путь к slot.xlsx
        """
        self.clientsbase_path = Path(clientsbase_path)
        self.norma_path = Path(norma_path)
        self.slot_path = Path(slot_path)
        
        # Кэш для загруженных данных
        self._clients_cache: Optional[pd.DataFrame] = None
        self._norma_cache: Optional[pd.DataFrame] = None
        self._slot_cache: Optional[pd.DataFrame] = None
        
        logger.info(
            "Инициализирован DatabaseManager (clientsbase=%s, norma=%s, slot=%s)",
            self.clientsbase_path,
            self.norma_path,
            self.slot_path,
        )
    
    def _load_clientsbase(self) -> pd.DataFrame:
        """Загружает базу клиентов."""
        if self._clients_cache is not None:
            return self._clients_cache
        
        try:
            if not self.clientsbase_path.exists():
                logger.warning(
                    "Файл clientsbase.xlsx не найден: %s. Создаю пустую базу.",
                    self.clientsbase_path,
                )
                # Создаем пустую базу с ожидаемыми колонками
                # Но не создаем файл автоматически - пусть пользователь создаст его вручную
                logger.warning(
                    "Файл clientsbase.xlsx не найден. Создайте его вручную с нужными колонками."
                )
                return pd.DataFrame()
            
            df = pd.read_excel(self.clientsbase_path)
            self._clients_cache = df
            logger.info(
                "Загружена база клиентов: %d записей",
                len(df),
            )
            return df
        except Exception as e:
            logger.error(
                "Ошибка загрузки clientsbase.xlsx: %s",
                e,
                exc_info=True,
            )
            return pd.DataFrame()
    
    def _load_norma(self) -> pd.DataFrame:
        """Загружает базу трудоемкости."""
        if self._norma_cache is not None:
            return self._norma_cache
        
        try:
            if not self.norma_path.exists():
                logger.warning(
                    "Файл norma.xlsx не найден: %s. Создаю пустую базу.",
                    self.norma_path,
                )
                df = pd.DataFrame(columns=["Работа", "Нормочасы"])
                df.to_excel(self.norma_path, index=False)
                return df
            
            df = pd.read_excel(self.norma_path)
            self._norma_cache = df
            logger.info(
                "Загружена база трудоемкости: %d записей",
                len(df),
            )
            return df
        except Exception as e:
            logger.error(
                "Ошибка загрузки norma.xlsx: %s",
                e,
                exc_info=True,
            )
            return pd.DataFrame()
    
    def _load_slot(self) -> pd.DataFrame:
        """Загружает расписание слотов."""
        if self._slot_cache is not None:
            return self._slot_cache
        
        try:
            if not self.slot_path.exists():
                logger.warning(
                    "Файл slot.xlsx не найден: %s. Создаю пустую базу.",
                    self.slot_path,
                )
                # Создаем пустую базу с колонками: Дата, Время, Пост, Занято
                df = pd.DataFrame(columns=["Дата", "Время", "Пост", "Занято"])
                df.to_excel(self.slot_path, index=False)
                return df
            
            df = pd.read_excel(self.slot_path)
            self._slot_cache = df
            logger.info(
                "Загружено расписание: %d записей",
                len(df),
            )
            return df
        except Exception as e:
            logger.error(
                "Ошибка загрузки slot.xlsx: %s",
                e,
                exc_info=True,
            )
            return pd.DataFrame()
    
    def find_client(
        self,
        fio: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Ищет клиента в базе по ФИО и/или телефону.
        
        :param fio: ФИО клиента
        :param phone: Номер телефона
        :return: Словарь с данными клиента или None
        """
        df = self._load_clientsbase()
        if df.empty:
            return None
        
        available_columns = df.columns.tolist()
        has_contragent = "Контрагент" in available_columns
        has_surname = "Фамилия" in available_columns
        has_name_col = "Имя" in available_columns
        # Ищем колонку телефона без учета регистра
        phone_col = None
        for col in available_columns:
            if str(col).strip().lower() == "телефон":
                phone_col = col
                break
        has_phone_col = phone_col is not None
        
        # 1. Поиск по телефону (самый надежный)
        if phone:
            phone_normalized = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("+", "")
            if phone_normalized.startswith("7"): phone_normalized = phone_normalized[1:]
            if phone_normalized.startswith("8"): phone_normalized = phone_normalized[1:]
            
            if has_phone_col:
                try:
                    df_phone_normalized = df[phone_col].astype(str).str.replace(r"\D", "", regex=True)
                    # Ищем совпадение последних 9-10 цифр
                    search_pattern = phone_normalized[-9:] if len(phone_normalized) >= 9 else phone_normalized
                    mask = df_phone_normalized.str.endswith(search_pattern, na=False)
                    matches = df[mask]
                    if not matches.empty:
                        logger.info(f"Клиент найден по телефону: {phone} (нормализован: {phone_normalized})")
                        return matches.iloc[0].to_dict()
                    else:
                        logger.debug(f"Клиент не найден по телефону: {phone} (нормализован: {phone_normalized}, паттерн: {search_pattern})")
                except Exception as e:
                    logger.warning(f"Ошибка поиска по телефону: {e}")
            else:
                # Поиск по телефону в любых строковых колонках (если колонки Телефон нет)
                try:
                    search_pattern = phone_normalized[-9:] if len(phone_normalized) >= 9 else phone_normalized
                    def _row_has_phone(row):
                        for val in row.astype(str).tolist():
                            digits = re.sub(r"\D", "", val)
                            if digits.endswith(search_pattern):
                                return True
                        return False
                    mask = df.apply(_row_has_phone, axis=1)
                    matches = df[mask]
                    if not matches.empty:
                        logger.info(f"Клиент найден по телефону (скан всех полей): {phone}")
                        return matches.iloc[0].to_dict()
                except Exception as e:
                    logger.warning(f"Ошибка поиска телефона по всем полям: {e}")

        # 2. Поиск по ФИО (гибкий)
        if fio:
            fio_words = set(fio.lower().split())
            if len(fio_words) >= 2:
                try:
                    # Приоритет: поиск по колонке "Контрагент" (если есть)
                    if has_contragent:
                        import difflib
                        def match_contragent(row):
                            contragent_text = str(row.get("Контрагент", "")).lower()
                            if not contragent_text or contragent_text == "nan":
                                return False
                            contragent_words = contragent_text.split()
                            fio_words_list = list(fio_words)
                            
                            # Точное совпадение слов
                            common_words = fio_words.intersection(set(contragent_words))
                            if len(common_words) >= 2:
                                return True
                            
                            # Fuzzy matching для фамилий (первое слово обычно фамилия)
                            if len(fio_words_list) > 0 and len(contragent_words) > 0:
                                fio_surname = fio_words_list[0]
                                db_surname = contragent_words[0]
                                # Проверяем схожесть фамилий (для опечаток типа "Казинков" vs "Козенков")
                                similarity = difflib.SequenceMatcher(None, fio_surname, db_surname).ratio()
                                if similarity >= 0.7:  # 70% схожести
                                    # Если фамилии похожи, проверяем остальные слова
                                    if len(fio_words_list) > 1 and len(contragent_words) > 1:
                                        # Проверяем имя (второе слово)
                                        fio_name = fio_words_list[1] if len(fio_words_list) > 1 else ""
                                        db_name = contragent_words[1] if len(contragent_words) > 1 else ""
                                        if fio_name and db_name:
                                            name_similarity = difflib.SequenceMatcher(None, fio_name, db_name).ratio()
                                            if name_similarity >= 0.7:
                                                return True
                                    else:
                                        # Если только фамилия похожа и достаточно длинная, считаем совпадением
                                        if len(fio_surname) >= 5 and similarity >= 0.8:
                                            return True
                            
                            return False
                        
                        mask = df.apply(match_contragent, axis=1)
                        matches = df[mask]
                        if not matches.empty:
                            logger.info(f"Клиент найден по ФИО (Контрагент): {fio}")
                            return matches.iloc[0].to_dict()
                    
                    # Fallback: поиск по отдельным колонкам (если есть)
                    if has_surname or has_name_col:
                        def match_fio(row):
                            row_text = ""
                            if has_surname: row_text += str(row.get("Фамилия", "")).lower() + " "
                            if has_name_col: row_text += str(row.get("Имя", "")).lower() + " "
                            if "Отчество" in available_columns: row_text += str(row.get("Отчество", "")).lower()
                            
                            row_words = set(row_text.split())
                            # Если все слова из запроса есть в строке (или наоборот)
                            return fio_words.issubset(row_words) or row_words.issubset(fio_words)

                        mask = df.apply(match_fio, axis=1)
                        matches = df[mask]
                        if not matches.empty:
                            logger.info(f"Клиент найден по ФИО: {fio}")
                            return matches.iloc[0].to_dict()
                except Exception as e:
                    logger.warning(f"Ошибка гибкого поиска по ФИО: {e}")
        
        return None
    
    def calculate_work_hours(self, work_list: str | None) -> float:
        """
        Рассчитывает трудоемкость работ в нормочасах по базе norma.xlsx.
        """
        if not work_list:
            return 1.0
        df = self._load_norma()
        if df.empty: return 2.0
        
        total_hours = 0.0
        work_list_lower = work_list.lower()
        
        # Ищем совпадения в базе (по частичному вхождению названия работы)
        found_any = False
        for _, row in df.iterrows():
            work_name = str(row.get("Наименование работ", row.get("Работа", ""))).lower()
            if work_name and work_name in work_list_lower:
                try:
                    # Извлекаем часы (могут быть в разных колонках)
                    hours = float(row.get("Норма времени", row.get("Нормочасы", 0)))
                    total_hours += hours
                    found_any = True
                except: continue
        
        if not found_any: return 2.0 # Дефолт если ничего не нашли
        return max(total_hours, 0.5)
    
    def find_available_slot(
        self,
        desired_date: str,
        required_hours: float,
        day_part: Optional[str] = None,
        start_time: Optional[str] = None,
    ) -> Optional[Tuple[str, str, int]]:
        """
        Ищет свободный слот в многолистовом Excel.
        desired_date: "YYYY-MM-DD"
        """
        try:
            xl = pd.ExcelFile(self.slot_path)
            # Преобразуем YYYY-MM-DD в DD.MM.YYYY для поиска листа
            dt_obj = datetime.strptime(desired_date, "%Y-%m-%d")
            sheet_name = dt_obj.strftime("%d.%m.%Y")
            
            if sheet_name not in xl.sheet_names:
                logger.warning(f"Лист {sheet_name} не найден в slot.xlsx")
                # Fallback с учетом времени суток
                if day_part == "afternoon":
                    return (desired_date, "13:00", 1)
                elif day_part == "evening":
                    return (desired_date, "16:00", 1)
                elif day_part == "morning":
                    return (desired_date, "09:00", 1)
                return (desired_date, "10:00", 1) # Fallback по умолчанию
            
            df = pd.read_excel(self.slot_path, sheet_name=sheet_name)
            
            # В вашем файле структура такая:
            #   Пост 1 - Архипов | Unnamed: 1 | ...
            # 0 Время            | Примечание | ...
            # 1 09:00            | NaN        | ...
            
            # Ищем колонки с временем
            time_col = None
            for col in df.columns:
                if "Пост" in str(col) or "Время" in str(df[col].iloc[0]):
                    time_col = col
                    break
            
            if time_col is None:
                # Fallback с учетом времени суток
                if day_part == "afternoon":
                    return (desired_date, "13:00", 1)
                elif day_part == "evening":
                    return (desired_date, "16:00", 1)
                elif day_part == "morning":
                    return (desired_date, "09:00", 1)
                return (desired_date, "10:00", 1)

            # Размер шага слота (предполагаем 15 минут)
            slot_minutes = 15
            required_minutes = max(required_hours * 60.0, slot_minutes)
            slots_needed = max(1, math.ceil(required_minutes / slot_minutes))

            start_minutes = None
            if start_time:
                try:
                    h_s, m_s = map(int, start_time.split(":")[:2])
                    start_minutes = h_s * 60 + m_s
                except Exception:
                    start_minutes = None

            def _time_in_day_part(h: int) -> bool:
                if day_part == "morning":
                    return 8 <= h < 12
                if day_part == "lunch":
                    return 12 <= h < 14
                if day_part in ("afternoon", "after_lunch"):
                    return 14 <= h < 17
                if day_part == "evening":
                    return 17 <= h < 20
                return True

            # Сначала ищем непрерывный блок свободных ячеек нужной длины
            for i in range(1, len(df)):
                time_val = str(df[time_col].iloc[i])
                if ":" not in time_val:
                    continue
                try:
                    hour, minute = map(int, time_val.split(":")[:2])
                except Exception:
                    continue
                if not _time_in_day_part(hour):
                    continue
                if start_minutes is not None and (hour * 60 + minute) < start_minutes:
                    continue

                start_idx = i
                end_idx = i + slots_needed
                if end_idx > len(df):
                    continue

                all_free = True
                for j in range(start_idx, end_idx):
                    note_val_j = str(df.iloc[j, 1]).lower()
                    if note_val_j not in ("nan", ""):
                        all_free = False
                        break
                if all_free:
                    return (desired_date, time_val, 1)

            # Если подходящий блок не найден, делаем простой fallback
            for i in range(1, len(df)):
                time_val = str(df[time_col].iloc[i])
                if ":" in time_val:
                    try:
                        hour, minute = map(int, time_val.split(":")[:2])
                    except Exception:
                        continue
                    if not _time_in_day_part(hour):
                        continue
                    if start_minutes is not None and (hour * 60 + minute) < start_minutes:
                        continue
                    note_val = str(df.iloc[i, 1]).lower()
                    if note_val in ("nan", ""):
                        return (desired_date, time_val, 1)
            
            # Fallback с учетом времени суток, если ничего не найдено
            if day_part == "afternoon":
                return (desired_date, "13:00", 1)
            elif day_part == "evening":
                return (desired_date, "16:00", 1)
            elif day_part == "morning":
                return (desired_date, "09:00", 1)
            return (desired_date, "10:00", 1)
        except Exception as e:
            logger.error(f"Ошибка find_available_slot: {e}")
            return (desired_date, "10:00", 1)

    def book_slot(
        self,
        date: str,
        time: str,
        post: int,
        hours: float,
        client_data: Dict,
    ) -> bool:
        """
        Реально записывает данные в slot.xlsx, обходя ограничение на запись в объединенные ячейки.
        """
        try:
            import openpyxl
            from openpyxl.utils import range_boundaries
            
            # 1. Преобразуем дату в имя листа
            dt_obj = datetime.strptime(date, "%Y-%m-%d")
            sheet_name = dt_obj.strftime("%d.%m.%Y")
            
            # 2. Открываем книгу через openpyxl
            wb = openpyxl.load_workbook(self.slot_path)
            if sheet_name not in wb.sheetnames:
                logger.warning(f"Лист {sheet_name} не найден")
                return False
                
            ws = wb[sheet_name]
            
            # 3. Ищем строку с временем
            target_row = None
            target_col = 1 # Обычно время в первой колонке
            
            for row in range(1, ws.max_row + 1):
                cell_val = str(ws.cell(row=row, column=target_col).value)
                if time in cell_val:
                    target_row = row
                    break
            
            if not target_row:
                # Пробуем искать во второй колонке
                target_col = 2
                for row in range(1, ws.max_row + 1):
                    cell_val = str(ws.cell(row=row, column=target_col).value)
                    if time in cell_val:
                        target_row = row
                        break

            if target_row:
                # Подготовим значения
                fio_val = client_data.get("fio")
                phone_val = client_data.get("phone")
                car_val = client_data.get("car")
                work_val = client_data.get("work_list")

                def _find_col(header_name: str) -> Optional[int]:
                    header_name_low = header_name.strip().lower()
                    max_row = min(ws.max_row, 3)
                    for r in range(1, max_row + 1):
                        for c in range(1, ws.max_column + 1):
                            cell_val = ws.cell(row=r, column=c).value
                            if cell_val is None:
                                continue
                            if str(cell_val).strip().lower() == header_name_low:
                                return c
                    return None

                def _set_cell_value(row: int, col: int, value: str | None) -> None:
                    if col is None:
                        return
                    cell = ws.cell(row=row, column=col)
                    # Проверяем объединенные ячейки
                    for merged_range in ws.merged_cells.ranges:
                        if cell.coordinate in merged_range:
                            min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
                            cell = ws.cell(row=min_row, column=min_col)
                            break
                    if value is not None:
                        cell.value = value

                # Ищем колонки по заголовкам
                col_client = _find_col("Клиент")
                col_phone = _find_col("Телефон")
                col_car = _find_col("Автомобиль")
                col_note = _find_col("Примечание")

                # Fallback: колонка примечания обычно следующая за временем
                if col_note is None:
                    col_note = target_col + 1

                # Количество слотов, которые нужно занять (по 15 минут каждый)
                slot_minutes = 15
                required_minutes = max(hours * 60.0, slot_minutes)
                slots_needed = max(1, math.ceil(required_minutes / slot_minutes))
                
                for offset in range(slots_needed):
                    r = target_row + offset
                    if r > ws.max_row:
                        break
                    _set_cell_value(r, col_client, fio_val)
                    _set_cell_value(r, col_phone, phone_val)
                    _set_cell_value(r, col_car, car_val)
                    _set_cell_value(r, col_note, work_val)
                wb.save(self.slot_path)
                logger.info(f"Успешная запись в {sheet_name} на {time}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Ошибка записи в Excel: {e}")
            return False
    
    def add_booking(
        self,
        date: str,
        time: str,
        fio: str,
        car: str,
        work_list: str,
        phone: Optional[str] = None,
        day_part: Optional[str] = None,
    ) -> bool:
        """
        Удобный метод для добавления бронирования.
        Автоматически рассчитывает время и ищет свободный слот.
        """
        try:
            # 1. Считаем часы и добавляем 30 минут на оформление/форс-мажор
            hours = self.calculate_work_hours(work_list)
            hours_with_buffer = hours + 0.5
            
            # 2. Ищем свободный слот на эту дату (или ближайший) с учётом времени суток
            slot = self.find_available_slot(date, hours_with_buffer, day_part)
            if not slot:
                logger.warning(f"Не удалось найти свободный слот на {date}")
                return False
                
            slot_date, slot_time, slot_post = slot
            
            # 3. Бронируем
            client_data = {"fio": fio, "phone": phone, "car": car, "work_list": work_list}
            return self.book_slot(slot_date, slot_time, slot_post, hours_with_buffer, client_data)
        except Exception as e:
            logger.error(f"Ошибка в add_booking: {e}")
            return False

    def clear_cache(self) -> None:
        """Очищает кэш загруженных данных."""
        self._clientsbase_cache = None
        self._norma_cache = None
        self._slot_cache = None
        logger.info("Кэш баз данных очищен")

    # ————— Async-обёртки: 1С API → Excel fallback —————

    async def find_client_async(
        self, fio: Optional[str] = None, phone: Optional[str] = None,
    ) -> Optional[Dict]:
        """Поиск клиента только в 1С (HTTP). Без clientsbase.xlsx."""
        try:
            from telegram_bot.services.ics_alfa_service import ICSAlfaService
            svc = ICSAlfaService()
            if svc.initialized:
                result = await svc.find_client(fio or "", phone or "")
                await svc.close()
                if result:
                    label = (
                        result.get("Контрагент")
                        or result.get("client_fio")
                        or result.get("fio")
                        or fio
                    )
                    logger.info("Клиент найден через 1С: %s", label)
                    return result
            else:
                logger.warning("1С HTTP URL не задан — поиск клиента пропущен")
        except Exception as exc:
            logger.warning("1С find_client недоступна: %s", exc)
        return None

    async def find_available_slot_async(
        self,
        desired_date: str,
        required_hours: float,
        day_part: Optional[str] = None,
        start_time: Optional[str] = None,
    ) -> Optional[Tuple]:
        """Поиск слота: сначала 1С, потом slot.xlsx."""
        try:
            from telegram_bot.services.ics_alfa_service import ICSAlfaService
            svc = ICSAlfaService()
            if svc.initialized:
                from datetime import date as date_cls
                from telegram_bot.services.ics_alfa_service import slots_date_to_exclusive

                duration_min = int(required_hours * 60)
                day = date_cls.fromisoformat(desired_date)
                slots = await svc.get_service_slots(
                    date_from=desired_date,
                    date_to=slots_date_to_exclusive(day, 1),
                    duration_min=duration_min,
                )
                await svc.close()
                if slots:
                    slot = slots[0]
                    from dialog.service_slot_time import slot_time_hhmm_from_raw

                    slot_time = slot_time_hhmm_from_raw(slot)
                    logger.info("Слот из 1С: %s %s", slot.get("date"), slot_time)
                    return (
                        slot["date"],
                        slot_time,
                        slot.get("post_id", "1"),
                        slot.get("acceptor_id", ""),
                    )
        except Exception as exc:
            logger.debug("1С get_service_slots недоступна: %s", exc)
        excel_slot = self.find_available_slot(desired_date, required_hours, day_part, start_time)
        if excel_slot:
            slot_date, slot_time, slot_post = excel_slot
            return slot_date, slot_time, slot_post, ""
        return None
