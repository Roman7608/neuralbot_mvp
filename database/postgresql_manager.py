"""
Менеджер для работы с PostgreSQL.
"""

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Set
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values, Json
from psycopg2.pool import SimpleConnectionPool
from psycopg2 import sql

from postgresql_config import (
    POSTGRESQL_HOST,
    POSTGRESQL_PORT,
    POSTGRESQL_DATABASE,
    POSTGRESQL_ANALYTICS_USER,
    POSTGRESQL_ANALYTICS_PASSWORD,
    POSTGRESQL_POOL_SIZE,
    POSTGRESQL_MAX_OVERFLOW,
    POSTGRESQL_SESSION_TIMEZONE,
)

logger = logging.getLogger(__name__)

# Лиды голосового бота: исход дозвона до сотрудников (админка «Лиды»).
VOICE_CONTACT_OUTCOME_CODES = frozenset(
    {
        "bot_only",
        "no_operator",
        "transfer_started",
        "unknown",
        "hangup_before_transfer",
        "after_hours_partial",
        "session_complete",
    }
)


def _lead_need_types_for_channel_filter(channel: str) -> List[str]:
    """
    Значение фильтра «канал» в UI → need_type в БД.
    Голос пишет new_cars_chery_tenet (ОП новых), ТГ — chery_tenet; запчасти — parts vs spares.
    """
    c = (channel or "").strip()
    if c == "chery_tenet":
        return ["chery_tenet", "new_cars_chery_tenet"]
    if c == "spares":
        return ["spares", "parts"]
    return [c] if c else []


def _synthetic_voice_transfer_sql_filter(
    channel: Optional[str], need_types_in: Optional[List[str]]
) -> Tuple[Optional[List[str]], bool]:
    """
    Фильтр синтетических строк «Лиды» из voice_bot_sessions по последней transfer_category
    (та же логика направлений, что на странице «Переводы бота»).

    Возвращает (список категорий для ANY(...), allow_no_transfer):
    - allow_no_transfer=True: показывать ещё сессии без ни одного события в voice_bot_transfer_events
      (сброс до перевода — для «прочие» / secretary).
    - (None, False): не сужать по категории (канал «Все» и без need_types_in).
    """
    # (категории AMI, разрешить сессии без событий перевода)
    by_ui_channel: Dict[str, Tuple[List[str], bool]] = {
        "chery_tenet": (["OP_CHERY_TENET", "OP_JETOUR"], False),
        "service": (["SERVICE_ASSISTANT", "WORKSHOP_SL"], False),
        "used_cars": (["OP_USED"], False),
        "body_repair": (["BODY"], False),
        "spares": (["PARTS"], False),
        "secretary": (["ADMIN"], True),
    }
    by_need_type: Dict[str, Tuple[List[str], bool]] = {
        "chery_tenet": (["OP_CHERY_TENET", "OP_JETOUR"], False),
        "new_cars_chery_tenet": (["OP_CHERY_TENET", "OP_JETOUR"], False),
        "jetour": (["OP_CHERY_TENET", "OP_JETOUR"], False),
        "service": (["SERVICE_ASSISTANT", "WORKSHOP_SL"], False),
        "used_cars": (["OP_USED"], False),
        "body_repair": (["BODY"], False),
        "spares": (["PARTS"], False),
        "parts": (["PARTS"], False),
        "secretary": (["ADMIN"], True),
        "accounting": (["ADMIN"], False),
        "other": ([], True),
        "service_cost": (["SERVICE_ASSISTANT", "WORKSHOP_SL"], False),
    }
    cat_acc: set[str] = set()
    allow_no = False
    ch = (channel or "").strip()
    if ch and ch in by_ui_channel:
        cats, a = by_ui_channel[ch]
        cat_acc |= set(cats)
        allow_no = allow_no or a
    if need_types_in:
        for nt in need_types_in:
            key = (nt or "").strip()
            if key in by_need_type:
                cats, a = by_need_type[key]
                cat_acc |= set(cats)
                allow_no = allow_no or a
    if not cat_acc and not allow_no:
        return None, False
    return (sorted(cat_acc) if cat_acc else None), allow_no


def _synthetic_session_passes_channel_filters(
    last_transfer_category: Optional[str],
    cats: Optional[List[str]],
    allow_no_xfer: bool,
) -> bool:
    """Те же правила, что в SQL _voice_sessions_without_lead_rows (категория / сброс)."""
    c = (last_transfer_category or "").strip() or None
    if c:
        if not cats:
            return True
        return c in cats
    if allow_no_xfer:
        return True
    if cats:
        return False
    return True


def _synthetic_session_passes_working_hours(started_at: Any, working_hours: Optional[str]) -> bool:
    if not working_hours or started_at is None:
        return True
    try:
        t = started_at
        if hasattr(t, "hour"):
            h, m = int(t.hour), int(t.minute)
        else:
            return True
        mins = h * 60 + m
        if working_hours == "working":
            return 8 * 60 <= mins < 20 * 60
        if working_hours == "non_working":
            return mins >= 20 * 60 or mins < 8 * 60
    except Exception:
        return True
    return True


def _lead_row_calendar_day(created_at: Any) -> Optional[str]:
    """YYYY-MM-DD для сопоставления лид vs сессия голоса за один день."""
    if created_at is None:
        return None
    s = str(created_at).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _phone_last10_digits(phone: Optional[Any]) -> Optional[str]:
    if phone is None:
        return None
    d = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(d) < 10:
        return None
    return d[-10:]


def _is_voice_synthetic_lead_row(row: Dict[str, Any]) -> bool:
    if not row:
        return False
    try:
        if int(row.get("id") or 0) < 0:
            return True
    except (TypeError, ValueError):
        pass
    if (row.get("need_type") or "").strip() == "phone_session":
        return True
    o = (row.get("lead_row_origin") or "").strip()
    return o in ("voice_session_fallback", "voice_list_session_merge")


