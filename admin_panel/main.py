"""
Веб-админка аналитики звонков.
Запуск из корня проекта: uvicorn admin_panel.main:app --reload --host 0.0.0.0 --port 8000
"""

import os
import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from datetime import date, time, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from admin_panel.report_time import (
    build_admin_config_payload,
    format_datetime_for_report,
    now_in_report_zone,
    report_zone,
)

from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form, Depends, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from call_analytics.sprecord_filename import date_from_sprecord_filename
from call_analytics.autofetch_sprecord import parse_call_time_from_filename

from admin_panel.auth import (
    get_current_user,
    AdminUser,
    create_session,
    parse_session,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    leads_date_range,
    can_access_calls,
    calls_department,
    calls_allowed_departments_for_list,
    can_access_call_department_row,
    can_see_transcription,
    can_upload,
    can_upload_from_sprecord,
    upload_department,
    can_access_analytics,
    analytics_department,
    can_exclude_calls,
    can_edit_manager_name,
    can_access_voice_transfers,
    can_access_leads,
    leads_need_types_allowlist,
)

try:
    from gpu_priority_lock import is_gpu_held_by_voice_bot
except ImportError:
    def is_gpu_held_by_voice_bot() -> bool:
        return False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from licensing.checker import verify_license, start_periodic_check, license_info
verify_license("admin_panel")

try:
    from config import AUDIO_CALLS_ROOT
except ImportError:
    AUDIO_CALLS_ROOT = Path("/mnt/audio_calls/calls")

app = FastAPI(title="Vikingi Call Analytics Admin", version="0.1.0")


