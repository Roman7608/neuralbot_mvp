"""Сессии MAX-пользователей (аналог FSM aiogram), ключ — user_id MAX."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

# user_id (int) -> {"state": str, "data": dict}
_sessions: dict[int, dict[str, Any]] = {}


def get_session(user_id: int) -> dict[str, Any]:
    if user_id not in _sessions:
        _sessions[user_id] = {"state": "WAITING_NEED", "data": {}}
    return _sessions[user_id]


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def session_state(user_id: int) -> str:
    return get_session(user_id)["state"]


def session_data(user_id: int) -> dict[str, Any]:
    return get_session(user_id)["data"]


def set_state(user_id: int, state: str) -> None:
    get_session(user_id)["state"] = state


def update_data(user_id: int, **kwargs: Any) -> None:
    get_session(user_id)["data"].update(kwargs)


def replace_data(user_id: int, data: dict[str, Any]) -> None:
    get_session(user_id)["data"] = deepcopy(data)