def _real_lead_phone_day_keys(rows: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """(последние 10 цифр телефона, день YYYY-MM-DD) по «живым» лидам — не phone_session."""
    keys: Set[Tuple[str, str]] = set()
    for r in rows:
        if _is_voice_synthetic_lead_row(r):
            continue
        p10 = _phone_last10_digits(r.get("client_phone"))
        day = _lead_row_calendar_day(r.get("created_at"))
        if p10 and day:
            keys.add((p10, day))
    return keys


def _synthetic_phone_day_key(row: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    p10 = _phone_last10_digits(row.get("client_phone"))
    day = _lead_row_calendar_day(row.get("created_at"))
    if p10 and day:
        return (p10, day)
    return None


def _dedupe_synthetic_voice_rows_against_real(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Убираем синтетические сессии, если за тот же день уже есть лид с тем же номером (оставляем лид с исходом/переводом)."""
    if not rows:
        return rows
    keys = _real_lead_phone_day_keys(rows)
    if not keys:
        return rows
    real = [r for r in rows if not _is_voice_synthetic_lead_row(r)]
    kept_syn: List[Dict[str, Any]] = []
    for s in rows:
        if not _is_voice_synthetic_lead_row(s):
            continue
        k = _synthetic_phone_day_key(s)
        if k is not None and k in keys:
            continue
        kept_syn.append(s)
    return real + kept_syn


def _prefer_voice_lead_row(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """При дублях по voice_bot_session_id: реальный лид (id > 0) важнее синтетики; иначе более поздний created_at."""
    try:
        ida = int(a.get("id") or 0)
        idb = int(b.get("id") or 0)
    except (TypeError, ValueError):
        return b
    if ida > 0 and idb <= 0:
        return a
    if idb > 0 and ida <= 0:
        return b
    ca = str(a.get("created_at") or "")
    cb = str(b.get("created_at") or "")
    return a if ca >= cb else b


def _dedupe_leads_one_per_voice_session(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Одна строка на голосовую сессию (telegram_leads.voice_bot_session_id / сессия из merge).
    Снимает дубли «лид + синтетика» и повтор одного session_id в выборке.
    """
    if not rows:
        return rows
    by_vb: Dict[int, Dict[str, Any]] = {}
    no_vb: List[Dict[str, Any]] = []
    for r in rows:
        vid = r.get("voice_bot_session_id")
        if vid is None:
            no_vb.append(r)
            continue
        try:
            vk = int(vid)
        except (TypeError, ValueError):
            no_vb.append(r)
            continue
        if vk not in by_vb:
            by_vb[vk] = r
        else:
            by_vb[vk] = _prefer_voice_lead_row(by_vb[vk], r)
    merged = list(by_vb.values()) + no_vb
    merged.sort(
        key=lambda x: (x.get("created_at") is None, str(x.get("created_at") or "")),
        reverse=True,
    )
    return merged


def _normalize_voice_contact_outcome(value: Optional[str]) -> Optional[str]:
    s = (value or "").strip()
    if not s:
        return None
    return s if s in VOICE_CONTACT_OUTCOME_CODES else None


def _dealer_wall_now_naive() -> datetime:
    """
    «Стеночное» время дилера для колонок TIMESTAMP WITHOUT TIME ZONE.
    Не зависит от timezone сессии PostgreSQL — то же значение, что в POSTGRESQL_SESSION_TIMEZONE.
    """
    try:
        return datetime.now(ZoneInfo(POSTGRESQL_SESSION_TIMEZONE)).replace(tzinfo=None)
    except Exception:
        # Fallback: TZ контейнера / системы (как datetime.now() при выставленном TZ)
        return datetime.now().astimezone().replace(tzinfo=None)


class PostgreSQLManager:
    """Менеджер подключений к PostgreSQL."""
    
    _pool: Optional[SimpleConnectionPool] = None
    # psycopg2 connection — C-объект, на него нельзя вешать произвольные атрибуты (ломало save_lead).
    _tz_applied_conn_ids: set[int] = set()
    
    @classmethod
    def get_pool(cls) -> SimpleConnectionPool:
        """Получить пул соединений (singleton)."""
        if cls._pool is None:
            cls._pool = SimpleConnectionPool(
                minconn=1,
                maxconn=POSTGRESQL_POOL_SIZE + POSTGRESQL_MAX_OVERFLOW,
                host=POSTGRESQL_HOST,
                port=POSTGRESQL_PORT,
                database=POSTGRESQL_DATABASE,
                user=POSTGRESQL_ANALYTICS_USER,
                password=POSTGRESQL_ANALYTICS_PASSWORD,
            )
        return cls._pool
    
    @classmethod
    @contextmanager
    def get_connection(cls):
        """Контекстный менеджер для получения соединения из пула."""
        pool = cls.get_pool()
        conn = pool.getconn()
        try:
            # Местное время дилера: DEFAULT CURRENT_TIMESTAMP, DATE()/TIME фильтры, отображение в админке.
            _cid = id(conn)
            if _cid not in cls._tz_applied_conn_ids:
                with conn.cursor() as _tz_cur:
                    _tz_cur.execute("SET SESSION TIME ZONE %s", (POSTGRESQL_SESSION_TIMEZONE,))
                cls._tz_applied_conn_ids.add(_cid)
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка в транзакции: {e}")
            raise
        finally:
            pool.putconn(conn)
    
    @classmethod
    def close_pool(cls):
        """Закрыть пул соединений."""
        if cls._pool:
            cls._pool.closeall()
            cls._pool = None
        cls._tz_applied_conn_ids.clear()


def _clip_str(value: Optional[str], max_len: int, field: str) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    logger.warning("telegram_leads.%s обрезано с %s до %s символов", field, len(value), max_len)
    return value[:max_len]


class TelegramLeadsDB:
    """Работа с лидами из Telegram в PostgreSQL."""

    # transfer_category из voice_bot_transfer_events → колонка «Перевод» (лиды, source=phone)
    VOICE_TRANSFER_CATEGORY_DISPLAY = {
        "ADMIN": "Администратор",
        "SERVICE_ASSISTANT": "Ассистент сервиса",
        # В отчёте «Переводы бота» слесарный цех суммируется с ассистентом сервиса — та же подпись в «Лидах».
        "WORKSHOP_SL": "Ассистент сервиса",
        "BODY": "Кузовной цех",
        "OP_USED": "Отдел продаж с пробегом",
        "OP_CHERY_TENET": "Отдел продаж Чери и Тенет",
        "OP_JETOUR": "Отдел продаж Чери и Тенет",
        "PARTS": "Отдел запчастей",
    }

    @staticmethod
    def _annotate_voice_transfer_display(row: Dict[str, Any]) -> None:
        """Поле voice_transfer_display: куда перевод (голос) / сброс; для MAX — тип лида или текст."""
        src = (row.get("source") or "telegram").strip()
        if src == "max":
            nt = (row.get("need_type") or "").strip()
            disp = TelegramLeadsDB.CHANNEL_DISPLAY_NAMES.get(nt, "")
            if disp:
                row["voice_transfer_display"] = disp
            else:
                txt = (row.get("need_text") or "").strip()
                row["voice_transfer_display"] = (
                    (txt[:100] + ("…" if len(txt) > 100 else "")) if txt else "—"
                )
            return
        if src != "phone":
            row["voice_transfer_display"] = "—"
            return
        cat = row.get("voice_last_transfer_category")
        if cat is not None and str(cat).strip():
            c = str(cat).strip()
            row["voice_transfer_display"] = TelegramLeadsDB.VOICE_TRANSFER_CATEGORY_DISPLAY.get(
                c, c
            )
            return
        vco = (row.get("voice_contact_outcome") or "").strip()
        if vco == "hangup_before_transfer":
            row["voice_transfer_display"] = "—"
            return
        if vco == "transfer_started":
            # Категорию AMI смотрим выше по voice_last_transfer_category (Администратор / Ассистент сервиса и т.д.).
            row["voice_transfer_display"] = "—"
            return
        if vco == "no_operator":
            row["voice_transfer_display"] = "Перевод не выполнен (нет оператора)"
            return
        if vco == "after_hours_partial":
            row["voice_transfer_display"] = "Нерабочее время (обрыв)"
            return
        if vco == "session_complete":
            row["voice_transfer_display"] = "Без перевода (сессия завершена)"
            return
        if vco == "bot_only":
            row["voice_transfer_display"] = "Только бот (нерабочее время)"
            return
        if vco == "unknown":
            row["voice_transfer_display"] = "Уточнить (неизвестный исход)"
            return
        row["voice_transfer_display"] = "—"

    # Потребность (голос): значение ClientNeed / админ при нерабочем — для колонки «Лиды».
    LEAD_VOICE_NEED_DISPLAY_RU = {
        "used_cars": "Автомобили с пробегом",
        "new_cars_chery_tenet": "Новые авто Чери и Тэнет",
        "service": "Сервис (слесарный цех)",
        "service_cost": "Стоимость работ",
        "parts": "Запчасти",
        "spares": "Запчасти",
        "body_repair": "Кузовной ремонт",
        "accounting": "Бухгалтерия / администратор",
        "secretary": "Администратор",
        "admin": "Администратор",
        "other": "Прочее",
        "unknown": "—",
        "phone_session": "—",
    }

    @staticmethod
    def _annotate_lead_need_display(row: Dict[str, Any]) -> None:
        """
        Колонка «Потребность» на странице «Лиды» (нерабочее время и др.: отдел из save_lead / need_type).
        """
        wh = row.get("working_hours")
        non_working = wh is False or wh == 0
        nt = (row.get("need_type") or "").strip().lower()
        src = (row.get("source") or "telegram").strip().lower()
        if not nt or nt == "phone_session":
            row["lead_need_display"] = "—"
            return
        label = TelegramLeadsDB.LEAD_VOICE_NEED_DISPLAY_RU.get(nt)
        if label:
            row["lead_need_display"] = label
            return
        label = TelegramLeadsDB.CHANNEL_DISPLAY_NAMES.get(nt)
        if label:
            row["lead_need_display"] = label
            return
        if non_working or src == "phone":
            row["lead_need_display"] = nt[:80] if nt else "—"
        else:
            row["lead_need_display"] = "—"

    @staticmethod
    def _enrich_lead_need_display_from_voice_stt(rows: List[Dict[str, Any]]) -> None:
        """
        Для строк с voice_bot_session_id колонка «Потребность» = тот же агрегат STT,
        что «Что запросил (STT)» в «Переводах бота» (до 2000 символов, как в list_sessions).
        Если STT пустой — остаётся значение из _annotate_lead_need_display (need_type).
        """
        ids: List[int] = []
        for r in rows:
            vid = r.get("voice_bot_session_id")
            if vid is None:
                continue
            try:
                ids.append(int(vid))
            except (TypeError, ValueError):
                pass
        if not ids:
            return
        by_sid = VoiceBotAnalyticsDB.batch_client_stt_summary(ids)
        for r in rows:
            vid = r.get("voice_bot_session_id")
            if vid is None:
                continue
            try:
                iv = int(vid)
            except (TypeError, ValueError):
                continue
            stt = (by_sid.get(iv) or "").strip()
            if not stt:
                continue
            # Совпадает с LEFT(..., 2000) в _SQL_CLIENT_STT_SUMMARY
            r["lead_need_display"] = stt if len(stt) <= 2000 else (stt[:1997] + "…")

    # Маппинг need_type → отображаемое название канала для UI (ТГ + голос + MAX)
    CHANNEL_DISPLAY_NAMES = {
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
        # Строка, синтезированная из voice_bot_sessions, если лид в telegram_leads не создался
        "phone_session": "голос: сессия (лид в БД не создан)",
    }

    @staticmethod
    def _voice_sessions_without_lead_rows(
        *,
        date_from: Optional[str],
        date_to: Optional[str],
        source_filter: Optional[str],
        working_hours: Optional[str],
        voice_contact_outcome: Optional[str],
        channel: Optional[str],
        need_types_in: Optional[List[str]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """
        Для страницы «Лиды»: если в telegram_leads нет строки (voice не смог save_lead и т.п.),
        показываем сессии голосового бота с теми же колонками, что и у лида — как раньше ожидалось по сводке.
        """
        if source_filter == "max":
            return []
        if voice_contact_outcome and str(voice_contact_outcome).strip().lower() not in ("", "unset"):
            return []
        try:
            cond = ["NOT EXISTS (SELECT 1 FROM telegram_leads tl WHERE tl.voice_bot_session_id = s.id)"]
            params: List[Any] = []
            if date_from:
                cond.append("DATE(s.started_at) >= %s")
                params.append(date_from)
            if date_to:
                cond.append("DATE(s.started_at) <= %s")
                params.append(date_to)
            if working_hours == "working":
                cond.append("(s.started_at::time >= TIME '08:00' AND s.started_at::time < TIME '20:00')")
            elif working_hours == "non_working":
                cond.append("(s.started_at::time >= TIME '20:00' OR s.started_at::time < TIME '08:00')")
            xfer_cat_sub = (
                "(SELECT te.transfer_category FROM voice_bot_transfer_events te "
                "WHERE te.session_id = s.id ORDER BY te.logged_at DESC LIMIT 1)"
            )
            no_xfer = "NOT EXISTS (SELECT 1 FROM voice_bot_transfer_events e0 WHERE e0.session_id = s.id)"
            cats, allow_no_xfer = _synthetic_voice_transfer_sql_filter(channel, need_types_in)
            if cats and allow_no_xfer:
                cond.append(f"({no_xfer} OR {xfer_cat_sub} = ANY(%s))")
                params.append(cats)
            elif cats:
                cond.append(f"({xfer_cat_sub} IS NOT NULL AND {xfer_cat_sub} = ANY(%s))")
                params.append(cats)
            elif allow_no_xfer:
                cond.append(no_xfer)

            where = " AND ".join(cond)
            params.append(max(1, min(int(limit), 2000)))
            # Те же COALESCE телефона/имени, что в VoiceBotAnalyticsDB.list_sessions («Переводы бота»).
            sql_name = VoiceBotAnalyticsDB._SQL_RECOGNIZED_CLIENT_NAME
            sql_phone = VoiceBotAnalyticsDB._SQL_CALLER_PHONE_COALESCE
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"""
                        SELECT
                            -s.id AS id,
                            0 AS telegram_user_id,
                            s.id AS voice_bot_session_id,
                            COALESCE({sql_name}, '') AS client_fio,
                            COALESCE({sql_phone}, '') AS client_phone,
                            'phone_session' AS need_type,
                            'Сессия голосового бота: запись в telegram_leads отсутствует.' AS need_text,
                            'phone_session' AS department,
                            'phone'::text AS source,
                            TRUE AS working_hours,
                            NULL::text AS car_brand,
                            NULL::text AS car_model,
                            NULL::text AS car_year,
                            NULL::text AS car_mileage,
                            NULL::text AS work_wishes,
                            NULL::text AS voice_contact_outcome,
                            s.started_at AS created_at,
                            (
                                SELECT te.transfer_category
                                FROM voice_bot_transfer_events te
                                WHERE te.session_id = s.id
                                ORDER BY te.logged_at DESC
                                LIMIT 1
                            ) AS voice_last_transfer_category
                        FROM voice_bot_sessions s
                        WHERE {where}
                        ORDER BY s.started_at DESC
                        LIMIT %s
                        """,
                        params,
                    )
                    out = [dict(r) for r in cur.fetchall()]
                    for r in out:
                        r["lead_row_origin"] = "voice_session_fallback"
                        TelegramLeadsDB._annotate_voice_transfer_display(r)
                    return out
        except Exception as e:
            logger.warning("voice_sessions_without_lead_rows: %s", e)
            return []

    @staticmethod
    def _lead_dict_from_voice_list_session(s: Dict[str, Any]) -> Dict[str, Any]:
        """Одна строка «как лид» из записи VoiceBotAnalyticsDB.list_sessions (страница «Переводы бота»)."""
        stt = (s.get("client_request_stt") or "").strip()
        nt = "phone_session"
        need_text = (
            (stt[:500] + "…")
            if len(stt) > 500
            else (stt or "Сессия голосового бота — данные из журнала сессий (как «Переводы бота»).")
        )
        vbs = s.get("voice_bot_session_id")
        if vbs is None:
            raise ValueError("list_sessions row без voice_bot_session_id для merge в лиды")
        sid = int(vbs)
        row: Dict[str, Any] = {
            "id": -sid,
            "telegram_user_id": 0,
            "voice_bot_session_id": sid,
            "client_fio": (s.get("recognized_client_name") or "").strip(),
            "client_phone": (s.get("caller_phone") or "").strip(),
            "need_type": nt,
            "need_text": need_text,
            "department": nt,
            "source": "phone",
            "working_hours": True,
            "car_brand": None,
            "car_model": None,
            "car_year": None,
            "car_mileage": None,
            "work_wishes": None,
            "voice_contact_outcome": None,
            "created_at": s.get("started_at"),
            "voice_last_transfer_category": s.get("last_transfer_category"),
            "lead_row_origin": "voice_list_session_merge",
        }
        TelegramLeadsDB._annotate_voice_transfer_display(row)
        return row

    @staticmethod
    def _merge_voice_list_sessions_into_leads(
        rows: List[Dict[str, Any]],
        *,
        date_from: Optional[str],
        date_to: Optional[str],
        source_filter: Optional[str],
        working_hours: Optional[str],
        voice_contact_outcome: Optional[str],
        channel: Optional[str],
        need_types_in: Optional[List[str]],
        limit: int,
        role_id: Optional[int] = None,
    ) -> None:
        """
        Дополняет выдачу сессиями из list_sessions, если сессия за период по started_at есть,
        а строки лида в текущем списке нет (лид с другим DATE(created_at), отфильтрован и т.д.).
        """
        if source_filter == "max":
            return
        if voice_contact_outcome and str(voice_contact_outcome).strip().lower() not in ("", "unset"):
            return
        cats, allow_no_xfer = _synthetic_voice_transfer_sql_filter(channel, need_types_in)
        covered: set[int] = set()
        for r in rows:
            vid = r.get("voice_bot_session_id")
            if vid is not None:
                try:
                    covered.add(int(vid))
                except (TypeError, ValueError):
                    pass
        blocked_phone_day = _real_lead_phone_day_keys(rows)
        cap = max(500, min(int(limit) * 10, 5000))
        try:
            sessions = VoiceBotAnalyticsDB.list_sessions(
                date_from=date_from,
                date_to=date_to,
                limit=cap,
                offset=0,
                order="desc",
                role_id=role_id,
            )
        except Exception as e:
            logger.warning("merge_voice_list_sessions: list_sessions: %s", e)
            return
        from internal_test_phone import admin1_sees_internal_test_phone, is_internal_test_phone

        for s in sessions:
            if not admin1_sees_internal_test_phone(role_id) and is_internal_test_phone(
                s.get("caller_phone")
            ):
                continue
            vbsid = s.get("voice_bot_session_id")
            if vbsid is None:
                continue
            try:
                sid = int(vbsid)
            except (TypeError, ValueError):
                continue
            if sid in covered:
                continue
            p10d = _phone_last10_digits(s.get("caller_phone"))
            dayd = _lead_row_calendar_day(s.get("started_at"))
            if p10d and dayd and (p10d, dayd) in blocked_phone_day:
                continue
            if not _synthetic_session_passes_working_hours(s.get("started_at"), working_hours):
                continue
            if not _synthetic_session_passes_channel_filters(
                s.get("last_transfer_category"), cats, allow_no_xfer
            ):
                continue
            rows.append(TelegramLeadsDB._lead_dict_from_voice_list_session(s))
            covered.add(sid)

    @staticmethod
    def save_lead(
        telegram_user_id: int,
        client_fio: str,
        client_phone: str,
        need_type: str,
        need_text: Optional[str],
        department: str,
        group_id: int,
        working_hours: bool,
        response_message: str,
        telegram_username: Optional[str] = None,
        phone_alt: Optional[str] = None,
        car_brand: Optional[str] = None,
        car_model: Optional[str] = None,
        car_year: Optional[str] = None,
        car_mileage: Optional[str] = None,
        work_wishes: Optional[str] = None,
        source: str = "telegram",
        voice_contact_outcome: Optional[str] = None,
        cdr_uniqueid: Optional[str] = None,
        voice_bot_session_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Сохранить лид в базу данных.

        voice_contact_outcome: для source=phone — см. VOICE_CONTACT_OUTCOME_CODES.
        voice_bot_session_id: для source=phone — voice_bot_sessions.id (колонка «Перевод / сброс» в «Лидах»).
        cdr_uniqueid: при импорте CDR Инфолады (Asterisk uniqueid); ON CONFLICT DO NOTHING.
        
        :return: ID созданного лида или None при ошибке / дубликате CDR
        """
        # Лимиты VARCHAR в схеме (после миграции 013 телефоны 128; до миграции — обрезка до 50 совместима)
        tu = _clip_str(telegram_username, 255, "telegram_username")
        cf = _clip_str(client_fio, 255, "client_fio") or ""
        cp = _clip_str(client_phone, 128, "client_phone") or ""
        pa = _clip_str(phone_alt, 128, "phone_alt")
        nt = _clip_str(need_type, 50, "need_type") or ""
        dep = _clip_str(department, 100, "department") or ""
        rm = response_message  # TEXT
        cb = _clip_str(car_brand, 255, "car_brand")
        cm = _clip_str(car_model, 255, "car_model")
        cy = _clip_str(car_year, 20, "car_year")
        cmi = _clip_str(car_mileage, 50, "car_mileage")
        src = _clip_str(source, 50, "source") or "telegram"
        vco = _normalize_voice_contact_outcome(voice_contact_outcome) if src == "phone" else None
        vbsid: Optional[int] = None
        if src == "phone" and voice_bot_session_id is not None:
            try:
                _sid = int(voice_bot_session_id)
                vbsid = _sid if _sid > 0 else None
            except (TypeError, ValueError):
                vbsid = None
        cdr_uid = _clip_str(cdr_uniqueid, 128, "cdr_uniqueid")
        if cdr_uid == "":
            cdr_uid = None
        wall_now = _dealer_wall_now_naive()
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    if cdr_uid:
                        cur.execute("""
                            INSERT INTO telegram_leads (
                                telegram_user_id, telegram_username, client_fio, client_phone,
                                phone_alt,
                                need_type, need_text, department, group_id, working_hours,
                                response_message,
                                car_brand, car_model, car_year, car_mileage, work_wishes,
                                source, voice_contact_outcome,
                                voice_bot_session_id,
                                cdr_uniqueid,
                                created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (cdr_uniqueid) DO NOTHING
                            RETURNING id
                        """, (
                            telegram_user_id,
                            tu,
                            cf,
                            cp,
                            pa,
                            nt,
                            need_text,
                            dep,
                            group_id,
                            working_hours,
                            rm,
                            cb,
                            cm,
                            cy,
                            cmi,
                            work_wishes,
                            src,
                            vco,
                            vbsid,
                            cdr_uid,
                            wall_now,
                            wall_now,
                        ))
                    else:
                        cur.execute("""
                            INSERT INTO telegram_leads (
                                telegram_user_id, telegram_username, client_fio, client_phone,
                                phone_alt,
                                need_type, need_text, department, group_id, working_hours,
                                response_message,
                                car_brand, car_model, car_year, car_mileage, work_wishes,
                                source, voice_contact_outcome,
                                voice_bot_session_id,
                                created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id
                        """, (
                            telegram_user_id,
                            tu,
                            cf,
                            cp,
                            pa,
                            nt,
                            need_text,
                            dep,
                            group_id,
                            working_hours,
                            rm,
                            cb,
                            cm,
                            cy,
                            cmi,
                            work_wishes,
                            src,
                            vco,
                            vbsid,
                            wall_now,
                            wall_now,
                        ))
                    row = cur.fetchone()
                    if not row:
                        return None
                    lead_id = row[0]
                    ts = wall_now.strftime("%Y-%m-%d %H:%M:%S")
                    src_disp = {
                        "phone": "голосовой бот",
                        "max": "MAX-бот",
                        "infolada_cdr": "Инфолада CDR",
                    }.get(source, "ТГ-бот")
                    logger.info(f"Лид сохранен: {ts} | {src_disp} | {dep} | {cf} | {cp} | {need_text or '-'}")
                    return lead_id
        except Exception as e:
            err = str(e).lower()
            if "cdr_uniqueid" in err or "on conflict" in err or "unique" in err:
                logger.warning(
                    "Сохранение лида: колонка cdr_uniqueid или UNIQUE — выполните миграцию 018: %s",
                    e,
                )
            logger.error(f"Ошибка сохранения лида в БД: {e}", exc_info=True)
            return None

    @staticmethod
    def get_lead(lead_id: int) -> Optional[Dict[str, Any]]:
        """Получить лид по ID."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM telegram_leads WHERE id = %s",
                        (lead_id,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения лида: {e}", exc_info=True)
            return None

    @staticmethod
    def update_lead_append(
        lead_id: int,
        work_wishes_append: Optional[str] = None,
    ) -> bool:
        """
        Дополнить лид (при повторном обращении в ту же группу).
        Добавляет work_wishes_append к существующему work_wishes через перевод строки.
        """
        try:
            lead = TelegramLeadsDB.get_lead(lead_id)
            if not lead:
                return False
            existing = (lead.get("work_wishes") or "").strip()
            new_part = (work_wishes_append or "").strip()
            merged = f"{existing}\n{new_part}".strip() if existing else new_part
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE telegram_leads SET work_wishes = %s, updated_at = %s WHERE id = %s",
                        (merged or None, _dealer_wall_now_naive(), lead_id),
                    )
            logger.info(f"Лид обновлен: ID={lead_id}, доп. пожелания")
            return True
        except Exception as e:
            logger.error(f"Ошибка обновления лида: {e}", exc_info=True)
            return False
    
    @staticmethod
    def update_ics_status(lead_id: int, ics_notification_created: bool, 
                          ics_client_found: Optional[bool] = None,
                          ics_client_id: Optional[str] = None) -> bool:
        """Обновить статус интеграции с 1С."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE telegram_leads
                        SET ics_notification_created = %s,
                            ics_client_found = %s,
                            ics_client_id = %s
                        WHERE id = %s
                    """, (ics_notification_created, ics_client_found, ics_client_id, lead_id))
                    return True
        except Exception as e:
            logger.error(f"Ошибка обновления статуса 1С: {e}", exc_info=True)
            return False
    
    @staticmethod
    def _pg_has_column(cur: Any, table: str, column: str) -> bool:
        """Есть ли колонка в public (для БД без миграции voice_bot_session_id)."""
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            LIMIT 1
            """,
            (table, column),
        )
        return cur.fetchone() is not None

    @staticmethod
    def get_leads_by_phone(phone: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Получить лиды по телефону."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM telegram_leads
                        WHERE client_phone = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """, (phone, limit))
                    return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения лидов: {e}", exc_info=True)
            return []

    @staticmethod
    def list_leads(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        channel: Optional[str] = None,
        need_types_in: Optional[List[str]] = None,
        source_filter: Optional[str] = None,
        working_hours: Optional[str] = None,
        incoming_number_filter: Optional[str] = None,
        voice_contact_outcome: Optional[str] = None,
        hostess_followup_only: bool = False,
        limit: int = 500,
        offset: int = 0,
        role_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Список лидов с фильтрами.
        channel: need_type (один). need_types_in: ограничение списком (если channel не задан).
        source_filter: если задан — только telegram | phone | max; если нет — все строки telegram_leads
        (как в src-архиве 2026-03-18) плюс синтетические строки из voice_bot_sessions без лида.
        working_hours: working (8-20) | non_working (20-8)
        voice_contact_outcome: bot_only | no_operator | transfer_started | unknown | unset (IS NULL)
        """
        try:
            from internal_test_phone import sql_lead_exclude_internal_test_for_role

            conditions = [sql_lead_exclude_internal_test_for_role("l", role_id=role_id)]
            params: List[Any] = []
            incoming_filter = (incoming_number_filter or "").strip()
            if date_from:
                conditions.append("DATE(l.created_at) >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("DATE(l.created_at) <= %s")
                params.append(date_to)
            if channel:
                nt_list = _lead_need_types_for_channel_filter(channel)
                if len(nt_list) == 1:
                    conditions.append("l.need_type = %s")
                    params.append(nt_list[0])
                elif len(nt_list) > 1:
                    ph = ",".join(["%s"] * len(nt_list))
                    conditions.append("l.need_type IN (" + ph + ")")
                    params.extend(nt_list)
            elif need_types_in:
                if len(need_types_in) == 0:
                    conditions.append("1=0")
                else:
                    placeholders = ",".join(["%s"] * len(need_types_in))
                    conditions.append("l.need_type IN (" + placeholders + ")")
                    params.extend(need_types_in)
            if source_filter:
                conditions.append("COALESCE(l.source, 'telegram') = %s")
                params.append(source_filter)
            if working_hours == "working":
                conditions.append("(l.created_at::time >= '08:00' AND l.created_at::time < '20:00')")
            elif working_hours == "non_working":
                conditions.append("(l.created_at::time >= '20:00' OR l.created_at::time < '08:00')")
            if voice_contact_outcome == "unset":
                conditions.append("l.voice_contact_outcome IS NULL")
            elif voice_contact_outcome and _normalize_voice_contact_outcome(voice_contact_outcome):
                conditions.append("l.voice_contact_outcome = %s")
                params.append(_normalize_voice_contact_outcome(voice_contact_outcome))
            if hostess_followup_only:
                # Список для обзвона хостес: только голосовые лиды без перевода.
                conditions.append("COALESCE(l.source, 'telegram') = 'phone'")
                conditions.append("COALESCE(l.voice_contact_outcome, '') <> 'transfer_started'")
            # Источник: как в архиве 2026-03-18 — фильтр только если передан source_filter;
            # жёсткий IN('phone','max') убирал из выдачи пустую БД лидов при живых сессиях в voice_bot_sessions.
            where = " AND ".join(conditions) if conditions else "1=1"
            incoming_clause = ""
            if incoming_filter:
                incoming_clause = (
                    " AND COALESCE(vb_direct.incoming_number, vb_heur.incoming_number, '') ILIKE %s"
                )
                params.append(f"%{incoming_filter}%")
            params.extend([limit, offset])
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    has_vb_sid = TelegramLeadsDB._pg_has_column(
                        cur, "telegram_leads", "voice_bot_session_id"
                    )
                    has_vb_incoming_did = TelegramLeadsDB._pg_has_column(
                        cur, "voice_bot_sessions", "incoming_did"
                    )
                    vb_incoming_expr = (
                        "NULLIF(TRIM(COALESCE(s.incoming_did, '')), '')"
                        if has_vb_incoming_did
                        else "NULL::text"
                    )
                    lead_sid_col = (
                        "l.voice_bot_session_id"
                        if has_vb_sid
                        else "NULL::bigint AS voice_bot_session_id"
                    )
                    vb_direct_where = (
                        "l.voice_bot_session_id IS NOT NULL AND te.session_id = l.voice_bot_session_id"
                        if has_vb_sid
                        else "FALSE"
                    )
                    vb_heur_where = (
                        "l.voice_bot_session_id IS NULL" if has_vb_sid else "TRUE"
                    )
                    list_sql = (
                        f"""
                        SELECT l.id, l.telegram_user_id, {lead_sid_col}, l.client_fio, l.client_phone, l.need_type,
                               l.need_text, l.department, COALESCE(l.source, 'telegram') AS source,
                               COALESCE(l.working_hours, TRUE) AS working_hours,
                               l.car_brand, l.car_model, l.car_year, l.car_mileage, l.work_wishes,
                               l.voice_contact_outcome,
                               l.created_at,
                               COALESCE(vb_direct.xfer_category, vb_heur.xfer_category) AS voice_last_transfer_category,
                               COALESCE(vb_direct.incoming_number, vb_heur.incoming_number) AS incoming_number,
                               CASE
                                   WHEN COALESCE(l.working_hours, TRUE) = FALSE THEN 'звонок в нерабочее время'
                                   WHEN COALESCE(l.voice_contact_outcome, '') <> 'transfer_started' THEN 'сброс'
                                   ELSE NULL
                               END AS hostess_reason,
                               (
                                   SELECT MIN(l2.created_at)
                                   FROM telegram_leads l2
                                   WHERE COALESCE(l2.source, 'telegram') = 'phone'
                                     AND l2.voice_contact_outcome = 'transfer_started'
                                     AND l2.created_at > l.created_at
                                     AND l2.created_at <= l.created_at + INTERVAL '24 hours'
                                     AND LENGTH(regexp_replace(BTRIM(COALESCE(l.client_phone, '')), '[^0-9]', '', 'g')) >= 10
                                     AND right(
                                         regexp_replace(BTRIM(COALESCE(l2.client_phone, '')), '[^0-9]', '', 'g'),
                                         10
                                     ) = right(
                                         regexp_replace(BTRIM(COALESCE(l.client_phone, '')), '[^0-9]', '', 'g'),
                                         10
                                     )
                               ) AS followup_transfer_at
                        FROM telegram_leads l
                        LEFT JOIN LATERAL (
                            SELECT te.transfer_category AS xfer_category,
                                   {vb_incoming_expr} AS incoming_number
                            FROM voice_bot_transfer_events te
                            JOIN voice_bot_sessions s ON s.id = te.session_id
                            WHERE {vb_direct_where}
                            ORDER BY te.logged_at DESC
                            LIMIT 1
                        ) vb_direct ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT (
                                SELECT te.transfer_category
                                FROM voice_bot_transfer_events te
                                WHERE te.session_id = s.id
                                ORDER BY te.logged_at DESC
                                LIMIT 1
                            ) AS xfer_category,
                            {vb_incoming_expr} AS incoming_number
                            FROM voice_bot_sessions s
                            WHERE {vb_heur_where}
                              AND COALESCE(l.source, 'telegram') = 'phone'
                              AND LENGTH(regexp_replace(BTRIM(COALESCE(l.client_phone, '')), '[^0-9]', '', 'g')) >= 10
                              AND l.created_at >= s.started_at - INTERVAL '30 minutes'
                              AND l.created_at <= COALESCE(s.ended_at, s.started_at) + INTERVAL '120 minutes'
                              AND (
                                  NULLIF(BTRIM(COALESCE(s.caller_phone, '')), '') IS NULL
                                  OR LENGTH(regexp_replace(BTRIM(COALESCE(s.caller_phone, '')), '[^0-9]', '', 'g')) < 10
                                  OR right(
                                      regexp_replace(BTRIM(COALESCE(s.caller_phone, '')), '[^0-9]', '', 'g'),
                                      10
                                  ) = right(
                                      regexp_replace(BTRIM(COALESCE(l.client_phone, '')), '[^0-9]', '', 'g'),
                                      10
                                  )
                              )
                            ORDER BY
                              EXISTS (SELECT 1 FROM voice_bot_transfer_events te0 WHERE te0.session_id = s.id) DESC,
                              (SELECT MAX(te1.logged_at) FROM voice_bot_transfer_events te1 WHERE te1.session_id = s.id) DESC NULLS LAST,
                              abs(
                                  EXTRACT(epoch FROM (l.created_at - COALESCE(s.ended_at, s.started_at)))
                              ),
                              s.id DESC
                            LIMIT 1
                        ) vb_heur ON TRUE
                        WHERE """
                        + where
                        + incoming_clause
                        + """
                        ORDER BY l.created_at DESC
                        LIMIT %s OFFSET %s
                        """
                    )
                    cur.execute(
                        list_sql,
                        params,
                    )
                    rows = [dict(row) for row in cur.fetchall()]
                    for r in rows:
                        TelegramLeadsDB._annotate_voice_transfer_display(r)
                    if hostess_followup_only:
                        # Если лид в telegram_leads не создался, берём непререведённые звонки из сессий бота.
                        has_incoming_did_s = TelegramLeadsDB._pg_has_column(cur, "voice_bot_sessions", "incoming_did")
                        incoming_expr_s = (
                            "NULLIF(TRIM(COALESCE(s.incoming_did, '')), '')"
                            if has_incoming_did_s
                            else "NULL::text"
                        )
                        s_cond = ["1=1"]
                        s_params: List[Any] = []
                        if date_from:
                            s_cond.append("s.started_at::date >= %s")
                            s_params.append(date_from)
                        if date_to:
                            s_cond.append("s.started_at::date <= %s")
                            s_params.append(date_to)
                        if working_hours == "working":
                            s_cond.append("(s.started_at::time >= '08:00' AND s.started_at::time < '20:00')")
                        elif working_hours == "non_working":
                            s_cond.append("(s.started_at::time >= '20:00' OR s.started_at::time < '08:00')")
                        s_where = " AND ".join(s_cond)
                        l3_exists_sql = (
                            """
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM telegram_leads l3
                                  WHERE COALESCE(l3.source, 'telegram') = 'phone'
                                    AND l3.voice_bot_session_id = s.id
                              )
                            """
                            if has_vb_sid
                            else ""
                        )
                        cur.execute(
                            f"""
                            SELECT
                                -s.id AS id,
                                NULL::bigint AS telegram_user_id,
                                s.id AS voice_bot_session_id,
                                NULLIF(BTRIM(COALESCE(s.client_spoken_name, '')), '') AS client_fio,
                                {VoiceBotAnalyticsDB._SQL_CALLER_PHONE_COALESCE} AS client_phone,
                                ''::text AS need_type,
                                NULL::text AS need_text,
                                NULL::text AS department,
                                'phone'::text AS source,
                                (s.started_at::time >= '08:00' AND s.started_at::time < '20:00') AS working_hours,
                                NULL::text AS car_brand,
                                NULL::text AS car_model,
                                NULL::text AS car_year,
                                NULL::text AS car_mileage,
                                NULL::text AS work_wishes,
                                NULL::text AS voice_contact_outcome,
                                s.started_at::timestamp AS created_at,
                                NULL::text AS voice_last_transfer_category,
                                {incoming_expr_s} AS incoming_number,
                                CASE
                                    WHEN (s.started_at::time >= '08:00' AND s.started_at::time < '20:00')
                                        THEN 'сброс'
                                    ELSE 'звонок в нерабочее время'
                                END AS hostess_reason,
                                (
                                    SELECT MIN(s2.started_at)
                                    FROM voice_bot_sessions s2
                                    WHERE s2.started_at > s.started_at
                                      AND s2.started_at <= s.started_at + INTERVAL '24 hours'
                                      AND LENGTH(regexp_replace(BTRIM(COALESCE(s.caller_phone, '')), '[^0-9]', '', 'g')) >= 10
                                      AND right(
                                            regexp_replace(BTRIM(COALESCE(s2.caller_phone, '')), '[^0-9]', '', 'g'),
                                            10
                                      ) = right(
                                            regexp_replace(BTRIM(COALESCE(s.caller_phone, '')), '[^0-9]', '', 'g'),
                                            10
                                      )
                                      AND EXISTS (
                                          SELECT 1
                                          FROM voice_bot_transfer_events e2
                                          WHERE e2.session_id = s2.id
                                            AND e2.ami_result = 'transfer_started'
                                      )
                                ) AS followup_transfer_at
                            FROM voice_bot_sessions s
                            WHERE {s_where}
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM voice_bot_transfer_events e
                                  WHERE e.session_id = s.id
                                    AND e.ami_result = 'transfer_started'
                              )
                              {l3_exists_sql}
                            ORDER BY s.started_at DESC
                            LIMIT %s
                            """,
                            [*s_params, limit],
                        )
                        rows.extend([dict(r) for r in cur.fetchall()])
                    if not hostess_followup_only:
                        blocked_phone_day = _real_lead_phone_day_keys(rows)
                        extra = TelegramLeadsDB._voice_sessions_without_lead_rows(
                            date_from=date_from,
                            date_to=date_to,
                            source_filter=source_filter,
                            working_hours=working_hours,
                            voice_contact_outcome=voice_contact_outcome,
                            channel=channel,
                            need_types_in=need_types_in,
                            limit=limit,
                        )
                        if extra:
                            rows.extend(
                                [
                                    e
                                    for e in extra
                                    if _synthetic_phone_day_key(e) not in blocked_phone_day
                                ]
                            )
                        TelegramLeadsDB._merge_voice_list_sessions_into_leads(
                            rows,
                            date_from=date_from,
                            date_to=date_to,
                            source_filter=source_filter,
                            working_hours=working_hours,
                            voice_contact_outcome=voice_contact_outcome,
                            channel=channel,
                            need_types_in=need_types_in,
                            limit=limit,
                            role_id=role_id,
                        )
                        rows = _dedupe_synthetic_voice_rows_against_real(rows)
                        rows = _dedupe_leads_one_per_voice_session(rows)
                    rows.sort(
                        key=lambda x: (x.get("created_at") is None, str(x.get("created_at") or "")),
                        reverse=True,
                    )
                    rows = rows[:limit]
                    for r in rows:
                        r.setdefault("incoming_number", None)
                        r.setdefault("hostess_reason", None)
                        r.setdefault("followup_transfer_at", None)
                        TelegramLeadsDB._annotate_lead_need_display(r)
                    TelegramLeadsDB._enrich_lead_need_display_from_voice_stt(rows)
                    return rows
        except Exception as e:
            logger.error(f"Ошибка list_leads: {e}", exc_info=True)
            raise

    @staticmethod
    def phone_leads_row_count_by_calendar_day(
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> Dict[str, int]:
        """
        Число строк в telegram_leads с source='phone' по календарным дням created_at.
        Совпадает с тем, как считаются строки в блоке «Лиды по дням» (не число сессий бота).
        Ключ даты — ISO YYYY-MM-DD (в TZ сессии БД).
        """
        try:
            cond = ["source = 'phone'"]
            params: List[Any] = []
            if date_from:
                cond.append("created_at::date >= %s")
                params.append(date_from)
            if date_to:
                cond.append("created_at::date <= %s")
                params.append(date_to)
            where = " AND ".join(cond)
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"""
                        SELECT created_at::date AS day, COUNT(*)::bigint AS n
                        FROM telegram_leads
                        WHERE {where}
                        GROUP BY created_at::date
                        """,
                        params,
                    )
                    out: Dict[str, int] = {}
                    for r in cur.fetchall():
                        d = r.get("day")
                        if d is None:
                            continue
                        key = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
                        out[key] = int(r.get("n") or 0)
                    return out
        except Exception as e:
            logger.warning("Ошибка phone_leads_row_count_by_calendar_day: %s", e, exc_info=True)
            return {}

    @staticmethod
    def get_leads_report(
        date_from: str,
        date_to: str,
        channel: Optional[str] = None,
        need_types_in: Optional[List[str]] = None,
        source_filter: Optional[str] = None,
        working_hours: Optional[str] = None,
        voice_contact_outcome: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Отчёт по лидам за период: число по каналу и по источнику."""
        try:
            conditions = ["DATE(created_at) >= %s", "DATE(created_at) <= %s"]
            params: List[Any] = [date_from, date_to]
            if channel:
                nt_list = _lead_need_types_for_channel_filter(channel)
                if len(nt_list) == 1:
                    conditions.append("need_type = %s")
                    params.append(nt_list[0])
                elif len(nt_list) > 1:
                    ph = ",".join(["%s"] * len(nt_list))
                    conditions.append("need_type IN (" + ph + ")")
                    params.extend(nt_list)
            elif need_types_in:
                if len(need_types_in) == 0:
                    conditions.append("1=0")
                else:
                    placeholders = ",".join(["%s"] * len(need_types_in))
                    conditions.append("need_type IN (" + placeholders + ")")
                    params.extend(need_types_in)
            if source_filter:
                conditions.append("COALESCE(source, 'telegram') = %s")
                params.append(source_filter)
            if working_hours == "working":
                conditions.append("(created_at::time >= '08:00' AND created_at::time < '20:00')")
            elif working_hours == "non_working":
                conditions.append("(created_at::time >= '20:00' OR created_at::time < '08:00')")
            if voice_contact_outcome == "unset":
                conditions.append("voice_contact_outcome IS NULL")
            elif voice_contact_outcome and _normalize_voice_contact_outcome(voice_contact_outcome):
                conditions.append("voice_contact_outcome = %s")
                params.append(_normalize_voice_contact_outcome(voice_contact_outcome))
            # Без жёсткого IN('phone','max'): как в архиве — только явный source_filter.
            where = " AND ".join(conditions)
            by_channel: Dict[str, int] = {}
            by_source: Dict[str, int] = {}
            by_voice: Dict[str, int] = {}
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT need_type, COALESCE(source, 'telegram') as source, COUNT(*) as cnt
                        FROM telegram_leads
                        WHERE """ + where + """
                        GROUP BY need_type, source
                    """, params)
                    rows = cur.fetchall()
                    for r in rows:
                        d = dict(r)
                        ch = d["need_type"]
                        src = d["source"]
                        cnt = int(d["cnt"])
                        by_channel[ch] = by_channel.get(ch, 0) + cnt
                        by_source[src] = by_source.get(src, 0) + cnt
                    try:
                        vo_conditions = list(conditions)
                        vo_params: List[Any] = list(params)
                        vo_conditions.append("COALESCE(source, 'telegram') = 'phone'")
                        vo_where = " AND ".join(vo_conditions)
                        cur.execute(
                            """
                            SELECT voice_contact_outcome, COUNT(*)::int AS cnt
                            FROM telegram_leads
                            WHERE """
                            + vo_where
                            + """
                            GROUP BY voice_contact_outcome
                            """,
                            vo_params,
                        )
                        for row in cur.fetchall():
                            dvo = dict(row)
                            key = (
                                dvo["voice_contact_outcome"]
                                if dvo["voice_contact_outcome"] is not None
                                else ""
                            )
                            by_voice[key] = int(dvo["cnt"])
                    except Exception as e2:
                        logger.warning("get_leads_report by_voice_contact_outcome: %s", e2)
            return {"by_channel": by_channel, "by_source": by_source, "by_voice_contact_outcome": by_voice}
        except Exception as e:
            logger.error(f"Ошибка get_leads_report: {e}", exc_info=True)
            return {"by_channel": {}, "by_source": {}, "by_voice_contact_outcome": {}}


class IncomingCallsDB:
    """Непринятые входящие звонки (CDR от Инфолады)."""

    @staticmethod
    def list_incoming_calls(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        did_filter: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Список непринятых звонков за период."""
        try:
            conditions = []
            params: List[Any] = []
            if date_from:
                conditions.append("call_date >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("call_date <= %s")
                params.append(date_to)
            if did_filter and str(did_filter).strip():
                conditions.append("COALESCE(did, '') ILIKE %s")
                params.append(f"%{str(did_filter).strip()}%")
            where = " AND ".join(conditions) if conditions else "1=1"
            params.extend([limit, offset])
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT id, call_date, call_time, caller_phone, did, status, reason, created_at
                        FROM incoming_calls
                        WHERE """ + where + """
                        ORDER BY call_date DESC, call_time DESC
                        LIMIT %s OFFSET %s
                        """,
                        params,
                    )
                    return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.debug("incoming_calls: %s (таблица может отсутствовать)", e)
            return []


class AdminAuthDB:
    """Пользователи админки: логин, хеш пароля, роль 1-6."""

    @staticmethod
    def get_logins() -> List[str]:
        """Список логинов активных пользователей."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT login FROM admin_users WHERE is_active = TRUE ORDER BY login"
                    )
                    return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка get_logins: {e}", exc_info=True)
            return []

    @staticmethod
    def verify_user(login: str, password: str) -> Optional[Dict[str, Any]]:
        """Проверить логин/пароль. Вернуть строку {id, login, role_id} или None."""
        try:
            import bcrypt
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, login, password_hash, role_id FROM admin_users WHERE login = %s AND is_active = TRUE",
                        (login,),
                    )
                    row = cur.fetchone()
            if not row:
                return None
            pw_bytes = password.encode("utf-8")
            hash_bytes = row["password_hash"].encode("utf-8") if isinstance(row["password_hash"], str) else row["password_hash"]
            if not bcrypt.checkpw(pw_bytes, hash_bytes):
                return None
            return {"id": row["id"], "login": row["login"], "role_id": int(row["role_id"])}
        except Exception as e:
            logger.error(f"Ошибка verify_user: {e}", exc_info=True)
            return None

    @staticmethod
    def create_user(login: str, password: str, role_id: int) -> Optional[int]:
        """Создать пользователя. Вернуть id или None."""
        try:
            import bcrypt
            pw_bytes = password.encode("utf-8")[:72]  # bcrypt limit 72 bytes
            pw_hash = bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO admin_users (login, password_hash, role_id) VALUES (%s, %s, %s) RETURNING id",
                        (login, pw_hash, role_id),
                    )
                    return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"Ошибка create_user: {e}", exc_info=True)
            return None

    @staticmethod
    def set_password(login: str, password: str) -> bool:
        """Изменить пароль пользователя по логину. Вернуть True при успехе."""
        try:
            import bcrypt
            pw_bytes = password.encode("utf-8")[:72]
            pw_hash = bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE admin_users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP WHERE login = %s AND is_active = TRUE",
                        (pw_hash, login),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка set_password: {e}", exc_info=True)
            return False

    @staticmethod
    def deactivate_user(login: str) -> bool:
        """Деактивировать пользователя по логину."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE admin_users SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE login = %s",
                        (login,),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка deactivate_user: {e}", exc_info=True)
            return False


class CallAnalyticsDB:
    """Работа с аналитикой звонков в PostgreSQL."""
    
    @staticmethod
    def save_call(
        file_path: str,
        file_name: str,
        internal_number: int,
        call_date: str,
        call_time: str,
        duration_seconds: Optional[int] = None,
        file_size_bytes: Optional[int] = None,
        department: Optional[str] = None,
        source_type: Optional[str] = None,
        call_source: Optional[str] = None,
    ) -> Optional[int]:
        """Сохранить запись о звонке. call_source: sprecord (по умолчанию) | bot."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    call_src = call_source or "sprecord"
                    cur.execute("""
                        INSERT INTO calls (
                            file_path, file_name, internal_number, call_date, call_time,
                            duration_seconds, file_size_bytes, call_source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        file_path, file_name, internal_number, call_date, call_time,
                        duration_seconds, file_size_bytes, call_src
                    ))
                    call_id = cur.fetchone()[0]
                    if department or source_type:
                        try:
                            cur.execute("SAVEPOINT save_call_update")
                            updates = []
                            params = []
                            if department:
                                updates.append("department = %s")
                                params.append(department)
                            if source_type:
                                updates.append("source_type = %s")
                                params.append(source_type)
                            params.append(call_id)
                            cur.execute(
                                "UPDATE calls SET " + ", ".join(updates) + " WHERE id = %s",
                                params,
                            )
                        except Exception:
                            cur.execute("ROLLBACK TO SAVEPOINT save_call_update")
                    logger.info(f"Звонок сохранен в БД: ID={call_id}, файл={file_name}")
                    return call_id
        except Exception as e:
            logger.error(f"Ошибка сохранения звонка: {e}", exc_info=True)
            return None

    @staticmethod
    def update_call_caller_phone_link(
        call_id: int,
        caller_phone: Optional[str],
        voice_bot_session_id: Optional[int],
    ) -> bool:
        """Записать номер клиента и привязку к сессии голосового бота (None — очистить поле)."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE calls
                        SET caller_phone = %s,
                            voice_bot_session_id = %s
                        WHERE id = %s
                        """,
                        (caller_phone, voice_bot_session_id, call_id),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка update_call_caller_phone_link (call_id={call_id}): {e}", exc_info=True)
            return False

    @staticmethod
    def try_match_voice_bot_for_call(call_id: int) -> bool:
        """Подставить caller_phone из voice_bot_sessions по времени (если ещё не заполнено)."""
        try:
            row = CallAnalyticsDB.get_call_with_details(call_id)
            if not row:
                return False
            if row.get("caller_phone"):
                return False
            src = (row.get("call_source") or "sprecord") or "sprecord"
            if src != "sprecord":
                return False
            from call_analytics.voice_bot_call_match import find_voice_bot_session_for_call_time

            m = find_voice_bot_session_for_call_time(
                row.get("call_date"),
                row.get("call_time"),
                exclude_call_id=call_id,
            )
            if not m:
                return False
            return CallAnalyticsDB.update_call_caller_phone_link(
                call_id,
                m["caller_phone"],
                m["voice_bot_session_id"],
            )
        except Exception as e:
            logger.warning(f"try_match_voice_bot_for_call id={call_id}: {e}", exc_info=True)
            return False

    @staticmethod
    def list_calls(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        department: Optional[str] = None,
        departments: Optional[List[str]] = None,
        extra_internal_numbers: Optional[List[int]] = None,
        call_type: Optional[str] = None,
        internal_number: Optional[int] = None,
        manager_name: Optional[str] = None,
        status: Optional[str] = None,
        show_excluded: bool = False,
        call_source: Optional[str] = None,
        working_hours: Optional[str] = None,
        sto_to_rubric_only: bool = False,
        sto_service_brand: Optional[str] = None,
        sto_work_type: Optional[str] = None,
        sto_narrow_rubric: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """Список звонков с фильтрами. order=asc — сначала утро.
        call_source: sprecord | bot. working_hours: working (8-20) | non_working (20-8).
        sto_to_rubric_only: только звонки с заполненным sto_to_rubric_type (оценка по скрипту ТО).
        sto_service_brand / sto_work_type: измерения СТО (см. infer_sto_booking_dimensions).
        sto_narrow_rubric: узкий слой СТО (колонка «Узкий СТО_ТО»): STO_TO_IN | STO_TO_OUT |
            STO_NARROW_OTHER (department=STO и sto_to_rubric_type IS NULL). Взаимоисключается с call_type.
        """
        try:
            conditions = []
            params: List[Any] = []
            if date_from:
                conditions.append("c.call_date >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("c.call_date <= %s")
                params.append(date_to)
            if internal_number is not None:
                conditions.append("c.internal_number = %s")
                params.append(internal_number)
            if manager_name:
                mn_stripped = manager_name.strip()
                try:
                    from admin_panel.managers_config import manager_filter_db_variants

                    variants = manager_filter_db_variants(mn_stripped)
                except ImportError:
                    variants = [mn_stripped]
                if len(variants) > 1:
                    ph = ", ".join(["%s"] * len(variants))
                    conditions.append(f"c.manager_name IN ({ph})")
                    params.extend(variants)
                else:
                    conditions.append("c.manager_name ILIKE %s")
                    params.append("%" + variants[0] + "%")
            if departments:
                ph = ", ".join(["%s"] * len(departments))
                dept_condition = f"c.department IN ({ph})"
                dept_params: List[Any] = list(departments)
            elif department:
                if department == "OTHER":
                    dept_condition = "(c.department = %s OR c.call_type = %s)"
                    dept_params = ["OTHER", "OTHER"]
                else:
                    dept_condition = "c.department = %s"
                    dept_params = [department]
            else:
                dept_condition = ""
                dept_params = []
            if dept_condition:
                extra_nums = [int(n) for n in (extra_internal_numbers or []) if n is not None]
                if extra_nums:
                    eph = ", ".join(["%s"] * len(extra_nums))
                    conditions.append(
                        "("
                        + dept_condition
                        + f" OR (c.internal_number IN ({eph}) AND COALESCE(c.call_source, 'sprecord') = 'sprecord')"
                        + ")"
                    )
                    params.extend(dept_params)
                    params.extend(extra_nums)
                else:
                    conditions.append(dept_condition)
                    params.extend(dept_params)
            snr = (sto_narrow_rubric or "").strip().upper()
            if snr in ("STO_TO_IN", "STO_TO_OUT", "STO_NARROW_OTHER", "NARROW_OTHER"):
                conditions.append("c.department = %s")
                params.append("STO")
                if snr == "STO_TO_IN":
                    conditions.append("c.sto_to_rubric_type = %s")
                    params.append("STO_TO_IN")
                elif snr == "STO_TO_OUT":
                    conditions.append("c.sto_to_rubric_type = %s")
                    params.append("STO_TO_OUT")
                else:
                    conditions.append("c.sto_to_rubric_type IS NULL")
            elif call_type:
                if call_type == "OTHER":
                    conditions.append("(c.call_type = %s OR c.call_type IS NULL)")
                    params.append("OTHER")
                else:
                    conditions.append("c.call_type = %s")
                    params.append(call_type)
            if status:
                conditions.append("c.status = %s")
                params.append(status)
            if not show_excluded:
                conditions.append("COALESCE(c.excluded, FALSE) = FALSE")
            if call_source:
                conditions.append("COALESCE(c.call_source, 'sprecord') = %s")
                params.append(call_source)
            if working_hours == "working":
                conditions.append("(c.call_time >= '08:00' AND c.call_time < '20:00')")
            elif working_hours == "non_working":
                conditions.append("(c.call_time >= '20:00' OR c.call_time < '08:00')")
            if sto_to_rubric_only:
                conditions.append("c.sto_to_rubric_type IS NOT NULL")
            if sto_service_brand:
                sb = sto_service_brand.strip().lower()
                if sb == "other_brand":
                    conditions.append("(c.sto_service_brand IS NULL OR c.sto_service_brand = %s)")
                    params.append("other_brand")
                elif sb in ("chery_tenet", "nissan"):
                    conditions.append("c.sto_service_brand = %s")
                    params.append(sb)
            if sto_work_type:
                wt = sto_work_type.strip().lower()
                if wt == "other_work":
                    conditions.append("(c.sto_work_type IS NULL OR c.sto_work_type = %s)")
                    params.append("other_work")
                elif wt in ("to", "warranty", "quality_check"):
                    conditions.append("c.sto_work_type = %s")
                    params.append(wt)
            where = " AND ".join(conditions) if conditions else "1=1"
            order_dir = "ASC" if (order or "").lower() == "asc" else "DESC"
            params.extend([limit, offset])
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT c.id, c.file_path, c.file_name, c.internal_number,
                               c.call_date, c.call_time, c.duration_seconds, c.file_size_bytes,
                               c.department, c.call_type, c.manager_name,
                               c.call_source, c.source_type, c.status,
                               c.sto_service_brand, c.sto_work_type, c.sto_is_booking,
                               c.sto_to_rubric_type, c.sto_to_rubric_reason, c.sto_appointment_agreed,
                               c.created_at, q.overall_score
                        FROM calls c
                        LEFT JOIN call_quality_scores q ON q.call_id = c.id
                        WHERE """ + where + """
                        ORDER BY c.call_date """ + order_dir + """, c.call_time """ + order_dir + """
                        LIMIT %s OFFSET %s
                    """, params)
                    rows = cur.fetchall()
                    result = []
                    for r in rows:
                        d = dict(r)
                        d.setdefault("department", "OP")
                        d.setdefault("call_type", None)
                        # Значения из БД; подстановка только если колонок нет (очень старая схема)
                        d.setdefault("source_type", "manual")
                        d.setdefault("call_source", "sprecord")
                        d.setdefault("status", "pending")
                        d.setdefault("excluded", False)
                        d.setdefault("manager_name", None)
                        result.append(d)
                    return result
        except Exception as e:
            logger.error(f"Ошибка list_calls: {e}", exc_info=True)
            return []

    @staticmethod
    def get_call_with_details(call_id: int) -> Optional[Dict[str, Any]]:
        """Получить звонок с транскрипцией и оценками."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM calls WHERE id = %s", (call_id,))
                    call = cur.fetchone()
                    if not call:
                        logger.warning(f"get_call_with_details: звонок id={call_id} не найден в таблице calls")
                        return None
                    result = dict(call)
                    cur.execute(
                        "SELECT * FROM call_transcriptions WHERE call_id = %s",
                        (call_id,),
                    )
                    tr = cur.fetchone()
                    result["transcription"] = dict(tr) if tr else None
                    cur.execute(
                        "SELECT * FROM call_quality_scores WHERE call_id = %s",
                        (call_id,),
                    )
                    q = cur.fetchone()
                    result["quality_scores"] = dict(q) if q else None
                    return result
        except Exception as e:
            logger.error(f"Ошибка get_call_with_details (call_id={call_id}): {e}", exc_info=True)
            raise
    
    @staticmethod
    def save_transcription(
        call_id: int,
        transcription_text: str,
        segments: Optional[Dict] = None,
        diarization: Optional[Dict] = None,
        manager_segments: Optional[Dict] = None,
        client_segments: Optional[Dict] = None,
        stt_model: Optional[str] = None,
        processing_time_seconds: Optional[float] = None,
    ) -> Optional[int]:
        """Сохранить транскрипцию звонка."""
        try:
            import json
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO call_transcriptions (
                            call_id, transcription_text, segments, diarization,
                            manager_segments, client_segments, stt_model, processing_time_seconds
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        call_id,
                        transcription_text,
                        json.dumps(segments) if segments else None,
                        json.dumps(diarization) if diarization else None,
                        json.dumps(manager_segments) if manager_segments else None,
                        json.dumps(client_segments) if client_segments else None,
                        stt_model,
                        processing_time_seconds,
                    ))
                    transcription_id = cur.fetchone()[0]
                    logger.info(f"Транскрипция сохранена: ID={transcription_id}, call_id={call_id}")
                    return transcription_id
        except Exception as e:
            logger.error(f"Ошибка сохранения транскрипции: {e}", exc_info=True)
            return None

    @staticmethod
    def update_transcription_text(transcription_id: int, transcription_text: str) -> bool:
        """Обновить текст существующей транскрипции (перенормализация без новой STT)."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_transcriptions
                        SET transcription_text = %s
                        WHERE id = %s
                        """,
                        (transcription_text, transcription_id),
                    )
                    ok = cur.rowcount > 0
                    if ok:
                        logger.info(
                            "Транскрипция обновлена: transcription_id=%s",
                            transcription_id,
                        )
                    return ok
        except Exception as e:
            logger.error(f"Ошибка обновления транскрипции id={transcription_id}: {e}", exc_info=True)
            return False
    
    @staticmethod
    def save_quality_scores(
        call_id: int,
        transcription_id: Optional[int],
        greeting_score: float,
        professionalism_score: float,
        clarity_score: float,
        listening_score: float,
        problem_solving_score: float,
        closing_score: float,
        overall_score: float,
        detailed_evaluation: Optional[Dict] = None,
        llm_model: Optional[str] = None,
        evaluation_prompt: Optional[str] = None,
        processing_time_seconds: Optional[float] = None,
    ) -> Optional[int]:
        """Сохранить оценки качества звонка."""
        try:
            import json
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO call_quality_scores (
                            call_id, transcription_id, greeting_score, professionalism_score,
                            clarity_score, listening_score, problem_solving_score, closing_score,
                            overall_score, detailed_evaluation, llm_model, evaluation_prompt,
                            processing_time_seconds
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        call_id, transcription_id, greeting_score, professionalism_score,
                        clarity_score, listening_score, problem_solving_score, closing_score,
                        overall_score,
                        json.dumps(detailed_evaluation) if detailed_evaluation else None,
                        llm_model, evaluation_prompt, processing_time_seconds,
                    ))
                    score_id = cur.fetchone()[0]
                    logger.info(f"Оценки качества сохранены: ID={score_id}, call_id={call_id}")
                    return score_id
        except Exception as e:
            logger.error(f"Ошибка сохранения оценок: {e}", exc_info=True)
            return None

    @staticmethod
    def exclude_call(call_id: int, excluded_by: str) -> bool:
        """Скрыть звонок из аналитики."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calls SET excluded = TRUE, excluded_at = CURRENT_TIMESTAMP, excluded_by = %s WHERE id = %s",
                        (excluded_by, call_id),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка exclude_call: {e}", exc_info=True)
            return False

    @staticmethod
    def restore_call(call_id: int) -> bool:
        """Восстановить звонок в аналитике."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calls SET excluded = FALSE, excluded_at = NULL, excluded_by = NULL WHERE id = %s",
                        (call_id,),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка restore_call: {e}", exc_info=True)
            return False

    @staticmethod
    def set_manager_name(call_id: int, manager_name: str, source: str = "manual") -> bool:
        """Задать имя менеджера для звонка."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calls SET manager_name = %s, manager_name_source = %s WHERE id = %s",
                        (manager_name.strip() or None, source, call_id),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка set_manager_name: {e}", exc_info=True)
            return False

    @staticmethod
    def update_call_department_type(
        call_id: int,
        department: str,
        call_type: Optional[str] = None,
    ) -> bool:
        """Обновить отдел и тип звонка (результат классификации по транскрипту)."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calls SET department = %s, call_type = %s WHERE id = %s",
                        (department, call_type, call_id),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка update_call_department_type: {e}", exc_info=True)
            return False

    @staticmethod
    def update_call_sto_dimensions(
        call_id: int,
        *,
        service_brand: Optional[str] = None,
        work_type: Optional[str] = None,
        is_booking: Optional[bool] = None,
        confidence: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Сохранить признаки STO-звонка (марка/вид работ/запись)."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE calls
                        SET sto_service_brand = %s,
                            sto_work_type = %s,
                            sto_is_booking = %s,
                            sto_dimension_confidence = %s,
                            sto_dimension_evidence = %s
                        WHERE id = %s
                        """,
                        (
                            service_brand,
                            work_type,
                            is_booking,
                            confidence,
                            Json(evidence) if evidence is not None else None,
                            call_id,
                        ),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка update_call_sto_dimensions: {e}", exc_info=True)
            return False

    @staticmethod
    def update_call_sto_rubric(
        call_id: int,
        *,
        rubric_type: Optional[str] = None,
        reason: Optional[str] = None,
        appointment_agreed: Optional[bool] = None,
    ) -> bool:
        """Узкая когорта STO_TO_IN/STO_TO_OUT и эвристика согласования визита."""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE calls
                        SET sto_to_rubric_type = %s,
                            sto_to_rubric_reason = %s,
                            sto_appointment_agreed = %s
                        WHERE id = %s
                        """,
                        (rubric_type, reason, appointment_agreed, call_id),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Ошибка update_call_sto_rubric: {e}", exc_info=True)
            return False

    @staticmethod
    def get_managers(department: Optional[str] = None) -> List[Dict[str, Any]]:
        """Список менеджеров (уникальные internal_number + manager_name) для фильтров аналитики."""
        try:
            conditions = ["COALESCE(c.excluded, FALSE) = FALSE"]
            params: List[Any] = []
            if department:
                conditions.append("c.department = %s")
                params.append(department)
            where = " AND ".join(conditions)
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT DISTINCT c.internal_number, c.manager_name
                        FROM calls c
                        WHERE """ + where + """
                        ORDER BY c.internal_number
                    """, params)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка get_managers: {e}", exc_info=True)
            return []

    @staticmethod
    def get_weekly_analytics(
        department: Optional[str] = None,
        internal_number: Optional[int] = None,
        manager_name: Optional[str] = None,
        call_type: Optional[str] = None,
        months: int = 6,
        show_excluded: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Понедельные средние оценки за последние N месяцев.
        Возвращает [{week_start, call_count, overall_avg, detailed_avg: {criterion: avg}}].
        По умолчанию исключённые звонки не учитываются; при show_excluded=True — учитываются.
        """
        try:
            conditions = [
                "q.overall_score IS NOT NULL",
                f"c.call_date >= CURRENT_DATE - INTERVAL '{int(months)} months'",
            ]
            params: List[Any] = []
            if department:
                conditions.append("c.department = %s")
                params.append(department)
            if internal_number is not None:
                conditions.append("c.internal_number = %s")
                params.append(internal_number)
            if manager_name:
                mn_stripped = manager_name.strip()
                try:
                    from admin_panel.managers_config import manager_filter_db_variants

                    variants = manager_filter_db_variants(mn_stripped)
                except ImportError:
                    variants = [mn_stripped]
                if len(variants) > 1:
                    ph = ", ".join(["%s"] * len(variants))
                    conditions.append(f"c.manager_name IN ({ph})")
                    params.extend(variants)
                else:
                    conditions.append("c.manager_name ILIKE %s")
                    params.append("%" + variants[0] + "%")
            if call_type:
                if call_type == "OTHER":
                    conditions.append("(c.call_type = %s OR c.call_type IS NULL)")
                    params.append("OTHER")
                else:
                    conditions.append("c.call_type = %s")
                    params.append(call_type)
            if not show_excluded:
                conditions.append("COALESCE(c.excluded, FALSE) = FALSE")
            where = " AND ".join(conditions)
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT
                            DATE_TRUNC('week', c.call_date)::date AS week_start,
                            COUNT(*) AS call_count,
                            AVG(q.overall_score) AS overall_avg,
                            AVG(q.greeting_score) AS greeting_avg,
                            AVG(q.professionalism_score) AS professionalism_avg,
                            AVG(q.clarity_score) AS clarity_avg,
                            AVG(q.listening_score) AS listening_avg,
                            AVG(q.problem_solving_score) AS problem_solving_avg,
                            AVG(q.closing_score) AS closing_avg,
                            q.detailed_evaluation
                        FROM calls c
                        JOIN call_quality_scores q ON q.call_id = c.id
                        WHERE """ + where + """
                        GROUP BY week_start, q.detailed_evaluation
                        ORDER BY week_start
                    """, params)
                    raw_rows = cur.fetchall()

            weekly: Dict[str, Dict[str, Any]] = {}
            for r in raw_rows:
                d = dict(r)
                ws = str(d["week_start"])
                if ws not in weekly:
                    weekly[ws] = {
                        "week_start": ws,
                        "call_count": 0,
                        "overall_sum": 0.0,
                        "criteria_sums": {},
                        "criteria_counts": {},
                    }
                w = weekly[ws]
                cnt = int(d["call_count"])
                w["call_count"] += cnt
                w["overall_sum"] += float(d["overall_avg"] or 0) * cnt

                det = d.get("detailed_evaluation")
                if det and isinstance(det, dict):
                    for k, v in det.items():
                        if k == "total_score":
                            continue
                        try:
                            fv = float(v)
                        except (TypeError, ValueError):
                            continue
                        w["criteria_sums"][k] = w["criteria_sums"].get(k, 0.0) + fv * cnt
                        w["criteria_counts"][k] = w["criteria_counts"].get(k, 0) + cnt

            result = []
            for ws in sorted(weekly.keys()):
                w = weekly[ws]
                cc = w["call_count"]
                entry: Dict[str, Any] = {
                    "week_start": ws,
                    "call_count": cc,
                    "overall_avg": round(w["overall_sum"] / cc, 3) if cc else 0,
                    "criteria": {},
                }
                for k in w["criteria_sums"]:
                    c_cnt = w["criteria_counts"].get(k, 0)
                    entry["criteria"][k] = round(w["criteria_sums"][k] / c_cnt, 3) if c_cnt else 0
                result.append(entry)
            return result
        except Exception as e:
            logger.error(f"Ошибка get_weekly_analytics: {e}", exc_info=True)
            return []

    @staticmethod
    def get_period_analytics_detail(
        department: Optional[str] = None,
        internal_number: Optional[int] = None,
        manager_name: Optional[str] = None,
        call_type: Optional[str] = None,
        months: int = 6,
        weeks: Optional[int] = None,
        show_excluded: bool = False,
    ) -> Dict[str, Any]:
        """
        Средние/мин/макс по каждому звонку за период (последние N месяцев или N недель), не по неделям.
        Нужно для админки: корректные минимум и максимум по списку звонков.
        Если weeks задан и > 0 — используется он, иначе months.
        """
        try:
            if weeks is not None and int(weeks) > 0:
                date_cond = f"c.call_date >= CURRENT_DATE - INTERVAL '{int(weeks)} weeks'"
            else:
                date_cond = f"c.call_date >= CURRENT_DATE - INTERVAL '{int(months)} months'"
            conditions = [
                "q.overall_score IS NOT NULL",
                date_cond,
            ]
            params: List[Any] = []
            if department:
                conditions.append("c.department = %s")
                params.append(department)
            if internal_number is not None:
                conditions.append("c.internal_number = %s")
                params.append(internal_number)
            if manager_name:
                mn_stripped = manager_name.strip()
                try:
                    from admin_panel.managers_config import manager_filter_db_variants

                    variants = manager_filter_db_variants(mn_stripped)
                except ImportError:
                    variants = [mn_stripped]
                if len(variants) > 1:
                    ph = ", ".join(["%s"] * len(variants))
                    conditions.append(f"c.manager_name IN ({ph})")
                    params.extend(variants)
                else:
                    conditions.append("c.manager_name ILIKE %s")
                    params.append("%" + variants[0] + "%")
            if call_type:
                if call_type == "OTHER":
                    conditions.append("(c.call_type = %s OR c.call_type IS NULL)")
                    params.append("OTHER")
                else:
                    conditions.append("c.call_type = %s")
                    params.append(call_type)
            elif department == "OP":
                conditions.append("c.call_type IN ('OP_IN', 'OP_OUT')")
            if not show_excluded:
                conditions.append("COALESCE(c.excluded, FALSE) = FALSE")
            where = " AND ".join(conditions)
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT q.overall_score, q.detailed_evaluation
                        FROM calls c
                        JOIN call_quality_scores q ON q.call_id = c.id
                        WHERE """
                        + where
                        + """
                        ORDER BY c.call_date, c.call_time
                        """,
                        params,
                    )
                    raw_rows = cur.fetchall()
        except Exception as e:
            logger.error(f"Ошибка get_period_analytics_detail: {e}", exc_info=True)
            return {
                "call_count": 0,
                "overall": None,
                "criteria": {},
                "date_from": "",
                "date_to": "",
            }

        overall_vals: List[float] = []
        crit_lists: Dict[str, List[float]] = {}

        for r in raw_rows:
            d = dict(r)
            os = d.get("overall_score")
            if os is not None:
                try:
                    overall_vals.append(float(os))
                except (TypeError, ValueError):
                    pass
            det = d.get("detailed_evaluation")
            if isinstance(det, dict):
                for k, v in det.items():
                    if k == "total_score":
                        continue
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    crit_lists.setdefault(k, []).append(fv)

        def _stat(vals: List[float]) -> Optional[Dict[str, Any]]:
            if not vals:
                return None
            return {
                "avg": round(sum(vals) / len(vals), 3),
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "n": len(vals),
            }

        criteria_out: Dict[str, Any] = {}
        for k, vals in crit_lists.items():
            st = _stat(vals)
            if st:
                criteria_out[k] = st

        date_from_str = ""
        date_to_str = ""
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    if weeks is not None and int(weeks) > 0:
                        cur.execute(
                            "SELECT (CURRENT_DATE - (%s || ' weeks')::interval)::date, CURRENT_DATE::date",
                            (str(int(weeks)),),
                        )
                    else:
                        cur.execute(
                            "SELECT (CURRENT_DATE - (%s || ' months')::interval)::date, CURRENT_DATE::date",
                            (str(int(months)),),
                        )
                    dr = cur.fetchone()
                    if dr:
                        date_from_str = str(dr[0])
                        date_to_str = str(dr[1])
        except Exception:
            pass

        return {
            "call_count": len(overall_vals),
            "overall": _stat(overall_vals),
            "criteria": criteria_out,
            "date_from": date_from_str,
            "date_to": date_to_str,
        }

    @staticmethod
    def get_op_manager_matrix(
        *,
        date_from: str,
        date_to: str,
        manager_labels: Optional[List[str]] = None,
        call_type: Optional[str] = None,
        show_excluded: bool = False,
    ) -> Dict[str, Any]:
        """
        Матрица ОП по менеджерам за период:
        - средние значения критериев по звонкам менеджера;
        - средний total_score (Итого);
        - число звонков.
        """
        try:
            from admin_panel.main import _op_criteria_labels
            from admin_panel.managers_config import (
                get_op_manager_matrix_labels,
                resolve_op_manager_roster_label,
            )

            criteria_labels = _op_criteria_labels()
            criteria_keys = list(criteria_labels.keys())
            roster = get_op_manager_matrix_labels()
            selected = [str(x).strip() for x in (manager_labels or []) if str(x).strip()]
            if not selected:
                selected = list(roster)
            else:
                selected = [x for x in selected if x in roster]
                if not selected:
                    selected = list(roster)

            conditions = [
                "c.call_date >= %s",
                "c.call_date <= %s",
                "c.department = 'OP'",
                "q.overall_score IS NOT NULL",
            ]
            params: List[Any] = [date_from, date_to]
            ct = (call_type or "").strip().upper()
            if ct in ("OP_IN", "OP_OUT"):
                conditions.append("c.call_type = %s")
                params.append(ct)
            else:
                conditions.append("c.call_type IN ('OP_IN', 'OP_OUT')")
            if not show_excluded:
                conditions.append("COALESCE(c.excluded, FALSE) = FALSE")

            where = " AND ".join(conditions)
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT c.manager_name, q.detailed_evaluation
                        FROM calls c
                        JOIN call_quality_scores q ON q.call_id = c.id
                        WHERE """
                        + where
                        + """
                        ORDER BY c.call_date, c.call_time
                        """,
                        params,
                    )
                    rows = cur.fetchall()

            buckets: Dict[str, Dict[str, Any]] = {}
            for manager in selected:
                buckets[manager] = {
                    "call_count": 0,
                    "criteria_sums": {k: 0.0 for k in criteria_keys},
                    "criteria_counts": {k: 0 for k in criteria_keys},
                    "total_sum": 0.0,
                    "total_count": 0,
                }

            for row in rows:
                manager_raw = str((row.get("manager_name") or "")).strip()
                manager_display = resolve_op_manager_roster_label(manager_raw)
                if not manager_display or manager_display not in buckets:
                    continue
                det = row.get("detailed_evaluation")
                if isinstance(det, str):
                    try:
                        det = json.loads(det)
                    except Exception:
                        det = None
                if not isinstance(det, dict):
                    continue

                b = buckets[manager_display]
                b["call_count"] += 1

                total_score = det.get("total_score")
                try:
                    if total_score is not None:
                        b["total_sum"] += float(total_score)
                        b["total_count"] += 1
                except (TypeError, ValueError):
                    pass

                for key in criteria_keys:
                    val = det.get(key)
                    try:
                        fv = float(val)
                    except (TypeError, ValueError):
                        continue
                    b["criteria_sums"][key] += fv
                    b["criteria_counts"][key] += 1

            out_rows: List[Dict[str, Any]] = []
            for manager in selected:
                b = buckets[manager]
                criteria_avg: Dict[str, Optional[float]] = {}
                for key in criteria_keys:
                    cnt = int(b["criteria_counts"].get(key) or 0)
                    criteria_avg[key] = round(float(b["criteria_sums"][key]) / cnt, 3) if cnt else None
                total_avg = round(float(b["total_sum"]) / int(b["total_count"]), 3) if int(b["total_count"]) else None
                out_rows.append(
                    {
                        "manager_name": manager,
                        "call_count": int(b["call_count"]),
                        "criteria": criteria_avg,
                        "total_avg": total_avg,
                    }
                )

            return {
                "date_from": date_from,
                "date_to": date_to,
                "criteria_labels": criteria_labels,
                "rows": out_rows,
            }
        except Exception as e:
            logger.error(f"Ошибка get_op_manager_matrix: {e}", exc_info=True)
            return {
                "date_from": date_from,
                "date_to": date_to,
                "criteria_labels": {},
                "rows": [],
            }

    @staticmethod
    def get_sto_booking_analytics(
        *,
        date_from: str,
        date_to: str,
        manager_name: Optional[str] = None,
        show_excluded: bool = False,
        sto_service_brand: Optional[str] = None,
        sto_work_type: Optional[str] = None,
        call_type: Optional[str] = None,
        sto_to_rubric_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Агрегаты STO_IN/STO_OUT по марке и виду работ:
        - calls: сколько звонков
        - bookings: признак записи (sto_is_booking)
        - appointments: эвристика согласованного визита (sto_appointment_agreed)

        Фильтры: марка/вид работ/направление звонка/только рубрика ТО — узкая выборка перед GROUP BY.
        """
        conditions = [
            "c.call_date >= %s",
            "c.call_date <= %s",
        ]
        params: List[Any] = [date_from, date_to]
        if call_type in ("STO_IN", "STO_OUT"):
            conditions.append("c.call_type = %s")
            params.append(call_type)
        else:
            conditions.append("c.call_type IN ('STO_IN', 'STO_OUT')")
        if manager_name:
            mn = manager_name.strip()
            try:
                from admin_panel.managers_config import manager_filter_db_variants

                variants = manager_filter_db_variants(mn)
            except ImportError:
                variants = [mn]
            if len(variants) > 1:
                ph = ", ".join(["%s"] * len(variants))
                conditions.append(f"c.manager_name IN ({ph})")
                params.extend(variants)
            else:
                conditions.append("c.manager_name ILIKE %s")
                params.append("%" + variants[0] + "%")
        if not show_excluded:
            conditions.append("COALESCE(c.excluded, FALSE) = FALSE")
        if sto_to_rubric_only:
            conditions.append("c.sto_to_rubric_type IS NOT NULL")
        if sto_service_brand:
            sb = sto_service_brand.strip().lower()
            if sb == "other_brand":
                conditions.append("(c.sto_service_brand IS NULL OR c.sto_service_brand = %s)")
                params.append("other_brand")
            elif sb in ("chery_tenet", "nissan"):
                conditions.append("c.sto_service_brand = %s")
                params.append(sb)
        if sto_work_type:
            wt = sto_work_type.strip().lower()
            if wt == "other_work":
                conditions.append("(c.sto_work_type IS NULL OR c.sto_work_type = %s)")
                params.append("other_work")
            elif wt in ("to", "warranty", "quality_check"):
                conditions.append("c.sto_work_type = %s")
                params.append(wt)
        where = " AND ".join(conditions)

        brand_rows: List[Dict[str, Any]] = []
        work_rows: List[Dict[str, Any]] = []
        totals: Dict[str, Any] = {"calls": 0, "bookings": 0, "appointments": 0}
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(c.sto_service_brand, 'other_brand') AS key,
                            COUNT(*)::int AS calls,
                            COUNT(*) FILTER (WHERE COALESCE(c.sto_is_booking, FALSE))::int AS bookings,
                            COUNT(*) FILTER (WHERE COALESCE(c.sto_appointment_agreed, FALSE))::int AS appointments
                        FROM calls c
                        WHERE """
                        + where
                        + """
                        GROUP BY 1
                        ORDER BY 1
                        """,
                        params,
                    )
                    brand_rows = [dict(r) for r in cur.fetchall()]

                    cur.execute(
                        """
                        SELECT
                            COALESCE(c.sto_work_type, 'other_work') AS key,
                            COUNT(*)::int AS calls,
                            COUNT(*) FILTER (WHERE COALESCE(c.sto_is_booking, FALSE))::int AS bookings,
                            COUNT(*) FILTER (WHERE COALESCE(c.sto_appointment_agreed, FALSE))::int AS appointments
                        FROM calls c
                        WHERE """
                        + where
                        + """
                        GROUP BY 1
                        ORDER BY 1
                        """,
                        params,
                    )
                    work_rows = [dict(r) for r in cur.fetchall()]

                    cur.execute(
                        """
                        SELECT
                            COUNT(*)::int AS calls,
                            COUNT(*) FILTER (WHERE COALESCE(c.sto_is_booking, FALSE))::int AS bookings,
                            COUNT(*) FILTER (WHERE COALESCE(c.sto_appointment_agreed, FALSE))::int AS appointments
                        FROM calls c
                        WHERE """
                        + where,
                        params,
                    )
                    row = cur.fetchone() or {}
                    totals = {
                        "calls": int(row.get("calls") or 0),
                        "bookings": int(row.get("bookings") or 0),
                        "appointments": int(row.get("appointments") or 0),
                    }
        except Exception as e:
            logger.error(f"Ошибка get_sto_booking_analytics: {e}", exc_info=True)

        return {
            "totals": totals,
            "by_brand": brand_rows,
            "by_work_type": work_rows,
            "date_from": date_from,
            "date_to": date_to,
        }


