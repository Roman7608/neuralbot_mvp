"""
Интеграция с 1С Альфа 6.0 через HTTP-сервисы.

Эндпоинты 1С (настраиваются программистом 1С):
  GET  /client/search?phone=...&fio=...
  POST /telegram/lead
  GET  /service/slots?date_from=...&date_to=...&duration_min=...
    date_to — начало дня после последнего включённого (exclusive); 1 день: date_to = date_from + 1 сутки
  GET  /service/appointments?desired_date=...&fio=...&phone=...&desired_time=...&duration_min=...&post_id=...&acceptor_id=...
  GET  /service/appointments?date_from=...&date_to=...  (список записей)
  POST /service/appointments/cancel
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List

import aiohttp

from telegram_bot_config import (
    ICS_ALFA_API_KEY,
    ICS_ALFA_HTTP_PASSWORD,
    ICS_ALFA_HTTP_URL,
    ICS_ALFA_HTTP_USER,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class ICSAlfaServiceError(RuntimeError):
    """Техническая ошибка HTTP-сервиса 1С (не равна пустому списку слотов)."""


def slots_date_to_exclusive(date_from: date, days_inclusive: int = 1) -> str:
    """Верхняя граница date_to для 1С /service/slots: начало дня после последнего включённого."""
    return (date_from + timedelta(days=max(days_inclusive, 1))).strftime("%Y-%m-%d")


class ICSAlfaService:
    """HTTP-клиент к 1С Альфа 6.0."""

    def __init__(self):
        self._base_url = ICS_ALFA_HTTP_URL.rstrip("/")
        self._headers = {"Content-Type": "application/json; charset=utf-8"}
        if ICS_ALFA_API_KEY:
            self._headers["X-API-Key"] = ICS_ALFA_API_KEY
        # 1С HTTP-сервис требует Basic auth (WWW-Authenticate: Basic). Если заданы user/password —
        # передаём их в aiohttp.BasicAuth; X-API-Key оставляем дополнительно для совместимости.
        # encoding='utf-8' критично: логин «Администратор» (кириллица) в latin-1 не лезет.
        self._auth: Optional[aiohttp.BasicAuth] = (
            aiohttp.BasicAuth(ICS_ALFA_HTTP_USER, ICS_ALFA_HTTP_PASSWORD, encoding="utf-8")
            if ICS_ALFA_HTTP_USER
            else None
        )
        self.initialized = bool(self._base_url)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=_REQUEST_TIMEOUT,
                headers=self._headers,
                auth=self._auth,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            logger.info("1С GET %s params=%s", url, params or {})
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                body = await resp.text()
                logger.warning("1С GET %s → %d: %s", path, resp.status, body[:300])
                if resp.status >= 400:
                    return {"success": False, "http_status": resp.status, "error_body": body[:500]}
                return None
        except Exception as exc:
            logger.error("1С GET %s ошибка: %s", path, exc)
            return None

    async def _post(self, path: str, data: dict) -> Optional[dict]:
        try:
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            async with session.post(url, json=data) as resp:
                result = await resp.json(content_type=None)
                if resp.status in (200, 201):
                    return result
                logger.warning("1С POST %s → %d: %s", path, resp.status, result)
                return result
        except Exception as exc:
            logger.error("1С POST %s ошибка: %s", path, exc)
            return None

    @staticmethod
    def _client_row_from_search_json(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Приводит ответ GET /client/search к словарю полей, совместимому с _process_client_from_db."""
        inner = result.get("client")
        if isinstance(inner, dict) and inner:
            return inner
        fio_val = (
            str(result.get("client_fio") or result.get("fio") or "").strip()
        )
        phone_val = str(result.get("client_phone") or result.get("phone") or "").strip()
        cars = result.get("cars")
        if not isinstance(cars, list):
            cars = []
        first: Dict[str, Any] = cars[0] if cars else {}
        brand = str(
            first.get("brand")
            or first.get("Марка")
            or first.get("mark")
            or ""
        ).strip()
        model = str(
            first.get("model")
            or first.get("Модель")
            or first.get("model_name")
            or ""
        ).strip()
        row: Dict[str, Any] = {}
        if fio_val:
            row["Контрагент"] = fio_val
        if phone_val:
            row["Телефон"] = phone_val
        if brand:
            row["Марка"] = brand
        if model:
            row["Модель"] = model
        if first.get("year") is not None:
            row["Год"] = first.get("year")
        cid = result.get("client_id") or result.get("id")
        if cid is not None:
            row["id"] = str(cid)
        if row:
            return row
        return None

    async def find_client(self, fio: str, phone: str) -> Optional[Dict[str, Any]]:
        """Поиск клиента в базе 1С."""
        result = await self._get("/client/search", {"phone": phone, "fio": fio})
        if not result or not result.get("found"):
            return None
        return self._client_row_from_search_json(result)

    async def create_notification(
        self,
        fio: str,
        phone: str,
        need: str,
        department: str,
        need_text: str,
        telegram_user_id: int,
        car_brand: Optional[str] = None,
        car_model: Optional[str] = None,
        car_year: Optional[str] = None,
        car_mileage: Optional[str] = None,
        work_wishes: Optional[str] = None,
    ) -> bool:
        """Создаёт лид/уведомление в 1С."""
        payload = {
            "fio": fio,
            "phone": phone,
            "need_type": need,
            "department": department,
            "need_text": need_text,
            "telegram_user_id": telegram_user_id,
        }
        if car_brand:
            payload["car_brand"] = car_brand
        if car_model:
            payload["car_model"] = car_model
        if car_year:
            payload["car_year"] = car_year
        if car_mileage:
            payload["car_mileage"] = car_mileage
        if work_wishes:
            payload["work_wishes"] = work_wishes

        result = await self._post("/telegram/lead", payload)
        if result is None:
            logger.warning("1С create_notification: нет ответа, считаем OK (лид сохранён в PostgreSQL)")
            return True
        return result.get("success", False)

    async def save_telegram_user_id(self, client_id: str, telegram_user_id: int) -> bool:
        """Сохраняет связь клиента с Telegram user_id в 1С."""
        result = await self._post("/client/telegram", {
            "client_id": client_id,
            "telegram_user_id": telegram_user_id,
        })
        return bool(result and result.get("success"))

    async def get_service_slots(
        self,
        date_from: str,
        date_to: str,
        duration_min: int = 60,
        post_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Получает свободные слоты из 1С."""
        params: Dict[str, Any] = {
            "date_from": date_from,
            "date_to": date_to,
            "duration_min": duration_min,
        }
        if post_id:
            params["post_id"] = post_id
        result = await self._get("/service/slots", params)
        if result is None:
            raise ICSAlfaServiceError(
                f"1С не ответила на запрос слотов {date_from}..{date_to}"
            )
        if isinstance(result, dict) and "slots" in result:
            from dialog.service_slot_time import normalize_1c_slots

            return normalize_1c_slots(result["slots"])
        status = result.get("http_status") if isinstance(result, dict) else None
        raise ICSAlfaServiceError(
            f"Некорректный ответ 1С на запрос слотов"
            f"{f' (HTTP {status})' if status else ''}: {date_from}..{date_to}"
        )

    async def create_appointment(
        self,
        fio: str,
        phone: str,
        desired_date: str,
        desired_time: str,
        duration_min: int,
        post_id: str,
        acceptor_id: str = "",
        car_brand: Optional[str] = None,
        model: Optional[str] = None,
        mileage: Optional[str] = None,
        listofworks: Optional[str] = None,
        car_year: Optional[str] = None,
        transmission: Optional[str] = None,
        engine_volume: Optional[str] = None,
        drive: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт запись на ТО в 1С (GET). Ответ: success, appointment_id, appointment_Date."""
        base_params: Dict[str, Any] = {
            "desired_date": desired_date,
            "fio": fio,
            "phone": phone,
            "desired_time": desired_time,
            "duration_min": int(duration_min),
        }
        if post_id:
            base_params["post_id"] = post_id
        acceptor_value = str(acceptor_id or "").strip()
        if acceptor_value:
            base_params["acceptor_id"] = acceptor_value

        params = dict(base_params)
        mileage_value = str(mileage).strip() if mileage is not None else ""
        listofworks_value = str(listofworks).strip() if listofworks else ""
        brand_value = str(car_brand).strip() if car_brand else ""
        model_value = str(model).strip() if model else ""
        year_value = str(car_year).strip() if car_year is not None else ""
        transmission_value = str(transmission).strip() if transmission is not None else "0"
        engine_volume_value = str(engine_volume).strip() if engine_volume is not None else "0"
        drive_value = str(drive).strip() if drive is not None else "0"

        # 1С падает, если brand/model/listofworks ушли без mileage — передаём расширение только с пробегом.
        if mileage_value:
            params["mileage"] = mileage_value
            if brand_value:
                params["brand"] = brand_value
            if model_value:
                params["model"] = model_value
            if year_value:
                params["car_year"] = year_value
            params["transmission"] = transmission_value or "0"
            params["engine_volume"] = engine_volume_value or "0"
            params["drive"] = drive_value or "0"
            if listofworks_value:
                params["listofworks"] = listofworks_value
        elif listofworks_value:
            params["listofworks"] = listofworks_value

        result = await self._get("/service/appointments", params)
        if result and result.get("success"):
            return result

        err_body = str((result or {}).get("error_body") or "")
        mileage_server_bug = "mileage" in err_body.lower()
        if mileage_server_bug:
            logger.error(
                "1С create_appointment: ошибка mileage на сервере 1С (нужен фикс программиста): %s",
                err_body[:200],
            )

        # Fallback на базовый query только если расширение отклонено и это не серверный баг mileage.
        if params != base_params and not mileage_server_bug:
            logger.warning(
                "1С create_appointment расширенный query отклонён, fallback на базовый: %s",
                {k: params.get(k) for k in (
                    "brand", "model", "mileage", "car_year",
                    "transmission", "engine_volume", "drive", "listofworks",
                ) if k in params},
            )
            result = await self._get("/service/appointments", base_params)
            if result and result.get("success"):
                return result
        return None

    async def cancel_appointment(self, appointment_id: str) -> bool:
        """Отмена записи на ТО."""
        result = await self._post("/service/appointments/cancel", {
            "appointment_id": appointment_id,
        })
        return bool(result and result.get("success"))

    async def get_service_appointments(
        self, start_date: datetime, end_date: datetime
    ) -> list:
        """Получает записи на сервис из 1С."""
        result = await self._get("/service/appointments", {
            "date_from": start_date.strftime("%Y-%m-%d"),
            "date_to": slots_date_to_exclusive(
                start_date.date(),
                (end_date.date() - start_date.date()).days + 1,
            ),
        })
        if result and "appointments" in result:
            return result["appointments"]
        return []