@app.middleware("http")
async def voice_bot_api_no_cache(request: Request, call_next):
    """Список сессий и сводка не должны отдаваться из кэша браузера (иначе таблица «Сессии» пуста при живой сводке)."""
    response = await call_next(request)
    if request.url.path.startswith("/api/voice-bot"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


VOICE_BOT_RECORDINGS_ROOT = os.environ.get("VOICE_BOT_RECORDINGS_ROOT", "").strip()
from internal_test_phone import (
    INTERNAL_TEST_PHONE_LAST10,
    filter_rows_hide_internal_test_phone,
    is_internal_test_phone,
)


def _safe_voice_bot_recording_path(raw: Optional[str]) -> Optional[Path]:
    """Путь к файлу записи: только внутри VOICE_BOT_RECORDINGS_ROOT (если задан)."""
    if not raw or not str(raw).strip():
        return None
    if not VOICE_BOT_RECORDINGS_ROOT:
        return None
    try:
        root = Path(VOICE_BOT_RECORDINGS_ROOT).resolve()
        raw_s = str(raw).strip()
        if os.path.isabs(raw_s):
            p = Path(raw_s).expanduser().resolve()
        else:
            p = (root / raw_s).resolve()
        p.relative_to(root)
    except (ValueError, OSError):
        return None
    if not p.is_file():
        return None
    return p

# Путь к папке для ручной загрузки — только HDD server7
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_BASE = Path(AUDIO_CALLS_ROOT) / "manual"
UPLOAD_BASE.mkdir(parents=True, exist_ok=True)
(UPLOAD_BASE / "OP").mkdir(exist_ok=True)
(UPLOAD_BASE / "STO").mkdir(exist_ok=True)


def _apply_leads_date_filter(user: AdminUser, date_from: Optional[str], date_to: Optional[str]) -> tuple:
    """Привести пару дат к корректному порядку; ограничений по ролям нет."""

    def _ordered_pair(a: Optional[str], b: Optional[str]) -> tuple:
        if a and b and len(str(a)) == 10 and len(str(b)) == 10 and str(a) > str(b):
            return b, a
        return a, b

    if leads_date_range(user.role_id) == "any":
        return _ordered_pair(date_from, date_to)
    today = date.today()
    yesterday = today - timedelta(days=1)
    df = date_from or yesterday.isoformat()
    dt = date_to or today.isoformat()
    # Обрезать за пределами [вчера, сегодня]
    if df < yesterday.isoformat():
        df = yesterday.isoformat()
    if dt > today.isoformat():
        dt = today.isoformat()
    if df > dt:
        df, dt = yesterday.isoformat(), today.isoformat()
    return df, dt


def _deny_internal_test_voice_session(sess: Optional[Dict[str, Any]], user: AdminUser) -> None:
    """Тестовый номер виден только admin1 (role_id=8)."""
    if user.role_id == 8 or not sess:
        return
    if is_internal_test_phone(sess.get("caller_phone")):
        raise HTTPException(status_code=404, detail="Сессия не найдена")


def _mask_lead_need_for_phone(row: Dict[str, Any], *, role_id: int = 0) -> None:
    """Скрыть поля потребности для целевого номера в таблице «Лиды» (кроме admin1)."""
    if role_id == 8:
        return
    raw_phone = str(row.get("client_phone") or "")
    digits = "".join(ch for ch in raw_phone if ch.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else ""
    if last10 != INTERNAL_TEST_PHONE_LAST10:
        return
    row["lead_need_display"] = ""
    row["lead_need_display_rich"] = ""
    row["need_text"] = ""


def _parse_voice_bot_report_date(raw: Optional[str]) -> Optional[str]:
    """Только YYYY-MM-DD; иначе None (игнор битого query)."""
    s = (raw or "").strip()
    if len(s) != 10:
        return None
    try:
        date.fromisoformat(s)
    except ValueError:
        return None
    return s


def _voice_bot_report_date_pair(
    date_from: Optional[str], date_to: Optional[str]
) -> Tuple[str, str]:
    """
    Период там, где нужен именно календарный день по умолчанию (например подстановка к лидам).
    Одна заполненная граница → один календарный день.
    Если даты не переданы — «сегодня» в TZ отчёта.
    """
    df = _parse_voice_bot_report_date(date_from)
    dt = _parse_voice_bot_report_date(date_to)
    if df and not dt:
        return df, df
    if dt and not df:
        return dt, dt
    if df and dt:
        return df, dt
    today = now_in_report_zone().date().isoformat()
    return today, today


def _serialize(obj: Any) -> Any:
    """Приведение к JSON-совместимым типам (дата/время, numpy из оценок и т.д.)."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, datetime):
        return format_datetime_for_report(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.isoformat()
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return _serialize(obj.item())
        if isinstance(obj, np.ndarray):
            return _serialize(obj.tolist())
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    try:
        from decimal import Decimal

        if isinstance(obj, Decimal):
            return float(obj)
    except ImportError:
        pass
    return str(obj)


def _cell_export(val: Any) -> Any:
    """Для CSV/XLSX: даты в часовом поясе отчётов."""
    if isinstance(val, datetime):
        return format_datetime_for_report(val)
    return val


def _sync_sto_dimensions_for_call(
    call_id: int,
    normalized_text: str,
    department: Optional[str],
    call_type: Optional[str],
) -> Dict[str, Any]:
    """STO-признаки для аналитики + узкая рубрика STO_TO_* для оценки по скрипту ТО."""
    from call_analytics.sync_call_sto_metadata import sync_call_sto_metadata

    return sync_call_sto_metadata(
        call_id,
        normalized_text or "",
        (department or "").strip(),
        (call_type or "").strip(),
    )


def _normalize_manager_field_in_call_row(row: Optional[dict]) -> None:
    """В ответе API — «Фамилия Имя», даже если в БД только фамилия (старые записи)."""
    if not row:
        return
    try:
        from admin_panel.managers_config import stored_manager_to_display

        mn = row.get("manager_name")
        if mn:
            s = str(mn).strip()
            try:
                from analyze_call_quality import _sanitize_extracted_manager_display

                if _sanitize_extracted_manager_display(s) is None:
                    row["manager_name"] = ""
                    return
            except Exception:
                pass
            disp = stored_manager_to_display(s, department=row.get("department"))
            if disp:
                row["manager_name"] = disp
    except Exception:
        pass


def _normalize_manager_field_in_call_rows(rows: list) -> None:
    for r in rows:
        if isinstance(r, dict):
            _normalize_manager_field_in_call_row(r)


start_periodic_check("admin_panel")


@app.get("/api/license")
def api_license():
    return license_info()


@app.get("/api/admin_config")
def api_admin_config(user: AdminUser = Depends(get_current_user)):
    """
    Календарные даты и периоды в часовом поясе сервера отчётов (как POSTGRESQL_SESSION_TIMEZONE).
    Нужен для фильтров в UI без привязки к часовому поясу браузера.
    """
    return build_admin_config_payload()


PRIVACY_POLICY_FILE = (
    BASE_DIR
    / "Analytic"
    / "Политика обработки персональных данных Викинги (18.11.22).docx"
)


@app.get("/public/privacy-policy")
def public_privacy_policy():
    """
    Публичная выдача Политики обработки ПДн (.docx) без входа в админку.
    Нужна для кнопки type=link в MAX-боте (HTTPS-URL с прокси на этот сервис).
    """
    if not PRIVACY_POLICY_FILE.is_file():
        raise HTTPException(
            status_code=404,
            detail="Файл политики не найден. Смонтируйте docx в контейнер admin-panel (см. docker-compose).",
        )
    return FileResponse(
        path=str(PRIVACY_POLICY_FILE),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="Politika_Vikingi.docx",
    )


# ————— Auth —————
@app.get("/api/auth/logins")
def auth_logins():
    """Список логинов для выбора на странице входа (без паролей)."""
    from database.postgresql_manager import AdminAuthDB
    return {"logins": AdminAuthDB.get_logins()}


@app.post("/api/auth/login")
def auth_login(login: str = Form(...), password: str = Form(...)):
    """Вход: логин + пароль. Возвращает {ok, role_id} и устанавливает cookie."""
    from database.postgresql_manager import AdminAuthDB
    user = AdminAuthDB.verify_user(login, password)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_session(user["id"], user["login"], user["role_id"])
    resp = Response(
        content=json.dumps({"ok": True, "role_id": user["role_id"], "login": user["login"]}),
        media_type="application/json",
    )
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@app.get("/api/auth/me")
def auth_me(user: AdminUser = Depends(get_current_user)):
    """Текущий пользователь: {id, login, role_id}."""
    return {"id": user.id, "login": user.login, "role_id": user.role_id}


@app.post("/api/auth/logout")
def auth_logout():
    """Выход: очищает cookie сессии."""
    resp = Response(content=json.dumps({"ok": True}), media_type="application/json")
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@app.get("/logout")
def logout_page():
    """Выход по ссылке: очищает cookie и переводит на /login."""
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


# ————— Calls (с проверкой прав) —————
@app.get("/api/calls")
def list_calls(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    call_type: Optional[str] = Query(None),
    internal_number: Optional[int] = Query(None),
    manager_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    show_excluded: bool = Query(False),
    call_source: Optional[str] = Query(None, regex="^(sprecord|bot)$"),
    working_hours: Optional[str] = Query(None, regex="^(working|non_working)$"),
    sto_to_rubric_only: bool = Query(
        False,
        description="Только звонки с узкой рубрикой ТО (STO_TO_IN/STO_TO_OUT) для чек-листа",
    ),
    sto_service_brand: Optional[str] = Query(
        None,
        description="Фильтр СТО: chery_tenet | nissan | other_brand",
    ),
    sto_work_type: Optional[str] = Query(
        None,
        description="Фильтр СТО: to | warranty | diagnostics | body_shop | quality_check | other_work",
    ),
    sto_narrow_rubric: Optional[str] = Query(
        None,
        description="Узкий слой СТО: STO_TO_IN | STO_TO_OUT | STO_NARROW_OTHER (не смешивать с call_type)",
    ),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    order: str = Query("desc", description="asc=сначала утро, desc=сначала вечер"),
):
    """Список звонков с фильтрами. По роли: ограничение по отделу и датам."""
    if not can_access_calls(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к звонкам")
    # Для ролей СТО (2/5): узкая рубрика всегда читается в контексте отдела STO,
    # даже если фронт не прислал department.
    if sto_narrow_rubric and user.role_id in (2, 5):
        department = "STO"
    dept, dept_in = calls_allowed_departments_for_list(user.role_id, department)
    restricted = calls_department(user.role_id)
    logger.info(
        "list_calls: role_id=%s, req_department=%r, restricted=%r, dept=%r, dept_in=%r",
        user.role_id,
        department,
        restricted,
        dept,
        dept_in,
    )
    try:
        from database.postgresql_manager import CallAnalyticsDB
        extra_internal_numbers = [_body_repair_internal_number()] if user.role_id == 5 else None
        rows = CallAnalyticsDB.list_calls(
            date_from=date_from,
            date_to=date_to,
            department=dept,
            departments=dept_in,
            extra_internal_numbers=extra_internal_numbers,
            call_type=call_type,
            internal_number=internal_number,
            manager_name=manager_name,
            status=status,
            show_excluded=show_excluded and can_exclude_calls(user.role_id),
            call_source=call_source,
            working_hours=working_hours,
            sto_to_rubric_only=sto_to_rubric_only,
            sto_service_brand=(sto_service_brand.strip() if sto_service_brand else None),
            sto_work_type=(sto_work_type.strip() if sto_work_type else None),
            sto_narrow_rubric=(sto_narrow_rubric.strip() if sto_narrow_rubric else None),
            limit=limit,
            offset=offset,
            order=order,
        )
        _normalize_manager_field_in_call_rows(rows)
        return JSONResponse(
            content=_serialize(rows),
            headers={"Cache-Control": "no-store, no-cache"},
        )
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calls/managers")
def list_calls_managers(
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Query(None),
):
    """Список менеджеров для фильтра «Звонки»: только справочник ОП или СТО из managers_config (без значений из БД)."""
    if not can_access_calls(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к звонкам")
    q = (department or "").strip().upper() or None
    if q == "OTHER" and user.role_id != 8:
        raise HTTPException(status_code=403, detail="Нет доступа к категории «Прочие»")
    try:
        from admin_panel.managers_config import get_managers_for_analytics

        seen: set[str] = set()
        result: List[Dict[str, Any]] = []

        def _append_roster(dept_key: str) -> None:
            for m in get_managers_for_analytics(dept_key):
                label = (m.get("manager_name") or "").strip()
                if label and label not in seen:
                    seen.add(label)
                    result.append({"manager_name": label, "display": label})

        _no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate"}
        restricted = calls_department(user.role_id)
        if restricted == "OP":
            _append_roster("OP")
            return JSONResponse(content=result, headers=_no_cache)
        if restricted == "STO":
            _append_roster("STO")
            return JSONResponse(content=result, headers=_no_cache)
        # Директор (6) и суперадмин (8): список зависит от выбранного отдела
        if user.role_id in (6, 8):
            if q == "OP":
                _append_roster("OP")
            elif q == "STO":
                _append_roster("STO")
            elif q == "OTHER":
                return JSONResponse(content=[], headers=_no_cache)
            else:
                _append_roster("OP")
                _append_roster("STO")
            return JSONResponse(content=result, headers=_no_cache)
        return JSONResponse(content=[], headers=_no_cache)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


def _check_call_access(user: AdminUser, call_department: Optional[str]) -> None:
    """Проверить доступ к звонку по отделу. Исключение при отказе."""
    if not can_access_call_department_row(user.role_id, call_department):
        if not can_access_calls(user.role_id):
            raise HTTPException(status_code=403, detail="Нет доступа к звонкам")
        raise HTTPException(status_code=403, detail="Нет доступа к этому отделу")


def _body_repair_internal_number() -> int:
    raw = str(os.environ.get("ASTERISK_TRANSFER_TARGET_BODY_REPAIR", "695873") or "").strip()
    try:
        return int(raw)
    except Exception:
        return 695873


def _can_access_body_sprecord_for_sto_plus(user: AdminUser, row: Dict[str, Any]) -> bool:
    if user.role_id not in (5, 6, 8):
        return False
    try:
        internal_number = int(row.get("internal_number"))
    except Exception:
        return False
    call_source = str(row.get("call_source") or "sprecord").strip().lower()
    return call_source == "sprecord" and internal_number == _body_repair_internal_number()


def _check_call_access_with_overrides(user: AdminUser, row: Dict[str, Any]) -> None:
    try:
        _check_call_access(user, row.get("department"))
        return
    except HTTPException as e:
        if e.status_code != 403:
            raise
        if _can_access_body_sprecord_for_sto_plus(user, row):
            return
        raise


@app.get("/api/calls/{call_id}")
def get_call(call_id: int, user: AdminUser = Depends(get_current_user)):
    """Детали звонка: информация, транскрипция, оценки."""
    try:
        from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access_with_overrides(user, row)
        # Подстановка номера из voice_bot_sessions для импорта SPRecord до первого backfill
        if not row.get("caller_phone") and (row.get("call_source") or "sprecord") == "sprecord":
            try:
                if CallAnalyticsDB.try_match_voice_bot_for_call(call_id):
                    row = CallAnalyticsDB.get_call_with_details(call_id)
                    if not row:
                        raise HTTPException(status_code=404, detail="Звонок не найден")
                    _check_call_access_with_overrides(user, row)
            except Exception:
                logger.debug(
                    "Подстановка caller_phone при открытии карточки не удалась (call_id=%s)",
                    call_id,
                    exc_info=True,
                )
        if row.get("duration_seconds") is None and row.get("file_path"):
            file_path = Path(row["file_path"])
            if not file_path.is_absolute():
                file_path = BASE_DIR / file_path
            if file_path.exists():
                try:
                    import soundfile as sf
                    info = sf.info(str(file_path))
                    duration_seconds = int(round(info.frames / info.samplerate))
                    row["duration_seconds"] = duration_seconds
                    with PostgreSQLManager.get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE calls SET duration_seconds = %s WHERE id = %s",
                                (duration_seconds, call_id),
                            )
                except Exception:
                    pass
        if not can_see_transcription(user.role_id):
            row["transcription"] = None
            row["quality_scores"] = None
        _normalize_manager_field_in_call_row(row)
        return _serialize(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/match-caller-phone")
def match_caller_phone_from_voice_bot(call_id: int, user: AdminUser = Depends(get_current_user)):
    """Подставить номер клиента из voice_bot_sessions по времени звонка (повторная попытка)."""
    if not can_upload(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав")
    try:
        from database.postgresql_manager import CallAnalyticsDB

        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access_with_overrides(user, row)
        ok = CallAnalyticsDB.try_match_voice_bot_for_call(call_id)
        row2 = CallAnalyticsDB.get_call_with_details(call_id)
        phone = (row2 or {}).get("caller_phone")
        return {"ok": True, "matched": ok, "caller_phone": phone}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calls/{call_id}/audio")
def get_call_audio(
    call_id: int,
    background_tasks: BackgroundTasks,
    raw: bool = Query(
        False,
        description=(
            "True — отдать исходный файл как на диске (для сохранения / VLC/ffplay). "
            "False — для <audio>: перекодировать WAV в браузеро-совместимый PCM через ffmpeg."
        ),
    ),
    user: AdminUser = Depends(get_current_user),
):
    """Отдать аудио: WAV по умолчанию → PCM 16 kHz mono (ffmpeg); ?raw=1 — байты файла как на диске."""
    try:
        from database.postgresql_manager import CallAnalyticsDB
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row or not row.get("file_path"):
            raise HTTPException(status_code=404, detail="Звонок или файл не найден")
        _check_call_access_with_overrides(user, row)
        file_path = Path(row["file_path"])
        if not file_path.is_absolute():
            file_path = BASE_DIR / file_path
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Файл не найден")
        logger.info("Отдача аудио: call_id=%s, path=%s", call_id, file_path)
        suffix = file_path.suffix.lower()
        if suffix == ".wav" and not raw:
            # Телефонные WAV (ADPCM/G.711) в браузерах часто не играют; отдаем временный PCM WAV.
            tmp = _transcode_sprecord_wav_for_browser(file_path)
            if tmp is not None:
                out_file = tmp

                def _unlink_play_tmp(fp: Path = out_file) -> None:
                    try:
                        fp.unlink(missing_ok=True)
                    except OSError:
                        pass

                background_tasks.add_task(_unlink_play_tmp)
                return FileResponse(str(tmp), filename=file_path.name, media_type="audio/wav")
            logger.error(
                "Аудио call_id=%s: ffmpeg не смог подготовить WAV для браузера. "
                "Путь=%s подскажите скачивание с параметром raw=1.",
                call_id,
                file_path,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Не удалось перекодировать WAV для встроенного плеера (проверьте ffmpeg на сервере и лог). "
                    "Используйте «Скачать аудио» — откроется исходный файл для VLC/ffplay и т.п."
                ),
            )
        media_types = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".gsm": "audio/basic"}
        media_type = media_types.get(suffix, "audio/wav")
        return FileResponse(str(file_path), filename=file_path.name, media_type=media_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


def _segments_json_safe(segments: Any) -> list:
    """Сегменты от stt-service → список dict с JSON-совместимыми полями."""
    out: list = []
    if not isinstance(segments, list):
        return out
    for s in segments:
        if not isinstance(s, dict):
            continue
        try:
            st = float(s.get("start", 0))
            en = float(s.get("end", 0))
        except (TypeError, ValueError):
            continue
        out.append({"start": st, "end": en, "text": str(s.get("text") or "")})
    return out


def _transcribe_via_stt_service(audio_path: Path) -> dict:
    """HTTP → stt-service (GigaAM на GPU). В slim-образе админки нет faster-whisper/GigaAM локально."""
    import time

    import httpx
    from text_normalization import normalize_text

    base = os.environ.get("STT_SERVICE_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("STT_SERVICE_URL не задан")
    t0 = time.perf_counter()
    url = f"{base}/transcribe"
    try:
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=60.0)) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, "audio/wav")}
                data = {"priority": "batch", "language": "ru", "model_size": "primary"}
                r = client.post(url, files=files, data=data)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                j = e.response.json()
                detail = j.get("detail", "")
            except Exception:
                detail = (e.response.text or "").strip()
            raise RuntimeError(detail or str(e)) from e
        body = r.json()
    except httpx.RequestError as e:
        raise RuntimeError(f"STT-сервис недоступен ({url}): {e}") from e
    raw = (body.get("text") or "").strip()
    normalized = normalize_text(raw)
    segments = _segments_json_safe(body.get("segments") or [])
    elapsed = time.perf_counter() - t0
    return {
        "normalized_text": normalized,
        "segments": segments,
        "processing_time_seconds": round(elapsed, 2),
        "stt_model": "gigaam",
    }


def _transcribe_call_audio(audio_path: Path) -> dict:
    """
    Транскрибация: при STT_SERVICE_URL — через stt-service (Docker), иначе в процессе админки
    (USE_GIGAAM / faster-whisper, как в run_local).
    """
    import time
    from analyze_call_quality import (
        USE_GIGAAM,
        load_gigaam_model,
        load_stt_model,
        transcribe_audio,
        transcribe_audio_gigaam,
    )

    if not audio_path.exists():
        raise FileNotFoundError(f"Файл не найден: {audio_path}")
    if os.environ.get("STT_SERVICE_URL", "").strip():
        return _transcribe_via_stt_service(audio_path)
    t0 = time.perf_counter()
    if USE_GIGAAM:
        model = load_gigaam_model()
        normalized, segments_with_ts, _ = transcribe_audio_gigaam(audio_path, model)
        stt_model = "gigaam"
    else:
        model = load_stt_model()
        normalized, segments_with_ts, _ = transcribe_audio(audio_path, model)
        stt_model = "faster-whisper"
    elapsed = time.perf_counter() - t0
    return {
        "normalized_text": normalized,
        "segments": segments_with_ts,
        "processing_time_seconds": round(elapsed, 2),
        "stt_model": stt_model,
    }


@app.post("/api/calls/{call_id}/process")
def process_call(
    call_id: int,
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Form(None),
    call_type: Optional[str] = Form(None),
):
    """
    Запустить на GPU транскрибацию и оценку по критериям для звонка.
    Сохраняет результат в БД, возвращает нормализованный текст и оценки.
    """
    try:
        from database.postgresql_manager import CallAnalyticsDB
        if not can_see_transcription(user.role_id):
            raise HTTPException(status_code=403, detail="Нет доступа к транскрипции")
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row or not row.get("file_path"):
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access_with_overrides(user, row)
        file_path = Path(row["file_path"])
        if not file_path.is_absolute():
            file_path = BASE_DIR / file_path
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Файл не найден")
        # Локальный GigaAM/Whisper в процессе админки конкурирует с ботом; HTTP STT — очередь на stt-service.
        if not os.environ.get("STT_SERVICE_URL", "").strip() and is_gpu_held_by_voice_bot():
            raise HTTPException(
                status_code=503,
                detail="ГПУ занят голосовым ботом. Повторите запрос через минуту.",
            )
        result = _transcribe_call_audio(file_path)
        normalized = result["normalized_text"]
        segments = result.get("segments") or []
        # Классификация до augment — для приветствия СТО вх. и выбора чек-листа оценки
        dept_for_augment = None
        ct_for_augment = None
        if department and call_type:
            dept_for_augment, ct_for_augment = (department or "").strip(), (call_type or "").strip()
        else:
            if len((normalized or "").strip()) < 20:
                dept_for_augment, ct_for_augment = "OTHER", "OTHER"
            else:
                try:
                    from call_analytics.classify_by_transcript import classify_auto

                    dept_for_augment, ct_for_augment = classify_auto(normalized)
                except Exception as e:
                    logger.warning("Классификация перед сохранением транскрипции: %s", e)
        # Автоклассификация: OP_OUT только при маркерах в тексте. Ручной тип из формы не перезаписываем.
        if dept_for_augment and ct_for_augment and not (department and call_type):
            from call_analytics.classify_by_transcript import _coerce_op_out_requires_site_lead

            dept_for_augment, ct_for_augment = _coerce_op_out_requires_site_lead(
                normalized, dept_for_augment, ct_for_augment
            )
        try:
            from text_normalization import augment_sto_in_greeting_if_needed

            if dept_for_augment and ct_for_augment:
                normalized = augment_sto_in_greeting_if_needed(
                    normalized, dept_for_augment, ct_for_augment
                )
        except Exception as e:
            logger.warning("augment_sto_in_greeting_if_needed: %s", e)
        # Удалить старые транскрипцию и оценки, если есть (повторный запуск)
        from database.postgresql_manager import PostgreSQLManager
        with PostgreSQLManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM call_quality_scores WHERE call_id = %s", (call_id,))
                cur.execute("DELETE FROM call_transcriptions WHERE call_id = %s", (call_id,))
        tr_id = CallAnalyticsDB.save_transcription(
            call_id=call_id,
            transcription_text=normalized,
            segments=segments,
            stt_model=result.get("stt_model")
            or ("gigaam" if os.environ.get("USE_GIGAAM") else "faster-whisper"),
            processing_time_seconds=result.get("processing_time_seconds"),
        )
        if not tr_id:
            raise HTTPException(
                status_code=500,
                detail="Не удалось сохранить транскрипцию в БД (см. логи admin-panel: save_transcription).",
            )
        # Отдел/тип → STO-метаданные → оценка (СТО только при STO_TO_IN/STO_TO_OUT)
        dept = None
        sto_meta: Dict[str, Any] = {"sto_to_rubric_type": None}
        if dept_for_augment and ct_for_augment:
            CallAnalyticsDB.update_call_department_type(call_id, dept_for_augment, ct_for_augment)
            sto_meta = _sync_sto_dimensions_for_call(
                call_id, normalized, dept_for_augment, ct_for_augment
            )
            dept = dept_for_augment
        scores: dict = {}
        overall: Optional[float] = None
        evaluation_kind = "none"
        ct_eval = (ct_for_augment or "").strip().upper() if ct_for_augment else ""
        if ct_eval and ct_eval != "OTHER":
            from call_analytics.quality_evaluation import save_quality_scores_for_call_type

            scores, overall, evaluation_kind = save_quality_scores_for_call_type(
                call_id=call_id,
                transcription_id=tr_id,
                normalized=normalized,
                call_type=ct_eval,
                sto_to_rubric_type=sto_meta.get("sto_to_rubric_type"),
            )
        # Имя менеджера: автоизвлечение из транскрипта.
        # Для OTHER тоже сохраняем, чтобы имя отображалось в колонке «Номер» списка звонков.
        if dept in ("OP", "STO", "OTHER"):
            try:
                from analyze_call_quality import apply_auto_manager_for_call

                extract_dept = dept if dept in ("OP", "STO") else None
                prev_row = CallAnalyticsDB.get_call_with_details(call_id)
                apply_auto_manager_for_call(
                    call_id, normalized, extract_dept, existing_row=prev_row
                )
            except Exception as e:
                logger.warning("Ошибка извлечения имени менеджера: %s", e)
        try:
            from database.postgresql_manager import PostgreSQLManager
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE calls SET status = %s WHERE id = %s", ("analyzed", call_id))
        except Exception:
            pass
        return _serialize({
            "ok": True,
            "call_id": call_id,
            "normalized_text": normalized,
            "scores": scores or {},
            "overall_score": overall,
            "evaluation_kind": evaluation_kind,
        })
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(e)
        msg = (str(e) or "").strip() or type(e).__name__
        raise HTTPException(status_code=500, detail=msg)


def _default_upload_department(role_id: int) -> str:
    """Дефолтный отдел для загрузки по роли."""
    allowed = upload_department(role_id)
    return allowed if allowed else "OP"


@app.post("/api/upload")
async def upload_audio(
    user: AdminUser = Depends(get_current_user),
    file: UploadFile = File(...),
    department: Optional[str] = Form(None),
):
    """Ручная загрузка аудио с компьютера. department опционален (по умолчанию из роли)."""
    if not can_upload(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к загрузке")
    dept = department or _default_upload_department(user.role_id)
    allowed = upload_department(user.role_id)
    if allowed and dept != allowed:
        raise HTTPException(status_code=403, detail=f"Загрузка только для отдела {allowed}")
    if dept not in ("OP", "STO"):
        raise HTTPException(status_code=400, detail="department должен быть OP или STO")
    if not file.filename or not file.filename.lower().endswith((".wav", ".mp3", ".ogg")):
        raise HTTPException(status_code=400, detail="Нужен файл .wav, .mp3 или .ogg")
    dest_dir = UPLOAD_BASE / dept
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._- ") or "audio.wav"
    dest_path = dest_dir / safe_name
    try:
        content = await file.read()
        dest_path.write_bytes(content)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))
    duration_seconds = None
    try:
        import soundfile as sf
        info = sf.info(str(dest_path))
        duration_seconds = int(round(info.frames / info.samplerate))
    except Exception:
        pass
    # Создать запись в БД (status = pending обработает пайплайн или миграция 003)
    try:
        from database.postgresql_manager import CallAnalyticsDB
        call_date_str = date.today().isoformat()
        call_time_str = "00:00:00"
        file_path_for_db = str(dest_path.resolve())
        call_id = CallAnalyticsDB.save_call(
            file_path=file_path_for_db,
            file_name=safe_name,
            internal_number=0,
            call_date=call_date_str,
            call_time=call_time_str,
            file_size_bytes=len(content),
            duration_seconds=duration_seconds,
            department=dept,
            source_type="manual",
            call_source="manual",
        )
        return {"ok": True, "path": str(dest_path), "call_id": call_id}
    except Exception as e:
        logger.warning(f"Файл сохранён, но запись в БД не создана: {e}")
        return {"ok": True, "path": str(dest_path), "call_id": None}


SPRECORD_ROOT = Path(os.environ.get("SPRECORD_ROOT", "/mnt/sprecord")).resolve()


def _safe_sprecord_relative_path(rel: str) -> Optional[Path]:
    """Путь к файлу внутри SPRECORD_ROOT; иначе None."""
    if rel is None:
        return None
    s = rel.strip().replace("\\", "/")
    if not s or s.startswith("/") or any(part == ".." for part in s.split("/")):
        return None
    try:
        root_r = SPRECORD_ROOT.resolve()
        p = (SPRECORD_ROOT / s).resolve()
    except OSError:
        return None
    try:
        p.relative_to(root_r)
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p


def _list_sprecord_files_for_browse(d0: date, d1: date, limit: int) -> List[Dict[str, Any]]:
    """
    Файлы в корне SPRECORD_ROOT с префиксом имени YYYY_MM_DD_ за каждый день диапазона
    (как при ручном ls по маске; без рекурсивного os.walk по всему архиву).
    """
    out: List[Dict[str, Any]] = []
    step = timedelta(days=1)
    cur = d0
    while cur <= d1:
        pat = cur.strftime("%Y_%m_%d") + "_*"
        try:
            matches = list(SPRECORD_ROOT.glob(pat))
        except OSError:
            matches = []
        for p in matches:
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".wav", ".mp3", ".ogg", ".gsm"):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size <= 0:
                continue
            name = p.name
            try:
                rel = str(p.relative_to(SPRECORD_ROOT)).replace("\\", "/")
            except ValueError:
                continue
            d_part = date_from_sprecord_filename(name)
            t_part = parse_call_time_from_filename(name)
            if d_part and t_part:
                dt_naive = datetime.combine(d_part, t_part)
                started_disp = dt_naive.strftime("%Y-%m-%d %H:%M:%S")
                sort_key = dt_naive.isoformat(sep=" ")
            elif d_part:
                started_disp = d_part.strftime("%Y-%m-%d") + " (время из имени не распознано)"
                sort_key = d_part.isoformat() + " " + name
            else:
                started_disp = "—"
                sort_key = name
            stem = p.stem
            parts = stem.split("_")
            id_suffix = parts[-1] if len(parts) >= 1 else stem
            out.append(
                {
                    "rel_path": rel,
                    "name": name,
                    "size": st.st_size,
                    "started_at_display": started_disp,
                    "file_stem": stem,
                    "id_suffix": id_suffix,
                    "_sort": sort_key,
                }
            )
        cur += step
    out.sort(key=lambda x: x["_sort"], reverse=True)
    for r in out:
        r.pop("_sort", None)
    return out[: max(1, min(int(limit), 5000))]


@app.get("/api/sprecord/browse/files")
def api_sprecord_browse_files(
    user: AdminUser = Depends(get_current_user),
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD, по умолчанию = date_from"),
    limit: int = Query(800, ge=1, le=5000),
):
    """
    Список файлов SpRecord в корне шары по префиксу даты в имени (сравнение с записью бота).
    Доступ: та же роль, что импорт из SPRecord (суперадмин / admin1).
    """
    if not can_upload_from_sprecord(user.role_id):
        raise HTTPException(status_code=403, detail="Раздел доступен только учётной записи с импортом из SPRecord (admin1)")
    if not SPRECORD_ROOT.exists():
        raise HTTPException(
            status_code=503,
            detail=f"SpRecord не примонтирован (путь: {SPRECORD_ROOT})",
        )
    try:
        d0 = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date_from: ожидается YYYY-MM-DD")
    raw_to = (date_to or date_from or "").strip()
    try:
        d1 = datetime.strptime(raw_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date_to: ожидается YYYY-MM-DD")
    if d1 < d0:
        d0, d1 = d1, d0
    rows = _list_sprecord_files_for_browse(d0, d1, limit)
    diagnostic: Optional[Dict[str, Any]] = None
    if not rows:
        diagnostic = {
            "root": str(SPRECORD_ROOT.resolve()),
            "exists": SPRECORD_ROOT.exists(),
            "sample_glob": d0.strftime("%Y_%m_%d") + "_*",
        }
        if SPRECORD_ROOT.exists():
            try:
                probe = next(SPRECORD_ROOT.glob(d0.strftime("%Y_%m_%d") + "_*"), None)
                diagnostic["found_in_root"] = probe is not None
                if probe is not None:
                    diagnostic["example"] = probe.name
            except OSError as e:
                diagnostic["glob_error"] = str(e)
    return {"ok": True, "root": str(SPRECORD_ROOT), "count": len(rows), "files": rows, "diagnostic": diagnostic}


def _transcode_sprecord_wav_for_browser(src: Path) -> Optional[Path]:
    """
    Телефонные WAV (G.711 A-law/µ-law, 8 kHz и т.п.) часто не воспроизводятся в <audio> в Firefox/Chrome.
    В образе admin-panel есть ffmpeg — отдаём PCM s16le mono 16 kHz WAV.
    """
    try:
        fd, out_name = tempfile.mkstemp(suffix=".wav", prefix="sprec_play_")
        os.close(fd)
        out_p = Path(out_name)
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "+discardcorrupt",
            "-err_detect",
            "ignore_err",
            "-i",
            str(src.resolve()),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            "-y",
            str(out_p),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0 or not out_p.is_file() or out_p.stat().st_size == 0:
            if out_p.is_file():
                out_p.unlink(missing_ok=True)
            logger.warning(
                "SpRecord browse: ffmpeg rc=%s stderr=%s",
                r.returncode,
                (r.stderr or "").strip()[:500],
            )
            return None
        return out_p
    except Exception as e:
        logger.warning("SpRecord browse: transcode failed: %s", e)
        return None


@app.get("/api/sprecord/browse/audio")
def api_sprecord_browse_audio(
    background_tasks: BackgroundTasks,
    user: AdminUser = Depends(get_current_user),
    rel_path: str = Query(..., description="Относительный путь под SPRECORD_ROOT"),
):
    """Прослушивание файла с шары SpRecord (без копирования в uploads)."""
    if not can_upload_from_sprecord(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    p = _safe_sprecord_relative_path(rel_path)
    if not p:
        raise HTTPException(status_code=404, detail="Файл не найден или путь недопустим")
    suf = p.suffix.lower()
    mt = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".gsm": "audio/basic",
    }.get(suf, "application/octet-stream")
    if suf == ".wav":
        tmp = _transcode_sprecord_wav_for_browser(p)
        if tmp is not None:
            out_file = tmp

            def _unlink_play_tmp(fp: Path = out_file) -> None:
                try:
                    fp.unlink(missing_ok=True)
                except OSError:
                    pass

            background_tasks.add_task(_unlink_play_tmp)
            return FileResponse(str(tmp), filename=p.name, media_type="audio/wav")
    return FileResponse(str(p), filename=p.name, media_type=mt)


@app.get("/api/upload/sprecord/files")
def list_sprecord_files(
    user: AdminUser = Depends(get_current_user),
    date_str: str = Query(..., description="YYYY-MM-DD"),
):
    """Список аудиофайлов из SPRecord за дату: mtime **или** дата из имени YYYY_MM_DD_ (wav SpRecord)."""
    if not can_upload(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    if not can_upload_from_sprecord(user.role_id):
        raise HTTPException(status_code=403, detail="Импорт из SPRecord доступен только суперадмину")
    if not SPRECORD_ROOT.exists():
        raise HTTPException(
            status_code=503,
            detail=f"SpRecord не примонтирован (путь: {SPRECORD_ROOT})",
        )
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты (YYYY-MM-DD)")
    files = []
    for dirpath, _, filenames in os.walk(SPRECORD_ROOT):
        for name in filenames:
            if not name.lower().endswith((".wav", ".mp3", ".ogg", ".gsm")):
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
                mtime_d = date.fromtimestamp(st.st_mtime)
                name_d = date_from_sprecord_filename(name)
                if mtime_d == target_date or name_d == target_date:
                    rel = str(p.relative_to(SPRECORD_ROOT))
                    files.append({"path": rel, "name": name, "size": st.st_size})
            except (FileNotFoundError, ValueError):
                continue
    return sorted(files, key=lambda x: x["path"])


@app.post("/api/upload/from-sprecord")
async def upload_from_sprecord(
    user: AdminUser = Depends(get_current_user),
    rel_path: str = Form(..., description="Путь относительно /mnt/sprecord"),
    call_type: str = Form(..., description="OP_IN, OP_OUT, STO_IN или STO_OUT"),
):
    """Скопировать файл из SPRecord и зарегистрировать в БД."""
    if not can_upload(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    if not can_upload_from_sprecord(user.role_id):
        raise HTTPException(status_code=403, detail="Импорт из SPRecord доступен только суперадмину")
    if not SPRECORD_ROOT.exists():
        raise HTTPException(
            status_code=503,
            detail=f"SpRecord не примонтирован (путь: {SPRECORD_ROOT})",
        )
    if call_type not in ("OP_IN", "OP_OUT", "STO_IN", "STO_OUT"):
        raise HTTPException(status_code=400, detail="call_type: OP_IN, OP_OUT, STO_IN или STO_OUT")
    allowed = upload_department(user.role_id)
    dept = "OP" if call_type.startswith("OP_") else "STO"
    if allowed and dept != allowed:
        raise HTTPException(status_code=403, detail=f"Загрузка только для отдела {allowed}")
    src = (SPRECORD_ROOT / rel_path).resolve()
    if not str(src).startswith(str(SPRECORD_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Недопустимый путь")
    if not src.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    dest_dir = UPLOAD_BASE / dept
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in src.name if c.isalnum() or c in "._- ") or "audio.wav"
    dest_path = dest_dir / safe_name
    try:
        import shutil
        shutil.copy2(src, dest_path)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))
    duration_seconds = None
    try:
        import soundfile as sf
        info = sf.info(str(dest_path))
        duration_seconds = int(round(info.frames / info.samplerate))
    except Exception:
        pass
    call_date_str = dest_path.stat().st_mtime
    call_date_str = date.fromtimestamp(call_date_str).isoformat()
    call_time_str = "00:00:00"
    try:
        from call_analytics.autofetch_sprecord import parse_call_time_from_filename
        t = parse_call_time_from_filename(src.name)
        if t:
            call_time_str = t.strftime("%H:%M:%S")
    except Exception:
        pass
    try:
        from database.postgresql_manager import CallAnalyticsDB
        call_id = CallAnalyticsDB.save_call(
            file_path=str(dest_path.resolve()),
            file_name=safe_name,
            internal_number=0,
            call_date=call_date_str,
            call_time=call_time_str,
            file_size_bytes=dest_path.stat().st_size,
            duration_seconds=duration_seconds,
            department=dept,
            source_type="auto",
            call_source="sprecord",
        )
        if call_id:
            try:
                CallAnalyticsDB.try_match_voice_bot_for_call(call_id)
            except Exception as em:
                logger.warning("match caller_phone после импорта SPRecord call_id=%s: %s", call_id, em)
        return {"ok": True, "path": str(dest_path), "call_id": call_id}
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


# ————— Exclude / Restore / Manager Name —————
@app.post("/api/calls/{call_id}/exclude")
def exclude_call(call_id: int, user: AdminUser = Depends(get_current_user)):
    """Скрыть звонок из аналитики (нерепрезентативный)."""
    if not can_exclude_calls(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав на скрытие звонков")
    try:
        from database.postgresql_manager import CallAnalyticsDB
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access(user, row.get("department"))
        ok = CallAnalyticsDB.exclude_call(call_id, excluded_by=user.login)
        return {"ok": ok}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/restore")
def restore_call(call_id: int, user: AdminUser = Depends(get_current_user)):
    """Восстановить скрытый звонок в аналитике."""
    if not can_exclude_calls(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав на восстановление звонков")
    try:
        from database.postgresql_manager import CallAnalyticsDB
        ok = CallAnalyticsDB.restore_call(call_id)
        return {"ok": ok}
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/re-categorize")
def recategorize_and_reevaluate(
    call_id: int,
    user: AdminUser = Depends(get_current_user),
    department: str = Form(...),
    call_type: str = Form(...),
):
    """
    Перевести звонок в другую категорию (ОП вх/исх, СТО вх, Прочие) и перезапустить оценку.
    Требуется транскрипция. Для «Прочие» оценки не вычисляются.
    """
    if not can_exclude_calls(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав")
    valid = [
        ("OP", "OP_IN"),
        ("OP", "OP_OUT"),
        ("STO", "STO_IN"),
        ("STO", "STO_OUT"),
        ("OTHER", "OTHER"),
    ]
    if (department, call_type) not in valid:
        raise HTTPException(
            status_code=400,
            detail="Допустимые категории: ОП вх, ОП исх, СТО_ТО_вх, СТО_ТО_исх, Прочие",
        )
    try:
        from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager
        from call_analytics.quality_evaluation import save_quality_scores_for_call_type

        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access(user, row.get("department"))
        tr = row.get("transcription") or {}
        normalized = (tr.get("transcription_text") or "").strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="Нет транскрипции. Сначала запустите транскрибацию.")

        # Ручная категория в UI не ограничиваем эвристикой «маркеры исходящего в тексте»
        # (_coerce_op_out_requires_site_lead — для автоклассификации, иначе ОП исх. нельзя сохранить).

        CallAnalyticsDB.update_call_department_type(call_id, department, call_type)
        sto_meta = _sync_sto_dimensions_for_call(call_id, normalized, department, call_type)
        CallAnalyticsDB.restore_call(call_id)
        with PostgreSQLManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM call_quality_scores WHERE call_id = %s", (call_id,))

        if (department, call_type) != ("OTHER", "OTHER"):
            save_quality_scores_for_call_type(
                call_id=call_id,
                transcription_id=tr.get("id"),
                normalized=normalized,
                call_type=call_type,
                sto_to_rubric_type=sto_meta.get("sto_to_rubric_type"),
            )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/sto-narrow-rubric")
def set_sto_narrow_rubric(
    call_id: int,
    user: AdminUser = Depends(get_current_user),
    sto_to_rubric_type: str = Form(..., description="STO_TO_IN | STO_TO_OUT | STO_NARROW_OTHER"),
):
    """Ручная установка узкой рубрики СТО в карточке + переоценка."""
    if not can_exclude_calls(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав")
    raw = (sto_to_rubric_type or "").strip().upper()
    if raw not in ("STO_TO_IN", "STO_TO_OUT", "STO_NARROW_OTHER"):
        raise HTTPException(status_code=400, detail="sto_to_rubric_type: STO_TO_IN, STO_TO_OUT, STO_NARROW_OTHER")
    try:
        from database.postgresql_manager import CallAnalyticsDB, PostgreSQLManager
        from call_analytics.quality_evaluation import save_quality_scores_for_call_type

        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access(user, row.get("department"))
        if (row.get("department") or "").strip().upper() != "STO":
            raise HTTPException(status_code=400, detail="Узкая рубрика доступна только для звонков отдела СТО")

        tr = row.get("transcription") or {}
        normalized = (tr.get("transcription_text") or "").strip()
        call_type = (row.get("call_type") or "").strip().upper()
        if call_type not in ("STO_IN", "STO_OUT"):
            raise HTTPException(status_code=400, detail="Широкий тип должен быть STO_IN или STO_OUT")

        rubric_type = None if raw == "STO_NARROW_OTHER" else raw
        rubric_reason = "manual_override_not_to" if raw == "STO_NARROW_OTHER" else "manual_override"
        ok = CallAnalyticsDB.update_call_sto_rubric(
            call_id,
            rubric_type=rubric_type,
            reason=rubric_reason,
            appointment_agreed=row.get("sto_appointment_agreed"),
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Не удалось сохранить узкую рубрику")

        with PostgreSQLManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM call_quality_scores WHERE call_id = %s", (call_id,))

        overall = None
        if rubric_type and normalized:
            _, overall, _ = save_quality_scores_for_call_type(
                call_id=call_id,
                transcription_id=tr.get("id"),
                normalized=normalized,
                call_type=call_type,
                sto_to_rubric_type=rubric_type,
            )

        return {
            "ok": True,
            "sto_to_rubric_type": rubric_type,
            "sto_to_rubric_reason": rubric_reason,
            "overall_score": overall,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/calls/{call_id}/department-type")
def set_call_department_type(
    call_id: int,
    user: AdminUser = Depends(get_current_user),
    department: str = Form(...),
    call_type: str = Form(...),
):
    """Задать отдел и тип звонка (ОП вход/исход, СТО вход)."""
    if not can_upload(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав")
    try:
        from database.postgresql_manager import CallAnalyticsDB
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access(user, row.get("department"))
        if department not in ("OP", "STO", "OTHER"):
            raise HTTPException(status_code=400, detail="department: OP, STO или OTHER")
        from call_analytics.classify_by_transcript import normalize_call_type_result

        department, call_type = normalize_call_type_result(department, call_type)
        if call_type not in ("OP_IN", "OP_OUT", "STO_IN", "STO_OUT", "OTHER"):
            raise HTTPException(
                status_code=400,
                detail="call_type: OP_IN, OP_OUT, STO_IN, STO_OUT, OTHER",
            )
        tr = row.get("transcription") or {}
        normalized = (tr.get("transcription_text") or "").strip()
        ok = CallAnalyticsDB.update_call_department_type(call_id, department, call_type)
        sto_meta = _sync_sto_dimensions_for_call(call_id, normalized, department, call_type)
        return {
            "ok": ok,
            "department": department,
            "call_type": call_type,
            "sto_to_rubric_type": sto_meta.get("sto_to_rubric_type"),
            "sto_to_rubric_reason": sto_meta.get("sto_to_rubric_reason"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/calls/{call_id}/manager-name")
def set_manager_name(
    call_id: int,
    user: AdminUser = Depends(get_current_user),
    manager_name: str = Form(""),
):
    """Задать/изменить имя менеджера для звонка."""
    if not can_edit_manager_name(user.role_id):
        raise HTTPException(status_code=403, detail="Нет прав на изменение имени менеджера")
    try:
        from database.postgresql_manager import CallAnalyticsDB
        row = CallAnalyticsDB.get_call_with_details(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Звонок не найден")
        _check_call_access(user, row.get("department"))
        mn = (manager_name or "").strip()
        if mn:
            try:
                from admin_panel.managers_config import stored_manager_to_display

                disp = stored_manager_to_display(mn, department=row.get("department"))
                if disp:
                    mn = disp
            except Exception:
                pass
        ok = CallAnalyticsDB.set_manager_name(call_id, mn, source="manual")
        return {"ok": ok}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


# ————— Analytics API —————
@app.get("/api/analytics/managers")
def analytics_managers(
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Query(None),
):
    """Список менеджеров для фильтров аналитики: значение фильтра — Фамилия Имя (как в списке звонков)."""
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    dept = analytics_department(user.role_id) or department
    try:
        from admin_panel.managers_config import get_managers_for_analytics

        return JSONResponse(
            content=get_managers_for_analytics(dept or "OP"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/weekly")
def analytics_weekly(
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Query(None),
    internal_number: Optional[int] = Query(None),
    manager_name: Optional[str] = Query(None),
    call_type: Optional[str] = Query(None),
    months: int = Query(6, ge=1, le=24),
    show_excluded: bool = Query(False),
):
    """Понедельные средние оценки для графиков аналитики."""
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    dept = analytics_department(user.role_id) or department
    try:
        from database.postgresql_manager import CallAnalyticsDB
        rows = CallAnalyticsDB.get_weekly_analytics(
            department=dept,
            internal_number=internal_number,
            manager_name=manager_name,
            call_type=call_type,
            months=months,
            show_excluded=show_excluded,
        )
        return _serialize(rows)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


def _op_criteria_labels() -> dict:
    """Подписи критериев ОП — бинарная шкала 0/1 (docs/OP_CALL_EVALUATION_CRITERIA_RUBRIC.md)."""
    return {
        "p3_intro": "3. Представление менеджера (имя и фамилия)",
        "p4_ask_name_form": "4. Как обращаться / обращение по имени",
        "p5_name_usage_3plus": "5. Обращение по имени клиента (≥ 1 раз)",
        "p6_car_interest": "6. Выявление интереса к автомобилю",
        "p7_familiar_with_car": "7. Знакомство с авто / ожидания",
        "p8_for_whom": "8. Для кого подбирается авто",
        "p9_purchase_timing": "9. Срок покупки",
        "p10_payment_form": "10. Форма оплаты",
        "p11_current_car": "11. Текущий автомобиль клиента",
        "p12_invite_to_dc": "12. Приглашение в салон / на визит",
        "p13_test_drive": "13. Согласование времени теста/визита",
        "p14_ask_contacts": "14. Запрос контактов клиента",
        "p15_send_contacts": "15. Отправка своих контактов",
        "p16_thanks": "16. Благодарность за звонок",
    }


def _sto_criteria_labels() -> dict:
    return {
        "sto_7": "7. Назвал должность, ИФ и поздоровался",
        "sto_8": "8. Уточнил, как обращаться по имени",
        "sto_9": "9. Обращался по имени >= 3 раз",
        "sto_10": "10. Обслуживался ли ранее",
        "sto_11": "11. Пожелания по работам",
        "sto_12": "12. Перечень работ на ТО",
        "sto_13": "13. Пожелания по доп. работам",
        "sto_14": "14. Пробег автомобиля",
        "sto_15": "15. Стоимость запасных частей",
        "sto_16": "16. Запчасть в наличии",
        "sto_17": "17. Стоимость ТО",
        "sto_18": "18. Продолжительность ТО",
        "sto_19": "19. >= 2 вариантов даты и времени",
        "sto_20": "20. Как добраться до ДЦ",
        "sto_21": "21. Какие документы взять",
        "sto_22": "22. Контактные данные клиента",
        "sto_23": "23. Формы оплаты",
        "sto_24": "24. Звонок-напоминание накануне",
        "sto_25": "25. Подвёл итог договорённостей",
        "sto_26": "26. Дополнительные вопросы",
        "sto_27": "27. Поблагодарил за звонок",
        "sto_28": "28. Нет уменьшительно-ласкательных",
        "sto_29": "29. VIN и отзывные кампании",
    }


def _criteria_labels_for_department(department: str) -> dict:
    return _sto_criteria_labels() if department == "STO" else _op_criteria_labels()


def _analytics_period_date_bounds(months: int, weeks: Optional[int]) -> Tuple[str, str]:
    today = now_in_report_zone().date()
    if weeks is not None and int(weeks) > 0:
        df = today - timedelta(weeks=int(weeks))
    else:
        # Приближение «месяц = 30 дней» достаточно для фильтров агрегатов.
        df = today - timedelta(days=max(1, int(months)) * 30)
    return df.isoformat(), today.isoformat()


@app.get("/api/analytics/period")
def analytics_period(
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Query(None),
    internal_number: Optional[int] = Query(None),
    manager_name: Optional[str] = Query(None),
    call_type: Optional[str] = Query(None),
    months: int = Query(6, ge=1, le=24),
    weeks: Optional[int] = Query(None, ge=1, le=104),
    show_excluded: bool = Query(False),
):
    """
    Сводка за период по звонкам: средний/мин/макс по overall и по каждому критерию.
    В отличие от weekly, минимум и максимум считаются по отдельным звонкам, а не по неделям.
    Если задан weeks — период в неделях, иначе months.
    """
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    dept = analytics_department(user.role_id) or department
    try:
        from database.postgresql_manager import CallAnalyticsDB

        data = CallAnalyticsDB.get_period_analytics_detail(
            department=dept,
            internal_number=internal_number,
            manager_name=manager_name,
            call_type=call_type,
            months=months,
            weeks=weeks,
            show_excluded=show_excluded,
        )
        return _serialize(data)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/sto-booking")
def analytics_sto_booking(
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Query(None),
    manager_name: Optional[str] = Query(None),
    months: int = Query(6, ge=1, le=24),
    weeks: Optional[int] = Query(None, ge=1, le=104),
    show_excluded: bool = Query(False),
    sto_service_brand: Optional[str] = Query(None, description="chery_tenet | nissan | other_brand"),
    sto_work_type: Optional[str] = Query(None, description="to | warranty | diagnostics | body_shop | quality_check | other_work"),
    call_type: Optional[str] = Query(None, description="STO_IN | STO_OUT"),
    sto_to_rubric_only: bool = Query(False, description="Только узкая рубрика ТО (чек-лист)"),
):
    """СТО-агрегаты по марке и виду работ (звонки/записи) за период."""
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    dept = analytics_department(user.role_id) or department
    if dept != "STO":
        return _serialize(
            {
                "totals": {"calls": 0, "bookings": 0, "appointments": 0},
                "by_brand": [],
                "by_work_type": [],
                "date_from": "",
                "date_to": "",
            }
        )
    try:
        from database.postgresql_manager import CallAnalyticsDB

        date_from, date_to = _analytics_period_date_bounds(months, weeks)
        ct = (call_type or "").strip().upper() or None
        if ct not in (None, "STO_IN", "STO_OUT"):
            ct = None
        data = CallAnalyticsDB.get_sto_booking_analytics(
            date_from=date_from,
            date_to=date_to,
            manager_name=manager_name,
            show_excluded=show_excluded,
            sto_service_brand=(sto_service_brand.strip() if sto_service_brand else None),
            sto_work_type=(sto_work_type.strip() if sto_work_type else None),
            call_type=ct,
            sto_to_rubric_only=sto_to_rubric_only,
        )
        return _serialize(data)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/op-manager-matrix")
def analytics_op_manager_matrix(
    user: AdminUser = Depends(get_current_user),
    date_from: str = Query(..., description="Дата начала периода YYYY-MM-DD"),
    date_to: str = Query(..., description="Дата конца периода YYYY-MM-DD"),
    manager_names: Optional[List[str]] = Query(None, description="Список менеджеров ОП (display из справочника)"),
    call_type: Optional[str] = Query(None, description="OP_IN | OP_OUT"),
    show_excluded: bool = Query(False),
):
    """Матрица качества по менеджерам ОП за выбранный период."""
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    dept = analytics_department(user.role_id)
    if dept == "STO":
        raise HTTPException(status_code=403, detail="Для роли СТО матрица ОП недоступна")
    if not date_from or not date_to:
        raise HTTPException(status_code=400, detail="Нужны date_from и date_to")
    try:
        date.fromisoformat(date_from)
        date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="date_from/date_to должны быть в формате YYYY-MM-DD")
    ct = (call_type or "").strip().upper()
    if ct not in ("", "OP_IN", "OP_OUT"):
        raise HTTPException(status_code=400, detail="call_type: OP_IN или OP_OUT")
    try:
        from database.postgresql_manager import CallAnalyticsDB

        data = CallAnalyticsDB.get_op_manager_matrix(
            date_from=date_from,
            date_to=date_to,
            manager_labels=manager_names,
            call_type=(ct or None),
            show_excluded=show_excluded and can_exclude_calls(user.role_id),
        )
        return _serialize(data)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/export")
def analytics_export(
    user: AdminUser = Depends(get_current_user),
    department: Optional[str] = Query(None),
    manager_name: Optional[str] = Query(None),
    call_type: Optional[str] = Query(None),
    months: int = Query(6, ge=1, le=24),
    weeks: Optional[int] = Query(None, ge=1, le=104),
    export_fmt: str = Query("csv", regex="^(csv|xlsx)$"),
):
    """
    Экспорт сводки аналитики в CSV или XLSX.
    Файл отдаётся в браузер как вложение — сохраняется на компьютер пользователя (папка «Загрузки»), не на сервер.
    """
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    dept = analytics_department(user.role_id) or department
    if not dept:
        raise HTTPException(status_code=400, detail="Укажите отдел")
    if dept == "STO":
        raise HTTPException(
            status_code=400,
            detail="Экспорт таблицы критериев для СТО отключён: для отдела СТО на странице доступна только сводка по маркам и видам работ (широкая классификация).",
        )
    try:
        import io
        import csv

        from database.postgresql_manager import CallAnalyticsDB

        data = CallAnalyticsDB.get_period_analytics_detail(
            department=dept,
            manager_name=manager_name,
            call_type=call_type,
            months=months,
            weeks=weeks,
            show_excluded=False,
        )
        labels = _criteria_labels_for_department(dept)
        ts = now_in_report_zone().strftime("%Y%m%d_%H%M%S")
        safe_dept = (dept or "X").replace("/", "-")
        base_name = f"analytics_{safe_dept}_{ts}"

        meta_rows = [
            ["Отчёт: аналитика качества звонков"],
            ["Сформировано", now_in_report_zone().strftime("%Y-%m-%d %H:%M")],
            ["Период с", data.get("date_from") or "", "по", data.get("date_to") or ""],
            ["Отдел", "СТО" if dept == "STO" else "Отдел продаж"],
            ["Менеджер", (manager_name or "").strip() or "Все"],
            ["Вид звонка", call_type or "Все"],
            ["Звонков с оценкой", str(data.get("call_count") or 0)],
            [],
            ["Критерий", "Среднее", "Мин.", "Макс.", "N"],
        ]

        data_rows = []
        ov = data.get("overall")
        if ov:
            data_rows.append(
                ["Средняя оценка за звонок", ov["avg"], ov["min"], ov["max"], ov["n"]]
            )
        crit = data.get("criteria") or {}
        for key, label in labels.items():
            st = crit.get(key)
            if st:
                data_rows.append([label, st["avg"], st["min"], st["max"], st["n"]])
            else:
                data_rows.append([label, "", "", "", ""])

        if export_fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            for row in meta_rows:
                w.writerow(row)
            for row in data_rows:
                w.writerow(row)
            body = buf.getvalue().encode("utf-8-sig")
            return Response(
                content=body,
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{base_name}.csv"',
                    "Cache-Control": "no-store",
                },
            )

        try:
            from openpyxl import Workbook
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="Для Excel установите пакет: pip install openpyxl",
            )
        wb = Workbook()
        ws = wb.active
        ws.title = "Аналитика"
        for row in meta_rows:
            if row:
                ws.append(row)
        for row in data_rows:
            ws.append(row)
        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        return Response(
            content=bio.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{base_name}.xlsx"',
                "Cache-Control": "no-store",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/criteria")
def analytics_criteria(
    department: Optional[str] = Query(None),
    user: AdminUser = Depends(get_current_user),
):
    """Список критериев оценки для выбранного отдела."""
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    op_criteria = _op_criteria_labels()
    sto_criteria = _sto_criteria_labels()
    restricted = analytics_department(user.role_id)
    dept = restricted if restricted is not None else department
    if dept == "STO":
        return {"criteria": sto_criteria}
    elif dept == "OP":
        return {"criteria": op_criteria}
    return {"criteria_op": op_criteria, "criteria_sto": sto_criteria}


# Статика и главная страница
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Страница входа. Если уже авторизован — редирект на главную."""
    u = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if u:
        return RedirectResponse(url="/", status_code=302)
    html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Главная — список звонков. Хостес (роль 3) перенаправляется на лиды."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role_id == 3:
        return RedirectResponse(url="/leads", status_code=302)
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Вставляем role_id для JS
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/call/{call_id}", response_class=HTMLResponse)
def call_page(request: Request, call_id: int):
    """Страница карточки звонка."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    html = (STATIC_DIR / "call.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/body-calls", response_class=HTMLResponse)
def body_calls_page(request: Request):
    """Отдельная вкладка кузовного цеха (только роли 5/6/8)."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role_id not in (5, 6, 8):
        raise HTTPException(status_code=403, detail="Нет доступа к вкладке «Кузовной цех»")
    html = (STATIC_DIR / "body_calls.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/api/body-calls")
def list_body_calls(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    manager_name: Optional[str] = Query(None),
    call_type: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    order: str = Query("asc", description="asc=сначала утро, desc=сначала вечер"),
):
    """Звонки кузовного направления: сессии voice-bot с BODY + соответствующие записи SPRecord."""
    if user.role_id not in (5, 6, 8):
        raise HTTPException(status_code=403, detail="Нет доступа к вкладке «Кузовной цех»")
    try:
        from database.postgresql_manager import CallAnalyticsDB, VoiceBotAnalyticsDB

        # Берём расширенное окно поиска, затем нормализуем по выбранному диапазону.
        sessions = VoiceBotAnalyticsDB.list_sessions(
            date_from=date_from,
            date_to=date_to,
            limit=5000,
            offset=0,
            order="asc",
            transfer_filter="yes",
            role_id=user.role_id,
        )
        body_sessions = []
        for s in sessions:
            cat = str(s.get("last_transfer_category") or "").strip().upper()
            ami = str(s.get("last_transfer_ami_result") or "").strip().lower()
            if cat == "BODY" and ami == "transfer_started":
                body_sessions.append(s)

        calls = CallAnalyticsDB.list_calls(
            date_from=date_from,
            date_to=date_to,
            call_source="sprecord",
            limit=5000,
            offset=0,
            order="asc",
        )

        def _parse_session_dt(v: Any) -> Optional[datetime]:
            if v is None:
                return None
            s = str(v).strip()
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None

        def _parse_call_dt(r: Dict[str, Any]) -> Optional[datetime]:
            d = str(r.get("call_date") or "").strip()
            t = str(r.get("call_time") or "").strip()
            if not d or not t:
                return None
            if len(t) >= 8:
                t = t[:8]
            try:
                return datetime.fromisoformat(f"{d} {t}")
            except Exception:
                return None

        call_items = []
        for c in calls:
            dt = _parse_call_dt(c)
            if dt is None:
                continue
            call_items.append((c, dt))

        used_call_ids: set[int] = set()
        matched_rows: List[Dict[str, Any]] = []
        max_diff_sec = 2 * 60 * 60  # до 2 часов от сессии: безопасный диапазон для пост-перевода.

        for s in body_sessions:
            sdt = _parse_session_dt(s.get("started_at"))
            if sdt is None:
                continue
            if sdt.tzinfo is not None:
                sdt = sdt.replace(tzinfo=None)
            best_idx = -1
            best_diff = None
            for i, (row, cdt) in enumerate(call_items):
                try:
                    rid = int(row.get("id"))
                except Exception:
                    continue
                if rid in used_call_ids:
                    continue
                diff = abs((cdt - sdt).total_seconds())
                if diff > max_diff_sec:
                    continue
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best_idx = i
            if best_idx >= 0:
                row = dict(call_items[best_idx][0])
                row["body_match_diff_sec"] = int(best_diff or 0)
                matched_rows.append(row)
                try:
                    used_call_ids.add(int(row.get("id")))
                except Exception:
                    pass

        if manager_name:
            m = manager_name.strip().lower()
            matched_rows = [r for r in matched_rows if m in str(r.get("manager_name") or "").lower()]
        if call_type:
            ct = call_type.strip().upper()
            matched_rows = [r for r in matched_rows if str(r.get("call_type") or "").upper() == ct]

        matched_rows.sort(
            key=lambda r: (str(r.get("call_date") or ""), str(r.get("call_time") or "")),
            reverse=(str(order).lower() != "asc"),
        )
        sliced = matched_rows[offset : offset + limit]
        _normalize_manager_field_in_call_rows(sliced)
        return JSONResponse(content=_serialize(sliced), headers={"Cache-Control": "no-store, no-cache"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    """Страница загрузки файлов."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_upload(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к загрузке")
    html = (STATIC_DIR / "upload.html").read_text(encoding="utf-8")
    sp_flag = "true" if can_upload_from_sprecord(user.role_id) else "false"
    html = html.replace(
        "</head>",
        '<script>window.ADMIN_ROLE_ID = %d; window.ADMIN_CAN_SPRECORD_UPLOAD = %s;</script>\n</head>'
        % (user.role_id, sp_flag),
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


# ————— API лидов —————
CHANNEL_DISPLAY = {
    "chery_tenet": "ОП Тенет",
    "new_cars_chery_tenet": "ОП новые авто (Чери/Тенет)",
    "service": "Слесарный цех",
    "used_cars": "авто с пробегом",
    "body_repair": "Кузовной цех",
    "spares": "отдел запчастей",
    "parts": "запчасти (голос)",
    "secretary": "прочие",
    "accounting": "администратор / бухгалтерия",
    "other": "иные вопросы",
    "service_cost": "стоимость работ",
    "jetour": "ОП (архив)",
}
SOURCE_DISPLAY = {
    "telegram": "ТГ-бот",
    "phone": "голосовой бот",
    "max": "MAX-бот",
    "infolada_cdr": "Инфолада (CDR)",
}

def _lead_voice_outcome_display(row: dict) -> str:
    """
    Колонка «Исход звонка» (лиды): только перевод в отдел (по журналу AMI) либо «-».
    Не дублируем текстовые статусы «обрыв», «сессия завершена» и т.п.
    """
    from database.postgresql_manager import TelegramLeadsDB

    dash = "-"
    if (row.get("source") or "").strip() != "phone":
        return dash
    cat = (row.get("voice_last_transfer_category") or "").strip()
    if cat:
        return TelegramLeadsDB.VOICE_TRANSFER_CATEGORY_DISPLAY.get(cat, cat)
    code = (row.get("voice_contact_outcome") or "").strip()
    if code == "transfer_started":
        return "Перевод к сотруднику"
    return dash


def _build_service_booking_view(row: Dict[str, Any]) -> None:
    """
    Обогащает строку лида признаком записи на ТО и текстом для ячейки «Потребность».
    """
    src = (row.get("source") or "").strip()
    need_type = (row.get("need_type") or "").strip()
    need_text = (row.get("need_text") or "").strip()
    car_brand = (row.get("car_brand") or "").strip()
    car_model = (row.get("car_model") or "").strip()
    car_mileage = (row.get("car_mileage") or "").strip()
    work_wishes = (row.get("work_wishes") or "").strip()

    has_booking_payload = any([car_brand, car_model, car_mileage, work_wishes])
    has_booking_marker = "1c booking:" in need_text.lower()
    is_service_booking = (
        src in ("phone", "max")
        and need_type == "service"
        and (has_booking_payload or has_booking_marker)
    )

    booking_source = None
    booking_label = ""
    if is_service_booking:
        if src == "phone":
            booking_source = "voice_bot"
            booking_label = "Запись на ТО голосовой бот"
        elif src == "max":
            booking_source = "max"
            booking_label = "Запись на ТО MAX"

    parts: List[str] = []
    if booking_label:
        parts.append(booking_label)
    if car_brand or car_model:
        parts.append(" ".join(p for p in [car_brand, car_model] if p).strip())
    if car_mileage:
        parts.append(f"{car_mileage} км")
    if work_wishes:
        parts.append(work_wishes)

    base_need = (row.get("lead_need_display") or "").strip() or "—"
    row["service_booking"] = bool(is_service_booking and booking_source)
    row["service_booking_source"] = booking_source
    row["service_booking_label"] = booking_label
    row["lead_need_display_rich"] = " | ".join(parts) if parts else base_need


def _filter_leads_by_service_booking(
    rows: List[Dict[str, Any]], service_booking: Optional[str]
) -> List[Dict[str, Any]]:
    """Фильтр узкой рубрики «запись на ТО» для страницы «Лиды» (only | voice | max)."""
    sb = (service_booking or "").strip().lower()
    if not sb:
        return rows
    if sb == "only":
        return [r for r in rows if bool(r.get("service_booking"))]
    if sb == "voice":
        return [
            r
            for r in rows
            if bool(r.get("service_booking")) and (r.get("service_booking_source") == "voice_bot")
        ]
    if sb == "max":
        return [
            r
            for r in rows
            if bool(r.get("service_booking")) and (r.get("service_booking_source") == "max")
        ]
    return rows


def _enrich_lead_rows_for_display(rows: List[Dict[str, Any]], *, role_id: int) -> List[Dict[str, Any]]:
    rows = filter_rows_hide_internal_test_phone(rows, role_id=role_id, phone_key="client_phone")
    for r in rows:
        src = (r.get("source") or "").strip()
        r["source_display"] = SOURCE_DISPLAY.get(src, src or "—")
        r["voice_outcome_display"] = _lead_voice_outcome_display(r)
        _build_service_booking_view(r)
        _mask_lead_need_for_phone(r, role_id=role_id)
    return rows


def _to_booking_count_by_calendar_day(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        if not r.get("service_booking"):
            continue
        created = r.get("created_at")
        if not created:
            continue
        ds = created.isoformat()[:10] if hasattr(created, "isoformat") else str(created)[:10]
        if ds:
            out[ds] = out.get(ds, 0) + 1
    return out


def _fetch_leads_for_admin(
    user: AdminUser,
    *,
    date_from: Optional[str],
    date_to: Optional[str],
    channel: Optional[str],
    source: Optional[str],
    working_hours: Optional[str],
    incoming_number: Optional[str],
    voice_outcome: Optional[str],
    hostess_followup: Optional[str],
    service_booking: Optional[str],
    limit: int = 500,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    df, dt = _apply_leads_date_filter(user, date_from, date_to)
    ch, need_in = _leads_channel_query_params(user, channel)
    from database.postgresql_manager import TelegramLeadsDB

    rows = TelegramLeadsDB.list_leads(
        date_from=df,
        date_to=dt,
        channel=ch,
        need_types_in=need_in,
        source_filter=source,
        working_hours=working_hours,
        incoming_number_filter=incoming_number,
        voice_contact_outcome=voice_outcome,
        hostess_followup_only=(
            str(hostess_followup or "").strip().lower() in ("1", "true", "yes")
        ),
        limit=limit,
        offset=offset,
        role_id=user.role_id,
    )
    rows = _enrich_lead_rows_for_display(rows, role_id=user.role_id)
    return _filter_leads_by_service_booking(rows, service_booking)


def _leads_channel_query_params(user: AdminUser, channel: Optional[str]) -> Tuple[Optional[str], Optional[List[str]]]:
    """Для ролей ОП/СТО: один канал или фильтр need_type IN (...). Иначе channel как пришёл."""
    allow = leads_need_types_allowlist(user.role_id)
    if allow is None:
        return channel, None
    if channel:
        if channel not in allow:
            raise HTTPException(status_code=400, detail="Канал недоступен для вашей роли")
        return channel, None
    return None, list(allow)


@app.get("/api/leads")
def list_leads(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    working_hours: Optional[str] = Query(None, regex="^(working|non_working)$"),
    incoming_number: Optional[str] = Query(None),
    voice_outcome: Optional[str] = Query(
        None,
        description="Голос: bot_only|no_operator|transfer_started|unknown|unset",
    ),
    hostess_followup: Optional[str] = Query(
        None,
        description="Только лиды для обзвона хостес: без перевода (1|true|yes)",
    ),
    service_booking: Optional[str] = Query(
        None,
        regex="^(only|voice|max)$",
        description="Фильтр по записи на ТО: only|voice|max",
    ),
    limit: int = Query(500, le=1000),
    offset: int = Query(0, ge=0),
):
    """Список лидов с фильтрами. По роли: ограничение дат (вчера+сегодня)."""
    if not can_access_leads(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к лидам")
    try:
        rows = _fetch_leads_for_admin(
            user,
            date_from=date_from,
            date_to=date_to,
            channel=channel,
            source=source,
            working_hours=working_hours,
            incoming_number=incoming_number,
            voice_outcome=voice_outcome,
            hostess_followup=hostess_followup,
            service_booking=service_booking,
            limit=limit,
            offset=offset,
        )
        return _serialize(rows)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/leads/unanswered")
def list_unanswered_calls(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    incoming_number: Optional[str] = Query(None),
    limit: int = Query(200, le=500),
    offset: int = Query(0, ge=0),
):
    """Непринятые входящие звонки (бот занят, сброс и т.д.)."""
    if not can_access_leads(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к лидам")
    df, dt = _apply_leads_date_filter(user, date_from, date_to)
    try:
        from database.postgresql_manager import IncomingCallsDB
        rows = IncomingCallsDB.list_incoming_calls(
            date_from=df, date_to=dt, did_filter=incoming_number, limit=limit, offset=offset
        )
        return _serialize(rows)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/leads/report")
def leads_report(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    working_hours: Optional[str] = Query(None, regex="^(working|non_working)$"),
    voice_outcome: Optional[str] = Query(None),
):
    """Отчёт по лидам: число по каналу и по источнику. По роли: ограничение дат."""
    if not can_access_leads(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к лидам")
    df, dt = _apply_leads_date_filter(user, date_from, date_to)
    try:
        ch, need_in = _leads_channel_query_params(user, channel)
        from database.postgresql_manager import TelegramLeadsDB, VoiceBotAnalyticsDB

        report = TelegramLeadsDB.get_leads_report(
            date_from=df, date_to=dt,
            channel=ch, need_types_in=need_in, source_filter=source, working_hours=working_hours,
            voice_contact_outcome=voice_outcome,
        )
        # Добавляем отображаемые названия
        by_ch = {CHANNEL_DISPLAY.get(k, k): v for k, v in report["by_channel"].items()}
        by_src = {SOURCE_DISPLAY.get(k, k): v for k, v in report["by_source"].items()}
        by_vo = report.get("by_voice_contact_outcome") or {}
        # Та же сводка по сессиям голосового бота, что на странице «Переводы бота» (период совпадает с фильтром дат лидов)
        vdf, vdt = df, dt
        if vdf and not vdt:
            vdt = vdf
        elif vdt and not vdf:
            vdf = vdt
        if not vdf or not vdt:
            vdf, vdt = _voice_bot_report_date_pair(date_from, date_to)
        try:
            voice_daily_summary = VoiceBotAnalyticsDB.daily_summary(
                date_from=vdf, date_to=vdt, role_id=user.role_id,
            )
        except Exception:
            logger.exception("voice_daily_summary for leads_report")
            voice_daily_summary = []
        phone_by_day = TelegramLeadsDB.phone_leads_row_count_by_calendar_day(vdf, vdt)
        to_booking_by_day: Dict[str, int] = {}
        try:
            enriched = _fetch_leads_for_admin(
                user,
                date_from=date_from,
                date_to=date_to,
                channel=channel,
                source=source,
                working_hours=working_hours,
                incoming_number=None,
                voice_outcome=voice_outcome,
                hostess_followup=None,
                service_booking="only",
                limit=5000,
                offset=0,
            )
            to_booking_by_day = _to_booking_count_by_calendar_day(enriched)
        except Exception:
            logger.exception("to_booking_by_day for leads_report")
        for vs in voice_daily_summary:
            d = vs.get("day")
            ds = d.isoformat() if hasattr(d, "isoformat") else (str(d)[:10] if d else "")
            vs["phone_leads_rows"] = phone_by_day.get(ds, 0) if ds else 0
            vs["to_booking_rows"] = to_booking_by_day.get(ds, 0) if ds else 0
        return _serialize(
            {
                "by_channel": by_ch,
                "by_source": by_src,
                "by_voice_contact_outcome": by_vo,
                "voice_daily_summary": voice_daily_summary,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


# ————— CSV/Excel Export —————

@app.get("/api/leads/export")
def export_leads(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    working_hours: Optional[str] = Query(None, regex="^(working|non_working)$"),
    incoming_number: Optional[str] = Query(None),
    voice_outcome: Optional[str] = Query(None),
    hostess_followup: Optional[str] = Query(None),
    fmt: str = Query("csv", regex="^(csv|xlsx)$"),
):
    """Экспорт лидов в CSV или Excel. Доступно ролям 4-6."""
    if not can_access_leads(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к лидам")
    if user.role_id not in (4, 5, 6, 8):
        raise HTTPException(status_code=403, detail="Нет доступа к экспорту")
    try:
        rows = _fetch_leads_for_admin(
            user,
            date_from=date_from,
            date_to=date_to,
            channel=channel,
            source=source,
            working_hours=working_hours,
            incoming_number=incoming_number,
            voice_outcome=voice_outcome,
            hostess_followup=hostess_followup,
            service_booking=None,
            limit=5000,
            offset=0,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    import io
    import csv

    if fmt == "xlsx":
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Лиды"
            headers = [
                "ID",
                "Дата",
                "Имя клиента",
                "Входящий номер",
                "Телефон клиента",
                "Исход звонка",
                "Причина (хостес)",
                "Последующий звонок (<=24ч, с переводом)",
                "Потребность",
                "Потребность (текст)",
                "Авто марка",
                "Авто модель",
                "Год",
                "Пробег",
                "Пожелания по работам",
            ]
            ws.append(headers)
            for r in rows:
                ws.append([
                    r.get("id"),
                    _cell_export(r.get("created_at")),
                    r.get("client_fio", ""),
                    r.get("incoming_number") or "",
                    r.get("client_phone", ""),
                    _lead_voice_outcome_display(r),
                    r.get("hostess_reason") or "",
                    _cell_export(r.get("followup_transfer_at")),
                    r.get("lead_need_display") or "",
                    r.get("need_text") or "",
                    r.get("car_brand", ""),
                    r.get("car_model", ""),
                    r.get("car_year", ""),
                    r.get("car_mileage", ""),
                    r.get("work_wishes", ""),
                ])
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            filename = f"leads_{df or 'all'}_{dt or 'all'}.xlsx"
            return Response(
                content=buf.getvalue(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except ImportError:
            fmt = "csv"

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "ID",
        "Дата",
        "Имя клиента",
        "Входящий номер",
        "Телефон клиента",
        "Исход звонка",
        "Причина (хостес)",
        "Последующий звонок (<=24ч, с переводом)",
        "Потребность",
        "Потребность (текст)",
        "Авто марка",
        "Авто модель",
        "Год",
        "Пробег",
        "Пожелания по работам",
    ])
    for r in rows:
        writer.writerow([
            r.get("id"),
            _cell_export(r.get("created_at")),
            r.get("client_fio", ""),
            r.get("incoming_number") or "",
            r.get("client_phone", ""),
            _lead_voice_outcome_display(r),
            r.get("hostess_reason") or "",
            _cell_export(r.get("followup_transfer_at")),
            r.get("lead_need_display") or "",
            r.get("need_text") or "",
            r.get("car_brand", ""),
            r.get("car_model", ""),
            r.get("car_year", ""),
            r.get("car_mileage", ""),
            r.get("work_wishes", ""),
        ])
    filename = f"leads_{df or 'all'}_{dt or 'all'}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/calls/export")
def export_calls(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
):
    """Экспорт звонков с оценками качества в CSV. Доступно ролям 4-6."""
    if user.role_id not in (4, 5, 6, 8):
        raise HTTPException(status_code=403, detail="Нет доступа к экспорту")
    dept, dept_in = calls_allowed_departments_for_list(user.role_id, department)
    if dept is None and dept_in is None:
        dept = calls_department(user.role_id) or department
    try:
        from database.postgresql_manager import CallAnalyticsDB
        rows = CallAnalyticsDB.list_calls(
            date_from=date_from,
            date_to=date_to,
            department=dept,
            departments=dept_in,
            limit=5000,
            offset=0,
        )
        _normalize_manager_field_in_call_rows(rows)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    import io, csv
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "ID", "Дата", "Время", "Длительность(с)", "Отдел", "Вн.номер",
        "Менеджер", "Статус", "Общая оценка",
    ])
    for r in rows:
        writer.writerow([
            r.get("id"), str(r.get("call_date", "")), str(r.get("call_time", "")),
            r.get("duration_seconds", ""), r.get("department", ""),
            r.get("internal_number", ""), r.get("manager_name", ""),
            r.get("status", ""), r.get("overall_score", ""),
        ])
    filename = f"calls_{date_from or 'all'}_{date_to or 'all'}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request):
    """Страница лидов."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_access_leads(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к лидам")
    html = (STATIC_DIR / "leads.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    """Страница аналитики — графики качества звонков."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_access_analytics(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа к аналитике")
    html = (STATIC_DIR / "analytics.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


# ————— Справочники СТО (Excel) —————

_EXCEL_FILES = {
    "norma": {"path": BASE_DIR / "norma.xlsx", "label": "Трудоёмкости и цены ТО"},
}


def _check_sto_access(user: AdminUser):
    if user.role_id not in (2, 5, 6, 8):
        raise HTTPException(status_code=403, detail="Доступ только ассистенту СТО, руководителю СТО или полному доступу")


@app.get("/api/sto/files")
def sto_files_list(user: AdminUser = Depends(get_current_user)):
    """Список Excel-справочников СТО."""
    _check_sto_access(user)
    result = []
    for key, info in _EXCEL_FILES.items():
        p = info["path"]
        exists = p.exists()
        size = p.stat().st_size if exists else 0
        mtime = (
            format_datetime_for_report(datetime.fromtimestamp(p.stat().st_mtime, tz=report_zone()))
            if exists
            else None
        )
        result.append({"key": key, "label": info["label"], "exists": exists, "size": size, "updated": mtime})
    return result


@app.get("/api/sto/files/{key}/download")
def sto_file_download(key: str, user: AdminUser = Depends(get_current_user)):
    """Скачать Excel-справочник."""
    _check_sto_access(user)
    if key not in _EXCEL_FILES:
        raise HTTPException(status_code=404, detail="Файл не найден")
    p = _EXCEL_FILES[key]["path"]
    if not p.exists():
        raise HTTPException(status_code=404, detail="Файл отсутствует на сервере")
    return FileResponse(str(p), filename=p.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/sto/files/{key}/upload")
async def sto_file_upload(key: str, file: UploadFile = File(...), user: AdminUser = Depends(get_current_user)):
    """Загрузить обновлённый Excel-справочник."""
    _check_sto_access(user)
    if key not in _EXCEL_FILES:
        raise HTTPException(status_code=404, detail="Неизвестный справочник")
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Допустимы только файлы .xlsx / .xls")
    p = _EXCEL_FILES[key]["path"]
    backup = p.with_suffix(f".backup_{now_in_report_zone().strftime('%Y%m%d_%H%M%S')}.xlsx")
    if p.exists():
        import shutil
        shutil.copy2(p, backup)
        logger.info("Бэкап: %s → %s", p.name, backup.name)
    content = await file.read()
    p.write_bytes(content)
    logger.info("Загружен %s (%d байт) пользователем %s", key, len(content), user.login)
    return {"ok": True, "key": key, "size": len(content), "backup": backup.name}


@app.get("/api/sto/files/{key}/preview")
def sto_file_preview(key: str, user: AdminUser = Depends(get_current_user), sheet: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    """Предпросмотр содержимого Excel (первые N строк)."""
    _check_sto_access(user)
    if key not in _EXCEL_FILES:
        raise HTTPException(status_code=404, detail="Файл не найден")
    p = _EXCEL_FILES[key]["path"]
    if not p.exists():
        raise HTTPException(status_code=404, detail="Файл отсутствует")
    try:
        import pandas as pd
        xls = pd.ExcelFile(p)
        sheet_names = xls.sheet_names
        target_sheet = sheet if sheet in sheet_names else sheet_names[0]
        df = pd.read_excel(xls, sheet_name=target_sheet, nrows=limit)
        df = df.fillna("")
        return {
            "sheets": sheet_names,
            "current_sheet": target_sheet,
            "columns": list(df.columns.astype(str)),
            "rows": df.astype(str).values.tolist(),
            "total_shown": len(df),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения: {e}")


@app.get("/sto", response_class=HTMLResponse)
def sto_page(request: Request):
    """Страница справочников СТО."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role_id not in (2, 5, 6, 8):
        raise HTTPException(status_code=403, detail="Нет доступа")
    html = (STATIC_DIR / "sto.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


# ————— Голосовой бот: журнал переводов и реплик —————


class VoiceBotFeedbackCreate(BaseModel):
    call_uuid: str = Field(..., min_length=8, max_length=80)
    transcript_seq: Optional[int] = Field(None, ge=0)
    comment: str = Field(..., min_length=1, max_length=8000)
    expected_tag: Optional[str] = Field(None, max_length=128)


class VoiceBotFeedbackStatusUpdate(BaseModel):
    status: Literal["new", "reviewed"]


@app.get("/api/voice-bot/sessions")
def api_voice_bot_sessions(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(200, le=500),
    offset: int = Query(0, ge=0),
    order: str = Query("desc", regex="^(asc|desc)$"),
    transfer: Optional[str] = Query(None, regex="^(yes|no)$"),
):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        df, dt = _voice_bot_report_date_pair(date_from, date_to)
        rows = VoiceBotAnalyticsDB.list_sessions(
            date_from=df,
            date_to=dt,
            limit=limit,
            offset=offset,
            order=order,
            transfer_filter=transfer,
            role_id=user.role_id,
        )
        rows = filter_rows_hide_internal_test_phone(
            rows, role_id=user.role_id, phone_key="caller_phone",
        )
        return _serialize(rows)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/voice-bot/sessions/detail/{call_uuid}")
def api_voice_bot_session_detail(call_uuid: str, user: AdminUser = Depends(get_current_user)):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        sess = VoiceBotAnalyticsDB.get_session_by_uuid(call_uuid)
        if not sess:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        _deny_internal_test_voice_session(sess, user)
        sid = int(sess["id"])
        transcript = VoiceBotAnalyticsDB.get_transcript(sid)
        transfers = VoiceBotAnalyticsDB.get_transfer_events(sid)
        return _serialize({"session": sess, "transcript": transcript, "transfers": transfers})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/voice-bot/daily-summary")
def api_voice_bot_daily_summary(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        df, dt = _voice_bot_report_date_pair(date_from, date_to)
        rows = VoiceBotAnalyticsDB.daily_summary(date_from=df, date_to=dt, role_id=user.role_id)
        return _serialize(rows)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/voice-bot/stats-30d")
def api_voice_bot_stats_30d(user: AdminUser = Depends(get_current_user)):
    """Сессии бота за 30 календарных дней: доли «перевод после одной реплики клиента» и «сброс»."""
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        data = VoiceBotAnalyticsDB.last_30_days_session_quality_stats(role_id=user.role_id)
        return _serialize(data)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/voice-bot/export")
def export_voice_bot_sessions(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    fmt: str = Query("csv", regex="^(csv|xlsx)$"),
    order: str = Query("desc", regex="^(asc|desc)$"),
    transfer: Optional[str] = Query(None, regex="^(yes|no)$"),
):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    df, dt = _voice_bot_report_date_pair(date_from, date_to)
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        rows = VoiceBotAnalyticsDB.list_sessions(
            date_from=df,
            date_to=dt,
            limit=5000,
            offset=0,
            order=order,
            transfer_filter=transfer,
            role_id=user.role_id,
        )
        rows = filter_rows_hide_internal_test_phone(
            rows, role_id=user.role_id, phone_key="caller_phone",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    import io
    import csv

    headers = [
        "№",
        "id",
        "call_uuid",
        "incoming_number",
        "caller_phone_client",
        "recognized_client_name",
        "client_request_stt",
        "started_at",
        "ended_at",
        "state_final",
        "client_turns",
        "bot_turns",
        "last_transfer_category",
        "last_admin_reason",
        "last_playback_wav",
        "last_transfer_ami_result",
        "audio_storage_path",
    ]

    if fmt == "xlsx":
        try:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "voice_bot"
            ws.append(headers)
            for idx, r in enumerate(rows, start=1):
                row_cells = [idx] + [_cell_export(r.get(h)) for h in headers[1:]]
                ws.append(row_cells)
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            fn = f"voice_bot_sessions_{df or 'all'}_{dt or 'all'}.xlsx"
            return Response(
                content=buf.getvalue(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{fn}"'},
            )
        except ImportError:
            fmt = "csv"

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(headers)
    for idx, r in enumerate(rows, start=1):
        writer.writerow([idx] + [_cell_export(r.get(h)) for h in headers[1:]])
    fn = f"voice_bot_sessions_{df or 'all'}_{dt or 'all'}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )


@app.get("/api/voice-bot/sessions/detail/{call_uuid}/recording")
def api_voice_bot_session_recording(call_uuid: str, user: AdminUser = Depends(get_current_user)):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        sess = VoiceBotAnalyticsDB.get_session_by_uuid(call_uuid)
        if not sess:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        _deny_internal_test_voice_session(sess, user)
        raw_path = sess.get("audio_storage_path")
        p = _safe_voice_bot_recording_path(str(raw_path) if raw_path else None)
        if not p:
            raise HTTPException(
                status_code=404,
                detail="Запись недоступна (нет пути или не задан VOICE_BOT_RECORDINGS_ROOT)",
            )
        return FileResponse(str(p), filename=p.name, media_type="audio/wav")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voice-bot/feedback")
def api_voice_bot_feedback_create(
    body: VoiceBotFeedbackCreate,
    user: AdminUser = Depends(get_current_user),
):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        sess = VoiceBotAnalyticsDB.get_session_by_uuid(body.call_uuid.strip())
        if not sess:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        _deny_internal_test_voice_session(sess, user)
        sid = int(sess["id"])
        stt_snapshot: Optional[str] = None
        if body.transcript_seq is not None:
            transcript = VoiceBotAnalyticsDB.get_transcript(sid)
            found = False
            for t in transcript:
                if int(t["seq"]) == int(body.transcript_seq):
                    found = True
                    if (t.get("role") or "").lower() != "client":
                        raise HTTPException(
                            status_code=400,
                            detail="Указанный seq не относится к реплике клиента",
                        )
                    stt_snapshot = (t.get("text") or "")[:16000]
                    break
            if not found:
                raise HTTPException(status_code=404, detail="Реплика с таким seq не найдена")
        fid = VoiceBotAnalyticsDB.add_misrecognition_feedback(
            session_id=sid,
            reporter_login=user.login,
            comment=body.comment.strip(),
            transcript_seq=body.transcript_seq,
            stt_text_snapshot=stt_snapshot,
            expected_tag=(body.expected_tag or "").strip() or None,
        )
        if fid is None:
            raise HTTPException(status_code=500, detail="Не удалось сохранить отметку")
        return _serialize({"ok": True, "id": fid})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/voice-bot/feedback")
def api_voice_bot_feedback_list(
    user: AdminUser = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(200, le=500),
    offset: int = Query(0, ge=0),
):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    if status is not None and status not in ("new", "reviewed"):
        raise HTTPException(status_code=400, detail="status: new или reviewed")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        df, dt = _voice_bot_report_date_pair(date_from, date_to)
        st = status if status in ("new", "reviewed") else None
        rows = VoiceBotAnalyticsDB.list_misrecognition_feedback(
            date_from=df,
            date_to=dt,
            status=st,
            limit=limit,
            offset=offset,
        )
        return _serialize(rows)
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/voice-bot/feedback/{feedback_id}")
def api_voice_bot_feedback_set_status(
    feedback_id: int,
    body: VoiceBotFeedbackStatusUpdate,
    user: AdminUser = Depends(get_current_user),
):
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    try:
        from database.postgresql_manager import VoiceBotAnalyticsDB

        ok = VoiceBotAnalyticsDB.update_misrecognition_feedback_status(feedback_id, body.status)
        if not ok:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sprecord-browse", response_class=HTMLResponse)
def sprecord_browse_page(request: Request):
    """Таблица файлов SpRecord по датам + прослушивание (admin1 / импорт SPRecord)."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_upload_from_sprecord(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    html = (STATIC_DIR / "sprecord_browse.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/voice-transfers", response_class=HTMLResponse)
def voice_transfers_page(request: Request):
    """Журнал переводов и реплик голосового бота."""
    user = parse_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_access_voice_transfers(user.role_id):
        raise HTTPException(status_code=403, detail="Нет доступа")
    html = (STATIC_DIR / "voice_transfers.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script>window.ADMIN_ROLE_ID = %d;</script>\n</head>' % user.role_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})
