"""
Inline-клавиатуры MAX (POST /messages, attachment type inline_keyboard).
См. https://dev.max.ru/docs-api — раздел «Клавиатура».
"""

from __future__ import annotations

from typing import Optional

from max_bot.config import (
    max_service_booking_via_alfa_enabled,
    policy_document_public_url,
    policy_local_file_path,
)
from max_bot.lead_session import session_data

# Тексты как в telegram_bot (ReplyKeyboard), здесь — callback.payload для маршрутизации
_ALL_MENU_ROWS: list[tuple[str, str]] = [
    ("Записаться на ТО", "svc_to"),
    ("ОП Tenet & Chery", "op_tenet"),
    ("Автомобили с пробегом", "used"),
    ("Слесарный цех", "wrench"),
    ("Кузовной цех", "body"),
    ("Запчасти", "parts"),
    ("Прочие", "misc"),
    ("Старт", "start"),
]


def get_menu_rows() -> list[tuple[str, str]]:
    """Без интеграции 1С Альфа кнопка «Записаться на ТО» скрыта (см. max_service_booking_via_alfa_enabled)."""
    if max_service_booking_via_alfa_enabled():
        return list(_ALL_MENU_ROWS)
    return [r for r in _ALL_MENU_ROWS if r[1] != "svc_to"]

POLICY_BUTTON_TEXT = "Ознакомиться с Политикой предприятия"
POLICY_PAYLOAD = "show_policy"

GREETING = (
    'Здравствуйте! Я — бот автоцентра «Викинги» на Заставной. Чем могу Вам помочь? '
    "Продолжая общение в боте, вы подтверждаете согласие на обработку персональных данных "
    "в соответствии с Федеральным законом № 152-ФЗ и Политикой обработки персональных данных."
)

# Ответы на выбор раздела (кратко; полная логика — позже через общий слой с telegram_bot)
MENU_REPLIES: dict[str, str] = {
    "svc_to": (
        "Запись на ТО: напишите марку, год автомобиля и желаемые работы — передадим в слесарный цех "
        "или предложим ближайший слот."
    ),
    "op_tenet": (
        "Отдел продаж новых автомобилей Chery и Tenet. Можем подобрать модель и комплектацию, "
        "записать на тест-драйв."
    ),
    "used": (
        "Отдел автомобилей с пробегом. Опишите, что ищете, или оставьте телефон для обратного звонка."
    ),
    "wrench": (
        "Слесарный цех: диагностика, ремонт, ТО. Кратко опишите проблему или марку/год автомобиля."
    ),
    "body": "Кузовной цех: окраска, рихтовка, детейлинг. Опишите задачу или приложите фото при возможности.",
    "parts": "Запчасти: укажите VIN или марку/модель и что нужно — проверим наличие.",
    "misc": "Прочие вопросы: опишите, чем помочь, мы направим в нужный отдел.",
    "start": GREETING,
    "show_policy": (
        "Полный текст Политики обработки персональных данных можно запросить в салоне или у администратора. "
        "Кратко: данные обрабатываются в целях записи, обратной связи и исполнения договора (152-ФЗ)."
    ),
}

PHONE_DONE_PAYLOAD = "phone_done"


def payload_to_menu_label(payload: str) -> Optional[str]:
    """
    callback payload → текст как у кнопки (для маршрутизации в lead_flow).
    Старый callback svc_to при выключенной записи на ТО обрабатываем как «Слесарный цех».
    """
    if payload == "svc_to" and not max_service_booking_via_alfa_enabled():
        return "Слесарный цех"
    # Устаревший callback старых клавиатур MAX → тот же ОП новых авто
    if payload == "op_jetour":
        return "ОП Tenet & Chery"
    p2t = {pl: label for label, pl in get_menu_rows()}
    p2t[POLICY_PAYLOAD] = POLICY_BUTTON_TEXT
    p2t[PHONE_DONE_PAYLOAD] = "Готово"
    return p2t.get(payload)


BACK_TO_MENU_TEXT = "Главное меню"
CLOSE_SESSION_TEXT = "Закрыть сессию"
CLOSE_SESSION_PAYLOAD = "close_session"


def _policy_button_row() -> list[dict]:
    """
    Локальный файл на server7 → callback, по нажатию бот грузит файл в MAX (без публичного HTTPS).
    Иначе публичный URL → link. Иначе callback с кратким текстом.
    """
    if policy_local_file_path() is not None:
        return [{"type": "callback", "text": POLICY_BUTTON_TEXT, "payload": POLICY_PAYLOAD}]
    url = policy_document_public_url()
    if url:
        return [{"type": "link", "text": POLICY_BUTTON_TEXT, "url": url}]
    return [{"type": "callback", "text": POLICY_BUTTON_TEXT, "payload": POLICY_PAYLOAD}]


def main_menu_attachments(user_id: int | None = None) -> list[dict]:
    """
    Полное меню: политика только до первого действия пользователя (текст / кнопка),
    затем строка с политикой скрывается до сброса сессии (/start, новый bot_started и т.п.).
    """
    buttons: list[list[dict]] = []
    show_policy = user_id is None or not session_data(user_id).get("policy_menu_dismissed")
    if show_policy:
        buttons.append(_policy_button_row())
    for label, payload in get_menu_rows():
        buttons.append([{"type": "callback", "text": label, "payload": payload}])
    buttons.append(
        [{"type": "callback", "text": CLOSE_SESSION_TEXT, "payload": CLOSE_SESSION_PAYLOAD}]
    )
    return [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]


def back_to_menu_attachments() -> list[dict]:
    """После выбора раздела — не дублировать всё меню: главное меню и закрытие сессии."""
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {"type": "callback", "text": BACK_TO_MENU_TEXT, "payload": "start"},
                        {"type": "callback", "text": CLOSE_SESSION_TEXT, "payload": CLOSE_SESSION_PAYLOAD},
                    ],
                ]
            },
        }
    ]


REQUEST_CONTACT_BUTTON_TEXT = "Передать номер телефона"


def phone_prompt_attachments() -> list[dict]:
    """
    Кнопка request_contact (см. dev.max.ru docs-api — тип кнопки request_contact).
    В одном ряду с link/request_contact — не более 3 кнопок; здесь одна + возврат в меню.
    """
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "request_contact", "text": REQUEST_CONTACT_BUTTON_TEXT}],
                    [
                        {"type": "callback", "text": BACK_TO_MENU_TEXT, "payload": "start"},
                        {"type": "callback", "text": CLOSE_SESSION_TEXT, "payload": CLOSE_SESSION_PAYLOAD},
                    ],
                ]
            },
        }
    ]


def phone_after_typed_attachments() -> list[dict]:
    """После ручного ввода номера: контакт + «Готово» (callback) + главное меню."""
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "request_contact", "text": REQUEST_CONTACT_BUTTON_TEXT}],
                    [
                        {"type": "callback", "text": "Готово", "payload": "phone_done"},
                        {"type": "callback", "text": BACK_TO_MENU_TEXT, "payload": "start"},
                    ],
                    [{"type": "callback", "text": CLOSE_SESSION_TEXT, "payload": CLOSE_SESSION_PAYLOAD}],
                ]
            },
        }
    ]


def attachments_for_menu_payload(payload: str, user_id: int | None = None) -> list[dict]:
    """Полная клавиатура только для «Старт» / главного меню; иначе — одна кнопка назад."""
    if payload == "start":
        return main_menu_attachments(user_id)
    return back_to_menu_attachments()