def _merge_consecutive_bot_transcript_turns(
    turns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Объединить части одной реплики бота до следующей реплики клиента."""
    merged: List[Dict[str, Any]] = []
    for turn in turns:
        current = dict(turn)
        role = str(current.get("role") or "").strip().lower()
        previous_role = (
            str(merged[-1].get("role") or "").strip().lower() if merged else ""
        )
        if merged and role == "bot" and previous_role == "bot":
            previous_text = str(merged[-1].get("text") or "").strip()
            current_text = str(current.get("text") or "").strip()
            merged[-1]["text"] = " ".join(
                part for part in (previous_text, current_text) if part
            )
            continue
        merged.append(current)
    return merged


class VoiceBotAnalyticsDB:
    """Журнал звонков голосового бота: сессии, реплики, события перевода."""

    # Телефон для отчёта: колонка сессии или лид с source=phone за то же окно времени, что и сессия («Лиды»).
    _SQL_CALLER_PHONE_COALESCE = """COALESCE(
    NULLIF(TRIM(s.caller_phone), ''),
    (SELECT tl.client_phone FROM telegram_leads tl
     WHERE tl.source = 'phone'
       AND TRIM(COALESCE(tl.client_phone, '')) <> ''
       AND tl.created_at >= s.started_at - INTERVAL '2 minutes'
       AND tl.created_at <= COALESCE(s.ended_at, s.started_at) + INTERVAL '20 minutes'
     ORDER BY ABS(EXTRACT(EPOCH FROM (tl.created_at - COALESCE(s.ended_at, s.started_at)))), tl.id DESC
     LIMIT 1)
)"""

    _SQL_INCOMING_DID = "NULLIF(TRIM(COALESCE(s.incoming_did, '')), '')"

    # То же окно времени, что у подстановки телефона из лидов (source=phone).
    # Сопоставление по последним 10 цифрам номера, если у сессии caller_phone задан (иначе — как раньше по времени).
    _SQL_VOICE_LEAD_CLIENT_FIO = """(
        SELECT NULLIF(TRIM(tl.client_fio), '')
        FROM telegram_leads tl
        WHERE tl.source = 'phone'
          AND TRIM(COALESCE(tl.client_phone, '')) <> ''
          AND tl.created_at >= s.started_at - INTERVAL '2 minutes'
          AND tl.created_at <= COALESCE(s.ended_at, s.started_at) + INTERVAL '20 minutes'
          AND (
              NULLIF(BTRIM(COALESCE(s.caller_phone, '')), '') IS NULL
              OR LENGTH(regexp_replace(BTRIM(s.caller_phone), '[^0-9]', '', 'g')) < 10
              OR right(regexp_replace(BTRIM(tl.client_phone), '[^0-9]', '', 'g'), 10)
                 = right(regexp_replace(BTRIM(s.caller_phone), '[^0-9]', '', 'g'), 10)
          )
        ORDER BY ABS(EXTRACT(EPOCH FROM (tl.created_at - COALESCE(s.ended_at, s.started_at)))), tl.id DESC
        LIMIT 1
    )"""

    # Имя в списке сессий: сначала то, что бот зафиксировал при finalize; иначе ФИО из лида (телефон).
    _SQL_RECOGNIZED_CLIENT_NAME = (
        "COALESCE(NULLIF(BTRIM(s.client_spoken_name), ''), "
        + _SQL_VOICE_LEAD_CLIENT_FIO.strip()
        + ")"
    )

    # Только реплики клиента после вопроса про отдел (WAV 12_ask_department* или TTS с «Какой отдел … интересует»).
    # Без коррелированного WITH (на части сборок PG ломал весь list_sessions → пустая админка).
    # translate(..., '+', '') — снимаем ударения Silero в тексте бота.
    _SQL_DEPT_QUESTION_BOT_MAX_SEQ = """
        (SELECT MAX(b.seq)
         FROM voice_bot_transcript_turns b
         WHERE b.session_id = s.id
           AND b.role = 'bot'
           AND (
               (b.meta IS NOT NULL AND (b.meta->>'wav_file') LIKE '12_ask_department%%')
               OR (
                   translate(lower(COALESCE(b.text, '')), '+', '') LIKE '%%какой отдел%%'
                   AND translate(lower(COALESCE(b.text, '')), '+', '') LIKE '%%интересует%%'
               )
           ))
    """.replace(
        "\n", " "
    ).strip()

    _SQL_CLIENT_STT_SUMMARY = (
        "("
        "SELECT LEFT(string_agg(TRIM(t.text), ' · ' ORDER BY t.seq), 2000) "
        "FROM voice_bot_transcript_turns t "
        "WHERE t.session_id = s.id "
        "AND t.role = 'client' "
        "AND TRIM(COALESCE(t.text, '')) <> '' "
        "AND ( "
        + _SQL_DEPT_QUESTION_BOT_MAX_SEQ
        + " IS NULL OR t.seq > "
        + _SQL_DEPT_QUESTION_BOT_MAX_SEQ
        + "))"
    )

    WAV_TO_CATEGORY = {
        "03_transfer_admin.wav": "ADMIN",
        "04_transfer_used_cars.wav": "OP_USED",
        "05_transfer_chery_tenet.wav": "OP_CHERY_TENET",
        # устар.: отдельного ОП больше нет; в аналитике как ОП Чери/Тэнет
        "06_transfer_jetour.wav": "OP_CHERY_TENET",
        "10_transfer_master.wav": "WORKSHOP_SL",
        "11_transfer_parts.wav": "PARTS",
        "13_transfer_body_repair.wav": "BODY",
        "16_transfer_service_assistant.wav": "SERVICE_ASSISTANT",
    }

    @staticmethod
    def category_for_wav(wav_file: Optional[str]) -> str:
        if not wav_file:
            return "UNKNOWN"
        return VoiceBotAnalyticsDB.WAV_TO_CATEGORY.get(wav_file, "OTHER")

    @staticmethod
    def ensure_session(
        call_uuid: str,
        greeting_type: Optional[str] = None,
        asterisk_channel_id: Optional[str] = None,
    ) -> Optional[int]:
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO voice_bot_sessions (call_uuid, greeting_type, asterisk_channel_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (call_uuid) DO UPDATE SET
                            greeting_type = COALESCE(EXCLUDED.greeting_type, voice_bot_sessions.greeting_type),
                            asterisk_channel_id = COALESCE(
                                EXCLUDED.asterisk_channel_id, voice_bot_sessions.asterisk_channel_id
                            )
                        RETURNING id
                        """,
                        (call_uuid, greeting_type, asterisk_channel_id),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else None
        except Exception as e:
            logger.error("Ошибка ensure_session voice_bot: %s", e, exc_info=True)
            return None

    @staticmethod
    def set_caller_phone(call_uuid: str, caller_phone: Optional[str]) -> None:
        """Проставить номер клиента для сессии (после AstDB). Пустые значения не пишем."""
        if not call_uuid:
            return
        ph = (caller_phone or "").strip()
        if not ph:
            return
        ph = ph[:40]
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE voice_bot_sessions
                        SET caller_phone = %s
                        WHERE call_uuid = %s
                        """,
                        (ph, call_uuid),
                    )
        except Exception as e:
            logger.error("Ошибка set_caller_phone voice_bot: %s", e, exc_info=True)

    @staticmethod
    def set_incoming_did(call_uuid: str, incoming_did: Optional[str]) -> None:
        """Проставить входящий номер (DID) для сессии (если колонка поддерживается)."""
        did = (incoming_did or "").strip()
        if not did:
            return
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    if not TelegramLeadsDB._pg_has_column(cur, "voice_bot_sessions", "incoming_did"):
                        return
                    cur.execute(
                        """
                        UPDATE voice_bot_sessions
                        SET incoming_did = %s
                        WHERE call_uuid = %s
                        """,
                        (did[:40], call_uuid),
                    )
        except Exception as e:
            logger.error("Ошибка set_incoming_did voice_bot: %s", e, exc_info=True)

    @staticmethod
    def finalize_session(
        call_uuid: str,
        state_final: Optional[str] = None,
        client_spoken_name: Optional[str] = None,
    ) -> None:
        try:
            csn = (client_spoken_name or "").strip() or None
            if csn:
                csn = csn[:255]
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE voice_bot_sessions
                        SET ended_at = CURRENT_TIMESTAMP,
                            state_final = COALESCE(%s, state_final),
                            client_spoken_name = COALESCE(%s, client_spoken_name)
                        WHERE call_uuid = %s
                        """,
                        (state_final, csn, call_uuid),
                    )
        except Exception as e:
            logger.error("Ошибка finalize_session voice_bot: %s", e, exc_info=True)

    @staticmethod
    def set_audio_storage_path(call_uuid: str, relative_path: str) -> None:
        """Относительный путь от VOICE_BOT_RECORDINGS_ROOT (например 2026-04-03/uuid_call.wav)."""
        if not relative_path or not str(relative_path).strip():
            return
        rp = str(relative_path).strip().replace("\\", "/")
        if ".." in rp or rp.startswith("/"):
            logger.warning("set_audio_storage_path: отклонён путь для %s", call_uuid)
            return
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE voice_bot_sessions
                        SET audio_storage_path = %s
                        WHERE call_uuid = %s
                        """,
                        (rp, call_uuid),
                    )
        except Exception as e:
            logger.error("Ошибка set_audio_storage_path voice_bot: %s", e, exc_info=True)

    @staticmethod
    def add_transcript_turn(
        session_id: int,
        seq: int,
        role: str,
        text: Optional[str],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            meta_adapt = Json(meta) if meta else None
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO voice_bot_transcript_turns (session_id, seq, role, text, meta)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, seq) DO UPDATE SET
                            text = EXCLUDED.text,
                            meta = EXCLUDED.meta
                        """,
                        (session_id, seq, role, (text or "")[:16000], meta_adapt),
                    )
        except Exception as e:
            logger.error("Ошибка add_transcript_turn: %s", e, exc_info=True)

    @staticmethod
    def add_transfer_event(
        session_id: int,
        playback_wav: Optional[str],
        transfer_category: Optional[str] = None,
        admin_reason: Optional[str] = None,
        exten: Optional[str] = None,
        tts_snippet: Optional[str] = None,
        client_need: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        cat = transfer_category or VoiceBotAnalyticsDB.category_for_wav(playback_wav)
        try:
            payload_adapt = Json(payload) if payload else None
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO voice_bot_transfer_events (
                            session_id, transfer_category, admin_reason, playback_wav,
                            exten, tts_snippet, client_need, payload
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            cat,
                            admin_reason,
                            playback_wav,
                            exten,
                            (tts_snippet or "")[:4000],
                            client_need,
                            payload_adapt,
                        ),
                    )
        except Exception as e:
            logger.error("Ошибка add_transfer_event: %s", e, exc_info=True)

    @staticmethod
    def update_latest_transfer_ami_result(session_id: int, ami_result: str) -> None:
        """Проставить результат AMI для последнего события перевода сессии (после проигрывания WAV)."""
        if not session_id or not ami_result:
            return
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE voice_bot_transfer_events AS e
                        SET ami_result = %s
                        FROM (
                            SELECT id FROM voice_bot_transfer_events
                            WHERE session_id = %s
                            ORDER BY logged_at DESC, id DESC
                            LIMIT 1
                        ) AS sub
                        WHERE e.id = sub.id
                        """,
                        (ami_result, session_id),
                    )
        except Exception as e:
            logger.error("Ошибка update_latest_transfer_ami_result: %s", e, exc_info=True)

    @staticmethod
    def list_sessions(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
        order: str = "desc",
        transfer_filter: Optional[str] = None,
        role_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Список строк для «Переводы бота»: одна строка на каждую запись лида source=phone за период
        (как таблица «Лиды»), с данными сессии при наличии voice_bot_session_id.
        Период: строка попадает в выборку, если в диапазон календарных дней попадает дата лида
        или (при связанной сессии) дата started_at сессии — чтобы ночные звонки не терялись из‑за
        расхождения дат. Без колонки voice_bot_session_id — прежняя логика по voice_bot_sessions.
        """
        try:
            order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    has_vb_sid = TelegramLeadsDB._pg_has_column(
                        cur, "telegram_leads", "voice_bot_session_id"
                    )
                    has_incoming_did = TelegramLeadsDB._pg_has_column(
                        cur, "voice_bot_sessions", "incoming_did"
                    )
                    incoming_did_expr = (
                        "NULLIF(TRIM(COALESCE(s.incoming_did, '')), '')"
                        if has_incoming_did
                        else "NULL::text"
                    )

                    if has_vb_sid:
                        from internal_test_phone import sql_lead_exclude_internal_test_for_role

                        cond_tl = [
                            "COALESCE(tl.source, 'telegram') = 'phone'",
                            sql_lead_exclude_internal_test_for_role("tl", role_id=role_id),
                        ]
                        params_tl: List[Any] = []
                        # Календарный день отчёта: совпадает с лидом ИЛИ со временем звонка в сессии.
                        # Иначе ночные звонки (started_at 10-го, created_at лида на другой день из‑за TZ/записи) пропадали.
                        if date_from and date_to:
                            cond_tl.append(
                                "("
                                "(tl.created_at::date >= %s AND tl.created_at::date <= %s) OR "
                                "(s.id IS NOT NULL AND s.started_at::date >= %s AND s.started_at::date <= %s)"
                                ")"
                            )
                            params_tl.extend([date_from, date_to, date_from, date_to])
                        elif date_from:
                            cond_tl.append(
                                "("
                                "(tl.created_at::date >= %s) OR "
                                "(s.id IS NOT NULL AND s.started_at::date >= %s)"
                                ")"
                            )
                            params_tl.extend([date_from, date_from])
                        elif date_to:
                            cond_tl.append(
                                "("
                                "(tl.created_at::date <= %s) OR "
                                "(s.id IS NOT NULL AND s.started_at::date <= %s)"
                                ")"
                            )
                            params_tl.extend([date_to, date_to])
                        tf = (transfer_filter or "").strip().lower()
                        if tf == "yes":
                            cond_tl.append(
                                "(s.id IS NOT NULL AND EXISTS "
                                "(SELECT 1 FROM voice_bot_transfer_events e WHERE e.session_id = s.id))"
                            )
                        elif tf == "no":
                            cond_tl.append(
                                "(s.id IS NULL OR NOT EXISTS "
                                "(SELECT 1 FROM voice_bot_transfer_events e WHERE e.session_id = s.id))"
                            )
                        where_tl = " AND ".join(cond_tl)
                        params_tl.extend([limit, offset])
                        caller_phone_expr = (
                            "COALESCE(NULLIF(TRIM(s.caller_phone), ''), NULLIF(TRIM(tl.client_phone), ''))"
                        )
                        recognized_name_expr = (
                            "COALESCE(NULLIF(BTRIM(s.client_spoken_name), ''), NULLIF(TRIM(tl.client_fio), ''))"
                        )
                        cur.execute(
                            f"""
                            SELECT tl.id AS id,
                                   tl.id AS telegram_lead_id,
                                   s.id AS voice_bot_session_id,
                                   s.call_uuid,
                                   {incoming_did_expr} AS incoming_number,
                                   {caller_phone_expr} AS caller_phone,
                                   {recognized_name_expr} AS recognized_client_name,
                                   {VoiceBotAnalyticsDB._SQL_CLIENT_STT_SUMMARY} AS client_request_stt,
                                   COALESCE(s.started_at, tl.created_at) AS started_at,
                                   s.ended_at,
                                   s.state_final, s.audio_storage_path,
                                   (SELECT COUNT(*) FROM voice_bot_transcript_turns t
                                    WHERE t.session_id = s.id AND t.role = 'client') AS client_turns,
                                   (SELECT COUNT(*) FROM voice_bot_transcript_turns t
                                    WHERE t.session_id = s.id AND t.role = 'bot') AS bot_turns,
                                   (SELECT e.transfer_category FROM voice_bot_transfer_events e
                                    WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_transfer_category,
                                   (SELECT e.admin_reason FROM voice_bot_transfer_events e
                                    WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_admin_reason,
                                   (SELECT e.playback_wav FROM voice_bot_transfer_events e
                                    WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_playback_wav,
                                   (SELECT e.ami_result FROM voice_bot_transfer_events e
                                    WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_transfer_ami_result
                            FROM telegram_leads tl
                            LEFT JOIN voice_bot_sessions s ON s.id = tl.voice_bot_session_id
                            WHERE {where_tl}
                            ORDER BY COALESCE(s.started_at, tl.created_at) {order_sql}
                            LIMIT %s OFFSET %s
                            """,
                            params_tl,
                        )
                        rows = [dict(r) for r in cur.fetchall()]
                        for r in rows:
                            cp = r.get("caller_phone")
                            if cp is not None:
                                r["caller_phone_client"] = cp
                            elif r.get("caller_phone_client") is None:
                                r["caller_phone_client"] = ""
                        return rows

                    cond = ["1=1"]
                    params: List[Any] = []
                    if date_from:
                        cond.append("(s.started_at::date >= %s)")
                        params.append(date_from)
                    if date_to:
                        cond.append("(s.started_at::date <= %s)")
                        params.append(date_to)
                    tf = (transfer_filter or "").strip().lower()
                    if tf == "yes":
                        cond.append(
                            "EXISTS (SELECT 1 FROM voice_bot_transfer_events e WHERE e.session_id = s.id)"
                        )
                    elif tf == "no":
                        cond.append(
                            "NOT EXISTS (SELECT 1 FROM voice_bot_transfer_events e WHERE e.session_id = s.id)"
                        )
                    where = " AND ".join(cond)
                    params.extend([limit, offset])
                    cur.execute(
                        f"""
                        SELECT s.id, s.call_uuid,
                               {incoming_did_expr} AS incoming_number,
                               {VoiceBotAnalyticsDB._SQL_CALLER_PHONE_COALESCE} AS caller_phone,
                               {VoiceBotAnalyticsDB._SQL_RECOGNIZED_CLIENT_NAME} AS recognized_client_name,
                               {VoiceBotAnalyticsDB._SQL_CLIENT_STT_SUMMARY} AS client_request_stt,
                               s.started_at, s.ended_at,
                               s.state_final, s.audio_storage_path,
                               (SELECT COUNT(*) FROM voice_bot_transcript_turns t
                                WHERE t.session_id = s.id AND t.role = 'client') AS client_turns,
                               (SELECT COUNT(*) FROM voice_bot_transcript_turns t
                                WHERE t.session_id = s.id AND t.role = 'bot') AS bot_turns,
                               (SELECT e.transfer_category FROM voice_bot_transfer_events e
                                WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_transfer_category,
                               (SELECT e.admin_reason FROM voice_bot_transfer_events e
                                WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_admin_reason,
                               (SELECT e.playback_wav FROM voice_bot_transfer_events e
                                WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_playback_wav,
                               (SELECT e.ami_result FROM voice_bot_transfer_events e
                                WHERE e.session_id = s.id ORDER BY e.logged_at DESC, e.id DESC LIMIT 1) AS last_transfer_ami_result
                        FROM voice_bot_sessions s
                        WHERE {where}
                        ORDER BY s.started_at {order_sql}
                        LIMIT %s OFFSET %s
                        """,
                        params,
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                    for r in rows:
                        cp = r.get("caller_phone")
                        if cp is not None:
                            r["caller_phone_client"] = cp
                        elif r.get("caller_phone_client") is None:
                            r["caller_phone_client"] = ""
                    return rows
        except Exception as e:
            logger.error("Ошибка list_sessions voice_bot: %s", e, exc_info=True)
            raise

    @staticmethod
    def batch_client_stt_summary(session_ids: List[int]) -> Dict[int, str]:
        """
        Агрегат STT после вопроса про отдел для нескольких сессий (как client_request_stt в list_sessions).
        """
        clean: List[int] = []
        for x in session_ids:
            try:
                ix = int(x)
                if ix > 0:
                    clean.append(ix)
            except (TypeError, ValueError):
                pass
        if not clean:
            return {}
        clean = list(dict.fromkeys(clean))
        if len(clean) > 5000:
            clean = clean[:5000]
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"""
                        SELECT s.id,
                               {VoiceBotAnalyticsDB._SQL_CLIENT_STT_SUMMARY} AS client_request_stt
                        FROM voice_bot_sessions s
                        WHERE s.id = ANY(%s)
                        """,
                        (clean,),
                    )
                    return {
                        int(r["id"]): ((r.get("client_request_stt") or "").strip())
                        for r in cur.fetchall()
                    }
        except Exception as e:
            logger.warning("batch_client_stt_summary voice_bot: %s", e)
            return {}

    @staticmethod
    def get_session_by_uuid(call_uuid: str) -> Optional[Dict[str, Any]]:
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    has_incoming_did = TelegramLeadsDB._pg_has_column(cur, "voice_bot_sessions", "incoming_did")
                    incoming_did_expr = (
                        "NULLIF(TRIM(COALESCE(s.incoming_did, '')), '')"
                        if has_incoming_did
                        else "NULL::text"
                    )
                    cur.execute(
                        f"""
                        SELECT s.id, s.call_uuid, s.greeting_type, s.asterisk_channel_id,
                               {incoming_did_expr} AS incoming_number,
                               {VoiceBotAnalyticsDB._SQL_CALLER_PHONE_COALESCE} AS caller_phone,
                               s.state_final, s.audio_storage_path, s.started_at, s.ended_at, s.created_at
                        FROM voice_bot_sessions s
                        WHERE s.call_uuid = %s
                        """,
                        (call_uuid,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error("Ошибка get_session_by_uuid: %s", e, exc_info=True)
            return None

    @staticmethod
    def get_transcript(session_id: int) -> List[Dict[str, Any]]:
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT seq, role, text, meta, logged_at
                        FROM voice_bot_transcript_turns
                        WHERE session_id = %s
                        ORDER BY seq ASC
                        """,
                        (session_id,),
                    )
                    turns = [dict(r) for r in cur.fetchall()]
                    return _merge_consecutive_bot_transcript_turns(turns)
        except Exception as e:
            logger.error("Ошибка get_transcript: %s", e, exc_info=True)
            return []

    @staticmethod
    def get_transfer_events(session_id: int) -> List[Dict[str, Any]]:
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT id, transfer_category, admin_reason, playback_wav, exten,
                               tts_snippet, client_need, logged_at, payload, ami_result
                        FROM voice_bot_transfer_events
                        WHERE session_id = %s
                        ORDER BY logged_at ASC
                        """,
                        (session_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("Ошибка get_transfer_events: %s", e, exc_info=True)
            return []

    @staticmethod
    def daily_summary(
        date_from: Optional[str],
        date_to: Optional[str],
        role_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Сводка по дням: первые три столбца — все телефонные лиды по календарному дню created_at (как «Лиды»)."""
        try:
            from internal_test_phone import (
                sql_lead_exclude_internal_test_for_role,
                sql_voice_session_exclude_internal_test_for_role,
            )

            cond_s = [
                "s.started_at >= CURRENT_TIMESTAMP - INTERVAL '400 days'",
                sql_voice_session_exclude_internal_test_for_role("s", role_id=role_id),
            ]
            params_s: List[Any] = []
            if date_from:
                cond_s.append("s.started_at::date >= %s")
                params_s.append(date_from)
            if date_to:
                cond_s.append("s.started_at::date <= %s")
                params_s.append(date_to)
            where_s = " AND ".join(cond_s)
            cond_tl = [
                "COALESCE(tl.source, 'telegram') = 'phone'",
                sql_lead_exclude_internal_test_for_role("tl", role_id=role_id),
            ]
            params_tl_only: List[Any] = []
            if date_from:
                cond_tl.append("tl.created_at::date >= %s")
                params_tl_only.append(date_from)
            if date_to:
                cond_tl.append("tl.created_at::date <= %s")
                params_tl_only.append(date_to)
            where_tl = " AND ".join(cond_tl)
            params = params_s + params_tl_only
            ami_ok = "e.ami_result = 'transfer_started'"
            # Как _lead_voice_outcome_display: категория последнего события по tl.voice_bot_session_id
            # или voice_contact_outcome = transfer_started (лиды без сессии — только outcome).
            lead_xfer_ui = """
                (
                    COALESCE(
                        (
                            SELECT NULLIF(BTRIM(te.transfer_category::text), '')
                            FROM voice_bot_transfer_events te
                            WHERE tl.voice_bot_session_id IS NOT NULL
                              AND te.session_id = tl.voice_bot_session_id
                            ORDER BY te.logged_at DESC, te.id DESC
                            LIMIT 1
                        ),
                        ''
                    ) <> ''
                    OR COALESCE(tl.voice_contact_outcome, '') = 'transfer_started'
                )
            """.replace(
                "\n", " "
            )
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    has_vb_sid = TelegramLeadsDB._pg_has_column(cur, "telegram_leads", "voice_bot_session_id")
                    if has_vb_sid:
                        cur.execute(
                            f"""
                            WITH xfer AS (
                                SELECT s.started_at::date AS day,
                                       COUNT(e.id) FILTER (WHERE e.transfer_category = 'ADMIN' AND {ami_ok}) AS xfer_admin,
                                       COUNT(e.id) FILTER (
                                           WHERE e.transfer_category IN ('SERVICE_ASSISTANT', 'WORKSHOP_SL') AND {ami_ok}
                                       ) AS xfer_service_assistant,
                                       COUNT(e.id) FILTER (
                                           WHERE e.transfer_category IN ('OP_CHERY_TENET', 'OP_JETOUR') AND {ami_ok}
                                       ) AS xfer_op_chery_tenet,
                                       COUNT(e.id) FILTER (WHERE e.transfer_category = 'BODY' AND {ami_ok}) AS xfer_body,
                                       COUNT(e.id) FILTER (WHERE e.transfer_category = 'OP_USED' AND {ami_ok}) AS xfer_op_used,
                                       COUNT(e.id) FILTER (WHERE e.transfer_category = 'PARTS' AND {ami_ok}) AS xfer_parts
                                FROM voice_bot_sessions s
                                LEFT JOIN voice_bot_transfer_events e ON e.session_id = s.id
                                WHERE {where_s}
                                GROUP BY s.started_at::date
                            ),
                            leads AS (
                                SELECT tl.created_at::date AS day,
                                       COUNT(DISTINCT tl.id) AS sessions_total,
                                       COUNT(DISTINCT tl.id) FILTER (WHERE {lead_xfer_ui}) AS sessions_with_transfer,
                                       COUNT(DISTINCT tl.id) FILTER (WHERE NOT ({lead_xfer_ui})) AS sessions_no_transfer
                                FROM telegram_leads tl
                                WHERE {where_tl}
                                GROUP BY tl.created_at::date
                            )
                            SELECT COALESCE(l.day, x.day) AS day,
                                   COALESCE(l.sessions_total, 0)::bigint AS sessions_total,
                                   COALESCE(l.sessions_with_transfer, 0)::bigint AS sessions_with_transfer,
                                   COALESCE(l.sessions_no_transfer, 0)::bigint AS sessions_no_transfer,
                                   COALESCE(x.xfer_admin, 0)::bigint AS xfer_admin,
                                   COALESCE(x.xfer_service_assistant, 0)::bigint AS xfer_service_assistant,
                                   COALESCE(x.xfer_op_chery_tenet, 0)::bigint AS xfer_op_chery_tenet,
                                   COALESCE(x.xfer_body, 0)::bigint AS xfer_body,
                                   COALESCE(x.xfer_op_used, 0)::bigint AS xfer_op_used,
                                   COALESCE(x.xfer_parts, 0)::bigint AS xfer_parts
                            FROM leads l
                            FULL OUTER JOIN xfer x ON l.day = x.day
                            WHERE COALESCE(l.day, x.day) IS NOT NULL
                            ORDER BY day DESC
                            LIMIT 120
                            """,
                            params,
                        )
                    else:
                        cur.execute(
                            f"""
                            SELECT s.started_at::date AS day,
                                   COUNT(DISTINCT s.id) AS sessions_total,
                                   COUNT(DISTINCT s.id) FILTER (WHERE e.id IS NOT NULL) AS sessions_with_transfer,
                                   COUNT(DISTINCT s.id) FILTER (WHERE e.id IS NULL) AS sessions_no_transfer,
                                   COUNT(e.id) FILTER (WHERE e.transfer_category = 'ADMIN' AND {ami_ok}) AS xfer_admin,
                                   COUNT(e.id) FILTER (
                                       WHERE e.transfer_category IN ('SERVICE_ASSISTANT', 'WORKSHOP_SL') AND {ami_ok}
                                   ) AS xfer_service_assistant,
                                   COUNT(e.id) FILTER (
                                       WHERE e.transfer_category IN ('OP_CHERY_TENET', 'OP_JETOUR') AND {ami_ok}
                                   ) AS xfer_op_chery_tenet,
                                   COUNT(e.id) FILTER (WHERE e.transfer_category = 'BODY' AND {ami_ok}) AS xfer_body,
                                   COUNT(e.id) FILTER (WHERE e.transfer_category = 'OP_USED' AND {ami_ok}) AS xfer_op_used,
                                   COUNT(e.id) FILTER (WHERE e.transfer_category = 'PARTS' AND {ami_ok}) AS xfer_parts
                            FROM voice_bot_sessions s
                            LEFT JOIN voice_bot_transfer_events e ON e.session_id = s.id
                            WHERE {where_s}
                            GROUP BY s.started_at::date
                            ORDER BY day DESC
                            LIMIT 120
                            """,
                            params_s,
                        )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("Ошибка daily_summary voice_bot: %s", e, exc_info=True)
            raise

    @staticmethod
    def last_30_days_session_quality_stats(
        role_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Статистика по сессиям voice_bot_sessions за последние 30 календарных дней (включая сегодня).

        По каждому дню (started_at::date):
        - contacts: число сессий;
        - transfers_ok: сессии с хотя бы одним событием перевода с ami_result = transfer_started;
        - transfers_after_first_client_only: из transfers_ok — ровно одна реплика клиента
          в журнале до момента первого успешного AMI (logged_at первого transfer_started);
        - hangups: сессии без успешного transfer_started (сброс / без перевода).

        Итоговые доли за период — от суммарных числителей к суммарным знаменателям.
        """
        from internal_test_phone import sql_voice_session_exclude_internal_test_for_role

        session_excl = sql_voice_session_exclude_internal_test_for_role("s", role_id=role_id)
        sql = f"""
            WITH bounds AS (
                SELECT (CURRENT_DATE - INTERVAL '29 days')::date AS d0,
                       CURRENT_DATE::date AS d1
            ),
            calendar AS (
                SELECT gs::date AS day
                FROM bounds b,
                     generate_series(b.d0, b.d1, INTERVAL '1 day') AS gs
            ),
            per_session AS (
                SELECT
                    s.started_at::date AS day,
                    s.id AS session_id,
                    EXISTS (
                        SELECT 1
                        FROM voice_bot_transfer_events e
                        WHERE e.session_id = s.id
                          AND e.ami_result = 'transfer_started'
                    ) AS xfer_ok,
                    (
                        SELECT MIN(e2.logged_at)
                        FROM voice_bot_transfer_events e2
                        WHERE e2.session_id = s.id
                          AND e2.ami_result = 'transfer_started'
                    ) AS first_xfer_at
                FROM voice_bot_sessions s
                CROSS JOIN bounds b
                WHERE s.started_at::date >= b.d0
                  AND s.started_at::date <= b.d1
                  AND {session_excl}
            ),
            per_session2 AS (
                SELECT
                    ps.day,
                    ps.session_id,
                    ps.xfer_ok,
                    CASE
                        WHEN NOT ps.xfer_ok THEN NULL::bigint
                        WHEN ps.first_xfer_at IS NULL THEN NULL::bigint
                        ELSE (
                            SELECT COUNT(*)::bigint
                            FROM voice_bot_transcript_turns t
                            WHERE t.session_id = ps.session_id
                              AND t.role = 'client'
                              AND t.logged_at < ps.first_xfer_at
                        )
                    END AS client_turns_before_first_xfer
                FROM per_session ps
            ),
            by_day AS (
                SELECT
                    day,
                    COUNT(*)::bigint AS contacts,
                    COUNT(*) FILTER (WHERE xfer_ok)::bigint AS transfers_ok,
                    COUNT(*) FILTER (
                        WHERE xfer_ok
                          AND client_turns_before_first_xfer IS NOT NULL
                          AND client_turns_before_first_xfer = 1
                    )::bigint AS transfers_after_first_client_only,
                    COUNT(*) FILTER (WHERE NOT xfer_ok)::bigint AS hangups
                FROM per_session2
                GROUP BY day
            )
            SELECT c.day::text AS day,
                   COALESCE(b.contacts, 0)::bigint AS contacts,
                   COALESCE(b.transfers_ok, 0)::bigint AS transfers_ok,
                   COALESCE(b.transfers_after_first_client_only, 0)::bigint
                       AS transfers_after_first_client_only,
                   COALESCE(b.hangups, 0)::bigint AS hangups
            FROM calendar c
            LEFT JOIN by_day b ON b.day = c.day
            ORDER BY c.day DESC
        """
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql)
                    rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                c = int(r["contacts"] or 0)
                t = int(r["transfers_ok"] or 0)
                f = int(r["transfers_after_first_client_only"] or 0)
                h = int(r["hangups"] or 0)
                r["pct_first_reply_of_transfers"] = (
                    round(100.0 * float(f) / float(t), 2) if t > 0 else None
                )
                r["pct_hangup_of_contacts"] = (
                    round(100.0 * float(h) / float(c), 2) if c > 0 else None
                )
            d0 = rows[-1]["day"] if rows else None
            d1 = rows[0]["day"] if rows else None
            tot_c = sum(int(r["contacts"] or 0) for r in rows)
            tot_t = sum(int(r["transfers_ok"] or 0) for r in rows)
            tot_f = sum(int(r["transfers_after_first_client_only"] or 0) for r in rows)
            tot_h = sum(int(r["hangups"] or 0) for r in rows)
            pct_first_of_xfer = (
                round(100.0 * float(tot_f) / float(tot_t), 2) if tot_t > 0 else None
            )
            pct_hang_of_contacts = (
                round(100.0 * float(tot_h) / float(tot_c), 2) if tot_c > 0 else None
            )
            daily_xfer_pcts: List[float] = []
            daily_hang_pcts: List[float] = []
            for r in rows:
                c = int(r["contacts"] or 0)
                t = int(r["transfers_ok"] or 0)
                f = int(r["transfers_after_first_client_only"] or 0)
                h = int(r["hangups"] or 0)
                if t > 0:
                    daily_xfer_pcts.append(100.0 * float(f) / float(t))
                if c > 0:
                    daily_hang_pcts.append(100.0 * float(h) / float(c))
            avg_daily_pct_first = (
                round(sum(daily_xfer_pcts) / len(daily_xfer_pcts), 2)
                if daily_xfer_pcts
                else None
            )
            avg_daily_pct_hangup = (
                round(sum(daily_hang_pcts) / len(daily_hang_pcts), 2)
                if daily_hang_pcts
                else None
            )
            return {
                "period_from": d0,
                "period_to": d1,
                "days": rows,
                "aggregate": {
                    "contacts": tot_c,
                    "transfers_ok": tot_t,
                    "transfers_after_first_client_only": tot_f,
                    "hangups": tot_h,
                    "pct_first_reply_of_transfers": pct_first_of_xfer,
                    "pct_hangup_of_contacts": pct_hang_of_contacts,
                },
                "avg_daily_pct_first_reply_of_transfers": avg_daily_pct_first,
                "avg_daily_pct_hangup_of_contacts": avg_daily_pct_hangup,
            }
        except Exception as e:
            logger.error("Ошибка last_30_days_session_quality_stats voice_bot: %s", e, exc_info=True)
            raise

    @staticmethod
    def add_misrecognition_feedback(
        session_id: int,
        reporter_login: str,
        comment: str,
        transcript_seq: Optional[int] = None,
        stt_text_snapshot: Optional[str] = None,
        expected_tag: Optional[str] = None,
    ) -> Optional[int]:
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO voice_bot_misrecognition_feedback (
                            session_id, transcript_seq, stt_text_snapshot,
                            reporter_login, comment, expected_tag, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, 'new')
                        RETURNING id
                        """,
                        (
                            session_id,
                            transcript_seq,
                            (stt_text_snapshot or "")[:16000],
                            (reporter_login or "")[:128],
                            (comment or "")[:8000],
                            (expected_tag or "")[:128] if expected_tag else None,
                        ),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else None
        except Exception as e:
            logger.error("Ошибка add_misrecognition_feedback: %s", e, exc_info=True)
            return None

    @staticmethod
    def list_misrecognition_feedback(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        cond = ["1=1"]
        params: List[Any] = []
        if date_from:
            cond.append("f.created_at::date >= %s")
            params.append(date_from)
        if date_to:
            cond.append("f.created_at::date <= %s")
            params.append(date_to)
        if status in ("new", "reviewed"):
            cond.append("f.status = %s")
            params.append(status)
        where = " AND ".join(cond)
        params.extend([limit, offset])
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"""
                        SELECT f.id, f.session_id, f.transcript_seq, f.stt_text_snapshot,
                               f.reporter_login, f.comment, f.expected_tag, f.status,
                               f.created_at, f.updated_at,
                               s.call_uuid, s.started_at AS session_started_at
                        FROM voice_bot_misrecognition_feedback f
                        JOIN voice_bot_sessions s ON s.id = f.session_id
                        WHERE {where}
                        ORDER BY f.created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        params,
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("Ошибка list_misrecognition_feedback: %s", e, exc_info=True)
            return []

    @staticmethod
    def update_misrecognition_feedback_status(feedback_id: int, status: str) -> bool:
        if status not in ("new", "reviewed"):
            return False
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE voice_bot_misrecognition_feedback
                        SET status = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (status, feedback_id),
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("Ошибка update_misrecognition_feedback_status: %s", e, exc_info=True)
            return False

    @staticmethod
    def delete_older_than_days(days: int = 14) -> tuple:
        """Удаляет сессии старше N дней (CASCADE — реплики и события). Возвращает (удалено сессий, список audio_storage_path)."""
        paths: List[str] = []
        try:
            with PostgreSQLManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, audio_storage_path FROM voice_bot_sessions
                        WHERE started_at < CURRENT_TIMESTAMP - make_interval(days => %s)
                        """,
                        (days,),
                    )
                    rows = cur.fetchall()
                    for sid, p in rows:
                        if p and str(p).strip():
                            paths.append(str(p).strip())
                    if rows:
                        ids = [r[0] for r in rows]
                        cur.execute(
                            "DELETE FROM voice_bot_sessions WHERE id IN %s",
                            (tuple(ids),),
                        )
                    return (len(rows), paths)
        except Exception as e:
            logger.error("Ошибка delete_older_than_days voice_bot: %s", e, exc_info=True)
            return (0, [])
