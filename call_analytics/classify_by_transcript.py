"""
Классификация звонка по транскрипту: department (OP/STO/OTHER) и call_type (OP_IN/OP_OUT/STO_IN/STO_OUT/OTHER).
Устаревшее значение call_type OP_MISC в нормализации схлопывается в OTHER/OTHER.
OP_OUT (ОП исходящий): исходящий контекст + маркеры первичного/CRM-контакта ОП (_has_op_outbound_surface_markers и др.);
без устойчивых маркеров исходящего ОП тип OP_OUT схлопывается в Прочие (_coerce_op_out_requires_site_lead — имя сохранено).
Тип STO_OUT («СТО исход.»): исходящий + контекст диспетчера/ассистента СТО +
приглашение по заявке / напоминание о ТО / субстантивная тема сервиса; автомобиль ещё не на сервисе.
Ранний матч по сильным маркерам СТО (sto_strong) при исходящем — тоже даёт STO_OUT, если сработало _is_sto_outbound_service_invite.
Без привязки к номерам телефонов — только по содержанию текста.
Классификация: главный путь — classify_by_transcript (v2), запасной — classify_by_transcript_legacy при VIKINGI_CLASSIFY_LEGACY=1.
Направление исходящий/входящий (СТО): рабочий вариант 1 — opening_role; откат на прежнюю цепочку маркеров — VIKINGI_STO_OUTBOUND_VARIANT=2.

Широкий слой: на линии СТО темы ТО, диагностики, гарантии, замены, неисправности — отдел **STO** и тип
**STO_IN**/**STO_OUT**, не Прочие; рубрика **СТО_ТО_*** (запись на регламентное ТО по скрипту) — только узкий
слой (sto_to_rubric). Иные причины (нет линии диспетчера/ассистента из справочника, отмена записи, ранний
OTHER в legacy и т.д.) по-прежнему дают Прочие.
Узкий слой (sto_to_rubric): при отделе STO и типе STO_IN/STO_OUT — либо рубрика СТО_ТО_* (запись на ТО по
скрипту), либо нет этой рубрики («НЕ_ТО» в смысле отсутствия sto_to_rubric_type, не отдельный тип в БД).

Менеджеры: 5 ОП (Чери/Тенет), 2 диспетчера СТО + стажер Дарья. Если говорящий не в списке — не ОП/СТО.
Наличие имени менеджера ОП/СТО само по себе не присваивает отдел.
"""

import os
import re
from typing import Tuple, Optional

# ОП: 5 менеджеров (Чери/Тенет); СТО: 2 диспетчера + стажер Дарья. Все остальные — не ОП и не СТО.
# Говорящий не в списке → не относим к ОП/СТО. Наличие имени ОП/СТО само по себе не присваивает отдел.
try:
    from admin_panel.managers_config import OP_MANAGERS, STO_MANAGERS
    _OP_MANAGER_NAMES = frozenset(
        m["manager_name"].lower() for m in OP_MANAGERS
    ) | frozenset({"евгений", "илья", "анастасия", "андрей"})  # имена для «Захаров Илья» и т.п. Справочник: docs/managers_reference.md
    _STO_MANAGER_NAMES = frozenset(
        m["manager_name"].lower() for m in STO_MANAGERS
    ) | frozenset({
        "александра", "юлия", "юлию", "юля", "юли", "андреев", "андреева",
        "дарью", "гранкина", "лилия", "лилию",
    })  # гранкина — ошибка STT; юли/андреев — STT «Андреев, Юли» (14040)
except ImportError:
    _OP_MANAGER_NAMES = frozenset({"евдокимов", "евгений", "захаров", "илья", "краснощекова", "краснощёкова", "анастасия", "щеголев", "калаев", "андрей"})
    _STO_MANAGER_NAMES = frozenset({
        "плаксина", "александра", "андреева", "андреев", "юлия", "юлию", "юля", "юли",
        "гринкина", "гранкина", "дарья", "дарью", "лилия", "лилию",
    })

# Имена/фамилии менеджеров ОП для STT («это Андрей», «Захаров Илья» и т.п.).
_OP_MGR_NAME_RE = (
    r"(?:евгений|евгения|илья|илью|анастасия|анастасию|андрей|андрея|"
    r"захаров|евдокимов|краснощек|краснощёк|калаев|калаева|щеголев)"
)

# Менеджеры линии «авто с пробегом» (не 5 ОП Чери/Тенет).
_USED_CARS_MANAGER_NAMES = frozenset({"антон", "николай"})
_USED_CARS_MGR_NAME_RE = r"(?:антон|николай)"

VALID_DEPTS = frozenset({"OP", "STO", "OTHER"})
VALID_CALL_TYPES = frozenset({"OP_IN", "OP_OUT", "STO_IN", "STO_OUT", "OTHER"})

# Голое «ожидайте» / «стоимость» / «цена» — не маркеры отдела (11518, 11299).
_OP_RECEPTION_HOLD_ROUTING_PHRASES = (
    "ожидайте на линии",
    "ожидайтесь на линии",
    "ожидайте соединения",
    "ожидайте соединение",
    "ожидайте ответа",
    "подождите на линии",
    "не отключайтесь",
)

# Перезвон диспетчера СТО клиенту («передали номер/телефон»). STT часто пишет «вас телефон передали» вместо «ваш».
STO_EMPLOYEE_PHONE_HANDOFF_MARKERS = (
    "ваш телефон передали",
    "передали ваш телефон",
    "ваш номер передали",
    "передали ваш номер",
    "передали ваш номер телефона",
    "телефон передали соединить",
    "вас телефон передали",
    "звонили, вас телефон",
    "звонили вас телефон",
    # 17540: «мне телефон ваш передали» / «телефон ваш передали» (порядок слов).
    "телефон ваш передали",
    "мне телефон ваш передали",
)

# После «Алло,» STT часто даёт приветствие, не имя клиента (14040: «Алло, здравствуйте, девушка»).
_ALLO_NOT_CLIENT_NAME_TOKENS = frozenset(
    {
        "здравствуйте",
        "здравствуй",
        "добрый",
        "день",
        "вечер",
        "девушка",
        "девушку",
        "слушаю",
        "да",
        "нет",
        "алло",
        "пожалуйста",
        "меня",
        "это",
    }
)


def _is_dispatcher_to_booking_question_not_outbound(head: str) -> bool:
    """«Вы хотели записаться/оплатить…» — вопрос диспетчера при приёмке, не исходящий CRM-скрипт (8508, 11927, 16829)."""
    h = (head or "").lower()
    if not re.search(r"\bвы\s+хотели\b", h):
        return False
    # 16829: «вы хотели на ТО записать» — «на то» между «хотели» и «записать».
    if not re.search(
        r"\bвы\s+хотели\s+(?:бы\s+)?(?:на\s+(?:то|сервис)\s+)?(?:запис|оплат)",
        h,
    ):
        return False
    return any(
        p in h
        for p in (
            "мне бы",
            "мне надо",
            "мне нужно",
            "диспетчер",
            "техническое обслуживание",
            "техобслуживание",
            "на ближайш",
            "на конкретн",
            "ассистент сервиса",
            "слушаю вас",
            "на то",
        )
    )


def _is_sto_outbound_crm_name_call_convenience_opening(text: str) -> bool:
    """
    Исходящий CRM СТО: «Алло, И.О.? … диспетчер … удобно говорить вам?» (12021, 12788).
    STT: «Алло. Юрий Николаевич?» — точка после «алло»; 13781: «Алло. И.О., добрый день…».
    16501: «Алло, Сергей Викторович, добрый день» — запятая сразу после «алло».
    """
    head = (text or "").lower()[:900]
    if not (
        re.search(
            r"\bалло[,.!?]?\s+[а-яё]{2,}(?:\s+[а-яё]{2,})?\s*\?",
            head[:450],
            re.I,
        )
        or re.search(
            r"\bалло[,.!?]?\s+[а-яё]{2,}(?:\s+[а-яё]{2,})?\s*,\s*(?:здравствуйте|добрый\s+(?:день|вечер))\b",
            head[:450],
            re.I,
        )
        # STT 16148: «Алексей Геннадьевич, ещё раз здравствуйте» — без «алло, имя?».
        or re.search(
            r"\b[а-яё]{2,}(?:\s+[а-яё]{2,}){0,2}\s*,\s*ещ[её]\s+раз\s+здравствуйте\b",
            head[:450],
            re.I,
        )
    ):
        return False
    if not (
        re.search(r"\bудобно\s+(?:говорить|разговаривать)", head[:750], re.I)
        or re.search(r"\bудобно\s*\?", head[:750], re.I)
    ):
        return False
    return ("диспетчер" in head[:750] or "ассистент" in head[:750]) and (
        "викинг" in head[:750] or "дилерск" in head[:750] or "чери" in head[:750]
    )


def _client_initiated_online_service_application_in_opening(head: str) -> bool:
    """12476: клиент звонит после онлайн-заявки («оставляла заявку на техническое») — входящий."""
    h = (head or "").lower()[:1200]
    # 16501: «вы оставляли заявку» — речь диспетчера при CRM-перезвоне, не клиентский inbound.
    if re.search(r"\bвы\s+оставлял\w*\s+заявк", h[:700], re.I):
        return bool(
            re.search(r"\b(?:я|мы)\s+(?:оставлял|оставляла|оставил|оставила)\s+заявк", h, re.I)
        )
    return bool(
        re.search(r"\b(?:я|мы)\s+(?:оставлял|оставляла|оставил|оставила)\s+заявк", h, re.I)
        or re.search(r"\bоставлял\w*\s+заявк", h[:700], re.I)
    )


def _client_followup_unanswered_service_request_in_opening(head: str) -> bool:
    """
    16769: клиент сам перезванивает — оставлял(а) заявку менеджеру на ТО, «мне не перезвонили».
    «Заявку получили» в ответе диспетчера — не исходящий CRM-перезвон.
    """
    h = (head or "").lower()[:1200]
    if not re.search(r"\b(?:мне|нам)\s+(?:как\s+и\s+)?не\s+перезвонил", h, re.I):
        return False
    to_topic = bool(
        re.search(r"\bпрохожд\w*[^.!?]{0,50}\bтоо?\b", h, re.I)
        or re.search(
            r"\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм)\w*\s+тоо?\b",
            h,
            re.I,
        )
        or re.search(r"\bтехническ\w+\s+обслуживан", h, re.I)
        or "техобслуживан" in h
    )
    left_request = bool(
        re.search(r"\bоставлял\w*\s+(?:в\s+)?менеджер", h, re.I)
        or re.search(r"\bоставлял\w*\s+заявк", h, re.I)
        or re.search(r"\bоставил\w*\s+заявк", h, re.I)
        or re.search(r"\bзаявк\w*[^.!?]{0,40}менеджер", h, re.I)
    )
    return to_topic and left_request


def _suppress_sto_lead_echo_as_outbound(head: str) -> bool:
    """Онлайн-заявка (12476) или follow-up «не перезвонили» (16769) — не CRM-исходящий."""
    return _client_initiated_online_service_application_in_opening(
        head
    ) or _client_followup_unanswered_service_request_in_opening(head)


def _client_initiated_booking_phrase_in_opening(head: str) -> bool:
    """Запрос на запись от клиента в начале разговора, не приглашение диспетчера (8508 vs 12021)."""
    h = (head or "").lower()[:1000]
    if re.search(r"\b(?:мастер|соедин\w+).{0,80}(?:ремонт|сервис)\b", h[:700], re.I):
        return True
    if re.search(r"\bпроконсультир\w*\b", h[:700]) and not re.search(
        r"\b(?:звоню|могу|готов|перезвон(?:ят|им)|звонил\w*)\s+[^.!?]{0,40}проконсультир",
        h[:700],
        re.I,
    ) and not re.search(r"\bпроконсультир\w*\s+хотел\w*", h[:700], re.I):
        return True
    if any(p in h for p in ("мне бы", "мне надо", "мне нужно", "поменять масл", "замену масл")):
        return True
    # STT: «мне ТО надо» — между «мне» и «надо» вставлено «то» (12032).
    if re.search(r"\bмне\s+то\s+надо\b", h[:500], re.I):
        return True
    if re.search(r"\b(?:я|мне)\s+хотел\w*\s+запис", h[:500], re.I):
        return True
    # STT 16724: «на седьмое ТО хотел записаться» без «я/мне» перед «хотел».
    if re.search(
        r"\b(?:на\s+)?(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм)\w*\s+то\s+хотел",
        h[:600],
        re.I,
    ):
        return True
    # 16769: «на прохождение третьего ТОО» / «третьего ТО» без явного «записаться» в первой фразе.
    if re.search(r"\bпрохожд\w*[^.!?]{0,50}\bтоо?\b", h[:700], re.I):
        return True
    if re.search(
        r"\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм)\w*\s+тоо?\b",
        h[:700],
        re.I,
    ):
        return True
    # 17180: «я на запись» после пропущенных от дилера.
    if re.search(r"\bя\s+на\s+запись\b", h[:600], re.I):
        return True
    if re.search(r"\b(?:можно|хочу|надо|нужно)\s+запис", h[:500], re.I):
        return True
    if re.search(
        r"\b(?:я|мне)\s+хотел\w*\s+узнать[^.!?]{0,70}(?:стоимость|цен|сколько)\b",
        h[:700],
        re.I,
    ):
        return True
    if re.search(r"\bсколько[^.!?]{0,50}стоит[^.!?]{0,50}\bто\b", h[:700], re.I):
        return True
    if _client_initiated_online_service_application_in_opening(h):
        return True
    if _client_followup_unanswered_service_request_in_opening(h):
        return True
    if re.search(
        r"\b(?:можем|давайте|предлага\w*|запиш\w*)\s+(?:с\s+вами\s+)?(?:заранее\s+)?(?:вас\s+)?запис",
        h[:900],
        re.I,
    ):
        return False
    # CRM-перезвон диспетчера: «хотите записаться» / «заявку получили» — не запрос клиента (13966, 13631).
    if re.search(r"\bхотите\s+(?:к\s+нам\s+)?запис", h[:700], re.I):
        return False
    if re.search(r"\bзаявк\w*\s+получил", h[:700], re.I) and not _suppress_sto_lead_echo_as_outbound(
        h
    ):
        return False
    if "записаться" in h[:500] or "записать" in h[:500]:
        if re.search(r"приглас\w*\s+запис|хотим\s+.{0,40}запис|приглаша\w*\s+запис", h[:900], re.I):
            return False
        return True
    return False


def _is_sto_outbound_company_dispatcher_perezanivayu_to_opening(text: str) -> bool:
    """
    Совместимость 16497 (variant2 / classic inbound).
    Канон variant1: _sto_opening_outbound_dial / _sto_opening_role.
    """
    h = (text or "").lower()[:1200]
    if not (
        re.search(r"\bэто\s+компания\s+викинг", h, re.I)
        and re.search(r"\bперезванива\w*", h[:900], re.I)
        and ("диспетчер" in h or "ассистент" in h)
    ):
        return False
    return _sto_opening_outbound_dial(h)


def _is_sto_outbound_company_dispatcher_zvonili_to_consult_opening(text: str) -> bool:
    """
    Совместимость 16555 (variant2 / classic inbound).
    Канон variant1: _sto_opening_outbound_dial / _sto_opening_role.
    """
    h = (text or "").lower()[:1200]
    if not re.search(
        r"\bалло[,.!?]?\s+[а-яё]{2,}(?:\s+[а-яё]{2,})?\s*,\s*(?:здравствуйте|добрый\s+(?:день|вечер))\b",
        h[:550],
        re.I,
    ):
        return False
    if "викинг" not in h and "дилерск" not in h:
        return False
    if "диспетчер" not in h and "ассистент" not in h:
        return False
    if not (
        re.search(r"\bзвонил\w*[^.!?]{0,100}\bхотел\w*", h[:1000], re.I)
        or re.search(r"\bпроконсультир\w*\s+хотел\w*", h[:1000], re.I)
        or re.search(
            r"\bпо\s+техническ\w+\s+обслуживан\w*[^.!?]{0,60}хотел\w*",
            h[:1000],
            re.I,
        )
    ):
        return False
    return _sto_opening_outbound_dial(h)


def _is_classic_sto_inbound_dispatcher_booking_intake(text: str) -> bool:
    """
    Классический входящий на линию диспетчера: приветствие СТО + запрос клиента на запись/работы (8508).
    """
    if not _has_initial_sto_reception_greeting(text):
        return False
    # Исходящий перезвон: «передали телефон, звонили, слушаю» + имя клиента (8570) — не входящий 8508.
    if _is_sto_employee_outbound_phone_handoff_opening(text):
        return False
    # Variant1: один канон opening_role вместо россыпи call_id-исключений.
    if _sto_outbound_logic_variant() != 2:
        if _sto_opening_role(text) == "outbound_dial":
            return False
    else:
        # Variant2 (откат): прежние точечные исключения.
        if _is_sto_outbound_crm_name_call_convenience_opening(text):
            return False
        if _is_sto_outbound_company_dispatcher_perezanivayu_to_opening(text):
            return False
        if _is_sto_outbound_company_dispatcher_zvonili_to_consult_opening(text):
            return False
    if _has_client_name_outbound_script_marker(text):
        return False
    return _client_initiated_booking_phrase_in_opening((text or "").lower()[:1000])


def _is_kuzov_outbound_master_claim_callback_opening(text: str) -> bool:
    """
    Исходящий перезвон мастера-приёмщика кузовного цеха по CRM-заявке
    («Игорь Александрович, добрый день … передали заявочку на техобслуживание», 8491).
    """
    head = (text or "").lower()[:950]
    if not re.search(
        r"\b[а-яё]{2,}(?:\s+[а-яё]{2,}){0,2}\s*,\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер)\b",
        head,
        re.I,
    ):
        return False
    if "кузовн" not in head:
        return False
    if not any(m in head for m in ("мастер-приёмщик", "мастер-приемщик", "мастер приемщик")):
        return False
    return "передали" in head and "заяв" in head


def _sto_employee_outbound_vy_zvonili_nam_opening(head: str) -> bool:
    """
    СТО исх.: «Алло, Андрей, добрый день. Компания Викинги, Юлия, диспетчер, вы звонили нам?» (10184).
    STT 14525: «вы нас звонили» + «меня зовут Юлия» без «диспетчер».
    Без «передали ваш номер» — не путать с входящим «Вы звонили, у меня пропущенный» (9985).
    """
    h = (head or "").lower()[:1200]
    if not (
        re.search(r"\bвы\s+звонили\s+нам\b", h)
        or re.search(r"\bвы\s+(?:нам|нас)\s+звонили\b", h)
    ):
        # STT 15849: «…диспетчер сервиса Юлия, тоже меня зовут, звонили» — обрезано «вы нам».
        if not (
            re.search(r"\bдиспетчер\s+сервис", h)
            and re.search(r"\bменя\s+зовут\b", h)
            and re.search(r"\bзвонили\b", h)
            and ("викинг" in h or "чери" in h or "черри" in h)
        ):
            return False
    if "компания викинг" not in h and "викинги" not in h:
        return False
    has_staff_role = "диспетчер" in h or "ассистент" in h
    has_staff_intro = has_staff_role or "меня зовут" in h
    if not has_staff_intro:
        return False
    return bool(
        re.search(
            r"\b(?:алло[.!?]?\s*)?(?:да[.!?]?\s+)?[а-яё]{2,}(?:\s+[а-яё]{2,}){0,2}\s*,\s*(?:здравствуйте|добрый\s+(?:день|вечер))\b",
            h,
            re.I,
        )
    )


def _is_sto_employee_outbound_phone_handoff_opening(text: str) -> bool:
    """СТО исх.: сотрудник перезванивает по переданному номеру («передали ваш номер, звонили нам?», 8444)."""
    head = (text or "").lower()[:1200]
    if _sto_employee_outbound_vy_zvonili_nam_opening(head):
        return True
    # STT: «диспетчер сервиса … звонили. Слушаю вас» без «передали номер» (8570, 15849).
    if re.search(r"\bзвонили\b", head) and re.search(r"\bслушаю\b", head):
        if re.search(
            r"\b[а-яё]{2,}\s*,\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер)\b",
            head,
            re.I,
        ):
            return True
        if "диспетчер сервиса" in head or "компания викинг" in head or "викинги" in head:
            return True
    if not any(p in head for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS):
        return False
    if re.search(r"\bзвонили\s+нам\b", head) or re.search(r"\bзвонили\s+вам\b", head):
        return True
    return False

# Фразы контекста автосервиса — усиление (одного слова «сервис» недостаточно)
_STO_SERVICE_STRONG = (
    "диспетчер сервиса", "ассистент сервиса",
    "технический отдел",
    "технического отдела",
    "перевожу на сервис", "переключу на сервис", "переведу на сервис",
    "переключаю на сервис", "на сервис переключу", "на сервис переведу",
    "запись на сервис", "записаться на сервис", "на сервис записаться",
    "викинги сервис", "чери сервис", "викинги чери сервис",
    "с сервисным центром", "с сервисом соединить", "с сервисом связаться",
    "со сервисом связаться", "с сервисом можно соединить",
)
# Ослабление: «телефонный сервис» и подобное — не автосервис
_STO_SERVICE_WEAK = ("телефонный сервис", "сервис переадресации", "сервис связи")

# Мастер-приёмщик / приёмка — не менеджер ОП (имя «Евгений» и др. может совпадать с ОП)
_STO_MASTER_PRIYOM_MARKERS = frozenset(
    (
        "мастер-приёмщик",
        "мастер приёмщик",
        "мастер приемщик",
        "мастер-приемщик",
        "мастерприёмщик",
        "мастерприемщик",
        "мастер приёма",
        "мастер приема",
        "приёмщик слесарного",
        "приемщик слесарного",
        "приёмка слесарного",
        "приемка слесарного",
        "мастер сервиса",
        "мастер автосервиса",
    )
    )


def _is_trade_in_assessment_without_op_roster(t: str) -> bool:
    """
    Линия трейд-ин / оценка на выкуп (в т.ч. удалённая): Прочие, не ОП вх.
    Явная тема трейд-ин — всегда Прочие (имена менеджеров в репликах не отменяют).
    """
    low = (t or "").lower()
    trade_in_topic = any(
        p in low
        for p in (
            "трейд-ин",
            "трейд ин",
            "трейдин",
            "trade-in",
            "trade in",
            "отдел трейд",
            "отдел продаж трейд",
            "это отдел трейд",
        )
    )
    if trade_in_topic:
        # Исходящий ОП по лиду с трейд-ин в теме — не «чистая» линия оценки (8890).
        if _has_op_outbound_surface_markers(t):
            return False
        # Trade-in в чистом виде относим в OTHER, но не ломаем полноценные ОП-диалоги:
        # дилерский контекст + линия/сотрудник ОП + предмет покупки (>=2 маркеров).
        op_handoff_or_roster = (
            _has_op_outbound_surface_markers(t)
            or "менеджер отдела продаж" in low
            or "менеджера отдела продаж" in low
            or "отдел продаж" in low
            or "отдела продаж" in low
            or "переведу на менеджера отдела продаж" in low
            or "переведу вас на менеджера отдела продаж" in low
            or "переключу на менеджера отдела продаж" in low
            or "перевожу вас на менеджера" in low
            or "переключаю вас на менеджера" in low
            or bool(re.search(r"\bперевож\w*\s+вас\s+на\s+менеджер", low[:4000]))
            or bool(re.search(r"\bперевед\w*\s+вас\s+на\s+менеджер", low[:4000]))
            or any(len(name) >= 3 and name in low for name in _OP_MANAGER_NAMES)
        )
        dealer_ctx = any(
            p in low
            for p in (
                "официальный дилер",
                "викинг",
                "чери",
                "тенет",
                "тэнет",
                "tenet",
                "chery",
            )
        )
        # Админ перевёл на менеджера ОП, менеджер вёл диалог — не «чистая» линия трейд-ин (13495).
        if (
            op_handoff_or_roster
            and dealer_ctx
            and _is_op_inbound_admin_handoff_to_op_manager(t)
            and _has_op_manager_self_intro_in_head(low[:3500])
        ):
            return False
        purchase_markers = (
            "покупк",
            "интересует автомобиль",
            "интересует машина",
            "автомобиль интересует",
            "комплектац",
            "в наличии",
            "доплат",
            "скидк",
            "кредит",
            "лизинг",
            "за налич",
            "наличк",
            "рассрочк",
            "срок покупки",
            "когда покуп",
            "приобрет",
            "полный привод",
            "полныйривод",
            "тест-драйв",
            "тестдрайв",
            "заявк",
            "поменять",
            "новую",
        )
        purchase_ctx_score = sum(1 for p in purchase_markers if p in low)
        op_sales_context = op_handoff_or_roster and dealer_ctx and purchase_ctx_score >= 2
        if not op_sales_context:
            return True
    appraisal_topic = any(
        p in low
        for p in (
            "удаленную оценку",
            "удалённую оценку",
            "удаленн оцен",
            "удалённ оцен",
            "заочн оцен",
            "заочно оцен",
            "заочная оцен",
            "оценк автомобил",
            "оценку автомобил",
            "оценить автомобил",
            "оценить машину",
            "оценка авто",
            "оценка машины",
            "могли бы заочно",
            "выкуп авто",
            "выкуп автомобил",
            "на обмен",
            "сдать авто",
            "принять ваш автомобил",
        )
    )
    vehicle_context = any(
        v in low
        for v in (
            "пробег",
            "комплектац",
            "тысяч",
            " руб",
            "рубл",
            "год выпуск",
            "октав",
            "шкода",
            "skoda",
            "turbo",
            "турбо",
        )
    )
    if not (appraisal_topic and vehicle_context):
        return False
    for name in _OP_MANAGER_NAMES:
        if len(name) >= 3 and name in low:
            return False
    return True


def _has_sto_service_context(text: str) -> bool:
    """True, если в тексте есть фраза автосервиса и нет ослабляющих контекстов."""
    t = (text or "").lower()
    if any(w in t for w in _STO_SERVICE_WEAK) and not any(p in t for p in _STO_SERVICE_STRONG):
        return False
    return any(p in t for p in _STO_SERVICE_STRONG)


def _has_real_sto_dispatcher_assistant_booking_line(text: str) -> bool:
    """
    В разговоре есть реальная линия диспетчера/ассистента сервиса (приёмка/запись).
    Не путать с «диспетчеры пересылают сообщения» у клиентской службы (17225).
    """
    low = (text or "").lower()
    return bool(
        re.search(r"\bдиспетчер\s+сервис", low)
        or re.search(r"\bассистент\s+сервис", low)
        or re.search(r"\bассистент-сервис", low)
    )


_CUSTOMER_SERVICE_DEPT_PHRASES = (
    "отдел по работе с клиентами",
    "отделом по работе с клиентами",
    "отдел по работе с клиентом",
    "отделом по работе с клиентом",
    "клиентская служба",
    "клиентскую службу",
    "клиентской службы",
)


def _is_customer_service_dept_legal_without_sto_booking_line_other(transcript: str) -> bool:
    """
    17225: отдел по работе с клиентом / клиентская служба — договор, ТЗ, юрист;
    без перевода на диспетчера/ассистента сервиса → Прочие (не СТО_ТО_*).
    """
    low = (transcript or "").lower()
    if not any(p in low[:2400] for p in _CUSTOMER_SERVICE_DEPT_PHRASES):
        return False
    if _has_real_sto_dispatcher_assistant_booking_line(low):
        return False
    legal_hits = sum(
        1
        for p in (
            "договор",
            "юрист",
            "коммерческ",
            "техзадани",
            "техническое задание",
            "норма час",
            "нормы часа",
            "нормочас",
            "без ндс",
            "с ндс",
            "отсрочка платежа",
            "служб безопасности",
            "служба безопасности",
        )
        if p in low
    )
    if re.search(r"\bтз\b", low):
        legal_hits += 1
    return legal_hits >= 2


def _has_sto_substantive_service_intent(t: str) -> bool:
    """
    В тексте есть смысл обращения в сервис дилера: запись/ТО, гарантия, ремонт/диагностика,
    приёмка, шиномонтаж и т.п. — не только приветствие линии или имя сотрудника.
    «Не работает/шумит» без привязки к авто/записи/диагностике — не смысл СТО (мусорный STT).
    """
    low = (t or "").lower()
    # STT: «на 3- то необходимо записать» / явная запись на порядковое ТО без слова «третье» (7958).
    if re.search(r"\bна\s+\d+\s*-\s*то\b", low) or re.search(
        r"\bто\s+необходимо\s+записать\b", low
    ):
        return True
    # 11394: запрос цены «стоимость первого Т» (STT обрезает «ТО» до «т»).
    if re.search(
        r"\bстоимост\w*.{0,50}?\b(?:перв|втор|трет|четвер|четвёрт|нулев)\w*(?:\s+т\b|\s+то\b)",
        low,
        re.I,
    ):
        return True
    # 11518: замена двигателя / авто уже на сервисе — сервисный смысл, не ОП из‑за «цены».
    if re.search(r"\bзамен\w+.{0,40}(?:двигател|мотора|мотор)\b", low, re.I):
        return True
    if re.search(r"\b(?:на\s+)?сервис\w*\b", low) and re.search(
        r"\b(?:ждёт|ждет|стоит|находится)\b", low
    ):
        return True
    # STT: «сделать то есть» — не регламентное ТО (регрессия 13708).
    if re.search(r"\bсделать\s+то\b(?!\s+есть)", low):
        return True
    _sto_replacement_part_tokens = (
        "стабилизатор",
        "стойк стабилиз",
        "амортизатор",
        "колодк",
        "рычаг",
        "шаров",
        "стойк",
        "сальник",
        "манжет",
        "форсунк",
        "турбин",
    )
    if any(x in low for x in _sto_replacement_part_tokens):
        if re.search(r"\b(?:замен|поменя)\w+\b", low) or any(
            p in low for p in ("просто работ", "свои будут", "свои запчаст")
        ):
            return True
        if any(p in low for p in ("цена", "стоимост", "сколько", "по времени")):
            return True
    if any(
        p in low
        for p in (
            "мастер-приёмщик",
            "мастер приемщик",
            "мастер-приемщик",
            "мастер приёмщик",
        )
    ):
        return True
    if any(
        p in low
        for p in (
            "записаться на сервис",
            "запись на сервис",
            "на сервис записаться",
            "запись на то",
            "записаться на то",
            "на то записаться",
            "на то записываться",
            "записываться на то",
            "на т о запис",
            "на то хотел",
            "техническое обслуживание",
            "записаться на техническое обслуживание",
            "по техническому обслуживанию",
            "уточнить время записи",
            "плотная запись",
            "гарантийн",
            "по гарантии",
            "гарантийный случай",
            "гарантийного случая",
            "сервисные кампании",
            "ранее обслуживали",
            "ранее обслуживались",
            "на кого оформлен",
            "на кого автомобиль оформлен",
            "автомобиль на кого оформлен",
            "на вас автомобиль оформлен",
            "госномер",
            "гос номер",
            "фамилию владельца",
            "фамилия владельца",
            "напомните фамилию",
            "скажите фамилию владельца",
            "фио",
            "какие работы сделать",
            "какие работы необходимо",
            "фаркоп",
            "фарков",
            "установка фаркоп",
            "поставить фаркоп",
            "подключить электрику",
            "подключение электрики",
            "проводка фаркоп",
            "отдел ремонта",
            "шиномонтаж",
            "машиномонтаж",  # STT: шиномонтаж
            "переобувк",
            "развал",
            "замена масла",
            "заменить масло",
            "меняется масло",
            "приёмка слесарного",
            "приемка слесарного",
            "слесарный цех",
            "хочу отремонтировать",
            "можно отремонтировать",
            "хочу заменить",
            "можно заменить",
            "хочу поменять",
            "можно поменять",
            "продиагностировать",
            "хочу продиагностировать",
            "можно продиагностировать",
            "загнать",
            "диагностик",
            "техобслуживание",
            "техосмотр",
            "то один",
            "первое техническое",
            "первое техническое обслуживание",
            "слесарн",
            "заказ наряд",
            "заказ-наряд",
            "обслуживаюсь",
            "обслуживается",
            "первое то",
            "второе то",
            "третье то",
            "четвёртое то",
            "четвертое то",
            "пятое то",
            "пятое тэо",
            "пройти то",
            "сделать тэо",
            "тэо сделать",
            "стоимость то",
            "узнать стоимость то",
            "уточнить цену на то",
            "уточнить цену на тв",
            "то можно делать",
            "то -",
            "замену колес",
            "замену колёс",
            "замена колес",
            "замена колёс",
            "записаться на шиномонтаж",
            "записаться на замену колес",
            "записаться на замену колёс",
            "со сервисом связаться",
            "с сервисом связаться",
            "с сервисом соединить",
            "с сервисным центром соединиться",
            "с сервисным центром",
            "соединить с сервисом",
            "викинги сервис",
            "чери сервис",
            "викинги чери сервис",
            "чинсервис",
            "чистк радиатор",
            "чистка радиатор",
            "прочистить радиатор",
            "радиатор",
            "перевожу на сервис",
            "переключу на сервис",
            "переведу на сервис",
            "переключаю на сервис",
            "ремонт авто",
            "ремонт машин",
            "кузовной ремонт",
            "кузовной цех",
            "покраск",
            "вмятин",
            "царапин",
            "профилактик",
            "перенести запись",
            "перенос записи",
            "перезапис",
            "уточнить запись",
            "кондиционер",
            "заправк",
            "фреон",
            "эвакуац",
            "компрессор",
            "красител",
            "ультрафиолет",
            "по поводу вашего автомобил",
        )
    ):
        return True
    if any(p in low for p in ("не работает", "не греет", "шумит", "дребезжит", "дребезжат", "стук")):
        if any(
            p in low
            for p in (
                "телеграм не работает",
                "telegram не работает",
                "whatsapp не работает",
                "макс не работает",
            )
        ):
            return False
        return any(
            a in low
            for a in (
                "авто",
                "машин",
                "автомобил",
                "мотор",
                "двигател",
                "коробк",
                "акпп",
                "вариатор",
                "кондиционер",
                "печк",
                "аккумулятор",
                "генератор",
                "тормоз",
                "подвеск",
                "стеклоподъ",
                "стеклоподъем",
                "рулев",
                "масло ",
                "маслян",
                "диагностик",
                "слесар",
                "шиномонтаж",
                "гарантийн",
                "запис",
                "техобслуживание",
                "техничес",
                "ремонт авто",
                "ремонт машин",
                "кузовной",
                "тэо",
                " то ",
            )
        )
    return False


def _is_sto_accessory_install_dialog(text: str) -> bool:
    """
    Сервисный диалог про установку доп.оборудования (фаркоп/проводка/монтаж), а не цикл ОП.
    Может содержать модель/цену, но без признаков покупки нового авто.
    """
    low = (text or "").lower()
    work = (
        any(
            p in low
            for p in (
                "фаркоп",
                "фарков",
                "подключить электрику",
                "подключение электрики",
                "доп оборуд",
                "допоборуд",
            )
        )
        or (
            ("проводк" in low or "электрик" in low)
            and any(p in low for p in ("монтаж", "установк", "подключ"))
        )
    )
    intent = any(
        p in low
        for p in (
            "поставить",
            "установить",
            "сколько",
            "стоит",
            "стоимость",
            "цена",
            "запис",
            "когда ближайшее",
            "ближайшее время",
        )
    )
    op_purchase = any(
        p in low
        for p in (
            "тест-драйв",
            "тест драйв",
            "хочу купить",
            "покупаю ",
            "покупку автомобиля",
            "покупка автомобиля",
            "новый автомобил",
            "выставочн",
        )
    )
    return work and intent and not op_purchase


def _is_sto_inbound_warranty_booking_dialog(text: str) -> bool:
    """
    Входящий сервисный диалог по гарантии/неисправности с явными атрибутами приёмки и записью.
    Такие кейсы относятся к широкому STO_IN даже при шумном STT в приветствии линии.
    Узкий слой может остаться НЕ_ТО (rubric NULL).
    """
    # Исходящий CRM по ТО (9088) — не гарантийный STO_IN; исх. мастер кузовного (8491) — оставляем.
    if _is_outbound(text or "") and not _is_kuzov_outbound_master_claim_callback_opening(text):
        return False
    low = (text or "").lower()
    warranty_or_fault = any(
        p in low
        for p in (
            "по гарантии",
            "гаранти",
            "гарантий",
            "чек загорел",
            "руль облазит",
            "неисправ",
        )
    )
    reception_attrs = any(
        p in low
        for p in (
            "ранее обслужив",
            "госномер",
            "гос номер",
            "фамилия",
            "автомобиль у вас",
        )
    )
    booking_dialog = any(
        p in low
        for p in (
            "запись",
            "записать",
            "записыв",
            "ближайш",
            "на июнь",
            "на май",
            "накануне",
            "напомним о визите",
            "удобно будет подъехать",
            "подъеду",
        )
    )
    explicit_op = any(
        p in low
        for p in (
            "менеджер отдела продаж",
            "тест-драйв",
            "тест драйв",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "выставочн",
        )
    )
    return warranty_or_fault and reception_attrs and booking_dialog and not explicit_op


def _is_sto_inbound_service_booking_reception_substantive_dialog(text: str) -> bool:
    """
    Входящий сервисный диалог записи/приёмки с сутью работ (не только «здравствуйте»),
    атрибутами приёмки и согласованием визита — широкий STO_IN без строки диспетчера в STT (8240).
    Пересечение с гарантийным правилом допустимо: оба ведут в STO_IN.
    """
    if _is_outbound(text or ""):
        return False
    try:
        from text_normalization import normalize_text

        low = (normalize_text(text or "") or text or "").lower()
    except Exception:
        low = (text or "").lower()
    if not _has_sto_substantive_service_intent(low):
        return False
    reception_attrs = any(
        p in low
        for p in (
            "ранее обслужив",
            "госномер",
            "гос номер",
            "фамилия",
            "автомобиль у вас",
        )
    )
    booking_dialog = any(
        p in low
        for p in (
            "запись",
            "записать",
            "записыв",
            "ближайш",
            "на июнь",
            "на май",
            "накануне",
            "напомним о визите",
            "удобно будет подъехать",
            "подъеду",
        )
    )
    explicit_op = any(
        p in low
        for p in (
            "менеджер отдела продаж",
            "тест-драйв",
            "тест драйв",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "выставочн",
        )
    )
    return reception_attrs and booking_dialog and not explicit_op


def _sto_booking_intake_blocks_false_op_gatekeeper(transcript: str) -> bool:
    """
    Живая запись/приёмка на сервис (развал, слот, госномер, пятое ТО) на линии диспетчера/ассистента —
    не трактовать как OP_IN через ложную «стоимость»+авто (регрессии 9002, 8964).
    """
    if not (transcript or "").strip():
        return False
    try:
        from text_normalization import normalize_text

        low = (normalize_text(transcript) or transcript).lower()
    except Exception:
        low = (transcript or "").lower()
    low = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", low)
    if not _has_sto_substantive_service_intent(low):
        return False
    line_ok = (
        _has_sto_service_line_context(transcript)
        or _has_sto_directory_assistant_or_dispatcher_line(transcript)
        or "ассистент сервиса" in low
        or "диспетчер сервиса" in low
        or "енсервис" in low
        or "итмсервис" in low
        or _has_sto_greeting_service_with_dispatcher_name(transcript)
    )
    if not line_ok:
        return False
    booking = any(
        p in low
        for p in (
            "развал",
            "схожд",
            "шиномонтаж",
            "запис",
            "запишите",
            "госномер",
            "гос номер",
            "ранее обслужив",
            "контрольный звонок",
            "накануне",
            "техобслужив",
            "замена масла",
            "диагностик",
            "по какой причине",
            "пятое то",
            "пятое тэо",
            "пятое т о",
            "четвертое то",
            "четвёртое то",
            "нулевое то",
        )
    )
    explicit_op = any(
        p in low
        for p in (
            "менеджер отдела продаж",
            "тест-драйв",
            "тест драйв",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "отдел продаж",
        )
    )
    return booking and not explicit_op


def _service_work_price_is_sto_context(low: str) -> bool:
    """«Стоимость/цена» работ (развал, запись) — не субстанция покупки авто для ОП."""
    low = (low or "").lower()
    if not any(p in low for p in ("стоимост", "цена", "цен ", "сколько")):
        return False
    if not any(
        p in low
        for p in (
            "развал",
            "схожд",
            "шиномонтаж",
            "техобслужив",
            "запись на",
            "записать",
            "запишите",
            "госномер",
            "диагностик",
            "замена масла",
            "контрольный звонок",
            "ранее обслужив",
            "по какой причине",
            "соляных блок",
            "колодк",
            "пятое то",
            "пятое тэо",
            "первое то",
            "второе то",
            "третье то",
            "четвертое то",
            "четвёртое то",
            "нулевое то",
            "кондиционер",
            "заправк",
            "фреон",
            "эвакуац",
            "красител",
            "ультрафиолет",
            "компрессор",
            "замен",
            "двигател",
            "мотор",
            "на сервисе",
            "приёмщик",
            "приемщик",
        )
    ):
        return False
    if any(
        p in low
        for p in (
            "тест-драйв",
            "комплектац",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "кредит на новый",
            "менеджер отдела продаж",
        )
    ):
        return False
    return True


def _has_sto_greeting_service_with_dispatcher_name(transcript: str) -> bool:
    """
    В шапке: линия сервиса (роль/STT) + Юлия/Андреева — ветка СТО (8964: итмсервис + Юлия).
    """
    if not (transcript or "").strip():
        return False
    raw_low = (transcript or "").lower()
    try:
        from text_normalization import normalize_text

        low = (normalize_text(transcript) or transcript).lower()
    except Exception:
        low = raw_low
    low = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", low)
    # STT часто даёт «диспетчер сервис» без окончания: считаем как линию сервиса.
    low = re.sub(r"\bдиспетчер[\s-]*сервис\b", "диспетчер сервиса", low)
    low = re.sub(r"\b(?:енсервис|итмсервис|снсервис)\b", "ассистент сервиса", low)
    # STT 13785: «на И ТУ» / «на ту» = на ТО; «Члюсти»/«Челсти» — линия сервиса.
    low = re.sub(r"\bна\s+и\s+ту\s+запис", " на то запис", low, flags=re.I)
    low = re.sub(r"\bна\s+ту\s+запис", " на то запис", low, flags=re.I)
    low = re.sub(r"\bч(?:ел|лю)сти\b", " чери сервис ", low, flags=re.I)
    head = low[:900]
    raw_head = raw_low[:900]
    names = (
        "юлия",
        "юлию",
        "юля",
        "андреева",
        "александра",
        "плаксина",
        "гринкина",
        "гранкина",
        "дарья",
        "дарью",
        "лилия",
        "лилию",
    )
    # 16512: «маркетолог Юля» — не диспетчер/ассистент сервиса Юлия.
    if "маркетолог" in head:
        names = tuple(n for n in names if n not in ("юлия", "юлию", "юля"))
    if not any(n in head for n in names):
        return False
    if "меня зовут" in head and any(b in head for b in ("викинг", "чери", "компани")):
        return True
    if "ассистент сервиса" in head or "диспетчер сервиса" in head:
        return True
    if re.search(r"\b(?:ен|итм)сервис\b", raw_head):
        return True
    if "сервис" in head and (
        "слушаю вас" in head
        or "слуша вас" in head
        or re.search(r"\bслуша(?:ю)?\s+вас\b", head[:320])
        or re.search(r"\bслуша(?:ю)?\b", head[:320])
    ):
        return True
    return False


def _is_sto_reception_mileage_not_used_vehicle(low: str) -> bool:
    """«Какой пробег» при записи на ТО — не тема б/у (8964)."""
    if not re.search(r"\bпробег\b", low or ""):
        return False
    if not _has_sto_substantive_service_intent(low):
        return False
    return any(
        p in low
        for p in (
            "госномер",
            "гос номер",
            "запис",
            "пятое то",
            "пятое тэо",
            "четвертое то",
            "четвёртое то",
            "нулевое то",
            "первое то",
            "второе то",
            "третье то",
            "техобслужив",
            "историю открою",
            "андреева",
            "ассистент сервиса",
            "диспетчер сервиса",
            "итмсервис",
            "енсервис",
            "контрольный звонок",
            "накануне позвоним",
        )
    )


def _is_sto_inbound_direct_service_line_opening(transcript: str) -> bool:
    """СТО вх.: прямое приветствие линии сервиса + диспетчер из справочника (до OP gatekeeper)."""
    if _is_outbound(transcript or ""):
        return False
    if not _has_sto_greeting_service_with_dispatcher_name(transcript):
        return False
    try:
        from text_normalization import normalize_text

        low = (normalize_text(transcript) or transcript).lower()
    except Exception:
        low = (transcript or "").lower()
    low = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", low)
    low = re.sub(r"\b(?:енсервис|итмсервис|снсервис)\b", "ассистент сервиса", low)
    if _has_sto_substantive_service_intent(low):
        return True
    return any(
        p in low
        for p in (
            "госномер",
            "гос номер",
            "пятое то",
            "пятое тэо",
            "запис",
            "запишите",
            "накануне",
            "контрольный звонок",
        )
    )


def _is_failed_sto_transfer_misc(text: str) -> bool:
    """
    Не приёмка СТО: попытка перевести на линию/отдел не удалась, абонент вернулся,
    диспетчер/ассистент снимает номер или обещает перезвон — классификация «прочие», не STO_IN.
    """
    t = (text or "").lower()
    if not any(w in t for w in ("викинги", "чери", "тенет", "тэнет", "дилер")):
        return False
    return any(
        p in t
        for p in (
            "звонок обратно вернулся",
            "звонок вернулся обратно",
            "звонок вернулся",
            "вернулся звонок",
            "перевести не удалось",
            "перевести не получилось",
            "соединить не удалось",
            "не смогли вас соединить",
            "не смогла вас соединить",
            "не удалось соединить",
            "не удалось вас соединить",
            "не могу соединить",
            "не получается соединить",
        )
    )


def _is_wrong_dealer_center_redirect_other(text: str) -> bool:
    """
    Клиент попал не в тот ДЦ, приёмка перенаправляет в другой (12586: Lada Vesta → Chery).
    «Вы не в тот дилерский центр позвонили… вас/вам нужно в другой» — Прочие, не STO_IN.
    """
    low = (text or "").lower()
    wrong_dc = ("не в тот" in low and "дилерск" in low) or "не в тот дилерский" in low
    redirect = any(
        p in low
        for p in (
            "в другой",
            "нужно в другой",
            "нужен другой",
            "надо в другой",
            "обратитесь в другой",
            "позвоните в другой",
        )
    )
    if not (wrong_dc and redirect):
        return False
    # Запись состоялась после переадресации — не этот сценарий.
    if any(p in low for p in ("записал", "записали", "запишу", "записываю", "с вами записал")):
        pos = max(low.rfind("другой"), low.rfind("дилерск"))
        tail = low[pos:] if pos >= 0 else ""
        if any(p in tail for p in ("записал", "записали", "запишу", "записываю")):
            return False
    return True


def _is_sto_outbound_record_confirmation_opening(text: str) -> bool:
    """
    Начало транскрипта похоже на исходящий звонок СТО: подтверждение/уточнение записи клиенту.
    Нужно, чтобы правило 0b (админ-линия + «техобслуживание» где угодно в тексте) не относило такие файлы к СТО вх.
    """
    head = (text or "").lower()[:1200]
    if any(
        p in head
        for p in (
            "запись подтверждена",
            "запись подтверждаете",
            "записан на ",
            "записаны на ",
            "к нас записаны",
            "к нам записаны",
            "записаны к нас",
        )
    ):
        return True
    if "подтверждаете" in head and "запис" in head:
        return True
    if "вы на завтра" in head and "записан" in head:
        return True
    if re.search(r"\bзаписыва\w*\s+автомобил\w*\s+на\s+завтра\b", head, re.I):
        return True
    if re.search(r"\bзавтра\b", head) and re.search(r"\bактуальн", head, re.I) and "техническ" in head:
        return True
    return False


def _is_sto_outbound_repeat_followup_opening(text: str) -> bool:
    """
    Исходящий звонок-продолжение диспетчера:
    «[Имя], ещё раз добрый день ... как я вам уже говорила».
    Используется для корректировки STO_IN -> STO_OUT.
    """
    head = (text or "").lower()[:2400]
    if not head.strip():
        return False
    if not re.search(
        r"\b[а-яё]{2,25}\s*,\s*ещ[её]\s+раз\s+добрый\s+(?:день|вечер)\b",
        head,
        re.I,
    ):
        return False
    if "диспетчер" not in head and "ассистент" not in head:
        return False
    return any(
        p in head
        for p in (
            "как я вам уже говорила",
            "как я вам уже говорил",
            "как я уже говорила",
            "как я уже говорил",
        )
    )


def _kuzov_body_topic(low: str) -> bool:
    """
    Тема кузовного цеха. STT не всегда даёт «кузовн…» — часто «кузовой», «кузова» (без подстроки «кузовн»).
    """
    if not (low or "").strip():
        return False
    low = low.lower()
    if "кузовн" in low or "кузоно" in low:
        return True
    # STT: «кузовной» → «кузавной», «кузавой» и т.п.
    if "кузав" in low:
        return True
    if re.search(r"\bкузов", low):
        # «в новом кузове», «новый кузов» — про поколение/рестайлинг модели, не кузовной цех
        if re.search(
            r"(?:новом|новый|новая|нового)\s+кузов[аеё]?\b",
            low,
        ) and "кузовн" not in low and "кузоно" not in low:
            return False
        return True
    return False


def _is_body_shop_master_opening(t: str) -> bool:
    """
    Первые ~400 символов: бренд (Викинги/Чери) + «кузовн…» + сотрудник из KUZOV_BODY_MANAGERS
    (имя или фамилия, см. managers_config.get_kuzov_body_word_tokens). Только в связке с кузовным.
    """
    head = (t or "").lower()[:400]
    if not _kuzov_body_topic(head):
        return False
    if not any(b in head for b in ("викинги", "виикинги", "чери", "черри")):
        return False
    m = re.search(r"(кузовн\w*|кузоно\w*|кузов\w*)", head)
    if not m:
        return False
    after_kuzov = head[m.start() :]
    try:
        from admin_panel.managers_config import get_kuzov_body_word_tokens

        tokens = get_kuzov_body_word_tokens()
    except ImportError:
        tokens = frozenset()
    for tok in tokens:
        if len(tok) < 3:
            continue
        if re.search(r"\b" + re.escape(tok) + r"\b", after_kuzov):
            return True
    return False


def _sto_dispatcher_sto_intent_for_kuzov(t: str) -> bool:
    """
    Исключение для 0q/0wk: перевод с кузовного на сервис — диспетчер + ТО/ремонт/диагностика;
    такие звонки не уводим в Прочие только из‑за слова «кузовной».
    """
    low = (t or "").lower()
    return (
        (_has_sto_service_context(t) or "диспетчер" in low)
        and any(
            n in low
            for n in ("андреева", "юлия", "юлию", "плаксина", "гринкина", "гранкина", "александра", "дарья")
        )
        and any(
            m in low
            for m in (
                "то ",
                " то",
                "тэо",
                "то делать",
                "сделать то",
                "сделать тэо",
                "тэо сделать",
                "продиагностировать",
                "загнать",
                "ремонт двигателя",
                "ремонт авто",
                "ремонт машин",
                "ремонт ",
                " ремонт",
                "епс",
                "индикатор ",
                " индикатор",
                "пробег",
                "пробегу",
                "механик",
                "слесар",
            )
        )
    )


def _kuzov_other_exempt_op_manager_sales_consult(low: str) -> bool:
    """
    Исходящая консультация менеджера ОП: «кузов» в описании комплектации/осмотра нового авто — не ремонт в КЦ.
    Управляется VIKINGI_CLASSIFY_KUZOV_OP_EXEMPT в config (0 = прежнее поведение).
    """
    try:
        from config import VIKINGI_CLASSIFY_KUZOV_OP_EXEMPT

        if not VIKINGI_CLASSIFY_KUZOV_OP_EXEMPT:
            return False
    except ImportError:
        pass
    if "менеджер отдела продаж" not in low and "менеджер отдела продажный" not in low:
        return False
    if not any(
        b in low
        for b in (
            "викинг",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "заставн",
            "тольятт",
        )
    ):
        return False
    body_shop_repair = any(
        p in low
        for p in (
            "цех кузовного ремонта",
            "кузовной цех",
            "ремонт кузова",
            "покраска кузова",
            "окраска кузова",
            "рихтовка кузова",
            "мастер кузов",
            "отдел кузова",
            "кузовной ремонт",
        )
    )
    if body_shop_repair:
        return False
    return any(
        p in low
        for p in (
            "комплектаци",
            "проконсультировать по автомобил",
            "проконсультировать по машин",
            "интересует покупка",
            "интересовались",
            "актуально",
            "удобно говорить",
            "мне передали",
            "передали вас",
            "передали автомобил",
            "передали нас",
            "вот звоню",
            "звоню вам проконсультировать",
            "звоню вас проконсультировать",
            "проконсультировать по автомобилю",
            "консультация по автомобил",
        )
    )


def _should_classify_kuzov_as_other(t: str) -> bool:
    """Подстрока кузовного цеха в тексте → Прочие, кроме явного перевода на линию СТО/ТО."""
    low = (t or "").lower()
    if not _kuzov_body_topic(low):
        return False
    if _kuzov_exempt_when_new_car_sales_markers(low):
        return False
    if _kuzov_other_exempt_op_manager_sales_consult(low):
        return False
    return not _sto_dispatcher_sto_intent_for_kuzov(t)


def _is_body_shop_repair_accessories_consult_other(text: str) -> bool:
    """
    Кузовной/ремонт: допоборудование (сетка, не заводская установка), страховка не покрывает,
    готовность авто к выдаче — Прочие, не ОП (16768; STT часто без слова «кузовной»).
    """
    low = (text or "").lower()
    if any(
        p in low
        for p in (
            "менеджер отдела продаж",
            "менеджера отдела продаж",
            "тест-драйв",
            "тест драйв",
            "тестдрайв",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "интересует автомобиль",
            "интересует машина",
            "интересует черри",
            "интересует chery",
            "интересует тенет",
            "интересует tenet",
        )
    ):
        return False
    accessories = any(p in low for p in ("допборудован", "допоборудован")) or (
        "сетк" in low
        and any(p in low for p in ("бампер", "поставить", "металлическ", "пластиков"))
    )
    repair_status = any(
        p in low
        for p in (
            "машина когда будет готова",
            "когда будет готова",
            "ближе к вечеру",
            "машину заберу",
            "как машину заберу",
            "оплачивать у вас потом",
        )
    )
    insurance_addon = (
        "страхов" in low
        and any(p in low for p in ("не покрыв", "не покроет", "не покроют"))
        and (accessories or "допборуд" in low or "допоборуд" in low or "не заводск" in low)
    )
    non_factory_install = "не заводск" in low and (accessories or "сетк" in low)
    return (accessories and repair_status) or insurance_addon or (
        non_factory_install and "страхов" in low
    )


def _is_body_shop_internal_coordination(t: str) -> bool:
    """
    Дилер + кузовной контент без токена «кузовн» в STT + координация ремонта/записи/документов → Прочие.
    Явная тема продажи нового авто — не перехватываем.
    """
    low = (t or "").lower()
    if "кузовн" in low or "кузоно" in low:
        return False
    op_sale = any(
        m in low
        for m in (
            "тест-драйв",
            "тест драйв",
            "тестдрайв",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "интересует черри",
            "интересует chery",
            "интересует тенет",
            "интересует tenet",
            "выставочн",
            "новый автомобил",
            "посмотреть машину",
            "посмотреть авто",
        )
    )
    if "комплектаци" in low and any(
        x in low
        for x in (
            "новый автомобил",
            "купить авто",
            "купить машину",
            "покупк автомобил",
            "покупк авто",
            "chery",
            "тенет",
            "интересует",
        )
    ):
        op_sale = True
    if op_sale:
        return False
    dealer = any(
        b in low
        for b in (
            "викинги",
            "виикинги",
            "чери",
            "черри",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "дилерск",
            "автосалон",
        )
    )
    if not dealer:
        return False
    body_alt = any(
        p in low
        for p in (
            "отдел кузова",
            "цех кузова",
            "ремонт кузова",
            "мастер по кузову",
            "по кузову ",
            " по кузову",
        )
    )
    if not body_alt:
        return False
    internal = any(
        p in low
        for p in (
            "запись на ремонт",
            "перенесли запись",
            "перенесла запись",
            "перенесли на ",
            "перенесла на ",
            "записали на",
            "записали вас",
            "записан на",
            "пригон",
            "привезти машин",
            "оставить автомобил",
            "сдача в ремонт",
            "стс",
            "доверенност",
            "свидетельств",
            "договор",
            "лизинг",
        )
    )
    return internal


def _pokupaju_is_personal_shopping_not_vehicle_sale(low: str) -> bool:
    """14202: «просто покупаю такие вещи» — не покупка автомобиля."""
    if "покупаю" not in (low or ""):
        return False
    if re.search(r"покупаю\s+так(?:ие|ую)\s+вещ", low, re.I):
        return True
    if re.search(r"просто\s+покупаю", low, re.I) and not any(
        p in low for p in ("автом", "машин", " авто", "тигго", "tiggo", "chery", "чери", "тенет", "tenet")
    ):
        return True
    return False


def _credit_mention_is_staff_reconciliation_not_vehicle_sale(low: str) -> bool:
    """14202: «кредит, естественно, все оплатили» / агентские комиссии — не тема ОП."""
    if not re.search(r"\bкредит", low or "", re.I):
        return False
    if re.search(r"кредит,\s*естественно", low, re.I):
        return True
    if re.search(r"кредит\w*,\s*и\s+ипотек", low, re.I):
        return True
    if "все оплатили" in low and "кредит" in low:
        return True
    if "агентк" in low and "комисс" in low:
        return True
    return False


def _is_internal_staff_document_flow_other(t: str) -> bool:
    """
    Внутренняя координация сотрудников дилера по заказ-нарядам, ОПД/УПД, пропавшим документам —
    без записи на ремонт и без приёмки клиента → Прочие (не СТО вх.).
    """
    low = (t or "").lower()
    dealer = any(
        b in low
        for b in (
            "викинги",
            "виикинги",
            "чери",
            "черри",
            "chery",
            "заставн",
        )
    )
    if not dealer:
        return False

    has_zakaz_naryad = (
        "заказ-наряд" in low
        or "заказ наряд" in low
        or "заказнаряд" in low
        or ("наряд" in low and "печата" in low)
    )
    has_opd_upd = "опд" in low or "упд" in low
    has_doc_chase = "документ" in low and any(
        q in low
        for q in (
            "где ",
            "куда дел",
            "нет документ",
            "не хватает документ",
            "пропали",
            "кому передали",
            "кому отдали",
        )
    )
    if not (has_zakaz_naryad or has_opd_upd or has_doc_chase):
        return False

    has_trainee = "стажер" in low or "стажёр" in low
    has_both_acct_docs = "опд" in low and "упд" in low
    has_lena = bool(re.search(r"(?:^|[\s,])лена(?:[\s,.\?!]|$)", low))
    internal = has_trainee or has_both_acct_docs or has_lena
    if not internal:
        return False

    if any(
        p in low
        for p in (
            "записаться на",
            "запись на ремонт",
            "запись на то",
            "записать на то",
            "записать на т ",
            "техническое обслуживание",
            "шиномонтаж",
            "продиагностировать",
            "диагностик автомобил",
            "не работает климат",
            "не работает кондиционер",
        )
    ):
        return False
    return True


def _test_drive_is_customer_sales_context(low: str) -> bool:
    """
    «Тест-драйв» в смысле сделки ОП (запись, заявка, интерес клиента), а не служебная реплика
    («сейчас на тест-драйв отправлю», «с автомобилем на тест-драйве» между сотрудниками СТО).
    """
    if not (low or "").strip():
        return False
    if _is_op_promotional_contest_crm_other_low(low):
        return False
    if not any(p in low for p in ("тест-драйв", "тест драйв", "тестдрайв", "тестовая поездка")):
        return False
    _customer_td_markers = (
        "записать на тест",
        "запись на тест",
        "заявк",
        "оставляли заявку",
        "интересует тест",
        "проходили тест-драйв",
        "проходили тест драйв",
        "хотел на тест",
        "хочу на тест",
        "хотела на тест",
    )
    staff_service_td = (
        r"с\s+автомобил\w*\s+на\s+тест[\s-]*драйв",
        r"автомобил\w*\s+на\s+тест[\s-]*драйв",
        r"на\s+тест[\s-]*драйве\b",
        r"мастер\w*[^.!?]{0,50}тест[\s-]*драйв",
        r"тест[\s-]*драйв[^.!?]{0,40}мастер",
    )
    if any(re.search(p, low) for p in staff_service_td):
        if not any(p in low for p in _customer_td_markers):
            return False
    staff_colloquial = (
        r"(?:отправлю|отправь|пойду|схожу|еду|уеду|вернусь|сейчас\s+на)[^.!?]{0,70}тест[\s-]*драйв",
        r"тест[\s-]*драйв[^.!?]{0,60}((?:отправлю|отправь|схожу|пойду|отправ))",
        r"давай\s+(?:я\s+)?(?:сейчас\s+)?на\s+тест[\s-]*драйв\s+отправ",
    )
    if any(re.search(p, low) for p in staff_colloquial):
        if not any(p in low for p in _customer_td_markers):
            return False
    return True


def _has_new_vehicle_model_in_purchase_context(low: str) -> bool:
    """
    Модель/бренд Чери·Тенet — маркер ОП только вместе с намерением покупки/сделки,
    не при упоминании авто клиента в сервисном разговоре (16382: Tiggo 4 + стабилизаторы).
    """
    if not (low or "").strip():
        return False
    if not any(
        m in low
        for m in (
            "тигго",
            "tiggo",
            "арризо",
            "arrizo",
            "дашинг",
            "jaecoo",
            "джак",
            "тенет",
            "тэнет",
            "tenet",
            "чери",
            "chery",
            "черри",
        )
    ):
        return False
    if _has_sto_greeting_service_with_dispatcher_name(low) and _has_sto_substantive_service_intent(low):
        return False
    purchase_ctx = (
        "интересует",
        "купить",
        "приобрести",
        "хочу",
        "хотел",
        "хотела",
        "комплектац",
        "в наличии",
        "сколько стоит",
        "какая цена",
        "кредит",
        "рассроч",
        "трейд-ин",
        "trade-in",
        "бронь",
        "менеджер отдела продаж",
        "менеджера отдела продаж",
        "отдел продаж",
        "новый автомоб",
        "новое авто",
        "записать на тест",
        "запись на тест",
        "по поводу покупки",
        "покупку автомоб",
    )
    return any(p in low for p in purchase_ctx)


def _has_client_facing_op_sales_cycle(transcript: str) -> bool:
    """Клиентский цикл ОП: менеджер с клиентом, перевод на ОП, покупка/заявка — не разговор сотрудников между собой."""
    low = (transcript or "").lower()
    if not low.strip():
        return False
    head = low[:1200]
    if "менеджер отдела продаж" in low or "менеджера отдела продаж" in low:
        return True
    if _has_op_manager_self_intro_in_head(head):
        return True
    if _has_op_outbound_surface_markers(transcript):
        return True
    if re.search(
        r"(переведу|переключ|перевожу|соединю)[^.!?]{0,120}(менеджер|отдел\s+продаж|менеджера)",
        low,
    ):
        return True
    if re.search(
        r"(?:официальн[^.!?]{0,80}дилер|администратор\s+салона)[^.!?]{0,160}слушаю\s+вас",
        low,
    ):
        return True
    if _is_client_initiated_after_greeting(transcript):
        return True
    if any(
        p in low
        for p in (
            "хочу купить",
            "хочу приобрести",
            "хотел купить",
            "хотела купить",
            "хотел приобрести",
            "хотела приобрести",
            "узнать цен",
            "уточнить цен",
            "какая цена",
            "сколько стоит",
            "интересует автомобиль",
            "интересует машин",
            "записать на тест",
            "запись на тест",
            "оставляли заявку",
            "удобно говорить",
            "удобно разговаривать",
        )
    ):
        return True
    return False


def _has_internal_staff_dialogue_signals(transcript: str) -> bool:
    """Признаки разговора сотрудников между собой (офис, площадка, логистика), а не линии с клиентом."""
    low = (transcript or "").lower()
    if not low.strip():
        return False
    head = low[:2000]

    if re.search(
        r"(?:^|[\s,.!?])[а-яё]{3,14},\s*(?:ты|слушай|слышишь|давай)(?:[\s,.!?]|$)",
        head,
    ):
        return True

    if any(
        p in low
        for p in (
            "слышишь",
            "иди кушай",
            "не вопрос",
            "я уехал",
            "я ушёл",
            "я ушел",
        )
    ):
        return True

    logistics_score = 0
    if "ключ" in low:
        logistics_score += 1
    if re.search(r"\bбухгалтер", low):
        logistics_score += 1
    if re.search(r"\bкабинет\s+(?:\d|двухсот|210)", low):
        logistics_score += 1
    if "служебн" in low and any(x in low for x in ("телефон", "кабинет", "номер")):
        logistics_score += 1
    if any(x in low for x in ("уборщиц", "шкафчик", "служебный кабинет")):
        logistics_score += 1
    if re.search(r"подним(?:ись|итесь)[^.!?]{0,100}(?:кабинет|к\s+нам|офис)", low):
        logistics_score += 1
    if re.search(r"продиктуй[^.!?]{0,50}(?:телефон|номер|мобильн|домашн)", low):
        logistics_score += 1
    if re.search(
        r"(?:отдать|перегнать|отвезти|погнать)[^.!?]{0,80}(?:на\s+мойк|мойку|стоянк|склад)",
        low,
    ):
        logistics_score += 1
    if "без номер" in low and any(
        x in low for x in ("машин", "авто", "элька", "тигго", "четвер", "четв")
    ):
        logistics_score += 1
    if re.search(
        r"тест[\s-]*драйв[а]?[^.!?]{0,50}(?:принес|отправ|пойду|схожу|отправлю|отправь)",
        low,
    ):
        logistics_score += 1
    if re.search(
        r"(?:отправлю|отправь|пойду|схожу|еду|уеду|сейчас\s+на)[^.!?]{0,70}тест[\s-]*драйв",
        low,
    ):
        logistics_score += 1

    admin_colleague = ("администратор салона" in low[:900]) or (
        "администратор" in low[:700] and "салон" in low[:700]
    )
    familiar_colleague = bool(
        re.search(r"(?:^|[\s,.!?])(?:оксан|лен[ауе]?|леноч)(?:[\s,.!?])", low[:1500])
    ) and any(g in low[:1200] for g in ("здравствуй", "добрый день", "слышишь", "не вопрос"))

    if logistics_score >= 2:
        return True
    if logistics_score >= 1 and admin_colleague and familiar_colleague:
        return True
    if logistics_score >= 1 and admin_colleague and re.search(
        r"позвонил[^.!?]{0,100}(?:посмотреть|подняться|служебн|ключ|бухгалтер|кабинет)",
        low,
    ):
        return True
    if logistics_score >= 1 and re.search(
        r"(?:^|[\s,.!?])(?:витал|оксан|лен[ауе]?|леноч|саш|серёж|серге)(?:[\s,.!?])",
        head,
    ):
        return True
    # 14202: проверка качества / заказ-наряды / коррозия — внутренний сервисный бэк-офис, не ОП.
    has_zn = any(p in low for p in ("заказнаряд", "заказ-наряд", "заказ наряд", "заказх наряд"))
    if has_zn and any(
        p in low
        for p in (
            "проверку качества",
            "проверка качества",
            "корроз",
            "моторн",
            "отсек",
            "заключен",
        )
    ):
        return True
    if "агентк" in low and "комисс" in low and any(
        p in low for p in ("подпис", "акт", "миллион", "остат", "документ")
    ):
        return True
    return False


def _is_internal_staff_dialogue_without_client_other(transcript: str) -> bool:
    """
    Рамочное правило: разговор между сотрудниками дилера без клиентского цикла ОП → Прочие.
    Офис, площадка, ключи, логистика машин — одна рамка; модель/тест-драйв в служебном контексте не дают ОП.
    """
    if not (transcript or "").strip():
        return False
    if _has_client_facing_op_sales_cycle(transcript):
        return False
    return _has_internal_staff_dialogue_signals(transcript)


def _is_internal_staff_office_coordination_other(transcript: str) -> bool:
    """См. _is_internal_staff_dialogue_without_client_other (офис + площадка)."""
    return _is_internal_staff_dialogue_without_client_other(transcript)


def _is_client_asks_transfer_to_body_shop_other(t: str) -> bool:
    """
    Клиент просит администратора/салон переключить на кузовной цех / отдел кузова — не линия приёмки СТО/ТО.
    """
    low = (t or "").lower()
    if not any(
        p in low
        for p in (
            "переключите на кузов",
            "переключи на кузов",
            "переключу на кузов",
            "переключаю на кузов",
            "переведите на кузов",
            "переведи на кузов",
            "переведу на кузов",
            "перевожу на кузов",
            "соедините с кузов",
            "соединить с кузов",
            "на кузовной цех",
            "на кузовной линию",
            "на отдел кузов",
        )
    ):
        return False
    if any(
        p in low
        for p in (
            "записаться на то",
            "запись на то",
            "техническое обслуживание",
            "замена масла",
            "замену масла",
        )
    ):
        return False
    return any(
        b in low
        for b in (
            "администратор",
            "салон",
            "викинги",
            "чери",
            "черри",
            "chery",
            "дилер",
            "официальн",
        )
    )


def _is_service_transfer_to_other_department(t: str) -> bool:
    """
    Со линии сервиса (ассистент/диспетчер) — перевод на другой отдел, не вопрос к СТО-приёмке.
    Страхование, запчасти, продажи, кузовной и т.п. → Прочие.
    """
    low = (t or "").lower()
    service_intro = (
        "ассистент сервиса" in low
        or "диспетчер сервиса" in low
        or (
            ("викинги сервис" in low or "чери сервис" in low or "викинги чери сервис" in low)
            and any(
                n in low
                for n in (
                    "андреева",
                    "юлия",
                    "юлию",
                    "плаксина",
                    "гринкина",
                    "гранкина",
                    "александра",
                )
            )
        )
    )
    if not service_intro:
        return False
    if not any(v in low for v in ("переведу", "перевожу", "переключу", "переключаю", "соединю", "соединю вас", "соединить")):
        return False
    # Углубление в сервис, не «на другой отдел»
    if re.search(
        r"(переведу|перевожу|переключу|переключаю)[^.;]{0,100}\b(на|к)\s+(сервис|диспетчер|ассистент|механик|слесар)",
        low,
    ):
        return False
    # Вопрос оператора «сервис или отдел продаж / куда соединить» — не «перевод с СТО на ОП»
    # После normalize_text бывает «ассистент сервиса или в отдел продаж» (не подходит «сервис или»).
    menu_service_or_sales = (
        (re.search(r"(?:сервис|сервиса)\s+или", low) or "или в отдел продаж" in low or "или в отделе продаж" in low)
        and "отдел продаж" in low
    )
    if menu_service_or_sales:
        if not any(
            p in low
            for p in (
                "переведу на отдел продаж",
                "перевожу на отдел продаж",
                "переключу на отдел продаж",
                "переключаю на отдел продаж",
                "переведу в отдел продаж",
                "перевожу в отдел продаж",
            )
        ):
            return False
    return any(
        m in low
        for m in (
            "на отдел страхования",
            "в отдел страхования",
            "к отделу страхования",
            "отдел страхования",
            "на отдел запасных частей",
            "в отдел запасных частей",
            "на отдел запчаст",
            "в отдел запчаст",
            "на отдел продаж",
            "в отдел продаж",
            "переключу на отдел продаж",
            "переведу на отдел продаж",
            "перевожу на отдел продаж",
            "переключаю на отдел продаж",
            "переведу в отдел продаж",
            "перевожу в отдел продаж",
            "переведу вас на менеджера",
            "переведу на менеджера",
            "перевожу вас на менеджера",
            "переведу на менеджера отдела продаж",
            "переведу вас на менеджера отдела продаж",
            "переключу на менеджера",
            "на менеджера по продажам",
            "менеджера отдела продаж",
            "на кузовной",
            "на отдел кузов",
            "переведу на кузов",
            "перевожу на кузов",
            "переключу на кузов",
            "на бухгалтер",
            "на склад",
        )
    )


def _service_line_took_inbound_call(low: str, transcript: str) -> bool:
    """Линия сервиса (ассистент/диспетчер) приняла входящий — не определяет итоговую категорию."""
    if "диспетчер сервиса" in low or "ассистент сервиса" in low:
        return True
    if re.search(r"\b(?:ассистент|диспетчер)\s+сервис", low):
        return True
    return bool(_has_sto_service_line_context(transcript))


def _transfer_to_op_sales_announced(low: str) -> bool:
    """Обещан/выполнен перевод на отдел продаж / менеджера ОП."""
    if re.search(
        r"(?:перевед|переключ|соедин)\w*[^.!?]{0,100}"
        r"(?:менеджер\w*[^.!?]{0,40}отдел\w*\s+продаж|отдел\w*\s+продаж|"
        r"на\s+менеджера\s+по\s+продаж)",
        low,
        re.I,
    ):
        return True
    return any(
        m in low
        for m in (
            "на отдел продаж",
            "в отдел продаж",
            "к отделу продаж",
            "переключу на отдел продаж",
            "переведу на отдел продаж",
            "перевожу на отдел продаж",
            "переключаю на отдел продаж",
            "переведу в отдел продаж",
            "перевожу в отдел продаж",
            "переведу вас на менеджера",
            "переведу на менеджера",
            "перевожу вас на менеджера",
            "переведу на менеджера отдела продаж",
            "переведу вас на менеджера отдела продаж",
            "переключу на менеджера",
            "на менеджера по продажам",
        )
    )


def _op_manager_spoke_after_service_handoff(low: str, transcript: str) -> bool:
    """После handoff с линии сервиса состоялся разговор с менеджером ОП (не только перезвон)."""
    if _is_op_all_managers_busy_callback_only_misc(transcript):
        return False
    if _is_op_failed_transfer_callback_only_misc(transcript):
        return False
    m_xfer = re.search(
        r"(?:перевед|переключ|соедин)\w*[^.!?]{0,120}"
        r"(?:менеджер\w*[^.!?]{0,40}отдел\w*\s+продаж|отдел\w*\s+продаж|"
        r"на\s+менеджера)",
        low,
        re.I,
    )
    m_mgr = re.search(r"\bменеджер\s+отдела\s+продаж\b", low)
    if m_mgr and (not m_xfer or m_mgr.start() > m_xfer.start()):
        return True
    tail = low[m_xfer.end() :] if m_xfer else low
    if any(len(n) >= 3 and n in tail for n in _OP_MANAGER_NAMES):
        return True
    if m_mgr and _has_op_sales_conversation_substance(transcript):
        return True
    return False


def _is_service_transfer_to_op_sales_successful_inbound(transcript: str) -> bool:
    """
    Ассистент/диспетчер сервиса принял звонок, перевод на менеджера ОП состоялся —
    ОП_вх. Кто взял трубку первым, не определяет категорию (16512 — исключение: маркетинг).
    """
    if _is_outbound(transcript):
        return False
    if _is_op_marketing_sponsorship_charity_contact_other(transcript):
        return False
    low = (transcript or "").lower()
    if not _service_line_took_inbound_call(low, transcript):
        return False
    if not _transfer_to_op_sales_announced(low):
        return False
    if not _op_manager_spoke_after_service_handoff(low, transcript):
        return False
    return bool(
        _v2_has_vehicle_purchase_substance(low)
        or _has_op_sales_conversation_substance(transcript)
    )


def _is_sto_reception_handoff_non_op_other(transcript: str) -> bool:
    """
    Продуктовый guard:
    звонок принят линией СТО (диспетчер/ассистент), далее перевод/соединение на другого сотрудника;
    если это не перевод в ОП-продажи, классифицируем как Прочие.
    """
    low = (transcript or "").lower()
    head = low[:5200]
    # Линия СТО приняла звонок.
    if not _has_initial_sto_reception_greeting(transcript):
        return False
    # Явный успешный перевод в ОП — исключение.
    if _is_service_transfer_to_op_sales_successful_inbound(transcript):
        return False
    # Прямой перевод/соединение на другой контакт.
    handoff = re.search(
        r"(?:\b(?:я|сейчас|секунд\w*)\b[^.!?]{0,60})?"
        r"(?:"
        r"\b(?:соедин(?:ю|ить|яю|ите|им)|переключ(?:у|ить|аю|ите)|перевед(?:у|ём|ите|яю))\b[^.!?]{0,50}\bва[сc]\b"
        r"|"
        r"\bва[сc]\b[^.!?]{0,50}\b(?:соедин(?:ю|ить|яю|ите|им)|переключ(?:у|ить|аю|ите)|перевед(?:у|ём|ите|яю))\b"
        r")",
        head,
        re.I,
    )
    if not handoff:
        return False
    # Если это углубление в сервисную же линию, не считаем «не-ОП переводом».
    if re.search(
        r"(?:переведу|перевожу|переключу|переключаю|соединю)[^.;]{0,120}\b(?:на|к)\s+(?:сервис|диспетчер|ассистент|механик|слесар)\b",
        head,
        re.I,
    ):
        return False
    # Нужен признак, что перевод именно не в ОП: либо явный не-ОП контекст, либо отсутствуют OP-признаки.
    handoff_window = head[handoff.start() : min(len(head), handoff.end() + 220)]
    non_op_target = any(
        p in handoff_window
        for p in (
            "кузов",
            "кузовн",
            "цех",
            "страхован",
            "запчаст",
            "склад",
            "бухгалтер",
            "дилерский центр",
            "специалист по кузов",
            "специалист по страх",
        )
    )
    if non_op_target:
        return True
    return False


def _is_service_taxi_dispatch_other(t: str) -> bool:
    """
    Заказ такси через линию сервиса (ассистент/диспетчер): адрес, стоимость, «ожидайте звонок».
    Не ОП и не приёмка на ТО/ремонт (8897: STT «заказа такси везёт»).
    """
    low = (t or "").lower()
    if "такси" not in low:
        return False
    dispatch = any(
        p in low
        for p in (
            "откуда вас забрать",
            "откуда забрать",
            "стоимость поездки",
            "стоимость поездк",
            "куда поедете",
            "куда поедет",
            "заказ такси",
            "заказа такси",
            "заказ такси вез",
            "ожидайте звонок",
            "ожидаем",
            "руб на данный момент",
            "руб.",
        )
    )
    if not dispatch:
        return False
    return (
        "ассистент сервиса" in low
        or "диспетчер сервиса" in low
        or ("ассистент" in low and "сервис" in low)
    ) and any(b in low for b in ("викинг", "чери", "chery", "заставн"))


def _is_bring_own_oil_service_visit_consult_other(transcript: str) -> bool:
    """
    Консультация перед визитом: какое моторное масло купить / приехать со своим маслом на обслуживание.
    Часто перевод на отдел запчастей (вязкость, литраж) — по смыслу не ядро реестра «СТО вх.», а Прочие.
    """
    low = (transcript or "").lower()
    own_or_buy = any(
        p in low
        for p in (
            "со своим масло",
            "со своим маслом",
            "своим масло",
            "своим маслом",
            "своё масло",
            "свое масло",
            "сам и приехать",
            "приехать к вам это со своим",
            "на обслуживание приехал",
            "хотел вот масло купить",
        )
    )
    consult = any(
        p in low
        for p in (
            "какое масло",
            "какое купить",
            "которое масло",
            "не подскажете",
            "сколько литров",
            "литров нужно",
            "литров покупать",
            "литров нужно поку",
        )
    )
    service_ctx = any(
        p in low
        for p in (
            "техобслуживание",
            "техническое обслуживание",
            "на обслуживание",
            "моторное",
        )
    )
    parts_or_handoff = "отдел запчаст" in low or (
        "запчаст" in low
        and (
            "переведу" in low
            or "перевожу" in low
            or "переключу" in low
            or "соединю" in low
            or "секунду" in low
        )
    )
    return own_or_buy and consult and service_ctx and parts_or_handoff


def _is_sto_service_dispatcher_internal_handoff_other(transcript: str) -> bool:
    """
    0bsw: диспетчер/ассистент сервиса → нейтральный перевод («переключу вас»), далее внутренняя линия
    (дилерский центр / смета / запчасти / цех) — не приёмка СТО и не ОП-вход.
    Явные переводы на другие отделы остаются на 0bs; удачный перевод на ОП — на исключение в 0bs.
    """
    low = (transcript or "").lower()
    if _is_outbound(transcript):
        return False
    head = low[:4200]
    service_intro = (
        "ассистент сервиса" in head
        or "диспетчер сервиса" in head
        or (
            ("викинги сервис" in head or "чери сервис" in head or "викинги чери сервис" in head)
            and any(
                n in head
                for n in (
                    "андреева",
                    "юлия",
                    "юлию",
                    "плаксина",
                    "гринкина",
                    "гранкина",
                    "александра",
                )
            )
        )
    )
    if not service_intro:
        return False
    if _is_service_transfer_to_other_department(transcript):
        return False
    if _is_service_transfer_to_op_sales_successful_inbound(transcript):
        return False
    if re.search(
        r"(?:переведу|перевожу|переключу|переключаю)[^.;]{0,100}\b(?:на|к)\s+(?:сервис|диспетчер|ассистент|механик|слесар)",
        head,
    ):
        return False
    neutral_handoff = bool(
        re.search(
            r"\b(?:понял[аи]?|поняла)\b[^.;]{0,55}(?:переключу|переведу)\s+ва[сc]\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:секундочку)\b[^.;]{0,35}(?:переключу|переведу)\s+ва[сc]\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:переключу|переведу|соединю)\s+ва[сc]\b", head, re.I)
    )
    if not neutral_handoff:
        return False
    if "дилерский центр" not in low:
        return False
    return any(
        p in low
        for p in (
            "смет",
            "отдел запасн",
            "отдел запчаст",
            "запчаст",
            "пружин",
            "амортизатор",
            "подвеск",
            "блок ",
            "слесар",
            "мастер цеха",
            "диагностик",
        )
    )


def _is_op_outbound_lead_request_marker(low: str) -> bool:
    """Лид на исходящий: сайт, CRM, Авито («у нас был запрос …»)."""
    head = (low or "")[:3200]
    if _is_site_lead_callback_phrase(low):
        return True
    if (
        "заявка от вас пришла" in head
        or ("от вас пришла" in head and "заявк" in head)
        or bool(re.search(r"заявк\w*[^.!?]{0,50}пришл", head))
        or bool(re.search(r"заявк\w*[^.!?]{0,50}приходил", head))
        or bool(re.search(r"от\s+ва[мс]\s+заявк\w*[^.!?]{0,40}приходил", head))
        or "заявку отправляли" in head
        or bool(re.search(r"заявк\w*\s+отправлял", head))
        or "у нас был запрос" in head
        or bool(re.search(r"\bу\s+нас\s+был\w*\s+запрос", head))
        or (
            bool(re.search(r"\bбыл\w*\s+запрос", head))
            and any(
                x in head
                for x in ("авито", " avito", "aвиit", "чери", "chery", "тенет", "тэнет", "tenet")
            )
        )
        or (
            any(x in head for x in ("авито", " avito", "aвиit"))
            and "запрос" in head
        )
    ):
        return True
    return False


def _is_op_outbound_primary_site_lead(text: str) -> bool:
    """
    Первичный исходящий ОП по лиду с сайта/CRM/Авито (9689: «заявка от вас пришла»; 11257: «запрос с Авито»).
    Не повторный follow-up и не спор о гарантии после покупки.
    """
    low = (text or "").lower()[:4500]
    if not low.strip() or not _is_outbound(low):
        return False
    if not _has_op_outbound_surface_markers(text):
        return False
    if _is_op_repeat_followup_outbound(text):
        return False
    lead = _is_op_outbound_lead_request_marker(low)
    purchase = any(
        p in low
        for p in (
            "покупка машины",
            "покупк",
            "интересует",
            "интересно",
            "комплектац",
            "тест-драйв",
            "хочу купить",
            "интересовались автомобил",
            "кредит",
            "рассроч",
            "налич",
            "тенет",
            "тэнет",
            "tenet",
            "тигго",
            "tiggo",
        )
    )
    return lead and purchase


def _is_post_purchase_warranty_misc(text: str) -> bool:
    """
    Спор или разъяснение срока/объёма гарантии по уже купленному авто.
    Такие звонки не ОП (часто в тексте «комплектация двигателя» — весовой маркер продаж).
    Не перехватываем явную запись на ТО / линию диспетчера СТО.
    """
    if _is_op_outbound_primary_site_lead(text):
        return False
    low = (text or "").lower()
    if "гарант" not in low and "гарантий" not in low:
        # Резерв без слова «гарантия» (STT): мастер дилера + неисправность
        # «мастер цеха» / слесарка — линия СТО, не спор о гарантии после покупки у мастера дилера.
        if (
            "мастер" in low
            and "мастер цеха" not in low
            and ("дилер" in low or "чери" in low or "викинги" in low)
            and any(w in low for w in ("руль", "руля", "руле", "камер", "камера", "неисправ", "поломк", "дефект"))
        ):
            return True
        # Исх.: мастер дилера уже сходил/уточнил по делу клиента
        if (
            "мастер" in low
            and "мастер цеха" not in low
            and ("дилер" in low or "чери" in low or "викинги" in low)
            and "сходил" in low
            and "уточн" in low
        ):
            return True
        return False
    if any(
        p in low
        for p in (
            "записаться на то",
            "запись на то",
            "запись на сервис",
            "записать на сервис",
            "диспетчер сервиса",
            "диспетчера сервиса",
            "диспетчеру сервиса",
            "ассистент сервиса",
            "напомните фамилию владельца",
            "на кого автомобиль оформлен",
            "вас записали",
            "записали вас",
            "тогда вас записали",
        )
    ):
        return False
    # STT часто даёт «Юлия диспетчер» без «диспетчер сервиса» — это линия СТО, не отсечение в Прочие по 0bg.
    if ("диспетчер" in low or "ассистент" in low) and any(
        n in low
        for n in (
            "юлия",
            "юлию",
            "андреева",
            "плаксина",
            "гринкина",
            "гранкина",
            "александра",
            "дарья",
        )
    ):
        return False
    if any(
        p in low
        for p in (
            "хочу купить",
            "покупку нового",
            "новый автомобиль",
            "новый авто ",
            "тест-драйв",
            "сколько стоит чери",
            "комплектации интересуют",
            "покупка машины",
            "заявка от вас пришла",
        )
    ):
        return False

    dispute_or_terms = any(
        p in low
        for p in (
            "обещал",
            "обещали",
            "обещано",
            "пятилет",
            "трёхлет",
            "трехлет",
            "не распростран",
            "вне гарант",
            "не по гарант",
            "срок гарантии",
            "гарантийн срок",
            "лет гарант",
            "года гарант",
            "год гарантии",
            "отказывают в гарант",
            "отказ в гарант",
            "сто тысяч",
            "100000",
            "100 000",
        )
    ) or bool(
        re.search(
            r"(?:срок\s+)?гарант\w*[^.!?]{0,70}(?:3|три|5|пять)\s+(?:год|лет)|"
            r"(?:3|три|5|пять)\s+(?:год|лет)[^.!?]{0,70}гарант",
            low,
        )
    )
    defect_warranty = "комплектац" in low and any(
        w in low for w in ("неисправ", "ремонт", "руль", "руля", "камер", "поломк", "дефект", "коррози")
    )
    master_dealer_defect = (
        "мастер" in low
        and "мастер цеха" not in low
        and ("дилер" in low or "чери" in low or "викинги" in low)
        and any(w in low for w in ("руль", "руля", "камер", "не работает", "километр", "пробег"))
    )
    return bool(dispute_or_terms or defect_warranty or master_dealer_defect)


def _is_b2b_bank_guarantee_tender_cold_call_other(t: str) -> bool:
    """
    B2B-звонок: банковские / «независимые» гарантии, тендеры, предложение сотрудничества (ищут приёмную/секретаря) —
    не ремонт и не гарантия на авто → Прочие.
    """
    low = (t or "").lower()
    tender_fin = any(
        p in low
        for p in (
            "банковские гарантии",
            "банковская гарантия",
            "гарантий на участие",
            "участвуете в тендерах",
            "участие в тендерах",
        )
    )
    tender_plus = "тендер" in low and (
        "гарант" in low or "банковск" in low or "оформлен" in low or "сотрудничеств" in low
    )
    indep_guarant_pitch = (
        "независим" in low
        and "гарант" in low
        and (
            any(
                x in low
                for x in (
                    "оформлен",
                    "сотрудничеств",
                    "предлож",
                    "актуален",
                    "тендер",
                )
            )
            or ("федеральн" in low and "компани" in low)
        )
    )
    finance_consult = "консалтинг" in low and ("финанс" in low or "гарант" in low or "тендер" in low)
    if not (tender_fin or tender_plus or indep_guarant_pitch or finance_consult):
        return False
    if any(
        p in low
        for p in (
            "записаться на",
            "запись на то",
            "запись на ремонт",
            "запись на сервис",
            "гарантийный случай",
            "гарантийного случая",
            "гарантийн ремонт",
            "по гарантии на автомобил",
            "диагностик автомобил",
        )
    ):
        return False
    if "тендер" not in low and any(
        p in low
        for p in (
            "диспетчер сервиса",
            "ассистент сервиса",
            "напомните фамилию владельца",
            "госномер",
            "замена масла",
        )
    ):
        return False
    return True


def _is_dealer_master_outbound_opening(t: str) -> bool:
    """
    Исходящий звонок мастера дилера: «Алло, (имя клиента), дилерский центр …, … мастер».
    В продукте нет STO_OUT — сервис + исходящий → Прочие, не OP_OUT (имя клиента может совпадать с именем менеджера ОП).
    Не перехватывать ОП: «меня зовут Захаров…», «звоню с официального дилера» + фамилия ОП — это менеджер продаж (фаркоп/комплектация тоже обсуждают мастера).
    """
    low = (t or "").lower()
    head = low[:700]
    if "дилер" not in head or "мастер" not in head:
        return False
    if not re.search(r"алло\s*[.,]?\s*[а-яё]{3,}", head):
        return False
    if "менеджер отдела продаж" in low[:450]:
        return False
    # Явное самопредставление менеджера ОП в начале — не линия мастера
    if re.search(
        r"меня\s+зовут\s+(?:захаров|евдокимов|краснощекова|краснощёкова|калаев|калаева|щеголев)(?:\s+[а-яё]+)?",
        head,
    ):
        return False
    if "звоню" in head and "официальн" in head and "дилер" in head:
        if any(
            n in head
            for n in (
                "захаров",
                "илья",
                "илью",
                "евгений",
                "евгения",
                "анастасия",
                "андрей",
                "евдокимов",
                "калаев",
                "калаева",
                "краснощек",
                "щеголев",
            )
        ):
            return False
    # Покупка / тест-драйв / трейд-ин в начале — сценарий ОП, даже если дальше «мастер по доп. оборудованию»
    if any(p in head for p in ["по поводу покупки", "позвонить по покупке", "тест-драйв", "тестдрайв", "трейд-ин", "trade-in"]):
        return False
    return True


def _is_sto_master_priyomshchik_outbound_other(text: str) -> bool:
    """
    Исходящий звонок мастера-приёмщика / приёмки слесарного цеха — не менеджер ОП.
    В продукте нет STO_OUT → Прочие (имя «Евгений» совпадает с менеджером ОП Евдокимовым).
    """
    if not _is_outbound(text):
        return False
    low = (text or "").lower()
    if "менеджер отдела продаж" in low[:800]:
        return False
    if any(p in low for p in _STO_MASTER_PRIYOM_MARKERS):
        return True
    return False


def _is_sto_master_priyom_self_intro_early_other(transcript: str) -> bool:
    """
    Раннее правило (legacy, до op_strong): в голове транскрипта явное самопредставление
    мастером-приёмщиком при контексте Викинги/Чери/дилер — Прочие, без требования _is_outbound.
    Иначе STT «что Евгений» + «комплектация» датчиков даёт ложный ОП вх.
    """
    t = (transcript or "").lower()
    head = t[:800]
    if not any(p in head for p in _STO_MASTER_PRIYOM_MARKERS):
        return False
    if not any(b in head for b in ("викинги", "виикинги", "чери", "черри", "дилерск", "chery")):
        return False
    if not any(g in head[:400] for g in ("алло", "здравствуйте", "добрый день", "добрый вечер")):
        return False
    if "менеджер отдела продаж" in head:
        return False
    if re.search(
        r"(?:переключу|переведу|переключаю|перевожу|соединю|соединяю)[^.;]{0,120}"
        r"(?:мастер[а-]?[-\s]?приём|мастер[а-]?[-\s]?прием|приёмщик|приемщик)",
        head,
        re.IGNORECASE,
    ):
        return False
    return True


def _service_context_blocks_complictaciya_op(t: str) -> bool:
    """
    «Комплектаци…» в речи про сервис (датчики, ТО, приёмщик) — не маркер комплектации авто для ОП.
    """
    low = (t or "").lower()
    if any(p in low for p in _STO_MASTER_PRIYOM_MARKERS):
        return True
    if "датчик" in low and (
        "давлен" in low or "колес" in low or "шин" in low or "программ" in low or "комплект" in low
    ):
        return True
    if "шиномонтаж" in low or "переобувк" in low:
        return True
    if "запись на то" in low or "записаться на то" in low or "на то запис" in low:
        return True
    if "запись на сервис" in low or "записаться на сервис" in low:
        return True
    if "гарантийн" in low or "по гарантии" in low:
        return True
    if "диагностик" in low and "автомобил" in low:
        return True
    if "замена масла" in low or "замену масла" in low or "техобслуживание" in low:
        return True
    if "техническое обслуживание" in low:
        return True
    if "приёмка слесарн" in low or "приемка слесарн" in low or "слесарный цех" in low:
        return True
    if "заказ наряд" in low or "заказ-наряд" in low:
        return True
    if "зимн" in low and "колес" in low:
        return True
    if _is_body_shop_repair_accessories_consult_other(t):
        return True
    return False


def _is_chery_tenet_core_vehicle_topic(head: str) -> bool:
    """Тема звонка — новые/основные модели Чери·Тенет·Omoda и линейка Tiggo (ядро ОП новых)."""
    h = (head or "").lower()
    return any(
        x in h
        for x in (
            "чери ",
            "чери,",
            " chery",
            "chery ",
            "тигго",
            "tiggo",
            "тенет",
            "тэнет",
            "tenet",
            "омода",
            "omoda",
            "аризо",
            "arrizo",
            "дашинг",
            "dashing",
        )
    )


def _is_used_cars_line_manager_opening(text: str) -> bool:
    """
    Входящий на линию «авто с пробегом»: в начале говорит Антон или Николай (не менеджеры ОП Чери/Тенet).
    Прямой звонок без «по БУ автомобилям» / перевода админа (13213: Volvo C-30).
    """
    head = (text or "").lower()[:1200]
    if not head.strip():
        return False
    if not any(re.search(rf"\b{re.escape(name)}\b", head[:500]) for name in _USED_CARS_MANAGER_NAMES):
        return False
    dealer_open = any(p in head[:600] for p in ("викинг", "дилер", "автосалон", "заставн", "тольятт"))
    mgr_greeting = bool(
        re.search(
            rf"\b{_USED_CARS_MGR_NAME_RE}\s*,\s*(?:добрый\s+(?:день|вечер|утро)|здравствуйте)",
            head[:600],
        )
        or re.search(
            rf"\bвикинг\w*\s*,\s*{_USED_CARS_MGR_NAME_RE}\s*,",
            head[:600],
        )
    )
    if not (dealer_open or mgr_greeting):
        return False
    if _is_chery_tenet_core_vehicle_topic(head):
        return False
    if "менеджер отдела продаж" in head or "менеджера отдела продаж" in head:
        return False
    return True


def _is_explicit_used_cars_department_topic(low: str) -> bool:
    """
    Явная линия «авто с пробегом» / STT «БУ» (не «б/у») — Прочие, до gatekeeper ОП и «комплектац».
    """
    if not (low or "").strip():
        return False
    if any(
        p in low
        for p in (
            "авто с пробегом",
            "автомобилей с пробегом",
            "автомобил с пробегом",
            "отдела авто с пробегом",
            "отдел авто с пробегом",
            "отдел автомобилей с пробегом",
            "менеджер отдела авто с пробегом",
        )
    ):
        return True
    if re.search(r"по\s+бу\s+автомобил", low):
        return True
    if re.search(r"\bбу\s+автомобил", low):
        return True
    # 27958: «по БУ-машинам ... по поводу штрафа» — линия авто с пробегом.
    if re.search(r"\bбу[\s-]*машин", low):
        return True
    # STT 16518: «менеджером по Б-автомобилям», «б-автомобил» (дефис/пробел между «б» и «автомобил»).
    if re.search(r"б[\s-]*автомобил", low):
        return True
    if re.search(r"менеджер\w*\s+по\s+б[\s-]*", low):
        return True
    return False


def _is_inbound_admin_transfer_to_used_cars_manager(text: str) -> bool:
    """
    Входящий через администратора → перевод на Антона/Николая (линия авто с пробегом), не ОП Чери·Тенет.
    16518: «с Николаем переговорить», «менеджером по Б-автомобилям», Hyundai ix35.
    """
    low = (text or "").lower()
    head = low[:3500]
    if not ("администратор" in head[:1200] or "администратор салона" in head[:700]):
        return False
    if _is_chery_tenet_core_vehicle_topic(head[:4000]):
        return False
    used_mgr_topic = (
        _is_explicit_used_cars_department_topic(low)
        or bool(
            re.search(
                rf"(?:с|к)\s+(?:{_USED_CARS_MGR_NAME_RE}|никола\w*|антон\w*)",
                head[:1800],
            )
        )
    )
    if not used_mgr_topic:
        return False
    if "менеджер отдела продаж" in head[:2200] or "менеджера отдела продаж" in head[:2200]:
        return False
    transfer_to_used_mgr = bool(
        re.search(
            rf"(?:перевед\w*|переключ\w*|соедин\w*)[^.!?]{{0,140}}"
            rf"(?:{_USED_CARS_MGR_NAME_RE}|никола\w*|антон\w*)",
            head,
        )
        or re.search(
            rf"\b(?:{_USED_CARS_MGR_NAME_RE}|никола\w*|антон\w*)\b[^.!?]{{0,80}}"
            rf"(?:добрый|здравствуйте|слушаю)",
            low[:5000],
        )
    )
    return transfer_to_used_mgr or _is_explicit_used_cars_department_topic(low)


def _is_used_cars_department_other(text: str) -> bool:
    """
    Линия «авто с пробегом» / продажа не-ядра ОП новых в аналитике — Прочие.
    Без перечисления марок: дилер + разговор о конкретном авто + нет темы ядра Чери·Тенет в тексте
    + признаки цены/пробега/осмотра/б/у-диалога (или явная Лада/Гранта).
    """
    low = (text or "").lower()
    if _is_sto_to_booking_live_dialog_not_secretary_misc(low):
        return False
    if _is_used_cars_line_manager_opening(text):
        return True
    if _is_explicit_used_cars_department_topic(low):
        return True
    used_line = _is_explicit_used_cars_department_topic(low) or any(
        p in low
        for p in (
            "с пробегом",
            "авто с пробегом",
            "автомобил с пробег",
            "подержанный автомобиль",
            "подержанное авто",
            "подержанн",
            "б/у ",
            " б/у",
            " б у ",
            "авито",
            " avito",
            "aвиit",
        )
    )
    # Не Чери/Тенет (частые марки б/у у дилера)
    other_brand = any(
        b in low
        for b in (
            "гранта",
            "грант ",
            "грант,",
            "грант.",
            " лада",
            "лада ",
            "лада,",
            "лада.",
            "ладу ",
            "ваз ",
            "нива ",
            "веста ",
            "lada",
        )
    )
    dealer_or_salon = any(w in low for w in ("викинги", "дилер", "автосалон", "официальный дилер"))
    sales_hint = any(
        w in low
        for w in (
            "руб",
            "тысяч",
            "цена",
            "скидк",
            "наличии",
            "в наличии",
            "смотреть",
            "подъехать",
            "пробег",
            "километр",
        )
    )
    commission_topic = any(
        p in low
        for p in (
            "комиссия",
            "на комиссии",
            "комиссионк",
            "поставить на комиссию",
            "выставлен на комиссию",
            "автомобиль выставлен на комиссию",
        )
    )
    service_history_topic = any(
        p in low
        for p in (
            "обслуживалась у вас",
            "обслуживался у вас",
            "обслуживание у вас",
            "история обслуживания",
            "обслуживалась у дилера",
            "обслуживался у дилера",
        )
    )
    by_listing_topic = any(
        p in low
        for p in (
            "по объявлению",
            "по вашему объявлению",
            "по машине из объявления",
        )
    )
    new_auto_antimarkers = any(
        p in low
        for p in (
            "новый автомобиль",
            "новая машина",
            "новые автомобили",
            "тест-драйв",
            "кредит на новый",
            "нового авто",
            "новую машину",
        )
    )
    head = low[:2200]
    has_op_manager_intro = (
        "менеджер отдела продаж" in head
        or "менеджера отдела продаж" in head
        or re.search(r"\bменеджер\s+оп\b", head) is not None
        or any(len(name) >= 3 and name in head for name in _OP_MANAGER_NAMES)
    )
    op_handoff_to_sales = bool(
        re.search(
            r"(перевед\w*|переключ\w*)[^.!?\n]{0,80}(?:менеджер\w*[^.!?\n]{0,40}отдел\w*\s+продаж|отдел\w*\s+продаж)",
            head,
        )
    ) or _is_op_inbound_with_reception_gatekeeper(text)
    dealer_ctx = any(p in low for p in ("официальный дилер", "викинг", "чери", "тенет", "тэнет"))
    purchase_markers = (
        "покупк",
        "интересует автомобиль",
        "интересует машина",
        "автомобиль интересует",
        "комплектац",
        "в наличии",
        "доплат",
        "скидк",
        "кредит",
        "лизинг",
        "за налич",
        "наличк",
        "рассрочк",
        "срок покупки",
        "когда покуп",
        "приобрет",
        "полный привод",
        "полныйривод",
    )
    purchase_ctx_score = sum(1 for p in purchase_markers if p in low)
    has_site_or_callback_marker = _is_dialer_or_site_outbound_opening(text) or _is_site_lead_callback_phrase(
        text
    )
    strong_op_context = dealer_ctx and (
        (
            purchase_ctx_score >= 2
            and (has_op_manager_intro or op_handoff_to_sales or has_site_or_callback_marker)
        )
        or (
            purchase_ctx_score >= 1
            and has_op_manager_intro
            and _is_outbound(text)
            and _is_op_outbound_lead_request_marker(low)
        )
    )
    no_op_manager_marker = not has_op_manager_intro
    if strong_op_context:
        return False
    # Исходящий ОП: лид с Авито на новое Чери/Тенет — не линия «авто с пробегом» (11257).
    if (
        used_line
        and any(x in low for x in ("авито", " avito", "aвиit"))
        and _is_outbound(text)
        and _has_op_outbound_surface_markers(text)
        and _is_op_outbound_lead_request_marker(low)
        and dealer_ctx
        and has_op_manager_intro
    ):
        return False
    if used_line:
        return True
    # 10396: звонок по автомобилю с Авито/пробегом (Qashqai 18 года),
    # где встречается только «тест-драйв» без маркеров ОП новых Чери/Тенет.
    if (
        no_op_manager_marker
        and dealer_or_salon
        and any(p in low for p in ("авито", " avito", "aвиit"))
        and any(p in low for p in ("звонил по поводу", "по поводу", "посмотреть автомобиль", "машина продана", "продадим"))
        and any(p in low for p in ("кашкай", "qashqai", "кошка", "18 года", "19 года", "20 года"))
        and purchase_ctx_score <= 1
    ):
        return True
    calling_about_vehicle = any(
        p in low[:2200]
        for p in (
            "звоню по автомобилю",
            "звоню по машине",
            "звоню по авто",
            "интересует автомобиль",
            "по поводу автомобиля",
            "насчет автомобиля",
            "насчёт автомобиля",
            "хочу посмотреть автомобиль",
            "интересует",
            "машина стоит",
            "машина прода",
            "продаётся",
            "продается",
            "про автомобиль ",
            "по машине рассказать",
            "по машине расскаж",
            "расскажите по машине",
            "есть такая машина",
            "смотреть надо автомобиль",
            "осмотр автомобил",
        )
    )
    used_dialog_hints = any(
        w in low
        for w in (
            "крашен",
            "предоплат",
            "осмотр",
            "стоимость указан",
            "торг возможен",
            "бронь приезжайте",
            "мотор у неё",
            "мотор у нее",
            "коробк",
            "атмосферн",
            "год выпуск",
        )
    )
    if (
        no_op_manager_marker
        and commission_topic
        and (calling_about_vehicle or service_history_topic or by_listing_topic or dealer_or_salon)
    ):
        return True
    if (
        no_op_manager_marker
        and (service_history_topic or by_listing_topic)
        and calling_about_vehicle
        and not new_auto_antimarkers
    ):
        return True
    head_for_core = low[:8000]
    if (
        dealer_or_salon
        and calling_about_vehicle
        and not _is_chery_tenet_core_vehicle_topic(head_for_core)
        and (used_dialog_hints or sales_hint or service_history_topic)
        and not new_auto_antimarkers
        and not (
            has_op_manager_intro
            and _is_op_inbound_admin_handoff_to_op_manager(text)
        )
    ):
        return True
    if other_brand and dealer_or_salon and sales_hint:
        return True
    return False


def _dispatcher_off_hours_or_unavailable_schedule(low: str) -> bool:
    """
    Диспетчер недоступен по графику: «работает до …», «только в будни»,
    запись с понедельника по пятницу (13576, 18515, 18530).
    """
    return bool(
        re.search(r"диспетчер[^.!?]{0,80}работает\s+до", low, re.I)
        or re.search(
            r"диспетчер[^.!?]{0,80}до[^.!?]{0,50}(?:часов?\s+)?работает",
            low,
            re.I,
        )
        or re.search(
            r"диспетчер\w*[^.!?]{0,80}(?:только\s+в\s+будн|работа\w*\s+"
            r"(?:только\s+)?в\s+будн)",
            low,
            re.I,
        )
        or re.search(
            r"диспетчер\w*[^.!?]{0,60}(?:сегодня|сейчас)?[^.!?]{0,25}не\s+работа\w*",
            low,
            re.I,
        )
        or re.search(
            r"(?:с\s+понедельника\s+по\s+пятниц\w*|с\s+понедельник\w*\s+"
            r"по\s+пятниц\w*)[^.!?]{0,80}(?:можно\s+)?запис",
            low,
            re.I,
        )
    )


def _sto_admin_service_closed_application_callback(low: str) -> bool:
    """
    17418: сервис не работает (сегодня/завтра), запись только в пн — заявка, перезвонят и запишут.
    Контекст сервисной линии у админа без фактической приёмки.
    """
    low = (low or "").lower()
    if not _sto_admin_callback_or_number_offer(low):
        return False
    return bool(
        re.search(r"\b(?:сегодня|завтра|сегодня-завтра).{0,50}не\s+работа", low, re.I)
        or re.search(r"\bзапис\w*.{0,60}только\s+в\s+понедельник", low, re.I)
        or (
            re.search(r"\bзаявоч", low, re.I)
            and re.search(r"\bпонедельник\b", low, re.I)
            and re.search(r"\bперезвон", low, re.I)
        )
    )


def _sto_admin_no_dispatcher_callback_keeps_sto_in_classification(transcript: str) -> bool:
    """
    Админ без диспетчера на линии: широкий STO_IN только при контексте сервисной линии
    (дилер, график диспетчера, попытка перевода, сервис закрыт/заявка). Иначе Прочие (11263).
    """
    try:
        from text_normalization import normalize_text

        low = (normalize_text(transcript) or transcript or "").lower()
    except Exception:
        low = (transcript or "").lower()
    if any(
        b in low
        for b in ("викинг", "чери", "chery", "тенет", "тэнет", "tenet", "дилер", "официальн")
    ):
        return True
    if _dispatcher_off_hours_or_unavailable_schedule(low):
        return True
    if _sto_admin_service_closed_application_callback(low):
        return True
    if re.search(
        r"(?:переключ|соедин|перевед)\w*[^.!?]{0,80}(?:диспетчер|сервис)",
        low,
        re.I,
    ):
        return True
    if re.search(
        r"диспетчер[^.!?]{0,60}не\s+мож(?:ет|ь)[^.!?]{0,50}разговарив",
        low,
        re.I,
    ):
        return True
    return False


def _sto_regulatory_to_booking_service_intent_present(low: str) -> bool:
    """Запрос записи на регламентное ТО, в т.ч. «записаться на третье ТО» (13675)."""
    if any(
        p in low
        for p in (
            "записаться на то",
            "запись на то",
            "на то запис",
            "техническое обслуживание",
            "на сервис",
            "техобслуж",
        )
    ):
        return True
    if re.search(
        r"\bзапис\w*[^.!?]{0,55}?(?:на\s+)?"
        r"(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+)\s+то\b",
        low,
        re.I,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+)\s+то\b",
            low,
            re.I,
        )
        and re.search(r"\bзапис\w*", low, re.I)
    )


# Имена администраторов/стойки салона (Диана, Оксана, Полина…).
_SALON_ADMIN_FIRST_NAMES = frozenset(
    {
        "диана",
        "оксана",
        "анастасия",
        "полина",
        "татьяна",
    }
)


def _is_salon_front_desk_opening(low: str) -> bool:
    """
    Открытие линии администратором/стойкой: «администратор …» или
    «официальный дилер …, меня зовут Полина» (18454/18455) без слова «администратор».
    """
    head = (low or "")[:900]
    if "администратор салона" in head:
        return True
    if "администратор" in (low or "")[:700] and any(
        x in (low or "")[:1200]
        for x in ("викинг", "чери", "тенет", "тэнет", "дилер", "диджер")
    ):
        return True
    if not re.search(r"\bофициальн\w*\s+дилер\b", head[:500], re.I):
        return False
    m = re.search(r"\bменя\s+зовут\s+([а-яё]{3,15})\b", head[:500], re.I)
    if m and m.group(1).lower() in _SALON_ADMIN_FIRST_NAMES:
        return True
    return False


def _dispatcher_busy_or_unavailable_for_admin_callback(low: str) -> bool:
    """Диспетчер недоступен: нет на линии, занят, STT «диспетчер сейчас…» (13675)."""
    return bool(
        re.search(r"\bнет\s+диспетчер\w*", low)
        or re.search(r"диспетчер\w*\s+нет\b", low)
        or re.search(r"диспетчер\w*\s+нету\b", low)
        or re.search(r"\bнет\s+ассистент\w*\s+сервис", low)
        or _dispatcher_off_hours_or_unavailable_schedule(low)
        or re.search(
            r"диспетчер[^.!?]{0,60}не\s+мож(?:ет|ь)[^.!?]{0,50}разговарив",
            low,
            re.I,
        )
        or re.search(r"диспетчер[^.!?]{0,40}(?:сейчас|сйчас|занят)\w*", low, re.I)
        or re.search(r"диспетчер\s+занят", low, re.I)
    )


def _sto_admin_callback_or_number_offer(low: str) -> bool:
    """Предложение оставить контактный номер или перезвонить позже (админ без приёмки СТО)."""
    if any(
        p in low
        for p in (
            "оставьте номер",
            "оставьте контактный номер",
            "оставите номер",
            "оставите контактный номер",
            "контактный номер запишу",
            "контактный номер телефона",
            "номер телефона контактный",
            "телефона контактный",
            "контактный записать",
            "номер запишу",
            "запишу номер",
            "записываю номер",
            "записать ваш номер",
            "записать номер",
            "перезвоните",
            "перезвонить",
            "перезвоню",
            "перезвоним",
            "перезвонм",
            "перезвонят",
            "перезвонил",
            "перезвонили",
            "ожидайте звонок",
            "ждите звонок",
            "сама позвоню",
            "сам позвоню",
            "завтра наберу",
            "в понедельник наберу",
            "сами позвон",
            "сами перезвон",
            "передам",
        )
    ):
        return True
    if "передам" in low and any(p in low for p in ("перезвонят", "перезвоним", "перезвоню")):
        return True
    if re.search(r"\bперезвон\w{0,4}\b", low):
        return True
    if re.search(
        r"\b(?:завтра|в\s+понедельник)\b[^.!?]{0,45}\b(?:позвон|перезвон|набер|набр)\w*",
        low,
        re.I,
    ):
        return True
    m = re.search(
        r"(?:контактн\w*.{0,28}номер|номер.{0,28}контактн|телефон\w*.{0,20}контактн)",
        low,
        re.I,
    )
    if m:
        # CRM-верификация («телефон контактный на … заканчивается»), не сбор номера у админа (15493).
        if re.search(
            r"телефон\w*\s+контактн\w*\s+на\s+\d|контактн\w*\s+телефон\w*\s+на\s+\d",
            low,
            re.I,
        ):
            return False
        return True
    return False


def _is_sto_no_service_assistant_connection_low(low: str) -> bool:
    """
    Админ салона: клиент просит ТО, связи с диспетчером/ассистентом сервиса нет (нет на линии — только перезвон).
    НЕ_ТО / не полноценная СТО_ТО_вх (11263: нет диспетчера; 11662: работает до …, перезвон завтра).
    """
    low = (low or "").lower()
    if not low.strip():
        return False
    low = re.sub(r"\bдиспетчер\s*сервис", "диспетчер сервис", low)
    low = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", low)
    low = re.sub(
        r"\b(?:енсервис|итмсервис|снсервис|аенсервис|ченсервис|чреенсервис)\b",
        "ассистент сервиса",
        low,
    )
    front_desk = _is_salon_front_desk_opening(low)
    # 18530: короткая стойка без распознанного имени администратора —
    # «официальный дилер … диспетчера только в будни, перезвоните в понедельник».
    generic_dealer_weekday_callback = bool(
        re.search(r"\bофициальн\w*\s+дилер\b", low[:700], re.I)
        and _dispatcher_off_hours_or_unavailable_schedule(low)
        and _sto_admin_callback_or_number_offer(low)
    )
    if not front_desk and not generic_dealer_weekday_callback:
        return False
    service_intent = _sto_regulatory_to_booking_service_intent_present(low)
    if not service_intent:
        return False
    if ("диспетчер сервиса" in low or "ассистент сервиса" in low) and any(
        g in low for g in ("здравствуйте", "слушаю", "добрый день", "добрый вечер")
    ):
        if any(
            n in low
            for n in (
                "юлия",
                "юлию",
                "андреева",
                "плаксина",
                "гринкина",
                "гранкина",
                "александра",
                "дарья",
                "лилия",
            )
        ):
            return False
        if re.search(
            r"(?:диспетчер|ассистент)\s+сервиса[^.!?]{0,100}(?:здравствуйте|слушаю)",
            low,
        ):
            return False
    dispatcher_unavailable = (
        _dispatcher_busy_or_unavailable_for_admin_callback(low)
        or (
            (front_desk or generic_dealer_weekday_callback)
            and _sto_admin_callback_or_number_offer(low)
            and not re.search(
                r"(?:диспетчер|ассистент)\s+сервиса[^.!?]{0,120}(?:здравствуйте|слушаю)",
                low,
            )
        )
    )
    if not dispatcher_unavailable:
        return False
    # Запись на слот состоялась в том же звонке — не «только перезвон» (15493).
    # Не путать с «записала контактный номер» / «заявочку оставлю» у админа (17418).
    if (
        re.search(r"\bзаписал[аи]\w*\s+(?:вас\s+)?на\s+(?:то\b|техническ|\d)", low)
        or re.search(r"\bзаписал[аи]\w*\s+вас\s+на\b", low)
        or "подтверждаю запись" in low
        or "ждем вас" in low
        or "ждём вас" in low
        or "будем вас ожидать" in low
        or ("подъезжайте" in low and re.search(r"\bзаписал\w*\s+(?:вас\s+)?на\b", low))
    ):
        return False
    # Голое «записали» при сборе номера/заявки админом — не состоявшаяся запись на ТО.
    if re.search(r"\bзаписал[аи]\b", low) and not (
        _sto_admin_callback_or_number_offer(low)
        or re.search(r"\b(?:заявоч|заявк|контактн|номер)\b", low, re.I)
    ):
        return False
    if re.search(
        r"(?:диспетчер|ассистент)\s+сервиса[^.!?]{0,80}(?:слушаю|здравствуйте|добрый)",
        low,
        re.I,
    ):
        return False
    if "слушаю вас" in low and (
        "диспетчер" in low or "ассистент" in low or "викинг" in low or "чери" in low
    ):
        return False
    if _sto_admin_callback_or_number_offer(low):
        return True
    return True


def _is_sto_no_service_assistant_connection_misc(text: str) -> bool:
    """Обёртка для полного транскрипта (нормализация STT)."""
    if not (text or "").strip():
        return False
    try:
        from text_normalization import normalize_text

        low = (normalize_text(text) or text).lower()
    except Exception:
        low = (text or "").lower()
    return _is_sto_no_service_assistant_connection_low(low)


def _is_sto_to_booking_live_dialog_not_secretary_misc(low: str) -> bool:
    """
    Полноценная запись на регламентное ТО (явная тема + календарь/слот или линия диспетчера СТО).
    Отсекает ложное срабатывание «перезвон» из «перезвоню» в конце уже состоявшегося разговора о записи.
    """
    low = (low or "").lower()
    if _is_sto_no_service_assistant_connection_low(low):
        return False
    if not _has_sto_substantive_service_intent(low):
        return False
    to_explicit = any(
        p in low
        for p in (
            "записаться на то",
            "запись на то",
            "на то записаться",
            "на то запис",
            "первое то",
            "второе то",
            "третье то",
            "нулевое то",
            "техническое обслуживание",
            "пройти то",
            "прошла нулевое",
            "первое тэо",
        )
    )
    if not to_explicit and re.search(r"\bна\s+\d+\s*-\s*то\b", low):
        to_explicit = True
    if not to_explicit and re.search(r"\bто\s+необходимо\s+записать\b", low):
        to_explicit = True
    if not to_explicit:
        return False
    # Линия занята + только номер на перезвон — нет согласования слота (golden: sto_trainee_to_dispatcher_busy...).
    if "линия занята" in low and any(
        p in low for p in ("контактный номер", "номер запишу", "перезвонят вам", "передам перезвонят")
    ):
        return False
    # 11662: админ — «диспетчер работает до …», перезвон завтра; не диалог приёмки о записи на ТО.
    if re.search(r"диспетчер[^.!?]{0,80}работает\s+до", low):
        return False
    if "мая" in low or "июня" in low or "июля" in low or "апрел" in low:
        return True
    if re.search(
        r"\b(?:понедельник|вторник|среду|четверг|пятниц|суббот|воскресенье)\b",
        low,
    ) and ("мая" in low or "числ" in low or "время" in low or "запис" in low):
        return True
    if "ближайш" in low and ("врем" in low or "запис" in low):
        return True
    if ("диспетчер" in low or "ассистент сервиса" in low) and (
        "запис" in low or "мая" in low or "слот" in low or "окош" in low
    ):
        return True
    return False


def _is_sto_reglam_to_clarification_not_secretary_misc(low: str) -> bool:
    """
    Уточнение заказа регламентного ТО (какое по счёту, стоимость) после предварительного контакта —
    не сценарий «хостес только номер» (_is_service_secretary_callback_misc).
    """
    low = (low or "").lower()
    if not _has_sto_substantive_service_intent(low):
        return False
    if not any(
        p in low
        for p in (
            "техническое обслуживание",
            "первое техническое",
            "второе техническое",
            "третье техническое",
            "ранее общались",
            "уточню информацию",
            "уточнить информацию",
            "по поводу то",
        )
    ):
        return False
    return any(p in low for p in ("викинг", "чери", "тенет", "тэнет", "chery", "официальный дилер"))


def _service_line_busy_or_unavailable(low: str) -> bool:
    """Линия сервиса/диспетчера занята (в т.ч. STT «линия на сервисе занята», 16700)."""
    if not (low or "").strip():
        return False
    if any(
        p in low
        for p in (
            "сервис занят",
            "сейчас сервис занят",
            "линия сервиса занята",
            "линия на сервисе занята",
            "линия занята",
            "диспетчер линия занята",
            "линия у диспетчера занята",
            "линия диспетчера занята",
            "линия у диспетчера сервиса занята",
            "диспетчер сервиса занят",
            "диспетчера сервиса занят",
            "диспетчер занят",
            "сейчас занят",
            "сейчас занята",
        )
    ):
        return True
    return bool(
        re.search(r"линия[^.!?\n]{0,40}диспетчер[^.!?\n]{0,30}занят", low)
        or re.search(r"линия[^.!?\n]{0,30}сервис[^.!?\n]{0,30}занят", low)
    )


def _is_op_outbound_site_callback_service_transfer_failed_other(text: str) -> bool:
    """
    Исходящий обратный звонок с сайта (ОП): в процессе выясняется интерес к сервису,
    перевод на сервис не состоялся — Прочие, не OP_OUT и не STO_IN (16700).
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    site_outbound = (
        _is_crm_ats_outbound_lead_prefix(text)
        or _is_dialer_or_site_outbound_opening(text)
        or "звонок с сайта" in low[:2000]
    )
    if not site_outbound or not _is_outbound(text):
        return False
    head = low[:3500]
    if "менеджер отдела продаж" not in head and not any(
        len(n) >= 3 and n in head for n in _OP_MANAGER_NAMES
    ):
        return False
    op_rejected_or_service_pivot = any(
        p in low
        for p in (
            "не интересовал",
            "не интересовало",
            "меня не интересовал",
            "уже приобрёл",
            "уже приобрел",
            "уже купил",
            "уже купили",
            "записывался",
            "записывалась",
            "на то ",
            "на т у",
            "техобслуживание",
            "техническое обслуживание",
        )
    )
    if not op_rejected_or_service_pivot:
        return False
    service_handoff_attempt = any(
        p in low
        for p in (
            "соединю вас с сервис",
            "соединю с сервис",
            "переведу на сервис",
            "переведу вас на сервис",
            "переключу на сервис",
            "перевожу на сервис",
            "переключаю на сервис",
        )
    )
    if not service_handoff_attempt:
        return False
    if not _service_line_busy_or_unavailable(low):
        return False
    callback_only = any(
        p in low
        for p in (
            "перезвонят",
            "передам",
            "передадим",
            "продиктуйте",
            "номер телефона",
            "запишу номер",
            "записала номер",
        )
    )
    if not callback_only:
        return False
    if ("диспетчер сервиса" in low or "ассистент сервиса" in low) and any(
        g in low for g in ("слушаю вас", "здравствуйте", "добрый день")
    ) and any(
        n in low
        for n in ("юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина", "александра", "дарья")
    ):
        return False
    return True


def _is_service_secretary_callback_misc(text: str) -> bool:
    """
    Клиент просит сервис, линия занята / не дозвонились до диспетчера — снимают номер, обещают перезвон.
    Администратор/хостес (в т.ч. имя из ОП) — не ОП_IN: нет продажи и нет приёмки СТО.
    """
    low = (text or "").lower()
    if _is_ats_outbound_dial_robot_prefix(text):
        return False
    if _is_op_outbound_site_callback_service_transfer_failed_other(text):
        return False
    if _is_sto_dealer_employee_outbound_service_call(text):
        return False
    if _is_sto_to_booking_live_dialog_not_secretary_misc(low):
        return False
    if _is_sto_reglam_to_clarification_not_secretary_misc(low):
        return False
    # Не относить к «callback-only misc», если это явный ОП-диалог:
    # линия/перевод на ОП + дилерский контекст + предмет покупки.
    head = low[:3000]
    op_handoff_or_intro = (
        "менеджер отдела продаж" in head
        or "отдел продаж" in head
        or bool(
            re.search(
                r"(перевед\w*|переключ\w*)[^.!?\n]{0,80}(?:менеджер\w*[^.!?\n]{0,40}отдел\w*\s+продаж|отдел\w*\s+продаж)",
                head,
            )
        )
        or any(len(name) >= 3 and name in head for name in _OP_MANAGER_NAMES)
    )
    dealer_ctx = any(p in low for p in ("официальный дилер", "викинг", "чери", "тенет", "тэнет"))
    op_purchase_markers = (
        "покупк",
        "интересует автомобиль",
        "интересует машина",
        "комплектац",
        "в наличии",
        "доплат",
        "скидк",
        "кредит",
        "за налич",
        "рассрочк",
        "срок покупки",
        "когда покуп",
    )
    op_purchase_score = sum(1 for p in op_purchase_markers if p in low)
    if op_handoff_or_intro and dealer_ctx and op_purchase_score >= 2:
        return False
    service_intent = any(
        p in low
        for p in (
            "на сервис",
            "к сервису",
            "с сервисом",
            "со сервисом",
            "в сервис",
            "переключите на сервис",
            "переключить на сервис",
            "соединить с сервис",
            "поговорить с сервис",
            "сервис интересует",
            "интересует сервис",
        )
    ) or ("сервис" in low and any(p in low for p in ("проблем", "поломк", "не работает", "ремонт")))
    service_intent = service_intent or any(
        p in low
        for p in (
            "отдел по работе с клиентами",
            "отделом по работе с клиентами",
            "отдел по работе с клиентом",
            "отделом по работе с клиентом",
            "клиентская служба",
            "клиентскую службу",
            "подменн",  # подменная машина, подменный автомобиль
            "записаться на то",
            "запись на то",
            "на то записаться",
            "записаться на тэо",
            "запись на тэо",
            "техническое обслуживание",
            "записаться на техническое обслуживание",
            "на то записываться",
            "записываться на то",
            "на т о запис",
        )
    )
    if not service_intent:
        return False
    if _is_sto_no_service_assistant_connection_low(low):
        # 11263: админ без дилерского контекста — Прочие; 13675/13576/12433 — широкий STO_IN.
        return _sto_admin_no_dispatcher_callback_keeps_sto_in_classification(text)
    callback_or_busy = any(
        p in low
        for p in (
            "сервис занят",
            "сейчас сервис занят",
            "линия сервиса занята",
            "линия на сервисе занята",
            "линия занята",
            "диспетчер линия занята",
            "линия у диспетчера занята",
            "линия диспетчера занята",
            "нет диспетчер",
            "перезвон",
            "перезвонят",
            "перезвоним",
            "передам информацию",
            "передам номер",
            "оставьте номер",
            "оставите номер",
            "запишу номер",
            "записала номер",
            "записываю номер",
            "как освобод",
            "как только освобод",
        )
    )
    if not callback_or_busy:
        return False
    # Линия диспетчера занята / только номер — Прочие, даже если в тексте назван «диспетчер сервиса Юлия»
    if _service_line_busy_or_unavailable(low):
        return True
    busy_line = any(
        p in low
        for p in (
            "линия у диспетчера занята",
            "линия у диспетчера сервиса занята",
            "линия диспетчера занята",
            "линия сервиса занята",
            "диспетчер сервиса занят",
            "диспетчера сервиса занят",
            "линия занята",
            "сейчас занят",
            "сейчас занята",
        )
    )
    if busy_line:
        return True
    # Уже полноценный разговор с приёмкой (диспетчер на линии без «занято/только номер») — не этот сценарий
    if ("диспетчер сервиса" in low or "ассистент сервиса" in low) and any(
        n in low for n in ("юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина", "александра", "дарья")
    ):
        return False
    return True


def _is_priority_busy_dispatcher_leave_number_callback(text: str) -> bool:
    """
    Приоритетный кейс: до фактического контакта с приёмкой СТО линия диспетчера занята,
    клиенту предлагают оставить номер или перезвонить позже.
    Такие звонки относим в Прочие (не STO_IN).
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    service_intent = _sto_regulatory_to_booking_service_intent_present(low)
    if not service_intent:
        return False
    busy_dispatcher = (
        bool(re.search(r"линия[^.!?\n]{0,40}диспетчер[^.!?\n]{0,30}занят", low))
        or "линия диспетчера занята" in low
        or "линия у диспетчера занята" in low
        or "диспетчер занят" in low
        or _dispatcher_busy_or_unavailable_for_admin_callback(low)
    )
    if not busy_dispatcher:
        return False
    callback_only = any(
        p in low
        for p in (
            "оставьте контактный номер",
            "оставьте номер",
            "оставите контактный номер",
            "оставите номер",
            "контактный номер запишу",
            "перезвоните",
            "перезвоню",
            "перезвонят",
            "перезвоним",
            "перезвонили",
            "ожидайте звонок",
            "ждите звонок",
            "передам",
        )
    ) or ("передам" in low and "перезвонят" in low)
    if not callback_only:
        return False
    # 11263: админ без дилерского контекста — Прочие; 13675/12433 — широкий STO_IN.
    return _sto_admin_no_dispatcher_callback_keeps_sto_in_classification(text)


def _is_op_all_managers_busy_callback_only_misc(text: str) -> bool:
    """
    Лид/входящий на ОП (Викинги/Чери/…), перевод на менеджера не состоялся: все менеджеры заняты,
    менеджер не берёт трубку, только сняли контакт / обещали перезвон — Прочие, не ОП вх.
    (разговора с менеджером не было).
    """
    low = (text or "").lower()
    dealer_branded = any(
        b in low
        for b in (
            "викинги",
            "виикинги",
            "чери",
            "черри",
            "chery",
            "тенет",
            "tenet",
            "дилерск",
            "автосалон",
            "официальный дилер",
        )
    )
    dealer = dealer_branded or any(
        b in low
        for b in (
            "администратор салона",
            "администратор саллона",
            "тест-драйв",
            "тест драйв",
        )
    )
    if not dealer:
        return False
    wants_op_manager = bool(
        re.search(
            r"\b(?:соедините|соединить|переключите|переключи|переведите|переведи|переведу|перевожу|переключу)\b"
            r"(?:[\s\S]{0,160}?)\b(?:с|на|к)\s+[а-яё]{2,}",
            low,
        )
        or re.search(
            r"\b(?:соедините|переключите|переведите|переведу|перевожу|переключу)\b[^.!?]{0,120}"
            r"\b(?:андре\w+|иль[еёи]\w+|анастас\w+|евген\w+|захаров|калаев|щеголев|менеджер\w*)\b",
            low,
            re.I,
        )
        or any(
            p in low
            for p in (
                "переключу на менеджера",
                "переведу на менеджера",
                "переведу вас на менеджера",
                "перевожу вас на менеджера",
                "перевожу вас",
                "переключу вас на менеджера",
                "переключу на отдел продаж",
                "переведу на отдел продаж",
                "переведу вас на менеджера отдела продаж",
                "переведу на менеджера отдела продаж",
                "на менеджера по продажам",
                "менеджера отдела продаж",
                "отдела продаж",
            )
        )
    )
    if not dealer_branded and not wants_op_manager:
        return False
    managers_busy = any(
        p in low
        for p in (
            "все менеджеры заняты",
            "все менеджер занят",
            "менеджеры заняты",
            "все менеджеры сейчас заняты",
            "сейчас все менеджеры заняты",
            "все наши менеджеры заняты",
            "все в отделе продаж заняты",
            "в отделе продаж все заняты",
            "никто из менеджеров не может",
            "ни один менеджер не может",
            "звонок вернулся",
            "звонок вернулся назад",
            "вернулся звонок",
            "звонок обратно вернулся",
            "обратно вернулся",
            "менеджеры с клиентами",
            "сейчас менеджеры с клиентами",
            "перевести не удалось",
            "перевести не получилось",
            "не берёт трубочку",
            "не берет трубочку",
            "не берёт трубку",
            "не берет трубку",
            "не отвечает",
        )
    ) or bool(
        # 11625: STT «все, к сожалению, заняты» / «менеджер… заняты» без полной фразы.
        re.search(r"\bменеджер\w*[^.!?]{0,140}?\bзанят\w*\b", low, re.I)
        or (
            re.search(r"\b(?:все|вс[её]?)\b[^.!?]{0,65}?\bзанят\w*\b", low, re.I)
            and ("менеджер" in low or "отдел продаж" in low)
        )
        or re.search(r"\bотдел\w*\s+продаж\w*[^.!?]{0,70}?\bзанят\w*\b", low, re.I)
    )
    if not managers_busy:
        return False
    takes_number_or_callback = any(
        p in low
        for p in (
            "перезвонят вам",
            "перезвоним вам",
            "мы вам перезвоним",
            "вам перезвонят",
            "перезвонят",
            "проконсультируют",
            "контактный номер",
            "номер телефона ваш",
            "ваш телефон запишу",
            "запишу номер",
            "записываю номер",
            "записала номер",
            "передам менеджер",
            "передам номер",
            "передам ваш номер",
            "передам контакт",
            "оставьте номер",
            "оставите номер",
            "напишите номер",
            "попозже наберите",
            "позже наберите",
            "наберите попозже",
            "чуть попозже",
        )
    ) or (
        "передам" in low and re.search(r"\bперезвон\w*\b", low)
    ) or (
        re.search(r"\bзапиш\w*\b", low) and "передам" in low
    ) or (
        "переключу" in low
        and any(
            p in low
            for p in (
                "не берёт трубочку",
                "не берет трубочку",
                "не берёт трубку",
                "не берет трубку",
            )
        )
    )
    if not takes_number_or_callback:
        return False
    # Полноценный разговор с менеджером ОП после перевода — не «срыв соединения».
    if re.search(r"\bменеджер\s+отдела\s+продаж\b", low) and any(
        p in low for p in ("комплектац", "кредит", "покупк", "трейд", "первоначальн")
    ):
        return False
    # Уже идёт полноценный разговор с диспетчером СТО / приёмкой — не сценарий «ОП все заняты»
    if any(p in low for p in ("диспетчер сервиса", "ассистент сервиса", "стажер дарья")) and any(
        n in low for n in ("юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина", "александра", "дарья")
    ):
        return False
    return True


def _is_op_failed_transfer_callback_only_misc(text: str) -> bool:
    """
    Админ переводил на менеджера ОП, разговора с менеджером не было —
    только обещание перезвона (14786). Прочие, не ОП_вх.
    """
    low = (text or "").lower()
    if not any(
        b in low
        for b in (
            "администратор",
            "автосалон",
            "официальный дилер",
            "викинги",
            "чери",
            "chery",
            "тенет",
            "tenet",
            "покупк",
        )
    ):
        return False
    if not re.search(r"\b(?:переключ\w+|перевед\w+|перевож\w+)\b", low):
        return False
    callback_promise = bool(
        re.search(r"\bперезвон\w*\b", low)
        or (
            re.search(r"\bудобно\s+будет", low)
            and re.search(r"\bподожду\b", low)
        )
    )
    if not callback_promise:
        return False
    post_transfer = low
    for sep in ("переключаю", "переключу", "перевожу", "переведу"):
        if sep in low:
            post_transfer = low.split(sep, 1)[-1]
            break
    if re.search(
        r"\b(?:меня\s+зовут|менеджер\s+отдела\s+продаж|официальный\s+дилер)\b",
        post_transfer,
    ) and any(
        p in post_transfer
        for p in (
            "комплектац",
            "кредит",
            "покупк",
            "трейд",
            "тест-драйв",
            "скидк",
            "цена",
            "стоимость",
            "хотим купить",
            "присматрива",
            "полный привод",
            "вариатор",
        )
    ):
        return False
    return True


def _is_op_transfer_to_manager_dropped_before_sales_talk_misc(text: str) -> bool:
    """
    Перевод на менеджера ОП состоялся формально (менеджер поприветствовал),
    но диалог оборвался сразу на «алло/слышно» без предметного разговора.
    Классифицируем как Прочие (20840), а не OP_IN/OP_OUT.
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    if _is_service_secretary_callback_misc(text):
        return False
    transfer_match = re.search(
        r"\b(?:переключ\w+|перевед\w+|перевож\w+)\b[^.!?]{0,140}\b(?:менеджер\w*|отдел\w*\s+продаж)\b",
        low,
        re.I,
    )
    if not transfer_match:
        return False
    if "оставайтесь на линии" not in low and "оставайтесь на линии." not in low:
        return False
    manager_intro = re.search(
        r"\b(?:меня\s+зовут|менеджер\s+отдела\s+продаж)\b",
        low[transfer_match.end() :],
        re.I,
    )
    if not manager_intro:
        return False
    manager_intro_abs_end = transfer_match.end() + manager_intro.end()
    tail = low[manager_intro_abs_end:]
    if not tail.strip():
        return False
    # Полноценный sales-диалог после перевода не должен попадать в этот guard.
    if _has_op_sales_conversation_substance(tail):
        return False
    if any(
        m in tail
        for m in (
            "комплектац",
            "кредит",
            "трейд",
            "покупк",
            "стоимость",
            "цена",
            "тест-драйв",
            "ежемесячн",
            "платеж",
            "автомобил",
            "модель",
        )
    ):
        return False
    has_drop_probe = bool(
        re.search(
            r"\b(?:алло|ало|слышно(?:\s+меня)?|не\s+слышно|слышите|тишина)\b",
            tail,
            re.I,
        )
    )
    if not has_drop_probe:
        return False
    tail_words = re.findall(r"[а-яёa-z0-9]+", tail, flags=re.I)
    if len(tail_words) > 20:
        return False
    return True


def _is_op_reception_phone_callback_no_manager_misc(text: str) -> bool:
    """
    Входящий лид на ОП: стойка/админ записала номер, обещала перезвон менеджера —
    без разговора с менеджером ОП. Прочие (13355).
    Без фразы «все менеджеры заняты» (_is_op_all_managers_busy_callback_only_misc).
    """
    low = (text or "").lower()
    if _is_ats_outbound_dial_robot_prefix(text):
        return False
    if _is_crm_ats_outbound_lead_prefix(text):
        return False
    if not any(
        b in low
        for b in (
            "викинги",
            "виикинги",
            "чери",
            "черри",
            "chery",
            "тенет",
            "tenet",
            "дилерск",
            "автосалон",
            "официальный дилер",
        )
    ):
        return False
    if "менеджер отдела продаж" in low:
        return False
    manager_callback = bool(
        re.search(r"\bменеджер\w*[^.!?]{0,60}перезвон\w*\b", low, re.I)
        or re.search(r"\bменеджер\w*[^.!?]{0,60}проконсультир\w*\b", low, re.I)
        or any(
            p in low
            for p in (
                "менеджеры вас полностью проконсультируют",
                "менеджеры проконсультируют",
                "перезвонят вам",
                "вам перезвонят",
            )
        )
    )
    phone_capture = any(
        p in low
        for p in (
            "продиктуйте номер",
            "запишу ваш номер",
            "запишу номер",
            "контактный номер",
            "ваш номер телефона",
        )
    ) or bool(re.search(r"\b\d{3}\b[^.!?]{0,40}\b\d{3}\b[^.!?]{0,40}\b\d{4}\b", low))
    if not (manager_callback and phone_capture):
        return False
    if re.search(r"\bменеджер\s+отдела\s+продаж\b", low) and any(
        p in low for p in ("комплектац", "кредит", "покупк", "трейд", "первоначальн", "скидк")
    ):
        return False
    if _is_service_secretary_callback_misc(text):
        return False
    # Исходящий ОП с консультацией менеджера (11257) — не «только номер на стойке».
    if re.search(r"\bудобно\s+говорить\b", low) and re.search(
        r"\b(?:слушаю\s+вас|что\s+именно\s+интересно)\b", low
    ):
        return False
    return True


def _is_credit_department_callback_without_sales_talk_other(text: str) -> bool:
    """
    Кредитный отдел: запросили перевод в кредитный отдел, но сотрудник не принял звонок;
    взяли номер и пообещали перезвонить. Без обсуждения покупки/выбора авто — Прочие.
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    has_credit_context = bool(
        re.search(r"\bкредитн\w*\s+отдел\w*\b", low, re.I)
        or "кредитный отдел" in low
        or "кредитного отдела" in low
    )
    if not has_credit_context:
        return False
    if not re.search(r"\b(?:переключ\w+|перевед\w+|перевож\w+)\b", low, re.I):
        return False
    if not any(
        p in low
        for p in (
            "с клиентом сейчас",
            "она с клиентом",
            "он с клиентом",
            "занят",
            "занята",
            "заняты",
        )
    ):
        return False
    has_callback_capture = (
        bool(re.search(r"\bостав[ьтт]е\s+(?:контактн\w*\s+)?номер\w*\b", low, re.I))
        or "ожидайте звонок" in low
        or bool(re.search(r"\bперезвон\w*\b", low, re.I))
    )
    if not has_callback_capture:
        return False
    if _has_client_facing_op_sales_cycle(text):
        return False
    return True


def _is_third_party_b2b_cold_pitch_other(t: str) -> bool:
    """
    Сторонний B2B/рекламный обзвон на линию дилера (12757: Яндекс Такси для юрлиц) — Прочие.
    Не путать с разговором менеджера ОП о скидке на автомобиль.
    """
    low = (t or "").lower()
    yandex_pitch = any(x in low for x in ("яндекс такси", "яндекс go", "яндекс год", "яндекс")) and any(
        p in low
        for p in (
            "подключаем компан",
            "юрлиц",
            "личный кабинет",
            "стоимость услуг такси",
            "бесплатно подключ",
            "наш сервис",
        )
    )
    if not yandex_pitch:
        return False
    if _discount_in_vehicle_sale_context(low):
        return False
    if any(
        p in low
        for p in (
            "менеджер отдела продаж",
            "тигго",
            "tiggo",
            "тенет",
            "тэнет",
            "tenet",
            "тест-драйв",
            "комплектац",
        )
    ):
        return False
    return True


def _is_avito_bridge_client_hung_up_before_op_talk_misc(text: str) -> bool:
    """
    Перемычка Авито: оператор площадки диктует контекст/номер менеджеру ОП,
    клиент сбросил до разговора с дилером — Прочие, не ОП вх. (12558).
    Не путать с 11740: там после перевода состоялся диалог менеджер–клиент.
    """
    low = (text or "").lower()
    if not any(x in low for x in ("авито", " avito", "aвиit")):
        return False
    if not any(
        p in low
        for p in (
            "положил трубку",
            "положила трубку",
            "положил трубочку",
            "положила трубочку",
            "сбросил",
            "сбросила",
            "сбросил вызов",
            "сбросила вызов",
        )
    ):
        return False
    avito_bridge = any(
        p in low
        for p in (
            "звонок от авито",
            "звонок из авито",
            "на линии клиент",
            "на линии покупатель",
            "перезвонить клиенту",
            "перезвоните клиенту",
            "запишите номер",
            "запишите, номер",
        )
    )
    if not avito_bridge:
        return False
    # Полноценный разговор менеджера ОП с клиентом о покупке — не «срыв моста» (11740).
    if any(
        p in low
        for p in (
            "вы хотели купить",
            "вы хотели приобрести",
            "планируете покупку",
            "тест-драйв",
            "тест драйв",
            "какая комплектац",
            "какая цена",
            "сколько стоит",
            "первоначальн",
            "кредит оформ",
        )
    ) and not re.search(r"\b(?:он|она)\s+только\s+что\s+положил", low):
        return False
    return True


def _is_sto_service_intent_with_service_assistant_line(text: str) -> bool:
    """
    СТО вх.: клиенту нужен сервис («на сервис надо», Ниссан+диагностика и т.д.) и звонок
    уходит на линию ассистента/диспетчера сервиса (Юлия и др.) — перевод состоялся.
    Не срабатывает при «линия занята / только номер» (см. _is_service_secretary_callback_misc).
    """
    low = (text or "").lower()
    if _is_service_secretary_callback_misc(text):
        return False
    if _is_failed_sto_transfer_misc(text):
        return False
    if not any(
        b in low
        for b in (
            "викинги", "чери", "черри", "chery", "chily", "тенет", "тэнет", "тенос",
            "дилер", "автосалон", "официальн", "ниссан", "nissan", "нисан",
        )
    ):
        return False
    client_need = any(
        p in low
        for p in (
            "на сервис надо",
            "на сервис мне надо",
            "мне на сервис надо",
            "мне нужен сервис",
            "нужен сервис",
            "надо на сервис",
            "соедините с сервис",
            "переведите на сервис",
            "переключите на сервис",
        )
    ) or ("на сервис" in low and "надо" in low)
    client_need = client_need or (
        any(n in low for n in ("ниссан", "nissan", "нисан", "nisssan"))
        and any(
            x in low
            for x in (
                "диагностик",
                "датчик",
                "неисправност",
                "двигател",
                "загорел",
                "стан посмотреть",
                "посмотреть стан",
                "мастер-приёмщик",
                "мастер приёмщик",
                "мастер-приемщик",
            )
        )
    )
    line_ok = (
        ("ассистент сервиса" in low or "диспетчер сервиса" in low)
        and any(
            n in low
            for n in ("юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина", "александра")
        )
    ) or ("юлия" in low and "андреева" in low) or (
        "слушаю вас" in low and ("юлия" in low or "андреева" in low)
    ) or any(
        p in low
        for p in (
            "перевожу на сервис",
            "переключу на сервис",
            "переведу на сервис",
            "переключаю на сервис",
            "соединяю с сервис",
        )
    )
    return bool(client_need and line_ok)


def _is_service_dispatcher_diagnostics_not_scheduled_to(transcript: str) -> bool:
    """
    Прочие (не СТО вх. в списке «Звонки»): линия диспетчера/ассистента сервиса, но запрос —
    диагностика/осмотр (в т.ч. запись на диагностику, симптомы неисправности), без явного ТО
    из маркеров планового обслуживания.
    """
    low = (transcript or "").lower()
    if _is_service_secretary_callback_misc(transcript):
        return False
    line_ok = (
        ("диспетчер сервиса" in low or "ассистент сервиса" in low)
        and any(
            n in low
            for n in (
                "юлия",
                "юлию",
                "андреева",
                "плаксина",
                "гринкина",
                "гранкина",
                "александра",
            )
        )
    )
    if not line_ok:
        return False
    explicit_to = any(
        p in low
        for p in (
            "запись на то",
            "записаться на то",
            "техническое обслуживание",
            "техобслуживание",
            "тех обслуживание",
            "нулевое то",
            "то нулевое",
            "нулевом то",
            "первое то",
            "второе то",
            "третье то",
            "то-1",
            "то-2",
            "то-3",
            "плановое то",
            "регламентное то",
            "периодическое то",
            "техосмотр",
            "плановое обслуживание",
        )
    )
    if explicit_to:
        return False

    symptom_mechanical = any(
        p in low
        for p in (
            "подвеск",
            "посторонние звук",
            "посторонние звуки",
            "посторонний шум",
            "стук",
            "шум в подвеск",
            "шумы в подвеск",
            "посторонние шум",
            "педаль",
            "газа",
            "проблем",
            "перестал",
            "перестаёт",
            "перестает",
            "перестают",
            "накатом",
            "на катом",
            "не работает",
            "поломк",
            "неисправн",
            "check engine",
            "ошибк",
            "на ходу",
            "откликаться",
            "откликается",
        )
    ) or ("шум" in low and "подвеск" in low)

    inspect = any(
        p in low
        for p in (
            "посмотреть",
            "посмотрите",
            "осмотр",
            "осмотреть",
            "диагностик",
            "проверить",
            "разобраться",
            "разберемся",
            "послушать",
            "глянуть",
            "посмотрим",
        )
    )

    # Явная запись именно на диагностику (не на ТО) — тоже «Прочие», даже после подбора слота.
    explicit_diag_booking = bool(re.search(r"запис\w*\s+(?:на\s+)?диагностик", low)) or (
        "диагностик" in low and "запис" in low
    )

    return bool(line_ok and not explicit_to and inspect and (symptom_mechanical or explicit_diag_booking))


def _is_service_dispatcher_cancellation_service_other(transcript: str) -> bool:
    """
    Прочие: линия диспетчера сервиса, но звонок про отмену/выход из записи
    (диагностика, перенос и т.д.), не «новая» запись на плановое ТО.
    """
    low = (transcript or "").lower()
    if _is_service_secretary_callback_misc(transcript):
        return False
    line_ok = (
        ("диспетчер сервиса" in low or "ассистент сервиса" in low)
        and any(
            n in low
            for n in (
                "юлия",
                "юлию",
                "андреева",
                "плаксина",
                "гринкина",
                "гранкина",
                "александра",
            )
        )
    )
    if not line_ok:
        return False
    explicit_new_to = any(
        p in low
        for p in (
            "запись на то",
            "записаться на то",
            "техническое обслуживание",
            "техобслуживание",
            "нулевое то",
            "то нулевое",
            "нулевом то",
            "первое то",
            "второе то",
            "третье то",
            "то-1",
            "то-2",
            "то-3",
        )
    )
    if explicit_new_to:
        return False
    cancel = any(
        p in low
        for p in (
            "отменяем запись",
            "отменить запись",
            "отменяю запись",
            "отмена записи",
            "выписаться",
            "хотела выписаться",
            "отмену записи",
            "тогда отменяем запись",
        )
    ) or ("отмен" in low and "запис" in low)
    svc_ctx = any(
        p in low
        for p in (
            "диагностик",
            "воскресенье",
            "воскресен",
            "записан",
            "записана",
            "на третье",
            "антрифриз",
            "антифриз",
        )
    )
    return bool(cancel and svc_ctx)


# Реестр «Звонки»: конкретные работы (масло, тормоза, подвеска, осмотр…) без явного планового ТО → Прочие.
_OIL_CHANGE_REGISTRY_TOPIC_MARKERS = (
    "замена масла",
    "замену масла",
    "заменить масло",
    "масло поменять",
    "поменять масло",
    "моторного масла",
    "масло в двигателе",
    "масло в коробке",
    "масляного фильтра",
    "замена масла в коробке",
    "замена масла в двигателе",
    "коробке передач",
)
_REGISTRY_SERVICE_WORK_EXTRA_MARKERS = (
    "шум при тормож",
    "шум при тормоз",
    "при торможен",
    "колодк",
    "тормозн",
    "подвеск",
    "ходовая",
    "ходовую",
    "ходовой",
    "ходов",
    "диагностик",
    "продиагностировать",
)
_REGISTRY_SERVICE_WORK_WITHOUT_TO_MARKERS = _OIL_CHANGE_REGISTRY_TOPIC_MARKERS + _REGISTRY_SERVICE_WORK_EXTRA_MARKERS
_SCHEDULED_TO_PHRASES_BLOCK_OIL_REGISTRY = (
    "запись на то",
    "записаться на то",
    "записать на то",
    "нулевое то",
    "то нулевое",
    "первое то",
    "второе то",
    "третье то",
    "четвертое то",
    "четвёртое то",
    "пятое то",
    "шестое то",
    "седьмое то",
    "восьмое то",
    "девятое то",
    "десятое то",
    "техническое обслуживание",
    "техобслуживание",
    "плановое то",
    "регламентное то",
    "полное то",
    "комплексное то",
    "пройти то",
    "прохожу то",
    "то-1",
    "то-2",
    "то-3",
    "то-4",
    "то-5",
    "то-6",
    "то-7",
    "тошку",
    "тошка",
    # цена/нулевое ТО при обсуждении регламента (рядом может быть «замена масла» как состав ТО)
    "стоимость то",
    "цену на то",
    "сколько стоит то",
    "нулевой то",
    "нулевом то",
    "на нулевом то",
)


def _low_for_scheduled_to_oil_blocklist(low: str) -> str:
    """Убираем разговорное «и на то, и на то» (= про оба варианта работ), не про регламентное ТО."""
    s = (low or "").lower()
    s = re.sub(r"\bи\s+на\s+то\s*,\s*и\s+на\s+то\b", " ", s)
    s = re.sub(r"\bна\s+то\s+и\s+на\s+то\b", " ", s)
    return s


def _registry_has_work_noise_inspect_combo(low: str) -> bool:
    """STT: «посмотреть шум», «шум … осмотр» без отдельных фраз из кортежа маркеров."""
    if "шум" not in low:
        return False
    if any(v in low for v in ("посмотреть", "посмотрите", "посмотрим", "глянуть", "осмотр", "проверить")):
        return True
    return False


def _is_sto_registry_other_service_work_without_scheduled_to(transcript: str) -> bool:
    """
    Реестр «Звонки»: в тексте есть конкретные работы сервиса без явного регламентного ТО — Прочие
    (не широкий СТО_ТО_вх). Линия ассистента/диспетчера здесь не проверяется — только тема и блок ТО.
    """
    low = (transcript or "").lower()
    if _is_service_secretary_callback_misc(transcript):
        return False
    # В консультации ОП «по ходовым качествам/характеристикам машины одинаковые»
    # описывает товар, а не запрос клиента на диагностику ходовой.
    work_low = re.sub(
        r"\b(?:по\s+)?ходов\w*\s+(?:качеств\w*|характеристик\w*)\b",
        " ",
        low,
        flags=re.I,
    )
    has_work = any(p in work_low for p in _REGISTRY_SERVICE_WORK_WITHOUT_TO_MARKERS) or _registry_has_work_noise_inspect_combo(
        work_low
    )
    if not has_work:
        return False
    low_sched = _low_for_scheduled_to_oil_blocklist(low)
    if any(p in low_sched for p in _SCHEDULED_TO_PHRASES_BLOCK_OIL_REGISTRY):
        return False
    return True


_BOOKING_VERIFICATION_REGISTRY_MARKERS = (
    "давайте проверим",
    "давай проверим",
    "проверим запись",
    "проверить запись",
    "на какое всё-таки",
    "на какое все-таки",
    "на какое число",
    "на какую дату",
    "два числа",
    "две даты",
    "два даты",
    "сбой в программе",
    "две записи",
)



def _is_inbound_client_existing_booking_after_misdirected_call(text: str) -> bool:
    """
    Входящий: клиент уже записывался на ТО, перезванивает после звонка дилера другому контакту (8963).
    «Вы жене позвонили» — не исходящий CRM-перезвон.
    """
    try:
        from text_normalization import normalize_text

        low = (normalize_text(text) or text).lower()
    except Exception:
        low = (text or "").lower()
    head = low[:1800]
    if not ("диспетчер" in head and any(n in head for n in ("дарья", "дарью", "бари", "юлия", "юлию"))):
        return False
    client_booked = bool(
        re.search(r"\b(?:я|мы)\s+.{0,55}(?:на\s+)?то\s+записывал", head, re.I)
        or re.search(r"\bзаписывался\b", head[:1000])
    )
    if not client_booked:
        return False
    return any(
        p in head
        for p in (
            "позвонили",
            "вам звонили",
            "вы мне звонили",
            "мне звонили",
            "жена",
            "муж",
            "сказала",
            "сказал",
        )
    )


def _has_booking_verification_intent(low: str) -> bool:
    """Клиент уточняет уже существующую запись (дата, оповещение), а не просит новую запись на ТО."""
    if any(p in low for p in _BOOKING_VERIFICATION_REGISTRY_MARKERS):
        return True
    if re.search(r"\b(?:я|мы)\s+.{0,55}(?:на\s+)?то\s+записывал", low, re.I):
        if any(p in low[:2000] for p in ("позвонили", "жена", "муж", "сказала", "сказал", "вам звонили")):
            return True
    if re.search(r"записан[аы]?\s+на\s+то\s+на\s+как", low):
        return True
    if ("перезаписывалась" in low or "перезаписывался" in low) and any(
        x in low for x in ("уточн", "на какое", "два числа", "проверим", "оповещен", "сказали")
    ):
        return True
    # «уточн…» + «запис…» в одной реплике про проверку/перенос своей записи — не любое совпадение в длинном тексте (12241: «своя запись… уточнить у них» про кузовной).
    if re.search(r"уточн\w*[^.!?]{0,45}запис", low):
        if re.search(
            r"(?:с(?:во(?:я|ей|ю)|их)|своя)\s+запис\w*[^.!?]{0,90}уточн\w*\s+у\s+(?:них|неё|него|неё)",
            low,
        ):
            return False
        if "записаться на то" in low or "запись на то" in low:
            return False
        if any(
            p in low
            for p in (
                "на какое",
                "на какую",
                "проверим",
                "оповещен",
                "перенести",
                "перезапис",
                "уточнить запись",
                "уточним запись",
                "уточню запись",
            )
        ):
            return True
        if re.search(r"уточн\w*\s+(?:запис|дат[уа]|время|слот)", low):
            return True
    return False


def _dispatcher_service_line_with_known_names(low: str) -> bool:
    """Линия «диспетчер сервиса» + знакомые имена СТО (без проверки темы)."""
    if "диспетчер" in low and any(
        n in low
        for n in (
            "юлия",
            "юлию",
            "андреева",
            "плаксина",
            "гринкина",
            "гранкина",
            "александра",
            "дарья",
            "дарью",
            "бари",  # STT: Дарья (9091/8963)
        )
    ):
        return True
    return (
        ("диспетчер сервиса" in low or "ассистент сервиса" in low)
        and any(
            n in low
            for n in (
                "юлия",
                "юлию",
                "андреева",
                "плаксина",
                "гринкина",
                "гранкина",
                "александра",
                "дарья",
                "дарью",
            )
        )
    )


def _explicit_scheduled_to_markers_present(low: str) -> bool:
    """Явные маркеры планового ТО (как в других правилах СТО→Прочие)."""
    return any(
        p in low
        for p in (
            "запись на то",
            "записаться на то",
            "техническое обслуживание",
            "техобслуживание",
            "тех обслуживание",
            "первое то",
            "второе то",
            "третье то",
            "то-1",
            "то-2",
            "то-3",
            "плановое то",
            "регламентное то",
            "периодическое то",
            "техосмотр",
            "плановое обслуживание",
        )
    )


def _is_service_dispatcher_remote_app_account_other(transcript: str) -> bool:
    """
    Прочие: диспетчер сервиса помогает с приложением MyCherry / доступом / логином и СМС —
    не запись на ТО и не ремонт автомобиля в смысле реестра «Звонки».
    """
    low = (transcript or "").lower()
    if _is_service_secretary_callback_misc(transcript):
        return False
    if not _dispatcher_service_line_with_known_names(low):
        return False
    if _explicit_scheduled_to_markers_present(low):
        return False
    app_topic = any(
        p in low
        for p in (
            "приложен",
            "my cherry",
            "май черри",
            "ми черри",
            "маи черри",
            "логин",
            "пароль",
            "восстановить доступ",
            "восстановить дуступ",
            "подобрать логин",
            "не могу подобрать",
            "горячую линию",
            "направлю смс",
            "направим смс",
            "отправила вам смс",
            "отправила смс",
            "придёт смс",
            "придет смс",
            "с логином и паролем",
            "cherri remot",
            "cherry remot",
            "чери ремонт",
            "chrri",
            "сms",
            "sms",
        )
    )
    return bool(app_topic)


def _is_service_dispatcher_booking_verification_registry_other(transcript: str) -> bool:
    """
    Прочие: линия диспетчера сервиса, тема — проверка / уточнение уже созданной записи (дата, сбой оповещения).
    Не новая запись на ТО в смысле реестра «Звонки».
    """
    low = (transcript or "").lower()
    if _is_service_secretary_callback_misc(transcript):
        return False
    if not _has_booking_verification_intent(low):
        return False
    return bool(_dispatcher_service_line_with_known_names(low))


def _has_named_dealership_or_op_manager_role(low: str) -> bool:
    """
    В тексте есть привязка к дилерскому центру (бренд/точка/оф. дилер) или явная роль менеджера отдела продаж.
    Для правила документов/архива: при наличии таких маркеров не относим звонок к «чистым» документным Прочим здесь.
    """
    low = (low or "").lower()
    if any(
        p in low
        for p in (
            "викинги",
            "виикинги",
            "на заставн",
            "заставной",
            "официальный дилер",
            "дилерский центр",
            "салон чери",
            "менеджер отдела продаж",
        )
    ):
        return True
    if re.search(r"\bменеджер\s+оп\b", low):
        return True
    if re.search(r"официальн\w*\s+дилер", low):
        return True
    return False


def _has_op_manager_sales_handoff(text: str) -> bool:
    """
    Был перевод или содержательный разговор с менеджером ОП новых (Чери/Тенет).
    12324: Анастасия + Tenet 4 Pro; 14831: только админ → страховая Наталья — False.
    """
    low = (text or "").lower()
    if re.search(r"менеджер\w*\s+отдела\s+продаж", low):
        return True
    if re.search(
        r"(?:переведу|переключу|перевожу|соединю)[^.!?]{0,120}"
        r"(?:на\s+)?менеджер\w*\s+отдела\s+продаж",
        low,
    ):
        return True
    op_mgr_spoke = any(len(n) >= 3 and n in low for n in _OP_MANAGER_NAMES)
    if not op_mgr_spoke:
        return False
    new_car_op = bool(
        re.search(
            r"(?:tenet|tent|тенет|тэнет|чери|chery|тигго|tiggo|arrizo|арризо|дашинг)"
            r"[^.!?]{0,40}(?:4|7|8|9|pro|про|active|prime|line|лайн)",
            low,
        )
        or re.search(r"четверт\w*\s+эл", low)
        or re.search(r"четвёрт\w*\s+эл", low)
        or (
            "комплектац" in low
            and any(b in low for b in ("tenet", "tent", "тенет", "тэнет", "chery", "чери", "тигго"))
        )
        or (
            "трейд-ин" in low
            and any(b in low for b in ("tenet", "tent", "тенет", "тэнет", "chery", "чери"))
        )
        or ("за наличку" in low and "менеджер отдела" in low)
        or ("ценовая категория" in low and op_mgr_spoke)
    )
    if not new_car_op:
        return False
    return (
        "менеджер отдела продаж" in low
        or "отдел продаж" in low
        or bool(
            re.search(
                r"меня зовут[^.!?]{0,60}(?:анастас|евген|иль|захар|андре|краснощ|калаев|щегол)",
                low,
            )
        )
    )


def _has_insurance_department_staff_dialogue(text: str) -> bool:
    """Явный разговор с сотрудником страхового отдела (14831: Наталья после перевода)."""
    low = (text or "").lower()
    insurance_dept = bool(
        re.search(r"отдел\s+страхован", low)
        or re.search(r"страхов\w*\s+отдел", low)
        or re.search(r"компания\s+викинг\w*[^.!?]{0,80}страхов", low)
    )
    if not insurance_dept:
        return False
    insurance_staff = bool(
        re.search(r"(?:наталь|дарьян)\w*", low)
        or re.search(r"отдел\s+страхован[^.!?]{0,80}(?:наталь|дарьян)", low)
        or re.search(r"(?:наталь|дарьян)\w*[^.!?]{0,40}отдел\s+страхован", low)
    )
    client_requested = bool(
        re.search(r"(?:соедин|переключ|перевед)[^.!?]{0,100}(?:со\s+)?страхов", low)
        or re.search(r"страхов\w*", low[:1500])
    )
    return insurance_staff or (client_requested and insurance_dept)


def _is_insurance_department_call_other(text: str) -> bool:
    """
    Перевод/разговор со страховым отделом: расчёт и оформление полиса — Прочие, не СТО (10722).
    «Новую машину» / «стоимость» здесь про страховку, не про приёмку сервиса.
    """
    low = (text or "").lower()
    if _is_outbound(text or ""):
        return False
    insurance_work = any(p in low for p in ("страхов", "каско", "осаго", "полис"))
    if not insurance_work:
        return False
    insurance_line = bool(
        re.search(r"страхов\w*\s+отдел", low)
        or "отдел страхован" in low
        or "занимается страхован" in low
        or re.search(r"компания\s+викинг\w*[^.!?]{0,50}страхов", low)
    ) or bool(
        re.search(r"(?:соедин|переключ|перевед|с\s+кем)[^.!?]{0,120}страхов", low)
    )
    if not insurance_line:
        return False
    # STT-артефакт «отдел страхования» посреди ОП-диалога (12324) — не перебивает OP_IN.
    if _has_op_sales_conversation_substance(text):
        if _has_insurance_department_staff_dialogue(text) and not _has_op_manager_sales_handoff(text):
            pass  # 14831: страховой отдел + Наталья, «кредит/покупали» про архив Nissan — не ОП
        else:
            return False
    if any(
        p in low
        for p in (
            "записаться на то",
            "запись на то",
            "на то запис",
            "записаться на сервис",
            "запись на сервис",
            "техобслуживание",
            "диагностик",
            "замена масла",
            "ремонт авто",
            "гарантийный случай",
        )
    ):
        return False
    return True


def _is_insurance_archive_documents_other(text: str) -> bool:
    """
    Тема документов / страховки / архива карточек при отсутствии содержательного разговора
    о выборе или покупке автомобиля (см. _v2_substantive_sales_topic) и без упоминания
    названия дилерского центра / бренда лота как контекста продаж и без роли менеджера ОП
    → Прочие. Не завязано на конкретные фамилии или STT-обрубки.
    """
    low = (text or "").lower()
    doc_topic = any(
        p in low
        for p in (
            "полис",
            "страхов",
            "в архиве",
            "архиве где",
            "архив",
            "карточк",
            "карточки",
            "не нашла карточ",
            "не нашли карточ",
            "справк",
            "подпишут",
            "антония степановна",
        )
    )
    if not doc_topic:
        return False
    if _v2_substantive_sales_topic(low):
        return False
    if _has_named_dealership_or_op_manager_role(low):
        return False
    return True


def _is_op_b2b_vehicle_transfer_process_other(text: str) -> bool:
    """
    B2B-процесс по передаче/реализации автомобиля (доверенность, письмо, площадка, аукцион)
    без клиентского цикла покупки нового Chery/Tenet.

    20080: разговор не про покупку нового авто, а про оформление и передачу машины.
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    process_markers = (
        "автозавод",
        "бывший ниссан",
        "доверенност",
        "онлайн-аукцион",
        "онлайн аукцион",
        "аукцион",
        "реализовывать с вашей площадки",
        "с вашей площадки",
        "передаст автомобиль",
        "передачи автомобиля",
        "паспортные данные",
        "письмо в апреле",
        "перешлю письмо",
        "хвостов",
    )
    if not any(p in low for p in process_markers):
        return False

    # Условие пользователя: диалог не с менеджером из справочника ОП.
    has_known_op_manager = any(len(name) >= 3 and name in low for name in _OP_MANAGER_NAMES)
    if has_known_op_manager:
        return False

    # Условие пользователя: марка не Chery/Tenet (в содержательной части нет модельных маркеров дилера).
    # Берём только модельные маркеры сделки; голые «chery/tenet» в приветствии
    # или e-mail домене (vikingi-tenet.ru) не считаем выбором бренда.
    has_chery_tenet_model = any(
        p in low
        for p in (
            "tiggo",
            "тигго",
            "arrizo",
            "арризо",
            " t4",
            " t7",
            " t8",
            " t9",
            "тенет т",
            "tenet t",
            "дашинг",
            "dashing",
        )
    )
    if has_chery_tenet_model:
        return False

    # Условие пользователя: нет выбора авто.
    has_vehicle_choice = any(
        p in low
        for p in (
            "какой автомобиль",
            "какую модель",
            "какую комплектац",
            "с комплектациями знакомы",
            "с панорамой или без",
            "в наличии",
        )
    )
    if has_vehicle_choice:
        return False

    # Условие пользователя: нет выбора формы оплаты.
    has_payment_choice = any(
        p in low
        for p in (
            "кредит",
            "лизинг",
            "наличн",
            "безнал",
            "рассроч",
            "первоначальн",
            "трейд-ин",
            "trade-in",
            "трейдин",
        )
    )
    if has_payment_choice:
        return False

    return True


def _is_post_sale_vehicle_documents_other(text: str) -> bool:
    """
    Документы по уже завершённой сделке (ПТС/ЭПТС, копия, отправка на почту) → Прочие.
    Короткое предложение менеджера рассмотреть новую покупку в конце разговора не
    превращает такой звонок в ОП без фактического процесса выбора/расчёта/оформления.
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    document_topic = bool(
        re.search(r"\b(?:э\s*птс|эптс|птс)\b", low, re.I)
        or (
            "документ" in low
            and any(p in low for p in ("электронн", "копи", "почт", "распечат", "архив"))
        )
    )
    historical_purchase = bool(
        re.search(
            r"\bпокупал\w*[^.!?]{0,80}\b(?:год|года|лет)\s+назад\b",
            low,
            re.I,
        )
        or re.search(
            r"\b(?:год|года|лет)\s+назад\b[^.!?]{0,80}\bпокупал\w*",
            low,
            re.I,
        )
        or re.search(r"\b(?:ранее|раньше)\b[^.!?]{0,80}\bпокупал\w*", low, re.I)
    )
    document_retrieval = any(
        p in low
        for p in (
            "электронной почт",
            "электронную почт",
            "в электронном виде",
            "распечатк",
            "вин-номер",
            "вин номер",
            "vin",
            "хранятся",
            "не нашла",
            "не нашёл",
            "не нашел",
            "отправляли",
        )
    )
    active_sales_process = any(
        p in low
        for p in (
            "тест-драйв",
            "тест драйв",
            "сколько стоит",
            "какая цена",
            "по цене",
            "комплектац",
            "в наличии",
            "рассроч",
            "кредит",
            "лизинг",
            "трейд-ин",
            "trade-in",
            "оценить автомобил",
            "оценка автомобил",
            "забронировать",
            "внести предоплат",
            "оформить покуп",
            "оформляем автомобил",
            "договор купли",
        )
    )
    return (
        document_topic
        and historical_purchase
        and document_retrieval
        and not active_sales_process
    )


def _is_admin_brand_unavailable_no_op_transfer_other(text: str) -> bool:
    """
    Администратор / линия салона: бренд не ведём (Jetour и т.д.) — не ОП вх., разговор не дошёл до продажи.
    """
    low = (text or "").lower()
    try:
        from call_analytics.sto_booking_dimensions import jetour_brand_mentioned
    except ImportError:
        jetour_brand_mentioned = lambda t: any(  # noqa: E731
            b in t for b in ("жетур", "житур", "jetour", "джитур", "джетур", "джтр", "дjtур")
        )
    if not jetour_brand_mentioned(low):
        return False
    if not any(
        p in low
        for p in (
            "не продаём",
            "не продаем",
            "не поставляем",
            "не продаём.",
            "самаре",
            "нет в наличии",
            "не ведём",
            "не ведем",
            "нельзя пройти",
            "не пройти",
            "не можем пройти",
            "не принимаем",
            "не являемся официальным",
            "не обслуживаем",
        )
    ):
        return False
    return any(
        a in low
        for a in (
            "администратор",
            "оксана",
            "анастасия",
            "диана",
            "полина",
            "викинги",
            "чери",
        )
    )


def _is_op_marketing_sponsorship_charity_contact_other_low(low: str) -> bool:
    """
    16512: маркетолог / спонсорство / реклама на мероприятии — не СТО и не цикл покупки авто, Прочие.
    """
    if not (low or "").strip():
        return False
    if "маркетолог" in low:
        return True
    if re.search(r"\bблаготворит", low, re.I) and any(
        p in low
        for p in (
            "спонсор",
            "прореклам",
            "турнир",
            "реклам",
            "президентск",
            "грант",
            "фонд",
            "спорт",
        )
    ):
        return True
    if re.search(r"\bпрореклам", low, re.I) and any(
        p in low for p in ("турнир", "спорт", "игрок", "мероприят", "фонд", "площадк")
    ):
        return True
    return False


def _is_op_marketing_sponsorship_charity_contact_other(text: str) -> bool:
    return _is_op_marketing_sponsorship_charity_contact_other_low((text or "").lower())


def _is_op_promotional_contest_crm_other_low(low: str) -> bool:
    """
    Менеджер ОП / админ: промо-конкурс (велосипед и т.п.), отметка тест-драйва в CRM для участия —
    не цикл выбора/покупки автомобиля, Прочие (15337, 15338, 15356).
    """
    if not (low or "").strip():
        return False
    if not any(p in low for p in ("велосипед", "конкурс", "выиграть", "розыгрыш", "розыгрыва")):
        return False
    if not any(p in low for p in ("тест-драйв", "тест драйв", "тестдрайв")):
        return False
    if not any(
        p in low
        for p in (
            "для участия",
            "простав",
            "срм",
            "crm",
            "зарегистрир",
            "регистрац",
            "регистрир",
            "заявк",
            "участв",
        )
    ):
        return False
    if any(
        p in low
        for p in (
            "комплектац",
            "в наличии",
            "сколько стоит",
            "какая цена",
            "кредит ",
            "лизинг",
            "трейд-ин",
            "хочу купить",
            "хотел купить",
            "тигго",
            "tiggo",
            "арризо",
            "arrizo",
            "дашинг",
            "tenet t",
            "тенет т",
            "четверт",
            "четвёрт",
        )
    ):
        return False
    return True


def _is_op_promotional_contest_crm_other(text: str) -> bool:
    return _is_op_promotional_contest_crm_other_low((text or "").lower())


def _is_sto_after_dealer_or_op_admin_handoff(text: str) -> bool:
    """
    Входящий: в начале официальный дилер / администратор (Анастасия…), затем основной разговор
    уходит на линию сервиса — это СТО вх., не ОП (имя менеджера ОП в приветствии не решает).
    Имя сотрудника сервиса может не распознаться, поэтому достаточно явного перевода в сервис
    и сервисного намерения после приветствия дилера/администратора.
    """
    if _is_inbound_op_sales_intent_after_non_op_line(text):
        return False
    if _is_op_inbound_admin_handoff_to_op_manager(text) and _has_op_sales_conversation_substance(
        text
    ):
        return False
    if _is_outbound(text or ""):
        return False
    low = (text or "").lower()
    head = low[:1800]
    # Приоритет ОП: перевод на менеджера ОП + дилерский контекст + предмет покупки.
    op_handoff = bool(
        re.search(
            r"(перевед\w*|переключ\w*)[^.!?\n]{0,80}(?:менеджер\w*[^.!?\n]{0,40}отдел\w*\s+продаж|отдел\w*\s+продаж)",
            low,
        )
        or (
            re.search(r"(?:перевед\w*|переключ\w*)", head[:2200])
            and any(
                p in head[:2200]
                for p in ("менеджер", "продажник", "отдел продаж", "менеджера отдела продаж")
            )
        )
    )
    has_op_intro = (
        "менеджер отдела продаж" in low
        or any(len(name) >= 3 and name in low[:3000] for name in _OP_MANAGER_NAMES)
    )
    dealer_ctx = any(p in low for p in ("официальный дилер", "викинг", "чери", "тенет", "тэнет"))
    purchase_markers = (
        "покупк",
        "интересует автомобиль",
        "интересует машина",
        "комплектац",
        "в наличии",
        "доплат",
        "скидк",
        "кредит",
        "за налич",
        "рассрочк",
    )
    purchase_score = sum(1 for p in purchase_markers if p in low)
    if op_handoff and has_op_intro and dealer_ctx and purchase_score >= 2:
        return False
    opener = (
        ("официальный дилер" in head or "официальн" in head[:500])
        or ("администратор" in head and "анастасия" in head)
        or (
            any(b in head for b in ("викинг", "чери", "chery", "черри"))
            and (
                "ассистент сервиса" in head
                or "диспетчер сервиса" in head
                or "енсервис" in head
                or (
                    ("слушаю вас" in head or re.search(r"\bслушаю\b", head[:280]))
                    and any(n in head for n in ("андреева", "юлия", "юлию", "александра", "плаксина"))
                )
            )
        )
    )
    if not opener:
        return False
    service_line_named = (
        ("юлия" in low and "андреева" in low)
        or ("ассистент сервиса" in low or "диспетчер сервиса" in low)
        or ("слушаю вас" in low and ("юлия" in low or "андреева" in low))
    )
    transfer_to_service = any(
        p in low
        for p in (
            "соединю вас с сервисом",
            "соединю с сервисом",
            "соединю с сервисной",
            "соединю с сервисным центром",
            "перевожу на сервис",
            "переключаю на сервис",
            "переведу на сервис",
            "переключу на сервис",
        )
    ) or (
        "оставайтесь на линии" in low
        and not op_handoff
        and not any(
            p in head[:2200]
            for p in ("продажник", "менеджер отдела продаж", "менеджера отдела продаж")
        )
    )
    if not service_line_named and not transfer_to_service:
        return False
    if _has_sto_substantive_service_intent(low):
        return True
    return any(
        x in low
        for x in (
            "ниссан",
            "nissan",
            "кашкай",
            "датчик",
            "диагностик",
            "мастер-приёмщик",
            "мастер приёмщик",
            "мастер-приемщик",
            "на сервис",
            "неисправност",
            "пробег",
            "записаться",
            "перевожу на сервис",
            "переключаю на сервис",
        )
    )


def _is_sto_inbound_failed_target_contact_misc(t: str) -> bool:
    """
    Линия дилера/СТО/кузовного: клиент просит соединить с конкретным сотрудником,
    перевод не состоялся или только обещание перезвона — Прочие, не СТО вх. (как «ОП без контакта с менеджером»).
    Краткое уточнение по теме сервиса у диспетчера без разговора с запрошенным лицом не отменяет это правило.
    """
    low = (t or "").lower()
    # Полноценная запись на регламентное ТО (слоты/даты): «перезвоню» в финале — не «срыв соединения».
    if _is_sto_to_booking_live_dialog_not_secretary_misc(low):
        return False
    line_ctx = any(
        b in low
        for b in (
            "викинги",
            "виикинги",
            "чери",
            "черри",
            "диспетчер сервиса",
            "ассистент сервиса",
            "на заставн",
            "заставной",
            "кузовн",
            "администратор салона",
            "администратор саллона",
        )
    ) or ("стажер" in low and "дарья" in low)
    if not line_ctx:
        return False
    wants_transfer = bool(
        re.search(
            r"\b(?:соедините|соединить|переведите|переведи|переключите|переключи)\b"
            r"(?:[\s\S]{0,160}?)\b(?:с|на|к)\s+[а-яё]{2,}",
            low,
        )
        or re.search(
            r"\b(?:соедините|переключите|переведите)\b[^.!?]{0,120}\b(?:андре\w+|иль[еёи]\w+|анастас\w+|евген\w+|захаров|калаев|щеголев|павл\w+|степан\w+)\b",
            low,
            re.I,
        )
    ) or any(
        p in low
        for p in (
            "с павлом",
            "к павлу",
            "на павла",
            "павлом соединит",
            "со степаном",
            "к степану",
            "на степана",
            "степаном соедин",
            "позовите ",
            "позови ",
        )
    )
    if not wants_transfer:
        return False
    outcome_misc = any(
        p in low
        for p in (
            "перезвоню",
            "перезвоним",
            "перезвонят",
            "перезвонит",
            "сообщу",
            "чтобы он вам перезвонил",
            "чтобы вам перезвонил",
            "чтобы он перезвонил",
            "попробую перевести",
            "попробую переключить",
            "ожидайте",
            "не могу соединить",
            "не смогу соединить",
            "не получилось соединить",
            "не удалось соединить",
            "не берёт трубочку",
            "не берет трубочку",
            "не берёт трубку",
            "не берет трубку",
            "попозже наберите",
            "позже наберите",
            "наберите попозже",
            "чуть попозже",
            "занят",
            "не на месте",
            "не в офисе",
            "не отвечает",
            "сейчас нет",
            "когда освободится",
            "как освободится",
            "пока занят",
            "пока занята",
            "передам",
            "скажу ему",
            "скажу ей",
            "наберёт вам",
            "наберет вам",
            "свяжется с вами",
        )
    ) or (
        "переключу" in low
        and any(
            p in low
            for p in (
                "не берёт трубочку",
                "не берет трубочку",
                "не берёт трубку",
                "не берет трубку",
            )
        )
    )
    if not outcome_misc:
        return False
    # 14841: админ → отдел продаж → Евгений, Tenet T7/наличие; «перезвоню» про резерв — не срыв СТО.
    if _has_op_manager_sales_handoff(low):
        return False
    return True


def normalize_call_type_result(dept: str, call_type: str) -> Tuple[str, str]:
    """Согласование пары отдел/тип; STO_OUT допустим только при department STO."""
    d = (dept or "").strip().upper()
    c = (call_type or "").strip().upper()
    if c == "STO_OUT":
        if d == "STO":
            return "STO", "STO_OUT"
        return "OTHER", "OTHER"
    if c == "OP_MISC":
        return "OTHER", "OTHER"
    return d, c


def _normalize_stt_classification_artifacts(text: str) -> str:
    """
    Типичные артефакты STT в начале разговора, мешающие распознать исходящий ОП:
    - «а. . Алло. Алло, Имя» / «Д. Алло. Алло, Имя» вместо «Алло, Имя»;
    - склейка «ИльяВикинги» / «Ильянет» без пробела после имени менеджера;
    - «здравствуйте.е.» и прочий мусор STT после приветствия.
    """
    if not text or not isinstance(text, str):
        return text or ""
    t = text
    t = re.sub(r"(?i)^(?:[а-яё]\s*[.,]\s*)+", "", t.lstrip())
    # STT-мусор до первого «алло» (12396: «…смотрю. р. . р. Алло»).
    m_allo = re.search(r"\bалло\b", t, re.I)
    if m_allo and 0 < m_allo.start() < 350:
        prefix = t[: m_allo.start()].lower()
        if not any(
            p in prefix for p in ("викинг", "дилерск", "официальн", "менеджер", "диспетчер")
        ) and (
            re.search(r"смотрю|хуйн", prefix)
            or re.search(r"угондошт|ундо\b", prefix)
            or re.search(r"нахер|неохот", prefix)
            or re.search(r"фольксваген|volkswagen", prefix)
            or (prefix.count(".") >= 2 and len(prefix.strip()) < 120)
            or (len(prefix.strip()) < 80 and not re.search(r"\b(?:менеджер|диспетчер|викинг)\b", prefix))
            or (
                len(prefix.strip()) < 200
                and re.search(r"\bт\s*то\b|\bтоо?\b", prefix)
                and not re.search(r"\b(?:менеджер|диспетчер|викинг|компания)\b", prefix)
            )
            or (
                re.search(r"\bоригинал", prefix)
                and re.search(r"\bаналог", prefix)
                and not re.search(r"\b(?:менеджер|диспетчер|викинг|компания)\b", prefix)
            )
        ):
            t = t[m_allo.start() :]
    t = re.sub(r"(?i)\bалло\s*[.,]\s*алло\s*,", "алло, ", t)
    t = re.sub(r"(?i)\bздравствуйте\s*\.?\s*е\s*\.?", "здравствуйте", t)
    t = re.sub(
        r"(?i)\b(илья|илью|евгений|евгения|анастасия|анастасию|андрей|андрея)"
        r"(викинги|виикинги|викинг|чери|chery|тенет|тэнет|tenet)\b",
        r"\1, \2",
        t,
    )
    t = re.sub(r"(?i)\bдилл?ерск\w*\s+центр\b", "дилерский центр", t)
    return t


def _is_ats_outbound_dial_robot_prefix(text: str) -> bool:
    """
    Префикс АТС при исходящем дозвоне («оставайтесь на линии», «абонент разговаривает»).
    Только признак исходящего звонка; не относит к ОП, СТО или Прочим.
    Не путать с «оставайтесь на линии» при переводе на диспетчера (нет робота АТС).
    """
    head = (text or "").lower()[:1500]
    if not re.search(r"оставайтесь\s+на\s+линии", head):
        return False
    return any(
        p in head
        for p in (
            "абонент разговаривает",
            "перезвоните позже",
            "продолжаем дозваниваться",
            "please wait",
            "call back later",
            "answering",
        )
    )


def _discount_in_vehicle_sale_context(low: str) -> bool:
    """
    «Скидка» сама по себе не указывает отдел (ОП/СТО/кузов/запчасти/сторонний звонок).
    Маркер сделки по новому авто — только если рядом тема покупки/модели/комплектации.
    """
    if "скидк" not in (low or ""):
        return False
    if any(
        p in low
        for p in (
            "скидка максимальная",
            "максимальная скидка",
            "скидку на автом",
            "скидка на авто",
            "скидку на авто",
            "скидка на автом",
        )
    ):
        return True
    return any(
        p in low
        for p in (
            "тигго",
            "tiggo",
            "арризо",
            "arrizo",
            "дашинг",
            "dashing",
            "jaecoo",
            "джак",
            "тенет",
            "тэнет",
            "tenet",
            "чери",
            "chery",
            "черри",
            "комплектац",
            "комплектаци",
            "купить автом",
            "купить авто",
            "покупк",
            "приобрести автом",
            "приобрести авто",
            "тест-драйв",
            "тест драйв",
            "трейд-ин",
            "trade-in",
            "трейдин",
            "по трейд",
            "новый автом",
            "новое авто",
            "авто в налич",
            "стоимость автомоб",
            "стоимость авто",
            "кассо",
            "госпрограмм",
            "бронь",
            "электронную визитку",
        )
    )


def _discount_is_service_to_credit_not_vehicle_sale(low: str) -> bool:
    """«Скидка» без темы покупки авто — не маркер ОП (12757: такси; 10267: ТО)."""
    if "скидк" not in (low or ""):
        return False
    if _discount_in_vehicle_sale_context(low):
        return False
    if re.search(
        r"скидк\w*[^.!?]{0,60}\b(?:в\s+то\b|на\s+то\b|техническ\w+\s+обслуживан|обслуживан)",
        low,
        re.I,
    ):
        return True
    return True


def _is_sto_dealer_employee_outbound_service_call(text: str) -> bool:
    """
    Исходящий звонок сотрудника дилера клиенту по авто в сервисе
    (беспокоит + дилерский центр / алло+имя + по поводу автомобиля; кондиционер, заправка…).
    """
    low = (text or "").lower()
    if len(low) < 50:
        return False
    head = low[:4000]
    employee_intro = bool(
        ("беспокоит" in head and re.search(r"\b(?:дилерск|викинг|чери|тенет|тэнет)\w*", head))
        or (
            re.search(r"\bалло[.,]?\s+[а-яё]{3,}(?:\s+[а-яё]{2,})?", head[:400])
            and re.search(r"по\s+поводу\s+.{0,60}автомобил", head[:1400], re.I)
        )
    )
    if not employee_intro:
        return False
    if not _has_sto_substantive_service_intent(low):
        return False
    if _v2_has_vehicle_purchase_substance(low):
        return False
    return True


def _is_op_inbound_admin_direct_client_call(text: str) -> bool:
    """
    Входящий ОП: администратор/стойка отвечает («официальный дилер…, меня зовут Анастасия»),
    клиент обращается к сотруднику по имени («Анастасия, добрый день») и далее задаёт вопрос о покупке.
    Консультация может вестись без перевода на менеджера ОП (16703).

    Не путать с исходящим CRM: «Станислав, добрый день. Меня зовут Анастасия, менеджер отдела продаж…»
    — сначала имя клиента, потом представление менеджера.
    """
    if _is_ats_outbound_dial_robot_prefix(text) or _is_crm_ats_outbound_lead_prefix(text):
        return False
    if _is_op_dealer_ringback_intro(text):
        return False
    t = _normalize_stt_classification_artifacts(text or "").lower()
    head = t[:1200]
    if not re.search(r"\bофициальн\w*\s+дилер\b", head[:500]):
        return False
    m_staff = re.search(r"\bменя\s+зовут\s+([а-яё]{3,15})\b", head[:500])
    if not m_staff:
        return False
    staff_name = m_staff.group(1)
    m_client_greet = re.search(
        rf"\b{re.escape(staff_name)}\s*,\s*(?:добрый\s+(?:день|вечер)|здравствуйте)\b",
        head[:800],
    )
    if not m_client_greet:
        return False
    # Сотрудник представился до обращения клиента по имени — входящий, не исходящий CRM.
    return m_client_greet.start() > m_staff.start()


def _is_op_inbound_admin_handoff_to_op_manager(text: str) -> bool:
    """
    Входящий в ОП через администратора: приветствие → клиент с вопросом → перевод на менеджера.
    «Алло, Имя» при удержании линии — не исходящий CRM (регрессия 9350).
    """
    if _is_op_dealer_ringback_intro(text):
        return False
    if _is_inbound_admin_transfer_to_used_cars_manager(text):
        return False
    raw_head = (text or "").lower()[:2500]
    t = _normalize_stt_classification_artifacts(text or "").lower()
    head = t[:2500]
    has_admin_phrase = (
        "администратор" in head[:1200]
        or "администратор салона" in head[:700]
        or "администратор" in raw_head[:1200]
        or "администратор салона" in raw_head[:700]
    )
    has_reception_handoff_phrase = bool(
        re.search(
            r"\b(?:соединит\w*\s*(?:,)?\s*пожалуйста\s*(?:,)?\s*с\s+менеджер\w*|"
            r"переключа\w*\s+вам\s+на\s+менеджер\w*|"
            r"перевед\w*\s+на\s+менеджер\w*)\b",
            raw_head[:2200],
            re.IGNORECASE,
        )
    )
    if not (has_admin_phrase or has_reception_handoff_phrase):
        return False
    if _is_crm_ats_outbound_lead_prefix(text):
        return False
    if _is_admin_sales_or_service_routing_question(head):
        return False
    if re.search(r"перевед|переключ|соедин", head[:2200]) and (
        "диспетчер" in head[:2200] or "на диспетчер" in head[:2200]
    ):
        return False
    if not any(
        p in head[:2200]
        for p in (
            "переключ",
            "перевед",
            "соедин",
            "на менеджера",
            "менеджера",
            "отдел продаж",
        )
    ):
        return False
    # «отдел продаж» без перевода на менеджера — не handoff в ОП.
    if "отдел продаж" in head[:2200] and not any(
        p in head[:2200] for p in ("на менеджера", "менеджера", "переключ", "перевед", "соедин")
    ):
        return False
    return True


def _is_op_inbound_direct_manager_sales_consultation(text: str) -> bool:
    """
    Прямой входящий разговор с менеджером ОП без явного handoff:
    дилер/центр + «меня зовут …, слушаю вас» + содержательная консультация
    по покупке (модель/цена/кредит/рассрочка/первоначальный взнос).

    20417: «Дилерский центр ... меня зовут Евгений, слушаю вас» и далее
    конкретные условия покупки Tenet T7 — это ОП_вх, не Прочие.
    """
    raw = text or ""
    low = _normalize_stt_classification_artifacts(raw).lower()
    if not low.strip():
        return False
    if _is_outbound(raw):
        return False
    if _is_op_inbound_admin_handoff_to_op_manager(raw):
        return False
    if _is_insurance_department_call_other(raw) or _is_op_b2b_vehicle_transfer_process_other(raw):
        return False

    head = low[:1200]
    has_direct_manager_intro = bool(
        re.search(r"\b(?:дилерск\w*\s+центр|официальн\w*\s+дилер|компания\s+викинг\w*)\b", head, re.I)
        and re.search(r"\bменя\s+зовут\s+[а-яё]{3,20}\b", head, re.I)
        and re.search(r"\bслуша\w*\s+вас\b", head, re.I)
    )
    if not has_direct_manager_intro:
        return False

    has_finance_or_price_context = bool(
        re.search(r"\b(?:стоимост\w*|цен[аы]|скидк\w*|кредит\w*|рассроч\w*|первоначальн\w*)\b", low, re.I)
    )
    if not has_finance_or_price_context:
        return False

    return _v2_has_vehicle_purchase_substance(low) and _has_op_sales_conversation_substance(raw)


def _is_op_post_showroom_visit_status_followup(text: str) -> bool:
    """
    26506: исходящий статус-check после визита в салон к менеджеру ОП.
    «к Илье подъезжали» / «посмотрели машину» + «звонил узнать, понравилась?» → OTHER.
    Не путать с первичным лидом: нужен факт прошлого визита/осмотра и status-вопрос.
    """
    low = (text or "").lower()[:4500]
    if not low.strip():
        return False
    head = low[:2200]
    has_prior_visit = bool(
        re.search(
            r"\b(?:к\s+(?:илье|евгению|андрею|анастасии|захарову|евдокимову|калаеву|щеголеву)\s+)?"
            r"(?:подъезжал\w*|приезжал\w*|заезжал\w*)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:подъезжал\w*|приезжал\w*|заезжал\w*)\b[^.!?]{0,60}\b"
            r"(?:к\s+)?(?:илье|евгению|андрею|анастасии|захарову|евдокимову|калаеву|щеголеву)\b",
            head,
            re.I,
        )
    )
    has_car_viewed = bool(
        re.search(r"\bпосмотрел\w*[^.!?]{0,40}\b(?:машин\w*|автомобил\w*)\b", head, re.I)
        or re.search(r"\b(?:машин\w*|автомобил\w*)\b[^.!?]{0,40}\bсмотрел\w*\b", head, re.I)
    )
    if not (has_prior_visit or has_car_viewed):
        return False
    # Передача от другого менеджера усиливает повторность («Илья мне передал, посмотрели»).
    has_manager_handoff_note = bool(
        re.search(
            r"\b(?:илья|евгений|андрей|анастасия|захаров|евдокимов|калаев|щеголев)\b"
            r"[^.!?]{0,50}\bпередал\w*",
            head,
            re.I,
        )
    )
    has_status_ask = bool(
        re.search(
            r"\b(?:звон\w*|хотел\w*)\s+(?:узнать|уточнить|спросить)\b[^.!?]{0,80}"
            r"\b(?:как\s+машин\w*|понравил\w*|не\s+понравил\w*|купил\w*|выбрал\w*)\b",
            head,
            re.I,
        )
        or re.search(r"\bпонравил\w*[^.!?]{0,30}\bне\s+понравил\w*\b", head, re.I)
    )
    if not has_status_ask:
        return False
    # Достаточно: (визит ИЛИ осмотр) + status-ask; handoff от коллеги — доп. подтверждение.
    if has_prior_visit and has_car_viewed:
        return True
    if has_manager_handoff_note and (has_prior_visit or has_car_viewed):
        return True
    return has_prior_visit and has_status_ask


def _is_op_outbound_crm_test_drive_followup_redial(text: str) -> bool:
    """
    Исходящий CRM-перезвон после тест-драйва (не первичный контакт):
    «Алло, Имя, здравствуйте» + Чери/Викинги/Заставная + «были у нас / проходили тест-драйв»
    + «хотел узнать, купили/выбрали» (регрессия 9402, 13636).
    """
    t = _normalize_stt_classification_artifacts(text or "").lower()[:4000]
    if not t.strip():
        return False
    head = t[:1500]
    if not re.search(
        r"\bалло\s*[,.!?]?\s*(?:да\s*[,.!?]?\s*)?"
        r"[а-яё]{3,15}\s*,\s*(?:здравствуйте|добрый\s+(?:день|вечер))",
        head,
        re.I,
    ):
        return False
    if not any(d in head for d in ("викинг", "чери", "chery", "заставн", "тенет", "тэнет", "tenet")):
        return False
    if not any(
        p in t
        for p in (
            "проходили тест-драйв",
            "проходили тест драйв",
            "проходили тестдрайв",
            "тест-драйв проходили",
            "тест драйв проходили",
            "тестдрайв проходили",
            "тест-драйв у нас",
            "были у нас",
        )
    ):
        return False
    return any(p in t for p in ("хотел узнать", "хотела узнать", "звоню узнать")) and any(
        p in t
        for p in (
            "купили машин",
            "купили авто",
            "купили автомоб",
            "выбрали",
            "заинтересовал",
            "что решили",
            "что надумали",
            "что подумали",
        )
    )


def _has_op_outbound_surface_markers(text: str) -> bool:
    """
    Поверхностные признаки *исходящего* разговора менеджера ОП (Чери·Тенет·Викинги)
    в начале транскрипта.

    Зафиксированные маркеры (реальные STT / скрины):
    - обращение к абоненту по имени после «алло»: «Алло, Максим», «Алло, да, Максим», «Алло, Вадим»;
    - «снова» + имя менеджера из списка ОП (повторный звонок);
    - «меня … зовут» + фигурирует имя/роль менеджера ОП в префиксе;
    - «компания викинги» или «дилерский центр» + типичный STT бренда («ренинги» и т.п.);
    - отсылка к оставленному запросу/заявке (CRM, без формулировки «на нашем сайте»):
      «запрос … оставляли», «заявку отправляли», «нас вот заявку», «вы заявку отправляли»;
    - первый контакт менеджера ОП: «мне передали [контакт/номер]», «вот звоню».

    Проверки только по префиксу (~3200 символов), плюс обязательный контекст дилера/бренда.
    """
    text = _normalize_stt_classification_artifacts(text or "")
    low = text.lower()[:3200]
    if _is_op_inbound_admin_handoff_to_op_manager(text):
        head_in = low[:3500]
        if not any(
            x in head_in
            for x in (
                "заявку отправляли",
                "нас вот заявку",
                "вы заявку отправляли",
                "мне передали ваш",
                "мне передали",
                "вот звоню",
                "обратный звонок",
                "заказан обратный",
            )
        ):
            return False
    if "викинг" in low or "виикинг" in low:
        dealer = True
    elif any(
        p in low
        for p in (
            "чери",
            "chery",
            "черри",
            "тенет",
            "тэнет",
            "tenet",
            "тольятт",
            "заставн",
            "официальн",
            "дилерск",
            "автосалон",
        )
    ):
        dealer = True
    elif "компания" in low and "викинг" in low:
        dealer = True
    elif "дилерский центр" in low:
        dealer = True
    elif "ренинг" in low and "центр" in low:
        dealer = True
    else:
        dealer = False
    if not dealer:
        return False

    # Перезвон салона по пропуску — тот же класс «исходящий ОП», что и по лиду (для OP_OUT в classify_auto).
    if _is_op_dealer_ringback_intro(text):
        return True

    op_mgr = _OP_MGR_NAME_RE

    if re.search(
        r"\b(?:вчера\s+)?вы\s+звонил\w*[^.!?]{0,50}по\s+поводу\b",
        low[:3500],
        re.I,
    ) and (
        "менеджер отдела продаж" in low[:3500]
        or re.search(r"\bперезвонил\w*\b", low[:3500], re.I)
        or re.search(rf"\b{op_mgr}\b", low[:3500]) is not None
    ):
        return True

    # Первичный исходящий лид-коллбэк без слова «заявка»:
    # «Вы нам звонили… вам [модель/авто] интересует?»
    # Не трактуем как repeat follow-up — это первый разговор менеджера с клиентом.
    if (
        re.search(r"\bвы\s+нам\s+звонил\w*\b", low[:1800], re.I)
        and re.search(
            r"\bвам\b[^.!?]{0,70}\b(?:интересует|интересовал|интересовало)\b",
            low[:1800],
            re.I,
        )
        and (
            re.search(r"\b(?:автомобил\w*|машин\w*)\b", low[:1800], re.I)
            or re.search(r"\bt\s*[- ]?\s*\d+\w*\b", low[:1800], re.I)
            or any(x in low[:1800] for x in ("чери", "chery", "тенет", "тэнет", "tenet", "тигго", "tiggo"))
        )
    ):
        return True

    if re.search(rf"\bснова\s*[,.]?\s*{op_mgr}\b", low):
        return True

    if re.search(r"\bменя\s+[а-яё]{2,}\s+зовут\b", low) and re.search(rf"\b{op_mgr}\b", low[:2200]):
        return True

    # «дилерский центр» в конце разговора (приглашение в салон) не считать маркером исходящего в начале.
    head_brand = low[:2200]
    # «компания Викинги» само по себе не признак исходящего:
    # для surface-маркера нужен CRM/продажный контекст в том же префиксе.
    # 12958: «отзывная компания» из CRM-истории + «проконсультируюсь» клиента — не исходящий ОП.
    _opening_dealer_intro = low[:500]
    _has_dealer_company_intro = bool(
        re.search(r"\bкомпания\s+[\w«\"]{0,8}?викинг", _opening_dealer_intro, re.I)
        or "дилерский центр" in _opening_dealer_intro
    )
    _employee_proconsult_ctx = bool(
        re.search(
            r"\b(?:звоню|могу|готов|перезвон(?:ят|им)|проконсультир(?:ую|уем|ует)?)\s+"
            r"[^.!?]{0,40}проконсультир",
            head_brand,
            re.I,
        )
        or re.search(
            r"\bпроконсультир(?:ую|уем|ует)?\s+(?:вас|тебя|по\s+авто)",
            head_brand,
            re.I,
        )
    )
    _company_or_dealer_with_op_ctx = (
        _has_dealer_company_intro
        and (
            any(
                x in head_brand
                for x in (
                    "менеджер отдела продаж",
                    "отдел продаж",
                    "передали",
                    "интересовались",
                    "заявк",
                    "звоню",
                )
            )
            or _employee_proconsult_ctx
            or bool(
                re.search(r"\b(?:перезвоню|перезваниваю|перезвоним)\b", head_brand)
            )
        )
    )
    if _company_or_dealer_with_op_ctx and (
        "ренинг" in low or "викинг" in low or "чери" in low or "тенет" in low or "тэнет" in low
    ):
        return True

    if (
        "нас вот заявку" in low
        or "вы заявку отправляли" in low
        or "заявку отправляли" in low
        or "отправляли заявку" in low
        or "заявка от вас пришла" in low
        or ("от вас пришла" in low and "заявк" in low)
        or re.search(r"заявк\w*[^.!?]{0,50}пришл", low)
        or re.search(r"заявк\w*\s+отправлял", low)
    ):
        return True
    # Исходящий follow-up менеджера ОП: «обещал(а) отзвониться / звоню по оценке».
    if (
        ("обещал" in low or "обещала" in low)
        and ("отзвон" in low or "перезвон" in low)
        and (
            "менеджер отдела продаж" in low[:2200]
            or re.search(rf"\b{op_mgr}\b", low[:2200]) is not None
        )
        and any(x in low for x in ("викинг", "чери", "тенет", "тэнет", "дилерск", "заставн"))
    ):
        return True
    if (
        "мне передали" in low
        or "передали ваш контакт" in low
        or "передали ваш номер" in low
        or "мне передали ваш" in low
    ) and ("вот звоню" in low or "звоню" in low):
        return True
    # CRM-передача контакта без явного «звоню»: «менеджер отдела продаж ... передали Андре(й)».
    if (
        "менеджер отдела продаж" in low[:2200]
        and "передали" in low[:2200]
        and any(x in low[:2200] for x in ("викинг", "чери", "тенет", "тэнет", "заставн", "тольятт"))
    ):
        return True
    # CRM: STT часто даёт «передали вас» без «мне передали» + «звоню (вас) проконсультировать».
    if (
        ("передали вас" in low or re.search(r"\bпередал[аи]\s+вас\b", low))
        and (
            "звоню" in low
            or "звоним" in low
            or re.search(r"\bзвоню\s+вас\b", low)
            or re.search(r"\bзвоню\s+проконсультир", low)
        )
        and (
            re.search(rf"\b{op_mgr}\b", low[:2200])
            or re.search(r"\bменеджер\s+отдела\s+продаж\b", low[:2200])
        )
    ):
        return True
    if "запрос" in low and "оставляли" in low:
        return True
    if _is_op_outbound_lead_request_marker(low) and re.search(rf"\b{op_mgr}\b", low[:2200]):
        return True

    # АТС / сайт: «обратный звонок» + домен/бренд в префиксе — исходящий по лиду (поверхность для OP_OUT).
    head_site = low[:3200]
    if ("обратный звонок" in head_site or "заказан обратный" in head_site) and (
        "tenet" in head_site
        or "тенет" in head_site
        or "тэнет" in head_site
        or "сайт" in head_site
        or "заявк" in head_site
    ):
        return True

    # Первый контакт с начала разговора:
    # «[имя клиента], добрый день. Меня зовут <имя>, менеджер отдела продаж ... (дилер/бренд/локация)»
    # Без обязательного маркера «заявка с сайта».
    # 16703: «официальный дилер… меня зовут Анастасия. Анастасия, добрый день» — входящий, не исходящий.
    if _is_op_inbound_admin_direct_client_call(text):
        return False
    head = low[:1800]
    _greeting_re = r"(?:добрый\s+день|добрый\s+вечер|здравствуйте|доброе\s+утро)"
    has_greeting = any(g in head for g in ("здравствуйте", "добрый день", "добрый вечер", "доброе утро"))
    has_client_name_address = bool(
        re.search(rf"\b[а-яё]{{4,}}\s*,\s*{_greeting_re}\b", head)
        or re.search(rf"\b{_greeting_re}\s*,\s*[а-яё]{{4,}}\b", head)
    )
    has_manager_intro = (
        "меня зовут" in head
        and re.search(r"\bменеджер\s+отдела\s+продаж\b", head) is not None
    ) or (
        re.search(rf"\bэто\s+{op_mgr}\b", head, re.I) is not None
        and re.search(r"\bменеджер\s+отдела\s+продаж\b", head) is not None
    ) or (
        "меня зовут" in head
        and re.search(r"\bофициальн\w*\s+дилер\b", head)
        and re.search(rf"\b{op_mgr}\b", head)
    )
    has_dealer_marker = any(
        p in head
        for p in (
            "викинг",
            "заставн",
            "официальный дилер",
            "дилер",
            "тенет",
            "тэнет",
            "tenet",
            "чери",
            "chery",
        )
    )
    if has_greeting and has_client_name_address and has_manager_intro and has_dealer_marker:
        return True

    # 21232: «Алло, здравствуйте. Наталья, это/меня зовут Илья. Звоню вам с ...»
    # Без явного «менеджер отдела продаж», но с типичным исходящим CRM-началом ОП.
    has_named_greeting = bool(
        re.search(r"\b(?:алло[?.!,]?\s*)?(?:здравствуйте|добрый\s+(?:день|вечер))\b", head, re.I)
    )
    has_client_name_vocative_intro = bool(
        re.search(
            rf"\b[а-яё]{{3,20}}\s*,\s*(?:это\s+)?(?:меня\s+)?зовут\s+{op_mgr}\b",
            head,
            re.I,
        )
    )
    has_calling_phrase = bool(
        re.search(r"\bзвоню\s+вам\b", head, re.I) or re.search(r"\bзвоню\s+с\b", head, re.I)
    )
    if has_dealer_marker and has_named_greeting and has_client_name_vocative_intro and has_calling_phrase:
        return True

    _fake_allo_names = frozenset({"здравствуйте", "добрый", "день", "вечер", "да", "нет", "алло", "угу", "ага"})
    for m_allo in re.finditer(
        r"\bалло\s*[!.?,]?\s*(?:да\s*,\s*)?([а-яё]{4,})\s*[,.!?]",
        low[:1400],
        re.I,
    ):
        token = m_allo.group(1).lower()
        if token in _fake_allo_names or token in _STO_MANAGER_NAMES:
            continue
        if re.search(rf"\b{op_mgr}\b", low[:2200]) or (
            "меня " in low[:2200] and "зовут" in low[:2200]
        ):
            return True

    return False


def _has_op_crm_callback_markers(text: str) -> bool:
    """
    Перезвон менеджера ОП по уже начатой продаже (CRM), без служебных фраз «звонок с сайта».
    Типично: «…менеджер отдела продаж Тенет», «вчера с вами по машине общались»,
    либо STT без «менеджер отдела продаж»: «Алло, Фарид, здравствуйте. Это Илья … Тенет.
    Созванивались…», «…как ваши дела? Посмотрели… купили?» — исходящий CRM, не ОП вх. (см. правило 0op).
    """
    text = _normalize_stt_classification_artifacts(text or "")
    low = text.lower()[:10000]
    dealer = any(
        p in low
        for p in (
            "тенет",
            "тэнет",
            "tenet",
            "чери",
            "chery",
            "викинг",
            "викинги",
            "автосалон",
            "дилерск",
            "тольятти",
            "заставн",  # ул. Заставная / дилер на Заставной (короткие звонки без «Викинги» в STT)
            "официальный дилер",
            "тентольятти",  # STT: «Тенет Тольятти» склеено в одно слово (подстрока «тенет» не попадает)
        )
    )
    if not dealer:
        return False
    callback_hints = (
        "вчера с вами",
        "вчера мы с вами",
        "мы вчера с вами",
        "по машине общались",
        "по машине с вами общались",
        "по машину с вами общались",  # STT: винительный «машину» (9726)
        "по машину с вами",
        "по машину общались",
        "по машине с вами",  # STT обрывает «…общались» или мешает «Во по машине…»
        "во по машине",  # частая склейка STT вместо «по машине»
        "мы с вами по машине",
        "по автомобилю общались",
        "по автомобилю с вами общались",
        "машине с вами общались",
        "мы с вами общались по",
        "мы с вами общались",
        "мы с вами обсуждали",
        "обсуждали с вами",
        "обсуждали покупку",
        "перезваниваю по",
        "перезваниваю",
        "звоню вам по",
        "звоню по поводу",
        "звоню узнать",
        "интересовало ваше",
        "интересовало наше",
        "интересовало предложение",
        "интересовало ли",
        "заинтересовало",
        "что решили",
        "уже взяли",
        "уже купили",
        "всё уже взяли",
        "все уже взяли",
        "поздравляю с покупкой",
        "обратный звонок",
        "заказывали обратный",
        "в бронь",
        "на бронь",
        "брониров",
        "резерв",
        "продолжим по машине",
        "напомню по машине",
        "созванивались",
        "на прошлой неделе",
        "с прошлой неделе",
        "прошлой неделе",
        "хотел узнать",
        "хотела узнать",
        "купили автомобиль",
        "купили авто",
        "не купили",
        "по покупке",
        "планируете к нам",
        "подъехать к нам",
        # Перезвон по ранее обсуждённой машине (без точного «по машине общались»)
        "говорили о машине",
        "говорили про машину",
        "разговаривали о машине",
        "разговаривали про машину",
        "раньше говорили",
        "ранее говорили",
        "с вами говорили",
        "звоню уточнить",
        "звоню вам уточнить",
        "хотел уточнить",
        "хотела уточнить",
        "рассматриваете покупку",
        "рассматриваете приобретение",
        # Переговоры после контакта: модель/цена/скидка, согласование с руководителем (CRM, не заявка с сайта)
        "мы обсуждали",
        "обсуждали модель",
        "обсуждали цену",
        "обсуждали вариант",
        "другую модель",
        "другая модель",
        "по цене",
        "руководител",
        "уточню у",
        "уточу у",
        "скидк",
        "разниц",
        # Отложили покупку, договорённость перезвонить позже / клиент сам наберёт (CRM)
        "через две недели",
        "через 2 недели",
        "через неделю",
        "перезвонить через",
        "перезвоню через",
        "можно перезвонить",
        "перезвонить вам",
        "отложили покупку",
        "откладываем",
        "пока откладываем",
        "пока откладываете",
        "сам позвоню",
        "сама позвоню",
        "сами позвоним",
        # Согласование тест-драйва / выдачи авто с ресепшн (CRM, не обязательно «заявка с сайта» в речи)
        "тест-драйв",
        "тест драйв",
        "тестдрайв",
        "на тест-драйв",
        "на тест драйв",
        "запишем тест",
        "записать на тест-драйв",
        "записать на тест драйв",
        "договорились на тест",
        "пробн",
        "ресепшн",
        "ресепшен",
        # Исходящий «как дела / смотрели / купили» без «созванивались» (STT длинный преамбулой сдвигает фразы)
        "как ваши дела",
        "как у вас дела",
        "посмотрели",
        "минутка есть",
        "не купила",
        "ничего не купила",
        "ничего не купили",
        # Договорённость перезвона (выходные / позже)
        "перезвоню в субботу",
        "перезвоню в воскресенье",
        "в субботу или в воскресенье",
        "в субботу или воскресенье",
        "созвонимся в субботу",
        # Длинный исходящий консультационный звонок (расчёт кредита/госпрограмм/визитка), без фраз «заявка с сайта»
        "электронную визитку",
        "отправлю электронную визитку",
        "отправлю визитку",
        "госпрограмма",
        "госпрограмм",
        "кредит одобр",
        "кредитное одобр",
        "кредит одобрен",
        "одобрение кредит",
        # Лизинг / банк-госпрограмма (перезвон с расчётом), не маркер сайта
        "лизинг",
        "физлиц",
        "физ лиц",
        "лизинг для физ",
        "совкомбанк",
        "мы ещё думаем",
        "еще думаем",
        "ещё думаем",
        "пока думаем",
        "когда-то говорили",
        "когда то говорили",
        "один раз говорили",
    )
    if not any(p in low for p in callback_hints):
        return False
    if "менеджер отдела продаж" in low or re.search(r"\bменеджер\s+отдела\s+продаж\b", low):
        return True
    # «это Илья/…» может оказаться после длинного мусора STT — ищем по всему окну low.
    if re.search(
        r"\bэто\s+(илья|евгения|евгений|анастасию|анастасия|андрея|андрей|захаров|евдокимов|краснощек|краснощёк|калаев|калаева|щеголев)\b",
        low,
    ):
        return True
    # Короткий STT: есть «менеджер» и явное «звоню узнать» (перезвон по интересу), без полного «менеджер отдела продаж».
    if re.search(r"\bменеджер\b", low) and "звоню узнать" in low:
        return True
    return False


def _op_outbound_zvoniu_primary_consult_not_repeat(low: str) -> bool:
    """
    «Звоню» в первичном исходящем контакте (CRM-передача лида, консультация) — не маркер повторного follow-up.
    Голое in «звоню» ловило «звоню проконсультировать» и уводило OP_OUT в Прочие.
    """
    if "звоню" not in low:
        return False
    if re.search(r"\bзвоню\s+проконсультир", low):
        return True
    if "передали вас" in low or re.search(r"\bпередал[аи]\s+вас\b", low):
        return True
    head = (low or "")[:3500]
    # 10539: перезвон по пропущенному, «звоню, не мог дозвониться».
    if re.search(r"\bпропущен\w*\s+звонок\b", head) and re.search(
        r"\bзвоню\b", head
    ):
        return True
    if re.search(r"\bзвоню\b", head) and re.search(
        r"\bне\s+мог\w*[^.!?]{0,50}дозвон",
        head,
    ):
        return True
    return False


def _transcript_opening_low(text: str, max_lines: int = 3, fallback_chars: int = 600) -> str:
    """
    Начало транскрипта: строго первые max_lines строк (диаризация).
    Для сплошного STT без переносов — первые max_lines реплик по «—», иначе ~fallback_chars
    символов начала (≈3 реплики; не 900+, чтобы не ловить маркеры из середины консультации).
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", raw) if ln.strip()]
    if len(lines) >= 2:
        return "\n".join(lines[:max_lines]).lower()
    dash_parts = [p.strip() for p in re.split(r"\s*—\s*", raw) if p.strip()]
    if len(dash_parts) >= 2:
        return " — ".join(dash_parts[:max_lines]).lower()
    return raw.lower()[:fallback_chars]


# prior_touch и regex прошлого контакта — только в начале звонка (см. _transcript_opening_low).
_OP_REPEAT_PAST_CONTACT_OPENING_ONLY_MARKERS = frozenset(
    {
        "был в отпуске",
        "ранее общались",
        "мы с вами общались",
        "мы с вами разговаривали",
        "по машине с вами общались",
        "по машину с вами общались",
        "по машину с вами",
        "по машине общались",
        "общались по машине",
        "общались неделю назад",
        "неделю назад",
        "месяц назад",
        "месяц назад с вами общались",
        "созванивались",
        "как договорились",
        "были у нас",
        "смотрели у нас",
        "смотрели у нас машину",
        "смотрели у нас автомобиль",
        "проходили тест-драйв у нас",
        "проходили тест драйв у нас",
        "не так давно интересовались",
        "говорили",
    }
)


def _op_repeat_past_contact_regex_in_opening(open_low: str) -> bool:
    """Regex-маркеры прошлого контакта — только в начале транскрипта."""
    if not open_low:
        return False
    if re.search(
        r"\b(?:мы\s+)?с\s*ва(?:м|ми)[^.!?]{0,120}(?:общал|разговаривал|рассматрив|осматрив|смотрел)\w*",
        open_low,
    ):
        return True
    if re.search(r"\bобозначил", open_low) and re.search(r"\b(?:когда\s+)?последн\w+\s+раз\b", open_low):
        if re.search(r"\b(?:мы\s+)?с\s*ва(?:м|ми)\s+разговаривал", open_low):
            return True
    if "в прошлый раз" in open_low and "обозначили" in open_low:
        return True
    if re.search(r"\b(?:мы\s+)?с\s*ва(?:м|ми)[^.!?]{0,200}созванив\w*", open_low):
        return True
    if re.search(r"\bс\s*ва(?:м|ми)[^.!?]{0,120}(?:общал|разговаривал|рассматрив|осматрив|смотрел)\w*", open_low):
        return True
    return False


def _op_outbound_strong_redial_verb_not_repeat_followup(low: str, marker: str) -> bool:
    """
    «Перезвоню/перезваниваю» в обещании перезвона в конце первичного разговора или при CRM «передали вас»
    не считаем жёстким маркером повторного follow-up (иначе OP_OUT → Прочие).
    """
    if marker not in ("перезвоню", "перезваниваю") or marker not in low:
        return False
    head = low[:4000]
    if "передали вас" in head or re.search(r"\bпередал[аи]\s+вас\b", head):
        return True
    if marker == "перезвоню" and re.search(r"перезвоню\s+сейчас\s+вас\b", low):
        return True
    if re.search(r"ну\s+вс[её]\s*,\s*перезвоню", low):
        return True
    # «обсужу с руководителем / предложение и … перезвоню» — финал консультации, не шаблон «перезвон по заявке»
    if marker == "перезвоню" and re.search(
        r"(?:руководител|обсужу|обсудим|предложен|согласую)[\s\S]{0,280}\bперезвоню\b",
        low,
    ):
        return True
    return False


_OP_PURCHASE_CTX_MARKERS = (
    "оценк",
    "трейд",
    "trade in",
    "комплектац",
    "скидк",
    "кредит",
    "рассрочк",
    "стоимость автомобил",
    "первоначальн",
    "коммерческ",
    "в наличии",
    "тест-драйв",
    "тестдрайв",
    "покупк",
)


def _op_purchase_context_score(low: str) -> int:
    return sum(1 for p in _OP_PURCHASE_CTX_MARKERS if p in (low or ""))


def _is_op_primary_outbound_sales_opening(open_low: str) -> bool:
    """
    Первичная исходящая консультация ОП в начале (наст. время): «рассматриваете покупку»,
    не CRM repeat «рассматривали» в прошедшем.
    """
    if not (open_low or "").strip():
        return False
    if "менеджер отдела продаж" not in open_low and "отдела продаж" not in open_low:
        return False
    # 23680: несколько неудачных попыток дозвона по новой заявке не делают
    # первый состоявшийся разговор повторным CRM follow-up.
    if (
        re.search(
            r"\bзаявк\w*[^.!?]{0,80}(?:был[ао]?\s+от\s+вас|от\s+вас|оставлял\w*|отправлял\w*)",
            open_low,
        )
        and re.search(r"\bне\s+(?:мог\w*\s+)?(?:вам\s+)?дозвон\w*", open_low)
        and re.search(r"\b(?:машин\w*|автомобил\w*)\b", open_low)
    ):
        return True
    # 10539: шум до соединения, затем менеджер ОП + «меня интересует Tenet T7».
    if re.search(
        r"\bменя\s+интересует\b",
        open_low,
    ) and re.search(
        r"\b(?:tenet|тенет|tiggo|тиго|chery|чери|arrizo|машин\w*|автомобил)",
        open_low,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:рас|о)сматривал\w*\s+(?:автомобил|машин\w*|авто|к\s+покупк)",
        open_low,
    ):
        return False
    if re.search(r"\b(?:рас|о)сматрива\w*(?:ете|уете|уем)\b", open_low) and re.search(
        r"покупк", open_low[:1400]
    ):
        return True
    if re.search(r"\bинтересовал\w*[^.!?]{0,50}(?:покупк|автомобил)", open_low[:1400]):
        return True
    return False


def _zvonil_is_third_party_or_competitor_call(low: str) -> bool:
    """«один менеджер звонил … Солнечный» / другой салон — не повторный CRM-звонок Викингов (10291)."""
    for m in re.finditer(r"\bзвонил\w*\b", low or ""):
        ctx = (low or "")[max(0, m.start() - 110) : m.end() + 110]
        # 10539: «вчера вам авто звонил» — конкурент, не наш CRM follow-up.
        if re.search(r"\bавто\s+звонил\w*\b", ctx):
            return True
        if re.search(
            r"\b(?:вам|тебе)\s+(?:авто|друг\w*\s+салон|конкурент\w*|салон\w*)\s+звонил\w*",
            ctx,
        ):
            return True
        if re.search(
            r"(?:один\s+)?менеджер\s+звонил\w*",
            ctx,
        ) and not re.search(r"(?:у\s+нас|наш\w*\s+менеджер|викинг|заставн|тольятт)", ctx):
            if re.search(
                r"солнечн|друг\w+\s+салон|другом\s+дилер|конкурент|было\s+уже\s+где",
                ctx,
            ):
                return True
        if re.search(
            r"менеджер\s+звонил\w*[^.!?]{0,100}(?:солнечн|друг\w+\s+салон|другом\s+дилер)",
            ctx,
        ):
            return True
    return False


def _zvonil_is_our_repeat_followup(low: str) -> bool:
    """«звонил» как маркер нашего повторного контакта, не чужого менеджера/салона."""
    if not re.search(r"\bзвонил\w*\b", low or ""):
        return False
    if _zvonil_is_third_party_or_competitor_call(low):
        return False
    # Клиент звонил в салон («вы сегодня уже звонили») — первичный перезвон ОП, не CRM follow-up (11260).
    if re.search(r"\bвы\s+(?:сегодня\s+)?(?:уже\s+)?звонил\w*\b", low or ""):
        return False
    return bool(
        re.search(
            r"\b(?:я|мы)\s+[^.!?]{0,70}\bзвонил\w*\b|"
            r"\bзвонил\w*\s+(?:вам|тебе)\b|"
            r"\bвам\s+[^.!?]{0,55}\bзвонил\w*\b|"
            r"\b(?:вчера|ранее|снова|ещё\s+раз|еще\s+раз)[^.!?]{0,90}\bзвонил\w*\b|"
            r"\bзвонил\w*\s+[^.!?]{0,50}\b(?:вчера|ранее|снова)\b|"
            r"(?:наш\w*|у\s+нас)\s+менеджер\s+звонил\w*",
            low,
        )
    )


def _op_repeat_followup_blocks_primary_outbound_misc(text: str) -> bool:
    """Не занижать до Прочих: первичный OP_OUT с полной консультацией (ложный «звонил» в середине)."""
    low = (text or "").lower()
    open_low = _transcript_opening_low(text)
    # 10466 / 11099: явный прошлый контакт или возобновление после обрыва — повторный follow-up,
    # даже если дальше идёт полноценная консультация по комплектации/цене.
    if _is_op_dropped_call_repeat_resume(text):
        return False
    if _op_repeat_prior_car_automobile_talk_in_opening(open_low):
        return False
    if any(
        p in open_low
        for p in (
            "по машине с вами общались",
            "по машину с вами общались",
            "по машине общались",
            "мы с вами общались",
        )
    ):
        return False
    if not _is_op_primary_outbound_sales_opening(open_low):
        return False
    if not _has_op_outbound_surface_markers(text):
        return False
    return _op_purchase_context_score(low) >= 2


def _repeat_followup_marker_match(low: str, marker: str) -> bool:
    """
    Совпадение маркера повторного follow-up. При VIKINGI_CLASSIFY_REPEAT_FIX (config, по умол. да):
    «звонил» — только отдельное слово (не дозвонился/звонили/перезвонили);
    «говорили» — отдельное слово (не договорились).
    Откат поведения: export VIKINGI_CLASSIFY_REPEAT_FIX=0
    """
    try:
        from config import VIKINGI_CLASSIFY_REPEAT_FIX

        fix = bool(VIKINGI_CLASSIFY_REPEAT_FIX)
    except ImportError:
        fix = True
    if not fix:
        return marker in low
    if marker == "звонил":
        return _zvonil_is_our_repeat_followup(low)
    if marker == "говорили":
        # Не «вы говорили про комплектацию» в первичной консультации и не «договорились»
        return bool(
            re.search(
                r"\b(?:мы\s+)?(?:уже|ранее)\s+говорили\b|"
                r"\bговорили\s+(?:с\s+вами|вчера|на\s+прошлой)\b|"
                r"\bмы\s+с\s+вами\s+говорили\b|"
                r"\b(?:когда|как)\s+мы\s+говорили\b",
                low,
            )
        )
    if marker == "ранее общались":
        # Вопрос админа «с кем-то ранее общались из наших менеджеров?» — не CRM follow-up (9231).
        if re.search(
            r"\b(?:с\s+кем-?то\s+)?ранее\s+общал\w*[^.!?]{0,55}(?:из\s+наших\s+менеджер|менеджеров)",
            low,
        ):
            return False
        return bool(re.search(r"\bранее\s+общал\w*", low))
    if marker == "смотрели":
        # Не «на сайте везде смотрели, на форумах читали» — самостоятельный ресёрч, не визит к дилеру (9231).
        if re.search(
            r"(?:на\s+)?(?:сайте|форум|интернет|везде|каталог)[^.!?]{0,50}(?<![а-яё])смотрел",
            low,
        ) or re.search(r"(?<![а-яё])смотрел\w*[^.!?]{0,40}(?:форум|сайте|читали)", low):
            return False
        # Не срабатывать на «посмотрели» (подстрока «смотрели») в первичной консультации.
        return bool(re.search(r"(?<![а-яё])смотрели(?![а-яё])", low))
    if marker == "не планируете":
        # Не дубли STT вопроса «планируете? … не планируете?» в цикле первичной продажи.
        return bool(
            re.search(
                r"\bуже\s+не\s+планируете\b|"
                r"\bне\s+планируете\s+(?:покупать|брать|забирать|сейчас|в\s+ближайш)\b",
                low,
            )
        )
    if marker == "хотели подъехать":
        return bool(re.search(r"\bхотел\w*.{0,35}подъех\w*", low))
    if marker == "подъедете":
        # Не «14:30. Подъедете?» / «приезжайте, подъезжайте» в первичной продаже (10036).
        return bool(
            re.search(
                r"\b(?:узнать|уточнить|напомнить|во\s+сколько)[^.!?]{0,50}подъедете\b|"
                r"\bподъедете\s+ли\b",
                low,
            )
        )
    if marker == "планировали":
        # Не «как планировали покупать» в активной консультации.
        return bool(
            re.search(
                r"\b(?:вы\s+)?планировал\w*\s+(?:к\s+нам|приехать|подъехать|завтра|сегодня|на\s+этой\s+недел)\b",
                low,
            )
        )
    return marker in low


# Исходящий ОП: перезвон по личному обещанию менеджера клиенту — всегда «Прочие», не OP_OUT
# (даже при «заявка с сайта» / CRM-оверрайде в _v2_apply_product_rules).
# STT: «бещал» ≈ «обещал» (18395).
_CALLBACK_PROMISE_VERB = r"(?:обещал|бещал)[аи]?"
_CALLBACK_PROMISE_TO_CLIENT_RE = re.compile(
    rf"\b(?:вот\s+)?(?:как\s+и\s+)?{_CALLBACK_PROMISE_VERB}\s+(?:вам\s+)?(?:отзвониться|от\s*звониться|перезвонить|дозвониться|назвонить)\b",
    re.IGNORECASE,
)
_CALLBACK_PROMISE_TO_CLIENT_RE2 = re.compile(
    rf"\b{_CALLBACK_PROMISE_VERB}\s+вам\s+(?:сейчас\s+)?(?:отзвониться|перезвонить|дозвониться)\b",
    re.IGNORECASE,
)
_CALLBACK_PROMISE_ZVONIU_KAK_RE = re.compile(
    rf"\b(?:звоню|перезваниваю|позвонил[аи]?)\s+(?:вам\s+)?как\s+{_CALLBACK_PROMISE_VERB}\b",
    re.IGNORECASE,
)


def _is_op_dropped_call_repeat_resume(text: str, *, max_chars: int = 1500) -> bool:
    """
    Возобновление после обрыва: «мы с вами общались, но связь прервалась».
    STT: «с вам» вместо «с вами»; фраза часто в 4-й реплике после «—», вне _transcript_opening_low.
    """
    low = (text or "").lower()[:max_chars]
    if not low:
        return False
    if re.search(
        r"\b(?:мы\s+)?(?:с\s+)?ва(?:м|ми)\s+(?:общал\w*|разговаривал\w*)"
        r"[^.!?]{0,100}(?:связь|связи|связью|линия|линии)[^.!?]{0,40}прервал\w*",
        low,
    ):
        return True
    if re.search(r"\b(?:с\s+)?ва(?:м|ми)[^.!?]{0,80}прервал\w*", low) and re.search(
        r"\b(?:мы\s+)?(?:с\s+)?ва(?:м|ми)[^.!?]{0,120}(?:общал\w*|разговаривал\w*)",
        low,
    ):
        return True
    return False


def _is_op_outbound_callback_promise_repeat(low: str) -> bool:
    """Менеджер отрабатывает обещание перезвона клиенту — повторный контакт, не первичный OP_OUT."""
    low = re.sub(r"\bбещал([аи]?)\b", r"обещал\1", (low or "").lower())
    if _CALLBACK_PROMISE_TO_CLIENT_RE.search(low) or _CALLBACK_PROMISE_TO_CLIENT_RE2.search(low):
        return True
    if _CALLBACK_PROMISE_ZVONIU_KAK_RE.search(low):
        return True
    # STT: слова разъехались («обещал … отзвониться»).
    if re.search(r"\b(?:обещал|бещал)[аи]?[\s,.]{0,24}\bотзвон", low, re.IGNORECASE):
        return True
    # 27004: «обещал в понедельник дозвониться» — повторный follow-up (не первичный OP_OUT).
    if re.search(r"\b(?:обещал|бещал)[аи]?[\s\S]{0,40}\bдозвон\w*\b", low, re.IGNORECASE):
        return True
    return False


_OP_REPEAT_VY_HOTELI_AFTER_HANDOFF_MARKERS = frozenset(
    {
        "вы хотели",
        "вот хотели",
        "хотели к нам",
        "хотели приехать",
        "хотели подъехать",
        "хотели завтра подъехать",
    }
)


def _is_op_inbound_admin_or_avito_handoff(text: str) -> bool:
    """
    Входящий через администратора / стойку или лид с Авито до менеджера ОП.
    «Вы хотели…» после такого перевода — перефраз клиента менеджером, не CRM follow-up (11740).
    """
    if _is_op_inbound_with_reception_gatekeeper(text):
        return True
    head = (text or "").lower()[:3200]
    has_avito = any(x in head for x in ("авито", " avito", "aвиit"))
    admin_ctx = "администратор" in head[:1800] or "ресепш" in head[:1800] or "reception" in head[:1800]
    transfer_ctx = any(
        p in head[:1800]
        for p in ("перевожу", "переводите", "переведу", "переключаю", "соединю")
    )
    if admin_ctx and transfer_ctx:
        return True
    if has_avito and admin_ctx:
        return True
    if has_avito and transfer_ctx and re.search(
        r"\b(?:официальн\w*|специальн\w*)\s+(?:дижер|дилер)\b", head[:1200]
    ):
        return True
    if has_avito and "звонок из авито" in head[:1200]:
        return True
    return False


def _op_repeat_prior_car_automobile_talk_in_opening(open_low: str) -> bool:
    """Прошлый контакт по машине/автомобилю в opening (STT: «мы с вам … по автомобилю общались»)."""
    if not open_low:
        return False
    return bool(
        "по машине общались" in open_low
        or re.search(r"\bпо\s+машин\w+\s+с\s+ва(?:м|ми)[^.!?]{0,80}общал\w*", open_low)
        or re.search(r"\bпо\s+автомобил\w+[^.!?]{0,50}общал\w*", open_low)
        or re.search(r"\bмы\s+с\s+вам\w*[^.!?]{0,60}общал\w*", open_low)
        or re.search(r"\bкак-?то\s+с\s+вами\b", open_low)
        or re.search(r"\b(?:мы\s+)?с\s+вами\s+как-?то\s+по\s+машин\w+[^.!?]{0,40}общал\w*", open_low)
        or re.search(r"\bкак-?то\s+по\s+машин\w+[^.!?]{0,40}общал\w*", open_low)
    )


def _is_op_kak_to_po_mashine_repeat_followup(open_low: str) -> bool:
    """«мы с вами как-то по машине общались» — устойчивый CRM follow-up → Прочие (18058)."""
    if not open_low:
        return False
    return bool(
        re.search(r"\b(?:мы\s+)?с\s+вами\s+как-?то\s+по\s+машин\w+[^.!?]{0,40}общал\w*", open_low)
        or re.search(r"\bкак-?то\s+по\s+машин\w+[^.!?]{0,40}общал\w*", open_low)
        # 19449: срок прошлого контакта стоит между «как-то» и «с вами»:
        # «мы как-то дня два назад с вами по машине общались».
        or re.search(
            r"\b(?:мы\s+)?как-?то[^.!?]{0,45}\bс\s+вами\s+по\s+машин\w+"
            r"[^.!?]{0,40}\bобщал\w*",
            open_low,
        )
        # 28475: «как-то машиной интересовались» — повторный CRM follow-up без «общались».
        or re.search(
            r"\bкак-?то\s+(?:машин\w*|автомобил\w*)\s+интересовал\w*\b",
            open_low,
            re.I,
        )
        or re.search(
            r"\bкак-?то\s+интересовал\w*[^.!?]{0,40}\b(?:машин\w*|автомобил\w*)\b",
            open_low,
            re.I,
        )
    )


def _is_op_crm_status_callback_without_immediate_pitch(open_low: str) -> bool:
    """
    CRM-перезвон по итогу прошлого разговора: «по машине общались» + «звоню узнать» без немедленного питча.
    15694: «звоню узнать, планируете?» → Прочие; 13019: сразу «Смотрите, прайсовая…» → не status-check.
    Не опирается на «есть в планах» / «кредит» — только структура opening после «звоню узнать».
    """
    if not open_low:
        return False
    if not _op_repeat_prior_car_automobile_talk_in_opening(open_low):
        return False
    m = re.search(r"\bзвоню\s+(?:узнать|уточнить|спросить)\b", open_low)
    if not m:
        return False
    tail = open_low[m.end() : m.end() + 500]
    if re.search(
        r"\b(?:смотрите|если\s+говорим\s+про|прайсов|скидк|трейд|комплектац|"
        r"стоимост\w*\s+автомобил|в\s+наличии|от\s+\d[\d\s]{3,}\s*(?:000|₽|руб))",
        tail,
    ):
        return False
    return True


def _is_op_repeat_month_ago_status_check_opening(open_low: str) -> bool:
    """
    Явный повторный CRM-обзвон в opening:
    «месяц/неделю назад ... общались по машине» + проверка статуса интереса к покупке.
    Должен оставаться repeat-follow-up даже при sales-контексте в хвосте.
    """
    if not (open_low or "").strip():
        return False
    dealer_ctx = any(
        p in open_low
        for p in (
            "викинг",
            "дилерск",
            "тольятт",
            "заставн",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "менеджер отдела продаж",
            "отдела продаж",
        )
    )
    if not dealer_ctx:
        return False
    prior_contact = bool(
        re.search(
            r"\b(?:месяц|недел\w*|дн(?:я|ей))\s+назад\b[^.!?]{0,120}\bобщал\w*"
            r"[^.!?]{0,80}\b(?:по\s+машин\w+|по\s+автомобил\w+)\b",
            open_low,
            re.I,
        )
        or re.search(
            r"\bобщал\w*[^.!?]{0,90}\b(?:по\s+машин\w+|по\s+автомобил\w+)\b"
            r"[^.!?]{0,90}\b(?:месяц|недел\w*|дн(?:я|ей))\s+назад\b",
            open_low,
            re.I,
        )
    )
    if not prior_contact:
        return False
    return bool(
        re.search(
            r"\b(?:рассматриваете|не\s+рассматриваете|в\s+планах)\b[^.!?]{0,70}\b"
            r"(?:покупк\w*|автомобил\w*|машин\w*)\b",
            open_low,
            re.I,
        )
        or re.search(
            r"\bкогда\b[^.!?]{0,60}\b(?:позвонить|созвон\w*|перезвон\w*)\b",
            open_low,
            re.I,
        )
    )


def _is_op_manager_handoff_client_base_followup(open_low: str) -> bool:
    """
    Повторный CRM-обзвон при передаче клиентской базы между менеджерами.

    Пример: «с Анастасией общались... перевелась в другую организацию...
    её клиентов обзваниваю... звоню узнать, есть в планах покупка».
    Такой кейс — follow-up (Прочие), а не первичный OP_OUT.
    """
    if not (open_low or "").strip():
        return False
    dealer_ctx = (
        ("менеджер отдела продаж" in open_low or "отдела продаж" in open_low)
        and any(
            p in open_low
            for p in (
                "викинг",
                "тольятт",
                "заставн",
                "чери",
                "тенет",
                "тэнет",
                "tenet",
                "дилерск",
            )
        )
    )
    if not dealer_ctx:
        return False
    prior_manager_contact = (
        "общал" in open_low
        and "с " in open_low
        and (
            "с менедж" in open_low
            or any(n in open_low for n in ("анастас", "евгени", "иль", "андре", "захаров", "калаев"))
        )
    )
    staff_change = (
        "перевел" in open_low
        or "перевёл" in open_low
        or "увол" in open_low
        or "переш" in open_low
    ) and ("друг" in open_low and "организац" in open_low)
    client_base_followup = (
        "клиент" in open_low
        and ("обзванива" in open_low or "перезванива" in open_low)
    )
    status_check = (
        ("звоню узнать" in open_low or "звоню уточнить" in open_low or "звоню спросить" in open_low)
        and ("в планах" in open_low or "планируете" in open_low or "покупк" in open_low)
    )
    return prior_manager_contact and staff_change and client_base_followup and status_check


def _is_op_deferred_decision_repeat_opening(open_low: str) -> bool:
    """
    Повторный звонок ОП по отложенному решению клиента.

    19134: STT «по … с общались, вот звоню» + «пока передумали»,
    «на какой срок решили отложить», «изменились планы».
    """
    if not (open_low or "").strip():
        return False
    dealer_manager = bool(
        re.search(r"\bменеджер\w*\s+отдел\w*\s+продаж\w*\b", open_low)
        and any(
            marker in open_low
            for marker in ("викинг", "дилер", "тольятт", "заставн", "чери", "тенет", "тэнет", "tenet")
        )
    )
    if not dealer_manager:
        return False
    prior_contact = bool(
        re.search(r"\bобщал\w*[^.!?]{0,45}\b(?:вот\s+)?звоню\b", open_low)
        or re.search(r"\bс\s+общал\w*[^.!?]{0,45}\b(?:вот\s+)?звоню\b", open_low)
    )
    if not prior_contact:
        return False
    deferred_status = bool(
        re.search(
            r"\b(?:пока\s+)?передумал\w*\b|"
            r"\b(?:пока\s+)?тормозн\w*\b|"
            r"\bрешил\w*[^.!?]{0,45}\botлож\w*\b|"
            r"\bна\s+какой\s+срок[^.!?]{0,60}\botлож\w*\b|"
            r"\bizменил\w*\s+план\w*\b",
            open_low,
            re.IGNORECASE,
        )
    )
    return deferred_status


def _is_op_crm_prior_interest_status_repeat_opening(open_low: str) -> bool:
    """
    CRM follow-up status-check в opening — Прочие, не OP_OUT (правило 0w):
    - 17256: «некоторое время назад интересовались… вопрос ещё актуален»
    - 17232: «хотел уточнить, вопрос с автомобилем решился?»
    - 16967: «по машине с вами как-то общались… звоню узнать, заинтересовало наше предложение»
    - 17013: «хотела узнать, вернулись в город?» — нетипичный CRM follow-up
    """
    if not (open_low or "").strip():
        return False
    dealer = any(
        p in open_low
        for p in (
            "викинг",
            "дилерск",
            "тольятт",
            "заставн",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "менеджер отдела продаж",
            "отдела продаж",
            "салон",
        )
    )
    if not dealer:
        return False
    # 17825: STT «по машине с вами акуальнобща?» = «по машине с вами актуально?».
    # Это проверка статуса прошлого интереса, а не первичный процесс продажи.
    if re.search(
        r"\bпо\s+(?:машин|автомобил)\w*[^.!?]{0,80}\bс\s+вами\b"
        r"[^.!?]{0,80}\b(?:актуальн|акуальн)\w*",
        open_low,
        re.I,
    ):
        return True
    # 17013: «хотела узнать, вернулись в город?» — нетипичный исходящий CRM follow-up.
    if (
        re.search(r"\b(?:хотел|хотела)\s+узнать\b", open_low)
        and re.search(r"\bвернул\w*[^.!?]{0,50}(?:в\s+город|из\s+город)", open_low)
    ):
        return True
    # 21339: пост-сделочный исходящий статус-чек
    # «хотела уточнить, как у вас с автомобилем, все ли в порядке».
    if (
        re.search(r"\b(?:хотел|хотела)\s+(?:узнать|уточнить)\b", open_low, re.I)
        and re.search(
            r"\bкак\s+у\s+ва(?:с|м)\b[^.!?]{0,100}\b(?:с|по)\s+(?:автомобил|машин)\w*",
            open_low,
            re.I,
        )
        and re.search(
            r"\b(?:вс[её]\s+ли\s+в\s+порядк\w*|вс[её]\s+нормальн\w*|вс[её]\s+ли\s+хорошо)\b",
            open_low,
            re.I,
        )
    ):
        return True
    # 16967: прошлый контакт по машине + «звоню узнать, заинтересовало наше предложение».
    if (
        _op_repeat_prior_car_automobile_talk_in_opening(open_low)
        and re.search(r"\bзвоню\s+(?:узнать|уточнить|спросить)\b", open_low)
        and (
            re.search(r"\bзаинтересовал\w*[^.!?]{0,80}(?:наше|наш[её])\s+предложени", open_low)
            or ("не заинтересовало" in open_low and "заинтересовало" in open_low)
        )
    ):
        return True
    clarify = bool(re.search(r"\b(?:хотел|хотела)\s+уточнить\b", open_low))
    if not clarify:
        return False
    still_actual = bool(
        re.search(r"\bвопрос\b[\s\S]{0,120}\b(?:еще|ещё)\s+актуален", open_low)
        or "вопрос ещё актуален" in open_low
        or "вопрос еще актуален" in open_low
        or "вопрос актуален" in open_low
    )
    resolved = bool(
        re.search(r"\bвопрос\b[\s\S]{0,120}решил\w*", open_low)
        or re.search(r"\bрешени\w*\s+приняли\b", open_low)
        or re.search(r"\bприняли\s+решени", open_low)
        or "какое-то решение" in open_low
    )
    prior_interest = bool(
        re.search(
            r"\b(?:не\s+так\s+давно|некотор\w*\s+врем\w*\s+назад)\b[\s\S]{0,180}\bинтересовал",
            open_low,
        )
        or re.search(r"\bинтересовались\s+возможност", open_low)
    )
    if prior_interest and still_actual:
        return True
    if resolved:
        return True
    return False


def _is_op_crm_prior_car_talk_repeat_followup_opening(open_low: str) -> bool:
    """
    Opening CRM follow-up: прошлый контакт по машине/авто + «звоню узнать» + купили/выбрали — Прочие (10466, 12396, 12399).
    Обрезок STT «как-то с вами» без «общались» — тоже follow-up (12400).
    Без требования _is_outbound: мусор STT до «алло» или «э-э» перед именем клиента.
    """
    if not open_low or not re.search(r"\bалло\b", open_low, re.I):
        return False
    if not ("менеджер отдела продаж" in open_low or "отдела продаж" in open_low):
        return False
    dealer = any(
        p in open_low
        for p in (
            "викинг",
            "тольятт",
            "заставн",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "дилерск",
        )
    )
    if not dealer:
        return False
    if re.search(r"\bкак-?то\s+с\s+вами\b", open_low):
        return True
    if _is_op_kak_to_po_mashine_repeat_followup(open_low):
        return True
    if not _op_repeat_prior_car_automobile_talk_in_opening(open_low):
        return False
    if not re.search(r"\bзвоню\s+(?:узнать|уточнить|спросить)\b", open_low):
        return False
    if not any(
        p in open_low
        for p in ("купили", "выбрали", "рассматриваете", "не рассматриваете")
    ):
        return False
    return True


def _is_op_repeat_followup_outbound(text: str) -> bool:
    """
    Повторный исходящий follow-up ОП (не первичный контакт):
    «смотрели/проходили тест-драйв у нас», «какие планы», «что решили/надумали», «купили?»;
    перезвон по обещанию («вот обещал вам отзвониться», «звоню как обещал»).
    Такие звонки относим в Прочие (OTHER), а не в OP_OUT.
    """
    low = (text or "").lower()[:9000]
    if not low:
        return False
    open_low = _transcript_opening_low(text)
    if _is_op_inbound_admin_handoff_to_op_manager(text):
        return False
    if _is_classic_sto_inbound_dispatcher_booking_intake(text):
        return False
    # Перезвон салона по пропуску / «звоночек был» обычно первичный исходящий контакт, не follow-up.
    # Исключение 26187: после явной прошлой консультации клиент подтверждает
    # «мне все рассказали, мы пока думаем» — это статусный повторный контакт.
    if _is_op_dealer_ringback_intro(text):
        if _is_op_dealer_ringback_post_consultation_status_followup_other(text):
            return True
        return False
    if _is_op_deferred_decision_repeat_opening(open_low):
        return True
    # 24879: «месяц назад общались по машине» + status-check по покупке.
    if _is_op_repeat_month_ago_status_check_opening(open_low):
        return True
    # 26921: прошлый контакт у другого менеджера + передача базы клиентов + статусный обзвон.
    if _is_op_manager_handoff_client_base_followup(open_low):
        return True
    # 19572: «машиной интересовались, тест-драйв проходили; звоню узнать,
    # заинтересовались» — повторный CRM-звонок, а не первичный ОП_исх.
    if _is_op_outbound_crm_test_drive_followup_redial(text):
        return True
    # 26506: после визита к менеджеру («к Илье подъезжали», «посмотрели машину»)
    # статус-check «звонил узнать, понравилась?» — повторный follow-up, не ОП_исх.
    if _is_op_post_showroom_visit_status_followup(text):
        return True
    # 21504: исходящий follow-up после прошлого контакта
    # («общались с вами как-то», «планы поменялись», «покупка не раньше ...»).
    if (
        re.search(r"\bобщал\w*\s+с\s+вами\s+как-?то\b", low, re.I)
        and re.search(r"\bплан\w*\s+поменял\w*\b", low, re.I)
        and re.search(r"\bпокупк\w*\b[^.!?]{0,80}\bне\s+раньше\b", low, re.I)
    ):
        return True
    # 20819: «вы сказали в начале августа, может быть, рассмотрите покупку.
    # звоню узнать, рассматриваете / не рассматриваете» — явный повторный follow-up.
    if (
        re.search(
            r"\bвы\s+сказал[аи]\b[^.!?]{0,120}\bв\s+(?:начале|середине|конце)\s+"
            r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
            open_low,
            re.I,
        )
        and re.search(r"\bзвоню\s+(?:вам\s+)?(?:узнать|уточнить|спросить)\b", open_low, re.I)
        and re.search(
            r"\b(?:рассматриваете|рассмотрите)\b[^.!?]{0,50}\b(?:покуп|не\s+рассматриваете)\b",
            low,
            re.I,
        )
    ):
        return True
    # 19138: служебная реплика менеджера до «алло» вытеснила явную повторность
    # из короткого opening; ищем устойчивую фразу в расширенном префиксе.
    if _is_op_kak_to_po_mashine_repeat_followup(low[:2400]):
        return True
    # 11099: «Мы с вам общались, но связь с вам прервалась» — повторный контакт после обрыва → Прочие.
    if _is_op_dropped_call_repeat_resume(text):
        return True
    # 16510: «[имя], ещё раз добрый день» + «Евгений Викинги» — повторный исх. ОП → Прочие.
    if re.search(
        r"(?:^|[.!?]\s*)(?:алло[.,]?\s*)?[а-яё]{3,}(?:\s+[а-яё]{2,})?\s*,?\s*ещ[её]\s+раз\s+"
        r"(?:здравствуйте|добрый\s+(?:день|вечер))\b",
        open_low,
    ) and re.search(
        r"\b(евгений|илья|анастасия|андрей|захаров|евдокимов|краснощеков|калаев|калаева|щеголев)\b"
        r"[^.!?]{0,50}\bвикинг",
        open_low,
    ):
        return True
    # 27863: «Добрый день ещё раз. Андрей, Тольятти. Общались с вами.»
    # Повторный контакт ОП даже без явного «Викинги» в коротком opening.
    if (
        "общались с вами" in open_low
        and ("добрый день ещё раз" in open_low or "добрый день еще раз" in open_low)
        and re.search(
            r"\b(?:андрей|евгений|илья|анастасия|захаров|евдокимов|краснощек(?:ов)?|калаев|калаева|щеголев)\b",
            open_low,
            re.I,
        )
        and ("тольятт" in open_low or "викинг" in open_low or "дилерск" in open_low)
    ):
        return True
    if _is_op_crm_prior_car_talk_repeat_followup_opening(open_low):
        return True
    # 27985: «мы с вами договаривались созвониться в ближайшие два дня...»
    # Это явный повторный follow-up по прошлой договоренности, даже если дальше
    # идет обсуждение условий сделки.
    if re.search(r"\b(?:мы\s+с\s+вами\s+)?договаривал(?:ись|ся)\b[\s\S]{0,80}\bсозвон", open_low):
        return True
    if _is_op_crm_prior_interest_status_repeat_opening(open_low) or _is_op_crm_prior_interest_status_repeat_opening(
        low[:1200]
    ):
        return True
    # 26924: «машиной у нас интересовались ранее, звоню узнать, есть в планах покупка» —
    # повторный CRM follow-up, даже если дальше менеджер переходит к презентации.
    if (
        re.search(r"\bинтересовал\w*[^.!?]{0,60}\bранее\b", open_low, re.I)
        and re.search(r"\bзвоню\s+(?:узнать|уточнить|спросить)\b", open_low, re.I)
        and re.search(r"\b(?:в\s+планах|планируете)\b[^.!?]{0,80}\b(?:покупк\w*|автомобил\w*)\b", open_low, re.I)
    ):
        return True
    if _is_op_crm_status_callback_without_immediate_pitch(open_low):
        return True
    # Первичный исходящий по лиду с сайта/CRM
    head_lead = low[:3200]
    crm_primary_handoff = bool(
        re.search(r"\bпередал\w*", head_lead)
        and re.search(r"\bзвонил\w*", head_lead)
        and any(p in head_lead for p in ("хотел", "приехать", "подъехать", "купить", "машин", "автомобил"))
    )
    if _has_op_outbound_surface_markers(text) and (
        re.search(r"заявк\w*\s+отправлял", head_lead)
        or "нас вот заявку" in head_lead
        or "вы заявку отправляли" in head_lead
        or crm_primary_handoff
        or (
            re.search(r"\bменя\s+[а-яё]{2,}\s+зовут\b", head_lead)
            and ("компания" in head_lead or "викинг" in head_lead)
            and ("викинг" in head_lead or "чери" in head_lead or "тенет" in head_lead or "tenet" in head_lead)
            and ("заявк" in head_lead or crm_primary_handoff)
        )
    ):
        return False
    # Явный входящий ОП: клиент первым задаёт тему — маркеры повторного исходящего не применяем (10906).
    if (
        not _is_outbound(text)
        and not _has_op_outbound_surface_markers(text)
        and not _is_crm_ats_outbound_lead_prefix(text)
        and _is_client_initiated_after_greeting(text)
    ):
        return False
    # B2B-первичный входящий/перевод в ОП: лизинговый менеджер уточняет, «клиент уже общался?».
    # Это не CRM follow-up и не повод уводить в OTHER.
    if (
        "лизинг" in low
        and "менеджер лизинговой компании" in low
        and re.search(r"\bклиент\b[^.!?]{0,80}\bобщал", low)
        and not any(
            p in low
            for p in (
                "перезваниваю",
                "звоню как обещал",
                "обещал вам отзвониться",
                "вчера общались",
                "в прошлый раз",
                "снова",
                "ещё раз",
                "еще раз",
            )
        )
    ):
        return False
    # Обещание перезвона менеджера + самопредставление ОП в начале — повторный контакт → Прочие,
    # даже без «Викинги» в STT (раньше отсекалось условием dealer ∧ исходящий ниже).
    head_rb = low[:1500]
    if _is_op_outbound_callback_promise_repeat(low) and (
        re.search(
            r"\bэто\s+(?:илья|ильянет|ильясет|евгения|евгений|анастасию|анастасия|андрея|андрей|"
            r"захаров|евдокимов|краснощеков|краснощёков|краснощек|краснощёк|калаев|калаева|щеголев)\b",
            head_rb,
            re.IGNORECASE,
        )
        or "менеджер отдела продаж" in head_rb
    ):
        return True
    # Менеджер после отпуска / паузы уточняет прошлый интерес — повторный follow-up → Прочие.
    # Раньше отсекалось условием dealer ∧ _is_outbound (STT «Ильянет», длинное начало → не исходящий).
    head_fw = low[:4000]
    dealer_fw = any(
        p in head_fw
        for p in (
            "викинг",
            "дилерск",
            "официальн",
            "автосалон",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "заставн",
            "тольятт",
        )
    )
    _op_repeat_outcome_markers = (
        "хотел узнать",
        "хотела узнать",
        "приезжали",
        "приезжал",
        "купили",
        "выбрали",
        "купили автомобиль",
        "купили авто",
        "выбрали машину",
        "не купили",
    )
    if dealer_fw and "был в отпуске" in head_fw:
        if any(m in head_fw for m in _op_repeat_outcome_markers):
            return True
    # 11125: «приезжали, машину смотрели» + «хотел узнать, купили?» — повторный follow-up после визита;
    # до STO-защиты, чтобы посторонние фразы в конце разговора не гасили маркеры в начале.
    if dealer_fw and any(m in open_low for m in ("приезжали", "приезжал")):
        if (
            "смотрел" in open_low
            and any(m in open_low for m in ("хотел узнать", "хотела узнать"))
            and any(m in open_low for m in ("купили", "не купили", "выбрали"))
        ):
            return True
    # 13708: «были у вас … смотрели, оценивали машину» + «хотел узнать, переговорили…» — CRM follow-up;
    # до STO-защиты (ложные «сделать то есть» / «госномер» в хвосте trade-in).
    if dealer_fw and "были у вас" in open_low:
        if (
            re.search(r"(?<![а-яё])смотрел\w*", open_low)
            and re.search(r"оценивал\w*", open_low)
            and any(m in open_low for m in ("хотел узнать", "хотела узнать"))
        ):
            return True
    purchase_ctx_score = _op_purchase_context_score(low)
    sales_followup_context = (
        purchase_ctx_score >= 2
        and ("менеджер отдела продаж" in low or "отдела продаж" in low)
    ) or (
        purchase_ctx_score >= 3
        and re.search(r"\bменя\s+[а-яё]{2,}\s+зовут\b", low[:2800])
        and dealer_fw
    )
    # Маркеры прошлого контакта — только в первых 2–3 строках/предложениях (не в середине первичного звонка).
    if _op_repeat_past_contact_regex_in_opening(open_low):
        if not _is_sto_reglam_to_clarification_not_secretary_misc(low) and (
            not sales_followup_context
            or _is_op_crm_status_callback_without_immediate_pitch(open_low)
        ):
            if any(
                p in open_low
                for p in (
                    "викинг",
                    "дилерск",
                    "официальн",
                    "автосалон",
                    "чери",
                    "chery",
                    "тенет",
                    "тэнет",
                    "tenet",
                    "тольятт",
                    "менеджер отдела продаж",
                    "отдела продаж",
                )
            ) or any(
                p in low[:3200]
                for p in (
                    "викинг",
                    "дилерск",
                    "тольятт",
                )
            ):
                return True
    # Менеджер ОП в начале говорит: «(мы с вами) рассматривали/осматривали автомобиль/машину/к покупке» —
    # устойчивая follow-up-формулировка; настоящее время («рассматриваете») — вопрос первичной консультации.
    if re.search(
        r"\b(?:рас|о)сматривал\w*\s+(?:автомобиль|машин\w*|авто|к\s+покупк\w*)",
        open_low,
    ):
        if not (
            _is_sto_reglam_to_clarification_not_secretary_misc(low)
            or _is_classic_sto_inbound_dispatcher_booking_intake(text)
        ):
            return True
    # «Не так давно интересовались ценой…» / «хотел уточнить … решение приняли» — CRM follow-up (7836);
    # до outbound: иначе при OP_IN и ложном inbound ранний return гасит has_repeat.
    _repeat_dealer_ctx = (
        "викинг",
        "дилерск",
        "официальн",
        "автосалон",
        "чери",
        "chery",
        "тенет",
        "тэнет",
        "tenet",
        "менеджер отдела продаж",
        "отдела продаж",
    )
    if re.search(r"\bне\s+так\s+давно\b[\s\S]{0,120}\bинтересовал", open_low):
        if any(p in open_low for p in _repeat_dealer_ctx) and not _is_sto_reglam_to_clarification_not_secretary_misc(
            low
        ):
            return True
    if re.search(r"\b(?:хотел|хотела)\s+уточнить\b", open_low) and re.search(
        r"\b(?:какое-то\s+)?решени\w*[\s,.:;-]{0,24}\bприняли\b|\bприняли\s+решени",
        open_low,
    ):
        if any(p in open_low for p in _repeat_dealer_ctx) and not _is_sto_reglam_to_clarification_not_secretary_misc(
            low
        ):
            return True
    # Защита от ложного STO при ОП-sales диалоге (в STT может встретиться «приемлемо» и т.п.).
    if _has_sto_substantive_service_intent(low) and not sales_followup_context:
        return False
    dealer = any(
        p in low
        for p in (
            "викинг",
            "дилерск",
            "официальн",
            "автосалон",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "заставн",
            "тольятт",
        )
    )
    if not dealer or not _is_outbound(text):
        return False
    # Повторный исходящий: «по машину/машине с вами общались» + подъезд/время визита (9726).
    if re.search(r"\bпо\s+машин\w+\s+с\s+ва(?:м|ми)[^.!?]{0,80}общал\w*", low) and re.search(
        r"\b(?:хотел\w*.{0,35}подъех\w*|подъед\w*|во\s+сколько\s+вас\s+ждать)\b",
        low,
    ):
        return True
    # Явное «обещал(а) отзвониться / звоню как обещал» — повторный CRM-контакт → Прочие,
    # даже если дальше в речи идёт консультация по комплектации/кредиту (см. golden op_out_callback_promise_other).
    if _is_op_outbound_callback_promise_repeat(low):
        return True
    # Жёсткие маркеры повторности: при наличии считаем звонок повторным follow-up
    # и относим в Прочие независимо от прочих OP-маркеров.
    # «звоню» без уточнения — не использовать (ложно на «звоню проконсультировать»); только явные follow-up-формы.
    has_prior_contact_phrase = bool(
        re.search(r"\bпо\s+машин\w+\s+с\s+ва(?:м|ми)[^.!?]{0,80}общал\w*", open_low)
        or "мы с вами общались" in open_low
        or "по машине общались" in open_low
    )
    strong_repeat_markers = (
        "вчера общались",
        "звонил",
        "перезваниваю",
        "перезвоню",
    )
    for m in strong_repeat_markers:
        if not _repeat_followup_marker_match(open_low, m):
            continue
        if _op_outbound_strong_redial_verb_not_repeat_followup(low, m):
            continue
        if (
            m == "звонил"
            and sales_followup_context
            and _is_op_primary_outbound_sales_opening(open_low)
            and not has_prior_contact_phrase
        ):
            continue
        return True
    if not sales_followup_context and "звоню" in open_low and not _op_outbound_zvoniu_primary_consult_not_repeat(open_low):
        if re.search(
            r"\bзвоню\s+(?:узнать|уточнить|спросить|проверить|вам\s+по|по\s+поводу|напомнить|ещё\s+раз|еще\s+раз|снова)\b",
            open_low,
        ):
            return True
    if re.search(r"\bвчера\b[\s\S]{0,80}\b(?:у\s+нас\s+)?были\b", open_low):
        return True
    if re.search(r"\bу\s+нас\b[\s\S]{0,60}\bбыли\b", open_low):
        return True
    if re.search(
        r"\b(?:снова|знова|с\s+нова)\b[\s,.:;-]*(?:это|я|менеджер)?[\s,.:;-]*"
        r"(?:[а-яё]{1,14}[\s,.:;-]+){0,3}"
        r"(евгений|евгения|илья|илью|анастасия|анастасию|андрей|андрея|"
        r"захаров|евдокимов|краснощек|краснощёк|калаев|калаева|щеголев)\b",
        open_low,
    ):
        return True

    prior_touch_markers = (
        "был в отпуске",
        "хотел узнать",
        "хотела узнать",
        "приезжали",
        "выбрали",
        "смотрели у нас",
        "смотрели у нас машину",
        "смотрели у нас автомобиль",
        "проходили тест-драйв у нас",
        "проходили тест драйв у нас",
        "были у нас",
        "ранее общались",
        "мы с вами общались",
        "по машине с вами общались",
        "по машину с вами общались",
        "по машину с вами",
        "по машине общались",
        "общались по машине",
        "общались неделю назад",
        "неделю назад",
        "месяц назад",
        "месяц назад с вами общались",
        "созванивались",
        "как договорились",
        "вот хотели",
        "вы хотели",
        "хотели к нам",
        "хотели приехать",
        "говорили",
        "хотели подъехать",
        "хотели завтра подъехать",
        "подъедете",
        "подъедите",
        "узнать подъедете",
        "во сколько вас ждать",
        # CRM: «вы не так давно интересовались ценой автомобиля» (7836).
        "не так давно интересовались",
    )
    followup_markers = (
        "мы смотрели",
        "смотрели",
        "планировали",
        "вы планировали",
        "уже не планируем",
        "уже не планируете",
        "не планируете",
        "купили или",
        "или отказались",
        "отказались от",
        "подобрали",
        "не подобрали",
        "какие планы",
        "какие у вас планы",
        "еще не решили",
        "ещё не решили",
        "пока не решили",
        "что решили",
        "что взяли",
        "что надумали",
        "надумали",
        "созвонимся на следующей неделе",
        "на следующей неделе созвонимся",
        "перезвоню на следующей неделе",
        "позвоню на следующей неделе",
        "будем на связи",
        "с вами свяжемся",
        "свяжемся с вами",
        "или купили",
        "уже купили",
        "уже взяли",
        "всё уже взяли",
        "все уже взяли",
        "купил машину",
        "купили машину",
        "купили автомобиль",
        "купили авто",
        "поздравляю с покупкой",
    )
    repeat_markers = prior_touch_markers + followup_markers
    handoff_avito_admin = _is_op_inbound_admin_or_avito_handoff(text)

    def _repeat_marker_counts(m: str) -> bool:
        if handoff_avito_admin and m in _OP_REPEAT_VY_HOTELI_AFTER_HANDOFF_MARKERS:
            return False
        return _repeat_followup_marker_match(open_low, m)

    has_repeat = any(_repeat_marker_counts(m) for m in repeat_markers)
    if re.search(
        r"\b(?:еще|ещё)\s+не\s+решил\w*|\bпока\s+не\s+решил\w*",
        open_low,
    ):
        has_repeat = True
    if _op_repeat_past_contact_regex_in_opening(open_low):
        has_repeat = True
    # 27985: «мы с вами договаривались созвониться в ближайшие два дня».
    # Явный follow-up по прошлой договоренности, не первичный OP_OUT.
    if re.search(r"\b(?:мы\s+с\s+вами\s+)?договаривал(?:ись|ся)\b[\s\S]{0,80}\bсозвон", open_low):
        has_repeat = True
    if re.search(
        r"\b(?:на\s+следующ\w+\s+недел\w+[\s\S]{0,30}(?:созвон|перезвон|позвон)|"
        r"(?:созвон|перезвон|позвон)[\s\S]{0,30}на\s+следующ\w+\s+недел\w+)",
        open_low,
    ):
        has_repeat = True
    if re.search(
        r"\bсмотрел\w*[\s\S]{0,90}\bпланировал\w*[\s\S]{0,120}\b(?:не\s+планир\w*|купил\w*|отказал\w*)",
        open_low,
    ):
        has_repeat = True
    # STT: «не так давно» и «интересовались» разъезжаются по фразе (в начале звонка).
    if re.search(r"\bне\s+так\s+давно\b[\s\S]{0,120}\bинтересовал", open_low):
        has_repeat = True
    if re.search(
        r"\bнекотор\w*\s+врем\w*\s+назад\b[\s\S]{0,180}\bинтересовал", open_low
    ) and re.search(r"\bвопрос\b[\s\S]{0,120}\b(?:еще|ещё)\s+актуален", open_low):
        has_repeat = True
    # «Хотел уточнить, какое-то решение приняли?» / «вопрос … решился?» — уточнение итога (17232, 17256).
    if re.search(r"\b(?:хотел|хотела)\s+уточнить\b", open_low) and (
        re.search(r"\bрешени\w*\s+приняли\b", open_low)
        or re.search(r"\bприняли\s+решени", open_low)
        or "какое-то решение" in open_low
        or re.search(r"\bвопрос\b[\s\S]{0,120}решил\w*", open_low)
    ):
        has_repeat = True
    # Согласование визита после прошлого контакта по машине — Прочие даже при скидке/кредите (9726).
    visit_after_prior_car_talk = bool(
        re.search(r"\bпо\s+машин\w+[^.!?]{0,80}общал\w*", open_low)
        and re.search(r"\b(?:хотел\w*.{0,35}подъех\w*|подъед\w+)\b", open_low)
    )
    # Маркеры повторности — не Прочие при активной продаже (тест-драйв/кредит/оценка/трейд-ин) — 9217, 9231.
    if has_repeat:
        if sales_followup_context and not visit_after_prior_car_talk:
            if not _is_op_crm_status_callback_without_immediate_pitch(open_low):
                if not _is_op_kak_to_po_mashine_repeat_followup(open_low):
                    return False
        return True
    # ОП-исходящий по активной продаже — без маркеров повторности выше не уводим в Прочие.
    if sales_followup_context:
        return False
    return False


def _is_op_dealer_ringback_post_consultation_status_followup_other(text: str) -> bool:
    """
    26187: ringback-префикс ОП + проверка результата прошлой консультации:
    «консультацию получили, остались вопросы?» + «мне всё рассказал, мы пока думаем».
    Это повторный статусный контакт (OTHER), не первичный OP_OUT.
    """
    low = (text or "").lower()[:4500]
    if not low.strip():
        return False
    if not _is_op_dealer_ringback_intro(text):
        return False
    head = _transcript_opening_low(text)[:1800]
    has_consult_followup_prompt = bool(
        re.search(
            r"\bконсультац\w*\s+получил\w*[^.!?]{0,80}\b(?:остал\w*|есть)\b[^.!?]{0,40}\bвопрос\w*",
            head,
            re.I,
        )
    )
    if not has_consult_followup_prompt:
        return False
    has_client_pending_decision = bool(
        re.search(r"\b(?:мы\s+)?пока(?:\s+ещ[её])?\s+дума\w*\b", low, re.I)
    )
    has_prior_manager_explained = bool(
        re.search(
            r"\b(?:илья|евгений|андрей|анастасия|захаров|евдокимов|калаев|щеголев)\b"
            r"[^.!?]{0,45}\b(?:мне\s+)?вс[её]\s+рассказал\w*",
            low,
            re.I,
        )
    ) or bool(re.search(r"\bмне\s+вс[её]\s+рассказал\w*", low, re.I))
    return has_client_pending_decision and has_prior_manager_explained


def _is_op_inbound_recent_dealer_contact_repeat(text: str) -> bool:
    """
    Входящий ОП после недавнего визита/разговора в салоне — повторный контакт.

    19027: «дня два-три назад был у вас, разговаривал»; последующее активное
    обсуждение комплектации и цены не делает продолжение первичным ОП_вх.
    """
    if not (text or "").strip() or _is_outbound(text):
        return False
    opening = _transcript_opening_low(text, max_lines=16, fallback_chars=2800)
    if not any(
        marker in opening
        for marker in (
            "викинг",
            "дилер",
            "салон",
            "чери",
            "chery",
            "тенет",
            "тэнет",
            "tenet",
            "менеджер отдела продаж",
        )
    ):
        return False
    recent_past = bool(
        re.search(
            r"\bдн(?:я|ей)?\s+(?:два|две)(?:[^.!?]{0,20}\bтри\b)?(?:\s+назад)?\b|"
            r"\b(?:два|две)(?:\s*[-–—]\s*|\s+[^.!?]{0,12})три\s+дн(?:я|ей)\s+назад\b|"
            r"\b(?:вчера|позавчера|на\s+днях|недавно)\b",
            opening,
            re.IGNORECASE,
        )
    )
    if not recent_past:
        return False
    completed_contact = bool(
        re.search(
            r"\b(?:у\s+вас|в\s+(?:вашем\s+)?салоне)\b[^.!?]{0,100}"
            r"\b(?:был|были|заезжал|приезжал|общал\w*|разговаривал\w*)\b|"
            r"\b(?:был|были|заезжал|приезжал|общал\w*|разговаривал\w*)\b"
            r"[^.!?]{0,100}\b(?:у\s+вас|в\s+(?:вашем\s+)?салоне)\b|"
            r"\b(?:общал\w*|разговаривал\w*)\b[^.!?]{0,80}"
            r"\b(?:с\s+(?:вашим|вашей|ним|ней)|с\s+менеджер\w*)\b",
            opening,
            re.IGNORECASE,
        )
    )
    return completed_contact


def _is_op_confirmed_repeat_followup(text: str) -> bool:
    """
    Подтверждённая повторность для исходящего ОП:
    - есть признак прошлого контакта (общались/созванивались/ещё раз),
    - и есть признак итога/статуса (решили/выбрали/купили/планы),
    либо жёсткая пара «звоню как обещал» + (решили/купили).
    Одиночные «перезваниваю»/«как договорились» не считаем достаточными.
    """
    low = (text or "").lower()[:9000]
    if not low:
        return False
    open_low = _transcript_opening_low(text)

    past_contact_markers = (
        "ранее общались",
        "мы с вами общались",
        "мы с вами разговаривали",
        "по машине общались",
        "по машине с вами общались",
        "по машину с вами общались",
        "по автомобилю общались",
        "созванивались",
        "еще раз здравствуйте",
        "ещё раз здравствуйте",
        "вчера общались",
    )
    outcome_markers = (
        "что решили",
        "что выбрали",
        "уже купили",
        "купили авто",
        "купили автомобиль",
        "купили машину",
        "не купили",
        "пока не решили",
        "ещё не решили",
        "еще не решили",
        "какие планы",
    )

    has_past_contact = any(m in open_low for m in past_contact_markers) or _op_repeat_past_contact_regex_in_opening(
        open_low
    )
    has_outcome = any(m in low for m in outcome_markers)

    if has_past_contact and has_outcome:
        return True

    # 20445: исходящий follow-up ОП по вчерашнему визиту/расчёту и статусу кредитной заявки.
    has_op_manager_intro = bool(
        re.search(r"\bменеджер\w*\s+отдела\s+продаж\b", open_low, re.I)
        and re.search(r"\b(?:тенет|тэнет|чери|chery|викинг)\b", open_low, re.I)
    )
    has_recent_credit_followup_context = bool(
        re.search(r"\bвчера\s+с\s+вами\b", low, re.I)
        and (
            re.search(r"\bрасч[её]т\w*\s+делал", low, re.I)
            or re.search(r"\bзаявк\w*\s+подавал", low, re.I)
            or re.search(r"\bвам\s+[а-яё]{2,20}\s+звонил[аи]?\b", low, re.I)
        )
        and re.search(r"\b(?:не\s+одобрили|одобрили)\b", low, re.I)
    )
    if has_op_manager_intro and has_recent_credit_followup_context:
        return True

    has_promised_callback = bool(
        re.search(r"\b(?:звоню|перезваниваю)\s+(?:вам\s+)?как\s+обещал[аи]?\b", low)
    )
    if has_promised_callback and (
        "что решили" in low
        or "уже купили" in low
        or "купили авто" in low
        or "купили автомобиль" in low
        or "купили машину" in low
    ):
        return True

    return False


def _coerce_op_out_requires_site_lead(transcript: str, dept: str, call_type: str) -> Tuple[str, str]:
    """
    OP_OUT («ОП исх.»): оставляем только при устойчивых маркерах исходящего контакта ОП
    (_has_op_outbound_surface_markers). Имя функции историческое; «заявка с сайта» больше не учитывается.
    """
    d = (dept or "").strip().upper()
    c = (call_type or "").strip().upper()
    if d == "OP" and c == "OP_OUT" and not _has_op_outbound_surface_markers(transcript):
        return "OTHER", "OTHER"
    return d, c


def _is_non_dealer_op_homonym_bank_or_billing_intro(t: str) -> bool:
    """
    «Это Илья/Евгений/Анастасия/Андрей» от банка (ВТБ и т.п.) или по счёту/НДС без контекста дилера
    Викинги/Заставная/Чери/Тенет — не ОП (ложное совпадение с op_strong «это …» и справочником имён ОП).
    Слабое «чери» (напр. «чери ребят» у бухгалтера) не отменяет правило, если явно банк/счёт/НДС/бухгалтерия.
    """
    low = (t or "").lower()[:6000]
    if not re.search(r"\bэто\s+(андрей|илья|евгений|анастасия)\b", low):
        return False
    strong_bank_context = (
        "втб" in low
        or "банк втб" in low
        or "из банка" in low
        or "сбербанк" in low
        or ("бухгалтер" in low and ("счёт" in low or "счет" in low or "ндс" in low))
        or ("ндс" in low and ("счёт" in low or "счет" in low or "20%" in low or "22%" in low))
    )
    dealer = any(
        x in low
        for x in (
            "викинги",
            "заставн",
            "на заставной",
            "чери",
            "chery",
            "тенет",
            "tenet",
            "дилерск",
            "официальный дилер",
            "автосалон",
            "тольятти тенет",
            "чери тенет",
        )
    )
    if dealer and not strong_bank_context:
        return False
    car_sales = any(
        x in low
        for x in (
            "тест-драйв",
            "тестдрайв",
            "комплектац",
            "хочу купить",
            "покупку автомобиля",
            "покупка автомобиля",
            "новый автомобил",
            "новое авто",
            "выставочн",
            "тигго",
            "tiggo",
            "jaecoo",
            "омода",
            "omoda",
            "арризо",
            "arrizo",
        )
    )
    if car_sales:
        return False
    bankish = (
        "втб" in low
        or "банк втб" in low
        or "сбербанк" in low
        or "тинькофф" in low
        or "тинькоф" in low
        or "альфа-банк" in low
        or "альфабанк" in low
        or "счёт-фактур" in low
        or "счет-фактур" in low
        or ("ндс" in low and ("счёт" in low or "счет" in low or "процент" in low or "20%" in low))
        or ("банк" in low and ("ндс" in low or "счёт" in low or "счет" in low or "р/с" in low or "рс " in low))
        or "бухгалтер" in low
    )
    return bool(bankish)


def _is_software_licensing_vendor_call(t: str) -> bool:
    """
    Звонок вендору/в IT по лицензиям ПО (Автомаркет, Abton и т.д.), не покупка автомобиля в дилере.
    Иначе op_strong ловит «по поводу покупки» (покупки лицензии) и «комплектаци» в речи про состав ПО.
    Сотрудник дилера может звонить вендору с рабочего места — при маркерах ПО это всё равно не ОП.
    """
    low = (t or "").lower()[:12000]
    return any(
        x in low
        for x in (
            "лиценз",
            "лицензион",
            "автомаркет",
            "автобизнес",
            "abton",
            "абтон",
            "hasp",
            "криптопро",
            "код защиты",
            "защиты компонент",
            "компонентов защит",
            "программное обеспеч",
            "программного обеспеч",
            "установк программ",
            "ключ активац",
            "номер ключа",
            "тридцатидвухразряд",
            "32-бит",
            "32 бит ",
            "windows 11",
            "windows11",
            "баз данных",
            "базы данных",
            "расширение до",
        )
    )


def _is_vikings_sto_parts_reception_context(t: str) -> bool:
    """Линия СТО/стажёр по запчастям или записи — не перехватывать в B2B-правило 0gl."""
    low = (t or "").lower()[:12000]
    if not any(b in low for b in ("викинги", "виикинги", "виинги", "чери", "черри")):
        return False
    if not any(
        n in low
        for n in (
            "андреева",
            "юлия",
            "юлию",
            "плаксина",
            "гринкина",
            "гранкина",
            "александра",
            "дарья",
            "стажер",
            "стажёр",
        )
    ):
        return False
    return any(
        p in low
        for p in (
            "диспетчер",
            "ассистент сервиса",
            "слушаю вас",
            "отдел запчаст",
            "отдел запасных",
            "записаться",
            "запись на",
            "шиномонтаж",
            "колодк",
            "тормоз",
            "тормозн",
        )
    )


def _is_vikings_b2b_parts_supplier_call(t: str) -> bool:
    """
    Сотрудник Викингов звонит по заказу/уточнению запчастей внешнему контрагенту (стекло, прокладки,
    подшипники, фильтры, «под заказ», счёт и т.д.). В речи может быть «комплектация» не в смысле ОП — не OP.

    Наталья и Лариса не в справочнике ОП: связка имён + запчасти — даже без «викинги» в STT.
    """
    low = (t or "").lower()[:12000]
    vikings = any(x in low for x in ("викинги", "виикинги", "виинги"))
    natalya_and_larisa = ("наталь" in low or "наташ" in low) and "ларис" in low
    if not vikings and not natalya_and_larisa:
        return False
    if vikings and _is_vikings_sto_parts_reception_context(t):
        return False

    part_tokens = (
        "по поводу стекла",
        "ветров",
        "лобов",
        "уплотнит",
        "стекло",
        "стёкло",
        "стекла",
        "автостекл",
        "атеекло",
        "прокладк",
        "подшипник",
        "сальник",
        "ремкомплект",
        "сайлентблок",
        "втулк",
        "свеч",
        "амортизатор",
        "грм",
        "помпа",
        "помпу",
        "помпы",
        "форсунк",
        "турбин",
        "шрус",
        "граната",
        "полуось",
        "стабилизатор",
        "рычаг",
        "шаров",
        "стойк",
        "резин",
        "манжет",
        "сальника",
    )
    specific_part = any(x in low for x in part_tokens)
    filter_oil = "фильтр" in low and any(x in low for x in ("масл", "воздуш", "топлив", "салон"))
    supplier_hint = any(
        x in low
        for x in (
            "поставщик",
            "у поставщика",
            "под заказ",
            "запрос на счёт",
            "запрос на счет",
            "выставьте сч",
            "выставить сч",
            "счёт на оплат",
            "счет на оплат",
            "оригинал",
            "неоригинал",
            "аналог",
            "артикул",
            "каталожн",
            "наличие на складе",
            "есть в наличии",
            "срок поставки",
        )
    )
    has_zch = "запчаст" in low

    if natalya_and_larisa:
        topic_ok = specific_part or filter_oil or has_zch or supplier_hint
    else:
        topic_ok = specific_part or filter_oil or (has_zch and supplier_hint)

    if not topic_ok:
        return False

    op_purchase_markers = (
        "тест-драйв",
        "тестдрайв",
        "менеджер отдела продаж",
        "менеджера отдела продаж",
        "переключу на менеджера",
        "переключаю на менеджера",
        "переведу на менеджера",
        "перевожу на менеджера",
        "переведу вас на менеджера",
        "перевожу вас на менеджера",
        "переведу на менеджера отдела продаж",
        "перевожу на менеджера отдела продаж",
        "интересует черри",
        "интересует chery",
        "интересует тенет",
        "интересует автомобиль",
        "покупку автомобиля",
        "покупк",
        "хочу купить автомобиль",
        "выставочн",
        "официальный дилер",
        "заказан обратный звонок",
        "заявка с сайта",
    )
    op_purchase_score = sum(1 for x in op_purchase_markers if x in low)
    if op_purchase_score >= 2 or any(len(name) >= 3 and name in low[:3000] for name in _OP_MANAGER_NAMES):
        return False
    return True


def _is_non_automotive_domestic_context(transcript: str) -> bool:
    """
    Разговор про быт / бытовую технику без ядра автосалона Чери·Тенет·Викинги.
    Нужен, чтобы «комплектация», «цвет», «менеджер» в шуме STT не давали ложный ОП вх.
    Слова вроде «мобильный»/«сотовый» не используем — в речи про номер телефона дают ложные срабатывания.
    """
    low = (transcript or "").lower()[:16000]
    domestic = any(
        p in low
        for p in (
            "холодильник",
            "морозильник",
            "микроволнов",
            "стиральн",
            "посудомоеч",
            "плита ",
            "плитой",
            "плиты ",
            "духовк",
            "плафон",
            "сосиск",
            "колбас",
            "рис вар",
            "варим рис",
            "дверцы ",
            "трубка сдох",
            "трубка ",
            "трубку ",
            "зарядник",
            "зарядк",
            "на зарядку",
            "выдвижн",
            "ящик",
        )
    )
    phone_spare_handset = "труб" in low and ("запасн" in low or "запасная" in low)
    if not domestic and not phone_spare_handset:
        return False
    auto_salon = any(
        p in low
        for p in (
            "официальный дилер",
            "дилер викинги",
            "викинги чери",
            "викинги черри",
            "чери викинги",
            "тенет викинги",
            "менеджер отдела продаж",
            "тест-драйв",
            "тест драйв",
            "покупку автомобиля",
            "покупка автомобиля",
            "интересует chery",
            "интересует чери",
            "интересует тенет",
            "интересует покупка автомобиля",
            " chery",
            "chery ",
            " tiggo",
            "тигго",
            "арризо",
            "дашинг",
            "новый автомобил",
            "автомобиль chery",
            " tenet",
            "tenet ",
            "tnt-",
            " тнт",
        )
    )
    return not auto_salon


def _has_sto_directory_assistant_or_dispatcher_line(transcript: str) -> bool:
    """
    В транскрипте есть линия ассистента/диспетчера СТО из справочника (контролируемый скрипт):
    «диспетчер сервиса» / «ассистент сервиса» (в т.ч. STT «ассистент-сервис») + имя из STO_MANAGERS,
    либо стажёр Дарья; либо типичное приветствие линии (Викинги/Чери + сервис/роль + имя из справочника).
    Без этого STO_IN/STO_OUT не оцениваем как целевой СТО-скрипт — см. _coerce_sto_requires_directory_assistant_dispatcher.
    """
    raw = (transcript or "").strip()
    if not raw:
        return False
    t = raw.lower()
    try:
        from text_normalization import normalize_text

        t = (normalize_text(raw) or raw).lower()
    except ImportError:
        pass
    t = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", t)
    t = re.sub(r"\bдария\b", "дарья", t)
    if re.search(r"\bстажёр?\s+дарь", t) or ("стажер" in t and "дарь" in t):
        return True
    if "диспетчер сервиса" in t or "ассистент сервиса" in t:
        return any(n in t for n in _STO_MANAGER_NAMES if len(n) >= 3)
    if any(n in t for n in _STO_MANAGER_NAMES if len(n) >= 3) and re.search(
        r"\b(?:диспетчер|ассистент)\b[^.!?]{0,50}\b(?:юлия|юлию|андреева|александра|плаксина|гринкина|гранкина|дарья|дарью|лилия|лилию)\b",
        t,
    ):
        return True
    if ("слушаю вас" in t or re.search(r"\bслушаю\b", t[:220])) and (
        "викинг" in t or "чери" in t or "черри" in t or "chery" in t
    ):
        if ("ассистент" in t or "диспетчер" in t or "сервис" in t) and any(
            n in t for n in ("андреева", "юлия", "юлию", "александра", "плаксина", "гринкина", "гранкина", "дарья", "лилия", "лилию")
        ):
            return True
    head_intro = t[:900]
    if "меня зовут" in head_intro and any(
        n in head_intro for n in ("лилия", "лилию", "юлия", "юлию", "андреева", "александра", "плаксина", "гринкина", "гранкина", "дарья", "дарью")
    ) and any(b in head_intro for b in ("викинг", "чери", "компани")):
        return True
    if "диспетчер" in t and any(
        n in t
        for n in (
            "дарья",
            "дарию",
            "юлия",
            "юлию",
            "андреева",
            "александра",
            "плаксина",
            "гринкина",
            "гранкина",
            "лилия",
            "лилию",
        )
    ):
        return True
    return False


def _has_sto_service_line_context(transcript: str) -> bool:
    """
    Контекст линии СТО (ассистент/диспетчер): для ветки «это звонок на сервисную линию».
    Не путать с правилом реестра «работы без регламентного ТО → Прочие» — там линия не проверяется.
    """
    raw = (transcript or "").strip()
    if not raw:
        return False
    low = raw.lower()
    try:
        from text_normalization import normalize_text

        low = (normalize_text(raw) or raw).lower()
    except ImportError:
        pass
    low = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", low)
    low = re.sub(r"\b(?:енсервис|итмсервис|снсервис)\b", "ассистент сервиса", low)
    if _has_sto_greeting_service_with_dispatcher_name(transcript):
        return True
    if "диспетчер сервиса" in low or "ассистент сервиса" in low:
        return True
    head = low[:700]
    if "диспетчер" in head and any(
        b in head for b in ("викинг", "чери", "chery", "заставн")
    ) and _sto_dispatcher_stt_name_alias_in_head(head):
        return True
    if _has_sto_directory_assistant_or_dispatcher_line(transcript):
        return True
    sto_names = (
        "андреева",
        "плаксина",
        "гринкина",
        "гранкина",
        "александра",
        "юлия",
        "юлию",
        "дарья",
        "дарью",
        "дария",
        "лилия",
        "лилию",
        "бари",  # STT: Дарья (9091)
    )
    if any(n in low for n in sto_names) and ("ассистент" in low or "диспетчер" in low):
        return True
    head_intro = low[:900]
    return any(n in head_intro for n in sto_names) and (
        "меня зовут" in head_intro
        and any(b in head_intro for b in ("викинг", "чери", "компани"))
    )


def _is_sto_inbound_service_booking_after_prior_visit_context(transcript: str) -> bool:
    """
    Фрагмент без шапки «ассистент/диспетчер сервиса…» (STT с середины): уже ездили к дилеру,
    запись на сервис/диагностику и согласование визита — широкий STO_IN, не Прочие (7869).
    """
    try:
        from text_normalization import normalize_text

        tl = (normalize_text(transcript) or transcript or "").lower()
    except Exception:
        tl = (transcript or "").lower()
    if not tl or not _has_sto_substantive_service_intent(tl):
        return False
    prior = any(
        p in tl
        for p in (
            "обслуживались у нас",
            "ранее к нам",
            "к нам заезжал",
            "раньше к вам",
            "книжку гарантийн",
            "гарантийную книжку",
            "книга гарантийн",
        )
    )
    if not prior:
        return False
    booking = "запис" in tl and (
        "на сервис" in tl
        or "сервис машин" in tl
        or "сервис машину" in tl
        or "на ремонт" in tl
        or "мая" in tl
        or "май" in tl
        or "июня" in tl
        or "июля" in tl
        or "апрел" in tl
        or bool(
            re.search(
                r"\b(?:понедельник|вторник|среду|четверг|пятниц|суббот|воскресенье)\b",
                tl,
            )
        )
        or "контрольный звонок" in tl
        or "накануне" in tl
    )
    return bool(booking)


def _coerce_sto_requires_directory_assistant_dispatcher(
    transcript: str, dept: str, call_type: str
) -> Tuple[str, str]:
    """STO_IN/STO_OUT только при линии ассистента/диспетчера из справочника; иначе Прочие."""
    d = (dept or "").strip().upper()
    c = (call_type or "").strip().upper()
    if d != "STO" or c not in ("STO_IN", "STO_OUT"):
        return d, c
    if _has_sto_service_line_context(transcript):
        return d, c
    try:
        from text_normalization import normalize_text

        tl = (normalize_text(transcript) or transcript or "").lower()
    except Exception:
        tl = (transcript or "").lower()
    if _is_sto_to_booking_live_dialog_not_secretary_misc(tl):
        return d, c
    if c == "STO_IN" and _is_sto_inbound_service_booking_after_prior_visit_context(transcript):
        return d, c
    if c == "STO_OUT" and _is_outbound(tl) and _sto_outbound_to_sched_reminder_vehicle(tl) and any(
        b in tl for b in ("викинг", "чери", "chery", "тенет", "тэнет", "tenet")
    ):
        return d, c
    # Широкий слой: сервисный исходящий (перезвон/приглашение/согласование работ) не уводим в OTHER.
    # Узкий слой STO_TO_* при этом может остаться пустым (STO_NARROW_OTHER) — это обрабатывает sto_to_rubric.
    if c == "STO_OUT":
        if _is_sto_dealer_employee_outbound_service_call(transcript):
            return d, c
        if _is_sto_outbound_service_invite(transcript) or _is_sto_outbound_master_service_workcall(transcript):
            return d, c
        if _is_outbound(transcript) and _sto_outbound_service_dispatcher_context(tl) and _has_sto_substantive_service_intent(tl):
            return d, c
    if c == "STO_IN" and _is_sto_accessory_install_dialog(tl):
        return d, c
    if c == "STO_IN" and _is_sto_inbound_warranty_booking_dialog(tl):
        return d, c
    if c == "STO_IN" and _is_sto_inbound_service_booking_reception_substantive_dialog(transcript):
        return d, c
    if c == "STO_IN" and (
        any(p in tl[:2400] for p in ("перенести то", "перенести запись", "перезапис"))
        and any(p in tl[:3200] for p in ("вы записаны к нам", "записан к нам", "записана к нам"))
    ):
        return d, c
    # 12433/13576: админ/хостес, диспетчер недоступен — широкий STO_IN при контексте сервисной линии; 11263 → Прочие.
    if c == "STO_IN" and (
        _is_priority_busy_dispatcher_leave_number_callback(transcript)
        or (
            _is_sto_no_service_assistant_connection_misc(transcript)
            and _sto_admin_no_dispatcher_callback_keeps_sto_in_classification(transcript)
        )
    ):
        return d, c
    return "OTHER", "OTHER"


def _is_showroom_admin_informational_inquiry_other(transcript: str) -> bool:
    """
    Линия администратора салона без перевода на менеджера ОП и без формулировки «менеджер отдела продаж» —
    как правило справка (наличие, география, «есть ли тест-драйв»), не полноценный входящий цикл ОП.
    """
    low = (transcript or "").lower()
    head = low[:1200]
    if "администратор салона" in head:
        pass
    elif "администратор" in head[:700] and "салон" in head[:700]:
        pass
    else:
        return False
    if "менеджер отдела продаж" in low or "менеджера отдела продаж" in low:
        return False
    if re.search(
        r"(переключ|переведу|перевожу|соединю)[^.!?]{0,100}(менеджер|отдел\s+продаж|менеджера)",
        low,
    ):
        return False
    return not _v2_has_vehicle_purchase_substance(low)


def _has_used_vehicle_topic_markers(low: str) -> bool:
    """Тема подержанного авто / автоплощадки (не полный список Лада — только частые в разговоре)."""
    if not (low or "").strip():
        return False
    if _is_explicit_used_cars_department_topic(low):
        return True
    if "б/у" in low or "б.у." in low or " б у " in f" {low} ":
        return True
    if any(
        p in low
        for p in (
            "подержан",
            "авто с пробегом",
            "машина с пробегом",
            "автомобиль с пробегом",
            "с пробегом",
            "вторичк",
            "не новый автомобиль",
            "не новая машина",
        )
    ):
        return True
    if re.search(r"\bпробег\w*\b", low):
        if not _is_sto_reception_mileage_not_used_vehicle(low):
            return True
    if any(
        p in low
        for p in (
            "калина",
            "ладу ",
            "лада ",
            "lada",
            "гранта",
            "приора",
        )
    ):
        return True
    if any(p in low for p in ("продаётся ещё", "продается еще", "ещё продаётся", "еще продается")):
        return True
    if any(p in low for p in ("ржавчин", "корроз", "покраск", "арки", "днищ")):
        return True
    return False


def _has_op_sales_cycle_preserves_op_in(low: str) -> bool:
    """
    Признаки полноценного цикла ОП (новые Чери/Тенет, сделка, трейд-ин, тест-драйв и т.д.).
    Не используем целиком _v2_has_vehicle_purchase_substance: голая «скидк» на б/у Лада удержала бы ОП вх.
    """
    if _is_used_cars_line_manager_opening(low):
        return False
    if _is_explicit_used_cars_department_topic(low):
        return False
    used_vm = _has_used_vehicle_topic_markers(low)
    new_chery_line_models = _has_new_vehicle_model_in_purchase_context(low)
    # Б/у «Лада/Калина/…»: без темы новых моделей Чери титул «официальный дилер Чери» и «комплектация» по старой машине
    # не считаем полноценным циклом ОП (иначе classify_auto не даёт Прочие для разговора только про б/у парк).
    bu_domestic = bool(
        used_vm
        and (
            re.search(r"\bкалин", low)
            or re.search(r"\b(?:гранта|приора|лада|lada|нива|веста)\b", low)
        )
    )
    if bu_domestic and not new_chery_line_models:
        if "менеджер отдела продаж" in low or "менеджера отдела продаж" in low:
            return True
        if re.search(
            r"(переведу|переключ|перевожу|соединю)[^.!?]{0,120}(менеджер|отдел\s+продаж|менеджера)",
            low,
        ):
            return True
        if _test_drive_is_customer_sales_context(low):
            return True
        if any(p in low for p in ("трейд-ин", "trade-in", "трейдин")):
            return True
        if any(p in low for p in ("сдаёте", "сдаете", "оценку вашего", "оценку автомобил", "зачёт", "зачет ")):
            return True
        return False

    if "менеджер отдела продаж" in low or "менеджера отдела продаж" in low:
        return True
    if re.search(
        r"(переведу|переключ|перевожу|соединю)[^.!?]{0,120}(менеджер|отдел\s+продаж|менеджера)",
        low,
    ):
        return True
    if any(
        b in low
        for b in (
            "тигго",
            "tiggo",
            "арризо",
            "дашинг",
            "jaecoo",
            "джак",
        )
    ):
        return True
    if any(b in low for b in ("тенет", "тэнет", "tenet", "чери", "chery", "черри")):
        return True
    if _test_drive_is_customer_sales_context(low):
        return True
    if any(p in low for p in ("трейд-ин", "trade-in", "трейдин")):
        return True
    if any(p in low for p in ("сдаёте", "сдаете", "оценку вашего", "оценку автомобил", "зачёт", "зачет ")):
        return True
    if any(p in low for p in ("кредит ", "кредит,", "лизинг", "рассроч", "комплектац", "бронь", "госпрограмм")):
        return True
    if any(p in low for p in ("визитк", "ватсап", "whatsapp", "отправлю номер", "напишу вам", "электронную визитку")):
        return True
    if any(p in low for p in ("приезжайте в салон", "приезжайте к нам", "ждём в салоне", "ждем в салоне")):
        return True
    return False


def _is_used_car_inquiry_without_op_sales_cycle_other(transcript: str) -> bool:
    """
    Входящий про б/у / подержанный парк без признаков цикла ОП по новым Чери·Тенет — Прочие.
    Если обсуждаются трейд-ин, менеджер ОП, модель Тенет/Чери, тест-драйв и т.п. — не трогаем.
    """
    low = (transcript or "").lower()
    if not _has_used_vehicle_topic_markers(low):
        return False
    if _has_op_sales_cycle_preserves_op_in(low):
        return False
    return True


def _is_op_rental_context_other(transcript: str) -> bool:
    """
    Прочие: разговор про аренду авто, а не про покупку.
    Не опираемся на нормализацию «ареда -> Arrizo»; смотрим только на явный арендный контекст.
    """
    low = (transcript or "").lower()
    if not low.strip():
        return False
    rental_markers = (
        "аренда",
        "аренде",
        "аренду",
        "арендовать",
        "арендный",
        # Частый STT-обрыв от «аренда».
        "ареда",
    )
    if not any(m in low for m in rental_markers):
        return False
    # В арендном кейсе обычно есть отказ/завершение без сделки.
    closure_markers = (
        "предложить нечего",
        "предложить него",
        "наш вариант не рассматривать",
        "не рассматривать",
        "не интересует",
    )
    has_closure = any(m in low for m in closure_markers)
    # Или явный follow-up без движения к сделке.
    has_repeat_followup = bool(
        re.search(r"\b(?:пропущенн\w*\s+от\s+вас|мы\s+как-то\s+с\s+вами\s+по\s+машин\w+\s+общались)\b", low, re.I)
    )
    return has_closure or has_repeat_followup


def _is_op_ordered_vehicle_arrival_status_followup_other(transcript: str) -> bool:
    """
    Прочие: клиент уже заказывал автомобиль и уточняет только статус поставки
    (поступление/распределение/сроки приезда), без нового цикла продажи.

    21839: «я у вас автомобиль заказывал ... было поступление автомобилей?».
    """
    if _is_outbound(transcript or ""):
        return False
    low = (transcript or "").lower()
    if not low.strip():
        return False
    opening = _transcript_opening_low(transcript, max_lines=16, fallback_chars=2800)
    has_ordered_marker = bool(
        re.search(r"\b(?:я|мы)\s+у\s+вас\s+автомобил\w*\s+заказывал\w*\b", opening, re.I)
        or re.search(r"\bпредоплат\w*\s+(?:оставил\w*|вн[её]с\w*)\b", low, re.I)
    )
    if not has_ordered_marker:
        return False
    has_delivery_status_topic = bool(
        re.search(r"\b(?:поступлен\w*|поступил\w*|поступили)\s+автомобил\w*\b", low, re.I)
        or re.search(r"\bжд[её]м\s+распределени\w*\b", low, re.I)
        or re.search(r"\bжд[её]м[^.!?]{0,60}\bот\s+завод\w*\b", low, re.I)
        or re.search(r"\bкогда\s+машин\w*\s+приед\w*\b", low, re.I)
        or re.search(r"\bсрок\w*[^.!?]{0,40}\bкогда\s+машин\w*\s+приед\w*\b", low, re.I)
    )
    if not has_delivery_status_topic:
        return False
    has_primary_sales_cycle = any(
        p in low
        for p in (
            "комплектац",
            "кредит",
            "лизинг",
            "трейд-ин",
            "trade-in",
            "рассроч",
            "тест-драйв",
            "покупк",
            "стоимост",
            "сколько стоит",
            "цена",
            "скидк",
            "первоначальн",
        )
    )
    if has_primary_sales_cycle:
        return False
    return True


def _is_op_inbound_repeat_continuation_other(transcript: str) -> bool:
    """
    Прочие: входящий повторный перезвон, когда клиент продолжает только что
    прерванный разговор, а не запускает новый цикл консультации.
    """
    low = (transcript or "").lower()
    if not low.strip():
        return False
    opening = _transcript_opening_low(transcript)
    has_just_spoke_marker = bool(
        re.search(r"\b(?:я\s+)?(?:вот\s+)?только\s+что\s+разговаривал\w*\b", opening, re.I)
    )
    if not has_just_spoke_marker:
        return False
    has_drop_marker = bool(
        re.search(r"\b(?:связь|линия)\b[^.!?]{0,40}\bпрервал\w*\b", low, re.I)
    )
    has_repeat_greeting = bool(
        re.search(r"\b(?:ещ[её]\s+раз\s+здравствуйте|здравствуйте\s+ещ[её]\s+раз)\b", low, re.I)
    )
    has_transfer_back_same_manager = bool(
        re.search(
            r"\bперевест[ии][^.!?]{0,90}\b(?:анастаси|андре|евгени|иль[ья]|менеджер)\b",
            opening,
            re.I,
        )
    )
    return has_drop_marker or has_repeat_greeting or has_transfer_back_same_manager


def _is_op_outbound_already_purchased_vehicle_followup_other(transcript: str) -> bool:
    """
    Прочие: исходящий звонок по старой заявке, где клиент подтверждает,
    что автомобиль уже куплен/взят у дилера.

    21841: «так я уже у вас взял седьмой ... да, взял».
    """
    if not _is_outbound(transcript or ""):
        return False
    low = (transcript or "").lower()
    if not low.strip():
        return False

    head = _transcript_opening_low(transcript, max_lines=18, fallback_chars=3200)
    has_prior_lead_context = bool(
        re.search(r"\bзаявк\w*\s+пришл\w*\b", head, re.I)
        or re.search(r"\bинтересовал\w*[\s\S]{0,40}\bпокупк\w*\b", head, re.I)
    )

    has_already_purchased_phrase = bool(
        re.search(r"\bтак\s+я\s+уже\s+у\s+вас\s+(?:взял|взяли|купил|купили)\b", low, re.I)
        or re.search(
            r"\b(?:я|мы)\s+(?:уже\s+)?(?:у\s+вас\s+)?(?:взял(?:и)?|купил(?:и)?)\b"
            r"[^.!?]{0,35}\b(?:седьм\w*|машин\w*|автомобил\w*|тенет|т[еэ]нет|chery|чери)\b",
            low,
            re.I,
        )
        or re.search(r"\bу\s+вас\s+взял(?:и)?\b", low, re.I)
    )
    if not has_already_purchased_phrase:
        return False

    has_affirmative_confirmation = bool(
        re.search(r"\bда\s*[,.]?\s*(?:взял(?:и)?|купил(?:и)?)\b", low, re.I)
        or re.search(r"\b(?:взял(?:и)?|купил(?:и)?)\s*[,.]?\s*да\b", low, re.I)
    )
    has_post_sale_closure = bool(
        re.search(r"\bпоздравля\w*\s+с\s+покупк\w*\b", low, re.I)
        or re.search(r"\bкак[^.!?]{0,25}автомобил\w*[^.!?]{0,25}прошл\w*\b", low, re.I)
    )
    return bool(has_prior_lead_context and (has_affirmative_confirmation or has_post_sale_closure))


def _is_op_test_drive_slot_booking_without_sales_cycle_other(transcript: str) -> bool:
    """
    Прочие: запись на тест-драйв через администратора в формате «подобрать время и записать»,
    без перехода в полноценный цикл ОП-консультации с менеджером.
    """
    low = (transcript or "").lower()
    if not low.strip():
        return False
    head = low[:1300]
    has_admin_opening = "администратор" in head
    if not has_admin_opening:
        return False
    has_test_drive_booking = bool(
        re.search(r"\bзапис\w*\s+на\s+тест[- ]?драйв\b", low, re.I)
        or re.search(r"\bна\s+тест[- ]?драйв\b", low, re.I)
    )
    if not has_test_drive_booking:
        return False
    no_manager_transfer = bool(
        re.search(r"\bне\s+нужно\b[^.!?]{0,80}\bменеджер\w*\b[^.!?]{0,80}\bперев", low, re.I)
        or re.search(r"\bбез\s+менеджер\w*\b", low, re.I)
        # 28728: «Вы с менеджером хотите ...? — Неет.»
        or ("с менеджером хотите" in low and bool(re.search(r"\bне+т\b", low, re.I)))
        or re.search(
            r"\b(?:с\s+менеджер\w*[^.!?]{0,60}хотите|хотите[^.!?]{0,60}с\s+менеджер\w*)"
            r"[^.!?]{0,80}\b(?:не+т|нет)\b",
            low,
            re.I,
        )
        or re.search(
            r"\b(?:не+т|нет)\b[^.!?]{0,80}\b(?:с\s+менеджер\w*|менеджер\w*)\b",
            low,
            re.I,
        )
    )
    if not no_manager_transfer:
        return False
    has_slot_booking_closure = bool(
        re.search(r"\b(?:на|в)\s+\d{1,2}\s*(?:час|вечера|утра)\b", low, re.I)
        or re.search(r"\bна\s+сегодня\b", low, re.I)
        or re.search(
            r"\bна\s+\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
            low,
            re.I,
        )
        or re.search(r"\bна\s+\d{1,2}\s+октября\b", low, re.I)
    ) and bool(
        re.search(r"\bзаписал\w*[^.!?]{0,30}\bвас\b", low, re.I)
        or re.search(r"\b(?:запиш\w*ся|жд[её]м\s+вас)\b", low, re.I)
        or re.search(r"\bконтактн\w*\s+номер\b", low, re.I)
        or re.search(r"\bостав[ьт][еия]\s+номер\s+телефон", low, re.I)
    )
    if not has_slot_booking_closure:
        return False
    return True


def _is_op_showroom_visit_scheduling_without_manager_consultation_other(transcript: str) -> bool:
    """
    Прочие: администратор согласует время визита в салон по заявке,
    клиент отказывается от консультации менеджера в текущем звонке.
    """
    low = (transcript or "").lower()
    if not low.strip():
        return False
    head = low[:1400]
    if "администратор" not in head:
        return False
    has_lead_marker = bool(
        re.search(r"\bоставлял\w*\s+заявк\w*\s+на\s+нашем\s+сайте\b", low, re.I)
        or re.search(r"\bзаявк\w*\s+на\s+сайте\b", low, re.I)
    )
    if not has_lead_marker:
        return False
    has_visit_time_scheduling = bool(
        re.search(r"\bкакое\s+время\s+вас\s+ожидать\b", low, re.I)
        or re.search(r"\bсегодня\s+или\s+завтра\b", low, re.I)
        or re.search(r"\b(?:в\s+шесть|в\s+семь|до\s+восьми)\b", low, re.I)
    )
    if not has_visit_time_scheduling:
        return False
    no_manager_consultation = bool(
        re.search(
            r"\bконсультац\w*\s+не\s+нужн\w*\b[^.!?]{0,80}\bменеджер\w*\b",
            low,
            re.I,
        )
        or re.search(r"\bне\s+нужн\w*\b[^.!?]{0,80}\bс\s+менеджер\w*\b", low, re.I)
    )
    if not no_manager_consultation:
        return False
    has_wait_you_closure = bool(re.search(r"\bжд[её]м\s+вас\b", low, re.I))
    return has_wait_you_closure


def _has_op_manager_self_intro_in_head(head: str) -> bool:
    """Менеджер ОП представился в начале записи (не «Андрей сказал»)."""
    h = (head or "").lower()
    if "менеджер отдела продаж" in h or "менеджера отдела продаж" in h:
        return True
    if re.search(
        r"\bменя\s+зовут\s+(?:андрей|евгений|илья|анастаси|захаров|евдокимов|краснощек|калаев|щеголев)\b",
        h,
    ):
        return True
    if re.search(
        r"\bэто\s+(?:я\s+)?(?:андрей|евгений|илья|анастаси|захаров|евдокимов|краснощек|калаев|щеголев)\b",
        h,
    ):
        return True
    if re.search(r"\bменеджер\s+(?:захаров|евдокимов|краснощек|калаев|щеголев)\b", h):
        return True
    return False


def _is_op_credit_handoff_calculation_other(transcript: str) -> bool:
    """
    Прочие: в транскрипте только кредитный расчёт после общения менеджера ОП с клиентом
    (менеджер переключил / передал на кредитного специалиста). Не полноценный OP_IN (9103).
    """
    if _is_outbound(transcript or ""):
        return False
    try:
        from text_normalization import normalize_text

        low = (normalize_text(transcript) or transcript or "").lower()
    except Exception:
        low = (transcript or "").lower()
    if not low.strip():
        return False

    head = low[:2200]
    start = low[:500]

    if _has_op_manager_self_intro_in_head(head):
        return False
    if ("администратор" in start or "администратор салона" in start) and re.search(
        r"\b(?:переключ|перевед|перевож)\w*\b", start[:1200]
    ):
        return False
    if _test_drive_is_customer_sales_context(low):
        return False
    if "комплектаци" in low and any(
        p in low
        for p in (
            "актив",
            "прайм",
            "робот",
            "вариатор",
            "максимальн",
            "комплектацию подскажите",
            "какая комплектация",
        )
    ):
        return False
    if any(
        p in low
        for p in (
            "покупку рассматриваете",
            "приезжайте в салон",
            "приезжайте к нам",
            "электронную визитку",
            "скинул визитку",
        )
    ):
        return False

    credit_strong = (
        "ставк",
        "платеж",
        "платёж",
        "переплат",
        "первоначальн",
        "взнос",
        "каско",
        "рассроч",
    )
    credit_banks = ("втб", "сбер", "альфа", "совком", "тбанк", "совкомбанк")
    credit_aux = (
        "посчита",
        "считаем",
        "округли",
        "в расчёт",
        "в расчет",
        "по банкам",
        "субсидирован",
        "одобрен",
        "одобрени",
        "0,01",
        "0.01",
        "ноль-один",
        "ноль один",
        "льготн",
    )
    strong_hits = sum(1 for p in credit_strong if p in low)
    if any(b in low for b in credit_banks):
        strong_hits += 1
    if re.search(r"\b(?:на|срок)\s+(?:два|три|2|3|четыре|4)\s+год", low):
        strong_hits += 1
    aux_hits = sum(1 for p in credit_aux if p in low)
    credit_calc_heavy = strong_hits >= 2 or (strong_hits >= 1 and aux_hits >= 2)
    if not credit_calc_heavy:
        return False

    handoff = bool(
        re.search(
            r"\b(?:андрей|евгений|илья|захаров|евдокимов|краснощек|калаев|щеголев|менеджер)\s+сказал\b",
            low,
        )
        or any(
            p in low
            for p in (
                "трубочку андрею",
                "трубку андрею",
                "передам андрею",
                "передам евгению",
                "ко мне вопросов нет",
                "ему надо будет скинуть",
                "ей надо будет скинуть",
                "он просил",
                "просил вам сказать",
                "мужу командировки",
            )
        )
    )
    if not handoff:
        return False

    continuation = bool(
        re.search(r"^\s*алло,?\s+[а-яё]{3,}", low[:100])
        or "удобно сейчас" in start
        or "удобно вам" in start
    )
    if not continuation and strong_hits < 3:
        return False

    return True


def classify_auto(transcript: str) -> Tuple[str, str]:
    """Классификация через classify_by_transcript (диспетчер v2 / legacy)."""
    if _is_wrong_dealer_center_redirect_other(transcript):
        return "OTHER", "OTHER"
    d, c = classify_by_transcript(transcript)
    d, c = normalize_call_type_result(d, c)
    d, c = _coerce_op_out_requires_site_lead(transcript, d, c)
    d, c = _coerce_sto_requires_directory_assistant_dispatcher(transcript, d, c)
    # 17225: клиентская служба / договор-юрист без линии диспетчера/ассистента → Прочие.
    if _is_customer_service_dept_legal_without_sto_booking_line_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_showroom_admin_informational_inquiry_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c in ("OP_IN", "OP_OUT") and _is_internal_staff_dialogue_without_client_other(
        transcript
    ):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_used_car_inquiry_without_op_sales_cycle_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_credit_handoff_calculation_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_insurance_department_call_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_b2b_vehicle_transfer_process_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_rental_context_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_test_drive_slot_booking_without_sales_cycle_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_showroom_visit_scheduling_without_manager_consultation_other(
        transcript
    ):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_inbound_repeat_continuation_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_IN" and _is_op_ordered_vehicle_arrival_status_followup_other(transcript):
        d, c = "OTHER", "OTHER"
    if (d, c) in (
        ("OP", "OP_IN"),
        ("OP", "OP_OUT"),
        ("STO", "STO_IN"),
        ("STO", "STO_OUT"),
    ) and _is_op_promotional_contest_crm_other(transcript):
        d, c = "OTHER", "OTHER"
    if (d, c) in (
        ("OP", "OP_IN"),
        ("OP", "OP_OUT"),
        ("STO", "STO_IN"),
        ("STO", "STO_OUT"),
    ) and _is_op_marketing_sponsorship_charity_contact_other(transcript):
        d, c = "OTHER", "OTHER"
    d, c = _coerce_short_op_dialog_to_other(transcript, d, c)
    if d == "OP" and c == "OP_OUT" and _is_op_repeat_followup_outbound(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_OUT" and _is_op_outbound_already_purchased_vehicle_followup_other(transcript):
        d, c = "OTHER", "OTHER"
    if d == "OP" and c == "OP_OUT" and _is_op_outbound_immediately_rejected_no_substance(transcript):
        d, c = "OTHER", "OTHER"
    if (d, c) in (("STO", "STO_IN"), ("STO", "STO_OUT")) and _is_insurance_department_call_other(
        transcript
    ):
        d, c = "OTHER", "OTHER"
    # 28728: тест-драйв через администратора с подбором времени/номера и отказом от разговора
    # с менеджером — ОП-тематика, но категория «Прочие», не СТО.
    if (d, c) in (("STO", "STO_IN"), ("STO", "STO_OUT")) and _is_op_test_drive_slot_booking_without_sales_cycle_other(
        transcript
    ):
        d, c = "OTHER", "OTHER"
    # Линия сервиса приняла звонок, но итог — разговор с менеджером ОП после перевода.
    if (d, c) in (("STO", "STO_IN"), ("STO", "STO_OUT")) and _is_service_transfer_to_op_sales_successful_inbound(
        transcript
    ):
        d, c = "OP", "OP_IN"
    if (d, c) == ("OTHER", "OTHER") and _is_op_inbound_direct_manager_sales_consultation(transcript):
        d, c = "OP", "OP_IN"
    return _finalize_service_guards_for_other(transcript, d, c)


_OP_PRODUCT_SUBSTANCE_MARKERS = (
    "тест-драйв",
    "тест драйв",
    "тестдрайв",
    "комплектац",
    "скидк",
    "кредит",
    "рассрочк",
    "трейд-ин",
    "трейд ин",
    "trade in",
    "trade-in",
    "вариатор",
    "роботизирован",
    "коробка",
    "коробке",
    "коробкой",
    "полный привод",
    "передний привод",
    "двигатель",
    "литров",
    "литра",
    "лошад",
    "приедете",
    "подъедете",
    "подъехать",
    "приобрест",
    "приобрет",
    "забрать машин",
    "забрать автомобиль",
    "тэнет 4",
    "тэнет-4",
    "тенет 4",
    "тенет-4",
    "тэнет 7",
    "тэнет-7",
    "тенет 7",
    "тенет-7",
    "тэнет 8",
    "тэнет-8",
    "тенет 8",
    "тенет-8",
    "тэнет 9",
    "тэнет-9",
    "тенет 9",
    "тенет-9",
    "арризо",
    "arrizo",
    "chery 4",
    "chery 7",
    "chery 8",
    "chery 9",
    "tiggo 4",
    "tiggo 7",
    "tiggo 8",
    "tiggo 9",
    "тигго 4",
    "тигго 7",
    "тигго 8",
    "тигго 9",
    "в наличии",
)

_OP_OUT_IMMEDIATE_REJECT_MARKERS = (
    "не интересует",
    "не интересуют",
    "не интересно",
    "нет, не нужно",
    "нет не нужно",
    "не нужно",
    "ничего не надо",
    "не надо ничего",
    "не планирую",
    "не планируем",
    "ошиблись номером",
    "не туда попали",
    "пока нет",
    "ничего не нужно",
    "нет, нет",
    "нет нет",
    "нет.нет",
    "нет,нет",
    "никак нет",
    "никак",
)

_OP_OUT_CLIENT_DEFER_MARKERS = (
    "неудобно говорить",
    "неудобно разговаривать",
    "сейчас неудобно",
    "не могу говорить",
    "не могу разговаривать",
    "сейчас на работе",
    "я на работе",
    "я сейчас на работе",
    "давайте завтра созвонимся",
    "завтра созвонимся",
    "давайте завтра перезвоним",
    "перезвоните завтра",
    "позже перезвоните",
    "перезвоните позже",
    "сейчас не могу",
)


def _is_op_outbound_immediately_rejected_no_substance(transcript: str) -> bool:
    """
    Исходящий ОП без содержания для анализа: клиент сразу отказывается от темы
    или просит перезвонить позже («неудобно говорить», «завтра созвонимся») —
    Прочие.

    Узко срабатывает только при выполнении всех условий:
    1) короткий звонок (<= ~800 символов после нормализации);
    2) есть явный исходящий ОП-префикс (менеджер отдела продаж / автосалон / официальный дилер);
    3) есть маркер отказа или отложенного разговора;
    4) нет ни одного «продуктового» маркера ОП из _OP_PRODUCT_SUBSTANCE_MARKERS.
    """
    text = (transcript or "").strip()
    if not text:
        return False
    try:
        from text_normalization import normalize_text

        text = (normalize_text(text) or text).strip()
    except Exception:
        pass
    low = text.lower()
    if len(low) > 800:
        return False
    if not _is_outbound(low):
        return False
    intro_op = (
        "менеджер отдела продаж" in low
        or "менеджера отдела продаж" in low
        or "автосалон" in low
        or "официальный дилер" in low
        or "официальный дилерский" in low
    )
    if not intro_op:
        return False
    has_immediate_reject = any(m in low for m in _OP_OUT_IMMEDIATE_REJECT_MARKERS)
    has_client_defer = any(m in low for m in _OP_OUT_CLIENT_DEFER_MARKERS)
    if not (has_immediate_reject or has_client_defer):
        return False
    has_substance = any(p in low for p in _OP_PRODUCT_SUBSTANCE_MARKERS)
    if has_substance:
        return False
    return True


def _coerce_short_op_dialog_to_other(transcript: str, dept: str, call_type: str) -> Tuple[str, str]:
    """
    Короткие ОП-диалоги считаем неполноценным циклом и относим в Прочие.
    Применяется только к OP_IN/OP_OUT.
    """
    d, c = normalize_call_type_result(dept, call_type)
    if c not in ("OP_IN", "OP_OUT"):
        return d, c
    text = (transcript or "").strip()
    if not text:
        return "OTHER", "OTHER"
    try:
        from text_normalization import normalize_text

        text = (normalize_text(text) or "").strip()
    except Exception:
        pass
    if len(text) < 700:
        if c == "OP_OUT" and _has_op_outbound_surface_markers(transcript):
            if _is_op_outbound_immediately_rejected_no_substance(transcript):
                return "OTHER", "OTHER"
            return d, c
        if c == "OP_IN" and _is_short_fragmented_op_without_manager_or_customer_choice(transcript):
            return "OTHER", "OTHER"
        # Вх. ОП: v2 уже отсек «серый» OP_IN без темы авто; короткий диалог с моделью/ценой — валидный OP_IN.
        if c == "OP_IN" and not _v2_gray_op_in_without_substance(transcript):
            return d, c
        return "OTHER", "OTHER"
    return d, c


def _is_short_fragmented_op_without_manager_or_customer_choice(transcript: str) -> bool:
    """
    Короткий фрагмент с обрывочными OP-словами (цена/комплектация),
    но без перевода на менеджера и без явного клиентского выбора авто/покупки.
    Такие записи относим к Прочим (пример: 20662).
    """
    low = (transcript or "").lower().strip()
    if not low:
        return False
    if len(low) >= 700:
        return False
    has_manager_intro_or_transfer = bool(
        re.search(r"\bменеджер(?:а)?\s+отдела\s+продаж\b", low, re.I)
        or re.search(r"\b(?:перевед|перевож|переключ|соедин)\w*[^.!?]{0,90}\b(?:менеджер|отдел\s+продаж)\b", low, re.I)
    )
    if has_manager_intro_or_transfer:
        return False
    has_customer_purchase_intent = bool(
        re.search(
            r"\b(?:интересует|рассматрива\w*\s+покуп|хочу\s+купить|наличи\w*\s+автомоб|"
            r"какой\s+автомоб|модель|кредит|трейд[-\s]?ин|рассроч)\b",
            low,
            re.I,
        )
    )
    if has_customer_purchase_intent:
        return False
    weak_fragment_markers = (
        "цены автомобиля",
        "удешевление цены автомобиля",
        "комплектаци",
        "крышк",
        "болты",
    )
    return any(m in low for m in weak_fragment_markers)


def _finalize_service_guards_for_other(transcript: str, dept: str, call_type: str) -> Tuple[str, str]:
    """
    После основной классификации: жёсткие сервисные границы для «Прочих» (OTHER/OTHER).
    Устаревший OP_MISC нормализуется в OTHER/OTHER.
    """
    d = (dept or "").strip().upper()
    c = (call_type or "").strip().upper()
    if (d, c) in (("STO", "STO_IN"), ("STO", "STO_OUT"), ("OP", "OP_IN"), ("OP", "OP_OUT")):
        return d, c
    low = (transcript or "").lower()
    service_hard_sto = _has_sto_service_line_context(transcript) and any(
        p in low
        for p in (
            "записаться на то",
            "запись на то",
            "на то записаться",
            "диспетчер сервиса",
            "диспетчер сервис",
            "ассистент сервиса",
        )
    )
    if service_hard_sto:
        return "OTHER", "OTHER"
    service_like_non_op = (
        (
            _is_service_dispatcher_booking_verification_registry_other(transcript)
            or _is_sto_registry_other_service_work_without_scheduled_to(transcript)
            or _is_service_dispatcher_diagnostics_not_scheduled_to(transcript)
            or _has_sto_service_context(low[:6000])
            or _has_sto_service_line_context(transcript)
            or _sto_outbound_service_dispatcher_context(low[:6000])
            or any(
                p in low[:6000]
                for p in (
                    "диспетчер сервис",
                    "диспетчер сервиса",
                    "ассистент сервиса",
                    "по записи на завтра",
                    "по записи звоню",
                    "на завтра записан",
                    "на завтра записаны",
                    "запланирован ваш автомобиль в работу",
                    "запланирован автомобиль в работу",
                    "все ли в силе",
                    "всё ли в силе",
                    "приедете",
                    "контрольный звонок",
                    "напомним о визите",
                )
            )
        )
        and not (
            _v2_has_vehicle_purchase_substance(low)
            or "менеджер отдела продаж" in low
        )
    )
    if service_like_non_op:
        return "OTHER", "OTHER"
    if d == "OP" and c in ("OP_IN", "OP_OUT"):
        return d, c
    if c == "OP_MISC":
        return "OTHER", "OTHER"
    return d, c


def classify_by_transcript_legacy(transcript: str) -> Tuple[str, str]:
    """
    Запасной путь: прежний монолит правил по транскрипции.
    Возвращает (department, call_type).
    """
    if not transcript or not isinstance(transcript, str):
        return "OTHER", "OTHER"

    try:
        from text_normalization import normalize_text
        transcript = normalize_text(transcript)
    except ImportError:
        pass
    transcript = _normalize_stt_classification_artifacts(transcript)

    t = transcript.lower().strip()
    # STT-склейка: «ассистент-сервис» / «ассистент сервис» = линия «ассистент сервиса».
    t = re.sub(r"\bассистент[\s-]*сервис\b", "ассистент сервиса", t)
    t = re.sub(r"\bдиспетчер[\s-]*сервис\b", "диспетчер сервиса", t)
    t = re.sub(r"\b(?:енсервис|итмсервис|снсервис)\b", "ассистент сервиса", t)
    t = re.sub(r"\bдиспетчер\s+бари\b", "диспетчер сервиса дарья", t)
    t = re.sub(r"\bстажер\s+бари\b", "стажер дарья", t)
    # STT 13785: «на И ТУ» / «на ту» = на ТО; «Члюсти»/«Челсти» — линия сервиса.
    t = re.sub(r"\bна\s+и\s+ту\s+запис", " на то запис", t, flags=re.I)
    t = re.sub(r"\bна\s+ту\s+запис", " на то запис", t, flags=re.I)
    t = re.sub(r"\bч(?:ел|лю)сти\b", " чери сервис ", t, flags=re.I)
    # STT 13702: «на ТЛ» / «КНТО» = на ТО / ТО (запись).
    t = re.sub(r"\bна\s+тл\b", " на то ", t, flags=re.I)
    t = re.sub(r"\bкнто\b", " на то ", t, flags=re.I)
    if len(t) < 20:
        return "OTHER", "OTHER"

    # Входящий перенос уже существующей записи на ТО:
    # «хотел(а) бы перенести», затем «вы записаны к нам ...».
    if (
        any(p in t[:2200] for p in ("перенести то", "перенести запись", "перезапис"))
        and any(p in t[:2800] for p in ("вы записаны к нам", "записан к нам", "записана к нам"))
    ):
        return "STO", "STO_IN"

    # 0ft. Прочие: неудачный перевод на линию — не СТО вход (нет приёмки/записи на ТО).
    if _is_failed_sto_transfer_misc(t):
        return "OTHER", "OTHER"

    # 0ftw. Прочие: «не в тот дилерский центр» — переадресация в другой ДЦ, без записи (12586).
    if _is_wrong_dealer_center_redirect_other(transcript):
        return "OTHER", "OTHER"

    # 0bw. Прочие: Викинги/Чери + кузовной + имя мастера в начале (линия кузова, статус ремонта) — не СТО вх.
    if _is_body_shop_master_opening(t):
        return "OTHER", "OTHER"

    # 0bkr. Прочие: кузовной/ремонт — допоборудование, сетка, страховка не покрывает, готовность авто (16768).
    if _is_body_shop_repair_accessories_consult_other(transcript):
        return "OTHER", "OTHER"

    # 0bp. Прочие: исходящее информирование о поставке запчастей (стажёр и др.) — не СТО вх.
    if "поступили запчасти" in t and _is_outbound(t):
        return "OTHER", "OTHER"

    # 0bs-op. Входящий: линия сервиса приняла звонок, перевод на менеджера ОП состоялся → ОП_вх.
    if _is_service_transfer_to_op_sales_successful_inbound(transcript):
        return "OP", "OP_IN"

    # 0bs. Прочие: со линии сервиса перевод на другой отдел (страхование, запчасти, кузовной…) — не СТО вх.
    if _is_service_transfer_to_other_department(t):
        return "OTHER", "OTHER"

    # 0bsw. Прочие: сервисная линия → нейтральный перевод → внутренний контакт (дилерский центр / цех / смета), не СТО вх.
    if _is_sto_service_dispatcher_internal_handoff_other(transcript):
        return "OTHER", "OTHER"

    # 0bg. Прочие: гарантия на купленный авто (спор/разъяснение) — не ОП, даже если есть «комплектация», «пробег».
    if _is_post_purchase_warranty_misc(t):
        return "OTHER", "OTHER"

    # 0bgt. Прочие: B2B — банковские/независимые гарантии, тендеры, консалтинг (ищут, к кому обратиться) — не СТО/не ОП-сделка.
    if _is_b2b_bank_guarantee_tender_cold_call_other(t):
        return "OTHER", "OTHER"

    # 0bgty. Прочие: сторонний B2B-обзвон (Яндекс Такси для юрлиц и т.п.) — не ОП вх. (12757).
    if _is_third_party_b2b_cold_pitch_other(t):
        return "OTHER", "OTHER"

    # 0bu. Прочие: авто с пробегом / не Чери·Тенет при продаже (Гранта, Лада…) — не ОП Чери (5 менеджеров).
    if _is_used_cars_department_other(t):
        return "OTHER", "OTHER"

    # СТО исх.: сотрудник дилера звонит по ремонту/кондиционеру (10267); до «секретарь/перезвон» STO_IN.
    if _is_outbound(t) and _is_sto_dealer_employee_outbound_service_call(transcript):
        return "STO", "STO_OUT"

    # 0bha. СТО вх. (исключение узкого слоя): «диспетчер занят, оставьте номер/перезвоните».
    # В узком слое такие кейсы должны попадать в НЕ_ТО, поэтому широкий слой оставляем STO.
    if _is_priority_busy_dispatcher_leave_number_callback(t):
        return "STO", "STO_IN"

    # 0bh-op-svc. Прочие: исх. звонок с сайта (ОП), интерес к сервису, перевод на сервис не состоялся (16700).
    if _is_op_outbound_site_callback_service_transfer_failed_other(transcript):
        return "OTHER", "OTHER"

    # 0bh. СТО вх. (исключение узкого слоя): запрос на сервис, линия занята / номер на перезвон.
    if _is_service_secretary_callback_misc(t):
        return "STO", "STO_IN"

    # 0bhs. СТО вх. (исключение узкого слоя): контакт с целевым сотрудником не состоялся / только перезвон.
    if _is_sto_inbound_failed_target_contact_misc(t):
        return "STO", "STO_IN"

    # 0bin. Прочие: внутренняя координация по заказ-нарядам / ОПД / УПД — не СТО вх. (до блоков с «дарья»/стажёр).
    if _is_internal_staff_document_flow_other(t):
        return "OTHER", "OTHER"

    # 0bio. Прочие: внутренний диалог сотрудников без клиентского цикла (офис, площадка, ключи) — не ОП.
    if _is_internal_staff_dialogue_without_client_other(transcript):
        return "OTHER", "OTHER"

    # 0taxi. Прочие: заказ такси (ассистент сервиса), не ОП и не СТО-приёмка (8897).
    if _is_service_taxi_dispatch_other(t):
        return "OTHER", "OTHER"

    # 0bik. Прочие: просят переключить на кузовной цех / отдел кузова (админ, не приёмка ТО).
    if _is_client_asks_transfer_to_body_shop_other(t):
        return "OTHER", "OTHER"

    # 0bho. Прочие: входящий лид на ОП, все менеджеры заняты — только номер / обещание перезвона, без ОП.
    if _is_op_all_managers_busy_callback_only_misc(t):
        return "OTHER", "OTHER"

    # 0bhoc. Прочие: перевод на менеджера ОП не состоялся — только перезвон (14786).
    if _is_op_failed_transfer_callback_only_misc(t):
        return "OTHER", "OTHER"

    # 0bhob. Прочие: стойка записала номер, обещала перезвон менеджера — без разговора с ОП (13355).
    if _is_op_reception_phone_callback_no_manager_misc(t):
        return "OTHER", "OTHER"

    # 0bhoa. Прочие: мост Авито — клиент сбросил до разговора с менеджером ОП (12558).
    if _is_avito_bridge_client_hung_up_before_op_talk_misc(t):
        return "OTHER", "OTHER"

    # 0bhp. Прочие: страховка / архив / карточки клиента — не покупка авто, не ОП вх. (А. Шилкин и т.п.).
    if _is_insurance_archive_documents_other(t):
        return "OTHER", "OTHER"

    # 0bhx. Прочие: B2B-процесс передачи авто/аукциона, не цикл покупки Chery/Tenet.
    if _is_op_b2b_vehicle_transfer_process_other(t):
        return "OTHER", "OTHER"

    # 0bhq. Прочие: страховой отдел — расчёт/оформление полиса, не линия СТО (10722).
    if _is_insurance_department_call_other(t):
        return "OTHER", "OTHER"

    # 0bhj. Прочие: Jetour не ведём / не продаём, только админ — не ОП вх.
    if _is_admin_brand_unavailable_no_op_transfer_other(t):
        return "OTHER", "OTHER"

    # 0bhk. Прочие: промо-конкурс (велосипед) + CRM-отметка тест-драйва — не ОП вх./исх. (15337).
    if _is_op_promotional_contest_crm_other(t):
        return "OTHER", "OTHER"

    # 0bhl. Прочие: маркетолог / спонсорство / благотворительный фонд — не СТО, не НЕ_ТО (16512).
    if _is_op_marketing_sponsorship_charity_contact_other(t):
        return "OTHER", "OTHER"

    # 1sto_diag. СТО вх. (НЕ_ТО): диспетчер сервиса, но тема — диагностика/осмотр, не регламентное ТО.
    if _is_service_dispatcher_diagnostics_not_scheduled_to(transcript):
        return "STO", "STO_IN"

    # 1sto_app. СТО вх. (НЕ_ТО): приложение MyCherry / логин / СМС на линии диспетчера.
    if _is_service_dispatcher_remote_app_account_other(transcript):
        return "STO", "STO_IN"

    # 1sto_cancel. СТО вх. (НЕ_ТО): отмена/выход из записи, не приёмка нового регламентного ТО.
    if _is_service_dispatcher_cancellation_service_other(transcript):
        return "STO", "STO_IN"

    # 1sto_assist. СТО вх.: «на сервис надо» / Ниссан+ремонт·диагностика + линия ассистента/диспетчера сервиса (Юлия…).
    if _is_sto_service_intent_with_service_assistant_line(transcript):
        return "STO", "STO_IN"

    # 0bt. Прочие: трейд-ин / оценка выкупа (в т.ч. удалённая) без менеджера ОП из справочника — не ОП вх.
    if _is_trade_in_assessment_without_op_roster(t):
        return "OTHER", "OTHER"

    # 0b. СТО вх.: в начале — линия администратора/дилера, затем клиент по сервису (шиномонтаж, перенос записи, ТО).
    # Не «СТО исх.»: клиент дозванивается после приветствия админа/бота.
    first_600 = t[:600]
    admin_line = (
        ("администратор" in first_600 or "официальный дилер" in first_600)
        and any(g in first_600 for g in ["добрый день", "добрый вечер", "здравствуйте"])
    )
    client_sto_intent = any(
        p in t
        for p in [
            "шиномонтаж",
            "переобувк",
            "перенести запись",
            "перенос записи",
            "перезапис",
            "я звонил",
            "звонил сегодня",
            "сегодня звонил",
            "сегодня примерно",
            "профилактик",
            "кондиционер",
            "запись на то",
            "записаться на",
            "техническое обслуживание",
            "уточнить запись",
            "фамилия имя отчество",
            "фио автомобиля",
            "назовите фио",
            "скажите фио",
        ]
    )
    if admin_line and client_sto_intent:
        if not any(
            m in t
            for m in [
                "покупку автомобиля",
                "тест-драйв",
                "комплектаци",
                "хочу купить",
                "покупаю ",
                "интересует черри",
            ]
        ):
            if _has_sto_service_context(t) or any(
                x in t
                for x in [
                    "шиномонтаж",
                    "переобувк",
                    "профилактик",
                    "запись на",
                    "записаться на",
                    "то ",
                    "тэо",
                    "сервис",
                    "фамилия имя отчество",
                    "фио автомобиля",
                    "назовите фио",
                    "скажите фио",
                ]
            ):
                if not _is_sto_outbound_record_confirmation_opening(t):
                    if _has_sto_substantive_service_intent(t):
                        return "STO", "STO_IN"

    # 0x. Прочие: Викинги + кузовной (в т.ч. после нормализации «кузоно») + отдел запчастей;
    # звонок принял мастер кузовного, перевод на запчасти — нет диспетчера/ассистента/стажёра из справочника СТО.
    if "викинги" in t and _kuzov_body_topic(t) and any(
        p in t for p in ["отдел запасных частей", "отдел запчастей", "отдел запчаст"]
    ):
        sto_from_directory = (
            any(
                n in t
                for n in [
                    "андреева",
                    "юлия",
                    "юлию",
                    "александра",
                    "дарья",
                    "плаксина",
                    "гринкина",
                    "гранкина",
                ]
            )
            and any(x in t for x in ["диспетчер сервиса", "ассистент сервиса", "стажер"])
        )
        if not sto_from_directory:
            return "OTHER", "OTHER"

    # 0y. Прочие: исходящий перезвон «просили набрать» Викинги/Чери — не менеджер ОП и не диспетчер/ассистент/стажёр
    # из справочника (мастер и др., напр. Дмитрий: «Алло, Иванович? Просили набрать … Викинги … Дмитрий»).
    if any(p in t for p in ["просили набрать", "вас просили набрать", "просили вас набрать"]):
        brand = any(b in t for b in ["викинги", "виикинги", "чери", "черри", "cherry"])
        pseudo_out = _is_outbound(t) or bool(
            re.search(r"алло\s*[.,]?\s*[а-яё]{3,}", t[:500])
        )
        if brand and pseudo_out:
            op_named = any(
                m in t
                for m in [
                    "евгений",
                    "евгения",
                    "илья",
                    "илью",
                    "анастасия",
                    "андрей",
                    "андрея",
                    "захаров",
                    "евдокимов",
                    "калаев",
                    "калаева",
                    "краснощекова",
                    "краснощёкова",
                    "щеголев",
                    "менеджер отдела продаж",
                    "менеджер захаров",
                    "менеджер евдокимов",
                    "менеджер краснощекова",
                    "менеджер калаев",
                    "менеджер щеголев",
                    "это илья",
                    "это евгений",
                    "это анастасия",
                    "это андрей",
                ]
            )
            sto_named = (
                any(
                    n in t
                    for n in [
                        "андреева",
                        "юлия",
                        "юлию",
                        "александра",
                        "плаксина",
                        "гринкина",
                        "гранкина",
                        "дарья",
                    ]
                )
                and any(x in t for x in ["диспетчер сервиса", "ассистент сервиса", "стажер", "стажёр"])
            )
            if not op_named and not sto_named:
                return "OTHER", "OTHER"

    # 0. Прочие: не ОП (нет выбора авто) — гарантия, сервис после покупки, информирование о запчастях
    other_strong = [
        "претензия",
        "проверка качества",
        "отдел по работе с клиентами",
        "отделом по работе с клиентами",
        "отдел по работе с клиентом",
        "отделом по работе с клиентом",
        "клиентская служба",
        "клиентскую службу",
        "клиентской службы",
        "отдел запасных частей",
        "отдел запчастей",
        "отдел запчаст",
        "нас прервали",
        "поступили запчасти",
        "запчасти поступили",
        "запчасти для выполнения",
        "сервисная кампания",
        "машину отдавал",
        "машину отдавала",
        "машину восстановим",
    ]
    for phrase in other_strong:
        if phrase in t:
            # Отдел ЗЧ внутри сценария приёмки/ассистента СТО («передам в отдел запасных частей, проверим наличие колодок») — не Прочие
            if phrase in ("отдел запасных частей", "отдел запчастей", "отдел запчаст"):
                sto_on_line = (
                    _has_sto_service_context(t)
                    or "диспетчер" in t
                    or "ассистент сервиса" in t
                    or "с сервисным центром" in t
                    or "переключ" in t
                    or "перевед" in t
                ) and any(
                    n in t
                    for n in [
                        "андреева",
                        "юлия",
                        "юлию",
                        "александра",
                        "дарья",
                        "плаксина",
                        "гринкина",
                        "гранкина",
                    ]
                )
                if sto_on_line:
                    continue
            # Исключение: диспетчер/ассистент СТО участвует — не Прочие (машину отдавал, сервисная кампания)
            sto_in_ctx = (
                (_has_sto_service_context(t) or "диспетчер" in t or "с сервисным центром" in t or "переключ" in t or "перевед" in t)
                and ("андреева" in t or "юлия" in t or "юлию" in t or "александра" in t or "дарья" in t or "плаксина" in t or "гринкина" in t)
                and any(
                    m in t
                    for m in [
                        "замена",
                        "фамил",
                        "замену колес",
                        "замену колёс",
                        "шиномонтаж",
                        "то ",
                        "ремонт",
                        "диагностик",
                        "пробег",
                        "записаться",
                        "поменять",
                        "обслужива",
                        "техобслуживание",
                        "колодк",
                        "колонк",
                        "тормоз",
                        "для записи",
                        "по наличию",
                        "заявку передам",
                        "слушаю вас",
                    ]
                )
            )
            if sto_in_ctx and phrase in [
                "машину отдавал",
                "машину отдавала",
                "сервисная кампания",
            ]:
                continue  # пропустить, пусть СТО обработает
            # 17225: клиентская служба без перевода на диспетчера/ассистента — Прочие;
            # если позже есть реальная линия СТО — не глушим.
            if phrase in (
                "отдел по работе с клиентами",
                "отделом по работе с клиентами",
                "отдел по работе с клиентом",
                "отделом по работе с клиентом",
                "клиентская служба",
                "клиентскую службу",
                "клиентской службы",
            ) and _has_real_sto_dispatcher_assistant_booking_line(t):
                continue
            return "OTHER", "OTHER"

    # 0c. Прочие: перевод не состоялся — линия диспетчера занята, звонок вернулся
    # Клиент хотел на сервис, но разговор с диспетчером/ассистентом не состоялся
    transfer_failed = any(p in t for p in [
        "линия занята", "линия у диспетчера занята", "линия диспетчера занята",
        "линия сервиса занята", "линия на сервисе занята",
        "сервис занят", "сейчас сервис занят",
        "вернулся звоночек", "вернулся звонок", "звоночек вернулся",
    ]) or bool(re.search(r"линия[^.!?\n]{0,30}сервис[^.!?\n]{0,30}занят", t))
    if transfer_failed:
        # Диспетчер/ассистент реально участвовал в разговоре («Юлия. Здравствуйте» и т.п.) — не Прочие
        dispatcher_answered = any(p in t for p in [
            "диспетчер сервиса юлия", "диспетчер сервиса александра", "диспетчер сервиса андреева",
            "диспетчер сервиса плаксина", "ассистент сервиса ", "стажер дарья",
        ]) and any(g in t for g in ["здравствуйте", "добрый день", "слушаю"])
        if not dispatcher_answered:
            return "OTHER", "OTHER"

    # 0d. Прочие: личный/бытовой разговор (плитка, духовка, квартира)
    private_strong = [
        "плитку", "плитка", "духовк", "квартир", "ремонт квартир",
        "забрали плитку", "забрали духовку",
    ]
    if any(p in t for p in private_strong):
        return "OTHER", "OTHER"

    # 0d2. Прочие: личный/бытовой разговор (ТВ, соседи, трубы) — нет темы дилера, покупки авто, ремонта/сервиса,
    # нет фамилий/ролей менеджеров ОП и СТО из справочника.
    if len(t) >= 220:
        personal_life = any(
            h in t
            for h in [
                "россия культура",
                "культура канал",
                "канал культура",
                "императриц",
                "мединск",
                "телевизор",
                "смотрю телек",
                "сосед",
                "соседк",
                "трубах",
                " труба",
                "труба ",
                "воду крутит",
                "крутит воду",
                "мешаю тебе",
                "тебе мешаю",
                "не мешаю",
            ]
        )
        auto_dealer_ctx = any(
            m in t
            for m in [
                "викинги",
                "виикинги",
                "чери викинги",
                "дилер",
                "автосалон",
                "автомобил",
                "отдел продаж",
                "отдел запас",
                "отдел запчаст",
                "запчаст",
                "тест-драйв",
                "комплектац",
                "покупк автомоб",
                "купить машину",
                "купить авто",
                "новый автомобил",
                "тенет",
                "tenet",
                "chery",
                "записаться на",
                "шиномонтаж",
                "диагностик",
                "диспетчер сервиса",
                "ассистент сервиса",
                "стажер",
                "менеджер отдела",
                "менеджер захар",
                "евдокимов",
                "захаров",
                "калаев",
                "краснощек",
                "щеголев",
                "андреева",
                "плаксина",
                "гринкина",
                "гранкина",
                "викинги сервис",
                "чери сервис",
                "ремонт авто",
                "ремонт машин",
                "техобслуживание",
                "запись на то",
                "тэо",
            ]
        )
        if personal_life and not auto_dealer_ctx:
            return "OTHER", "OTHER"

    # 0k. Прочие: сторонние организации (не автосалон)
    other_companies = ["грандлайн", "клямера", "видеорегистратор"]
    if any(p in t for p in other_companies):
        return "OTHER", "OTHER"

    # 0t. Прочие: не автосалон — фитнес, спортзал, личные дела (нет авто/кредитов/тест-драйва)
    non_auto_business = ["спортал", "фитнес", "йога", "пилатес", "стретчинг", "абонемент", "гостевой визит"]
    auto_markers = ["викинги", "черри", "чери", "автомобил", "машин", "тест-драйв", "отдел продаж", "отдел продажный"]
    if any(p in t for p in non_auto_business) and not any(m in t for m in auto_markers):
        return "OTHER", "OTHER"

    # 0u. Прочие: «оставляли телефончик»/«оставляли телефон» после приветствия (глагол в пр. вр.) — исходящий перезвон.
    # Нет «переключу на менеджера», нет имени менеджера, нет намерений купить авто → Прочие 100%
    outbound_followup = re.search(r"оставлял[иа]\s+телефон(?:чик)?\b", t)
    if outbound_followup:
        has_op_transfer = any(p in t for p in [
            "переключу на менеджера", "перевожу на менеджера", "переключаю на менеджера", "переведу на менеджера",
            "переключаю на отдел продаж", "перевожу на отдел продаж", "переключу на отдел продаж", "переведу на отдел продаж",
        ])
        has_op_manager = any(m in t for m in [
            "евгений", "илья", "илью", "анастасия", "андрей", "захаров", "евдокимов", "калаев", "калаева", "краснощекова", "краснощёкова", "щеголев",
        ])
        has_op_intent = any(m in t for m in [
            "интересует", "тест-драйв", "комплектац", "хочу купить", "покупаю", "выставочн",
            "покупку автомобиля", "новый авто", "с пробегом", "подержанн",
        ])
        if not has_op_transfer and not has_op_manager and not has_op_intent:
            return "OTHER", "OTHER"

    # 0wm. Прочие: исходящий мастера дилера (сервисный контент, в продукте не STO_OUT).
    if _is_dealer_master_outbound_opening(t):
        return "OTHER", "OTHER"

    # 0wp. Прочие: исходящий мастера-приёмщика СТО (не ОП исх., даже если «это Евгений»).
    if _is_sto_master_priyomshchik_outbound_other(transcript):
        return "OTHER", "OTHER"

    # 0wk. Прочие: кузовной / внутренняя координация при дилере — до 0w и op_strong (иначе «Анастасия»,
    # op_strong уводит в ОП раньше правила 0q).
    if _should_classify_kuzov_as_other(t):
        return "OTHER", "OTHER"
    if _is_body_shop_internal_coordination(t):
        return "OTHER", "OTHER"

    # 0w3. Прочие: CRM follow-up «некоторое время назад интересовались… вопрос ещё актуален» (17256).
    if _is_op_crm_prior_interest_status_repeat_opening(t[:900]):
        return "OTHER", "OTHER"

    # 0w. ОП исх.: «Алло, [имя клиента]?» + приветствие + (менеджер ОП) + первый вопрос к клиенту
    # Пример: «Алло, Михаил? Добрый день. Евгений, Дилерский центр Викинги. Вы планировали к нам подъехать...»
    first_400 = t[:400]
    allo_client_name = re.search(
        r"алло\s*,\s*([а-яё]{3,})(?:\s+[а-яё]{2,})?\s*[,?]?",
        first_400,
    )
    if allo_client_name and ("викинги" in first_400 or "дилерский" in first_400):
        client_name = allo_client_name.group(1).split()[0].lower()
        # «Алло, здравствуйте» — приветствие, не «Алло, Михаил» (STT часто даёт так в начале входящего)
        _fake_name_after_allo = frozenset({"здравствуйте", "добрый", "день", "вечер", "секунду"})
        if (
            client_name != "алло"
            and client_name not in _OP_MANAGER_NAMES
            and client_name not in _STO_MANAGER_NAMES
            and client_name not in _fake_name_after_allo
        ):
            has_greeting = any(g in first_400 for g in ["добрый день", "добрый вечер", "здравствуйте"])
            # Имя после «Алло,» — клиент; одно вхождение «евгений»/«андрей» и т.д. не менеджер ОП
            _op_name_tokens = [
                "евгений", "илья", "анастасия", "андрей", "захаров", "евдокимов", "калаев", "калаева", "краснощеков", "щеголев",
            ]
            has_op_manager = False
            for m in _op_name_tokens:
                if m not in first_400:
                    continue
                if m == client_name and first_400.count(m) < 2:
                    continue
                has_op_manager = True
                break
            first_question_to_client = any(
                q in first_400 for q in [
                    "вы планировали", "подъехать", "посмотреть машину", "посмотреть авто",
                    "вопрос актуален", "вопрос ещё актуален", "планировали к нам",
                    "купили автомобиль", "купили авто", "что выбрали по итогу", "что выбрали",
                ]
            )
            if (
                has_greeting
                and has_op_manager
                and first_question_to_client
                and not _speaker_not_op_manager(transcript)
                and not _is_op_inbound_with_reception_gatekeeper(transcript)
                and not _is_op_crm_prior_interest_status_repeat_opening(first_400)
            ):
                return "OP", "OP_OUT"

    # 0w2r. Повторный OP follow-up: «[имя], ещё раз здравствуйте/добрый день» + «[менеджер], Викинги ...»
    # По продуктовой договорённости такие повторные контакты относим в Прочие (16510).
    first_500 = t[:500]
    repeat_client_greeting = bool(
        re.search(
            r"(?:^|[.!?]\s*)(?:алло[.,]?\s*)?[а-яё]{3,}(?:\s+[а-яё]{2,})?\s*,?\s*ещ[её]\s+раз\s+"
            r"(?:здравствуйте|добрый\s+(?:день|вечер))\b",
            first_500,
            re.I,
        )
    )
    repeat_op_manager_intro = bool(
        re.search(
            r"\b(евгений|илья|анастасия|андрей|захаров|евдокимов|краснощеков|калаев|калаева|щеголев)\b[^.!?]{0,50}\bвикинг",
            first_500,
            re.I,
        )
    )
    if repeat_client_greeting and repeat_op_manager_intro and not _speaker_not_op_manager(transcript):
        return "OTHER", "OTHER"

    # 0w2. ОП исх.: «Алло, [имя клиента], добрый день ещё раз» + «это [менеджер ОП]» — повторный звонок
    # Пример: «Алло. Антон, добрый день ещё раз. это снова Евгений Тольятти.»
    if "добрый день ещё раз" in first_400 or "добрый вечер ещё раз" in first_400:
        if re.search(r"алло[.,]?\s+[а-яё]{3,}\s*,", first_400):
            has_eto_manager = re.search(
                r"\bэто\s+(снова\s+)?(евгений|илья|анастасия|андрей|захаров|евдокимов|краснощеков|калаев|калаева|щеголев)\b",
                first_400,
            )
            if has_eto_manager and not _speaker_not_op_manager(transcript):
                return "OP", "OP_OUT"

    # 0w3. ОП исх.: «Алло, [имя клиента], добрый день/здравствуйте» + «это/что снова [менеджер ОП]».
    # STT часто даёт "что снова" вместо "это снова", поэтому учитываем оба варианта.
    first_500 = t[:500]
    has_allo_client = re.search(r"алло[.,]?\s+[а-яё]{3,}(?:\s+[а-яё]{2,})?\s*[,?]", first_500)
    has_greeting = any(g in first_500 for g in ["добрый день", "добрый вечер", "здравствуйте"])
    has_repeat_manager = re.search(
        r"\b(?:это|что)\s+снова\s+(евгений|илья|анастасия|андрей|захаров|евдокимов|краснощеков|калаев|калаева|щеголев)\b",
        first_500,
    )
    if (
        has_allo_client
        and has_greeting
        and has_repeat_manager
        and not _speaker_not_op_manager(transcript)
    ):
        return "OP", "OP_OUT"

    # 0s. Прочие: «Алло, [имя]» / «Алло? [имя]» в начале — исходящий (зовут клиента), нет маркеров ОП
    # Исключение: сильные маркеры СТО входящего (сервис, диспетчер, записаться, замена масла, фамилия) — не Прочие
    first = t[:200]
    if re.search(r"алло\s*[.,?]?\s*[а-яё]{3,}(?:\s+[а-яё]{2,})?\s*[,.]", first) and ("викинги" in t or "дилерский" in t):
        op_markers_present = any(m in t for m in [
            "интересует", "тест-драйв", "комплектац", "хочу купить", "покупаю", "выставочн",
            "отдел продаж", "менеджер отдела", "это илья", "это евгений", "это анастасия", "это андрей",
            "менеджер захаров", "менеджер краснощекова", "менеджер калаев", "менеджер щеголев", "менеджер евдокимов",
            "калаев андрей", "андрей калаев",
        ]) or bool(re.search(r"меня\s+[а-яё]{3,}\s+зовут\b", t[:1200]))
        sto_in_strong = (
            (_has_sto_service_context(t) or "диспетчер" in t or "ассистент" in t)
            and ("андреева" in t or "юлия" in t or "юлию" in t or "александра" in t or "дарья" in t or "лилия" in t or "лилию" in t)
            and any(m in t for m in [
                "записаться", "записат", "замена масла", "замену колес", "замену колёс", "замена колес", "замена колёс", "шиномонтаж",
                "фамил", "фамилию владельца", "напомните фамилию",
                "на кого", "оформлен",  # на кого автомобиль оформлен
                "с сервисом", "с сервисным центром",  # с сервисом соединить, с сервисным центром
            ])
        )
        # Линия Чери/Викинги + имя СТО (Дарья и др.) + тормоза/колодки/отдел запчастей — весь t, не только first[:200]
        sto_parts_voice = (
            ("викинги" in t or "чери" in t or "черри" in t)
            and ("дарья" in t or "андреева" in t or "юлия" in t or "юлию" in t or "александра" in t or "плаксина" in t or "гринкина" in t or "лилия" in t or "лилию" in t)
            and any(
                m in t
                for m in [
                    "колодк",
                    "тормозн",
                    "тормоз",
                    "тормозные",
                    "диск",
                    "отдел запчаст",
                    "отдел запасных",
                    "стоимость работ",
                    "слушаю вас",
                ]
            )
        )
        sto_out_service_invite = _is_outbound(t) and _is_sto_outbound_service_invite(transcript)
        if (
            not op_markers_present
            and not _has_op_outbound_surface_markers(transcript)
            and not sto_in_strong
            and not sto_parts_voice
            and not sto_out_service_invite
        ):
            return "OTHER", "OTHER"

    # 0v. Прочие: Слова → Алло → (имя клиента) → приветствие → (имя ассистента/менеджера) — признак исходящего.
    # СТО исх. не отслеживаем → Прочие.
    first_300 = t[:300]
    words_before_allo = re.match(r"^.{10,}?алло\b", first_300)
    allo_then_name_greeting = re.search(
        r"алло\s*[.,?]?\s+([а-яё]{3,}(?:\s+[а-яё]{2,})?)\s*[,.]\s*(добрый\s+день|добрый\s+вечер|здравствуйте)",
        first_300,
    )
    has_assistant_or_company = any(
        m in first_300 for m in ["викинги", "юлия", "юлию", "компания", "диспетчер", "ассистент", "андреева", "плаксина", "гринкина", "гранкина", "александра", "дарья", "лилия", "лилию"]
    )
    sto_context = any(
        m in t for m in ["масло", "масляный", "замена масла", "редуктор", "вариатор", "записаться", "запись на то", "то-", "диагностик"]
    ) or _has_sto_service_context(t)
    op_markers_0v = any(
        m in t for m in [
            "интересует", "тест-драйв", "комплектац", "хочу купить", "покупаю", "выставочн",
            "отдел продаж", "менеджер отдела", "это илья", "это евгений", "это анастасия", "это андрей",
        ]
    )
    if words_before_allo and allo_then_name_greeting and has_assistant_or_company and sto_context and not op_markers_0v:
        return "OTHER", "OTHER"

    # 1sto. СТО вх.: запись на ТО, «ранее обслуживались», замена масла — однозначно приёмка СТО
    # Не требует диспетчера/ассистента по имени — контент однозначен
    sto_to_clear = (
        ("ранее обслужив" in t or "по поводу то" in t or "по поводу тэо" in t or "то по счету" in t or "то по счёту" in t or "какое то по" in t)
        and any(m in t for m in [
            "замена масла", "замену масла", "масляного фильтра", "моторного масла",
            "техническое обслуживание", "запись на то", "записаться на то",
            "первое то", "второе то", "третье то", "первое техническое",
        ])
        and not any(s in t for s in ["тест-драйв", "хочу купить", "покупку автомобиля", "выставочн"])
    )
    if sto_to_clear:
        if _is_outbound(t):
            return "STO", "STO_OUT"
        return "STO", "STO_IN"

    # 1sto_acc. СТО вх.: установка доп.оборудования (фаркоп/проводка/монтаж), даже если звучат
    # модель/цена («Tiggo 7 Pro Max», «сколько стоит») — это сервисная запись, не продажа авто.
    if _is_sto_accessory_install_dialog(t):
        if _is_outbound(t):
            return "OTHER", "OTHER"
        return "STO", "STO_IN"

    # 1sto_zch. СТО вх.: замена/оценка тормозов, колодок, дисков + отдел запчастей + линия Чери/Викинги — не ОП
    # Пример: Nissan на сервисе, «переведу в отдел запчастей», обсуждение Sangsin/оригинала, стоимость работ.
    sto_brake_or_disks = any(
        p in t
        for p in (
            "колодк",
            "тормозн",
            "тормоз",
            "тормозные",
            "диск",
            "сангсин",
            "шиномонтаж",
            "сезон шиномонтаж",
            "сезон шиномонтажа",
        )
    )
    sto_parts_dept = any(p in t for p in ("отдел запчаст", "отдел запасных", "отдел зч"))
    dealer_brand = any(b in t for b in ("викинги", "чери", "черри", "chery"))
    op_new_car_block = any(
        m in t
        for m in (
            "тест-драйв",
            "комплектаци",
            "хочу купить",
            "покупаю ",
            "покупку автомобиля",
            "покупка автомобиля",
            "новый автомобил",
            "выставочн",
            "интересует черри",
            "интересует тенет",
            "интересует chery",
        )
    )
    if sto_brake_or_disks and (sto_parts_dept or ("стоимость работ" in t or "цена работ" in t)) and dealer_brand and not op_new_car_block:
        if _is_outbound(t):
            return "OTHER", "OTHER"
        return "STO", "STO_IN"
    # То же без явного «отдел запчастей» в STT: стажёр/имя Дарья + Викинги + тормоза/колодки
    if (
        sto_brake_or_disks
        and dealer_brand
        and ("дарья" in t or "стажер" in t or "стажёр" in t or "слушаю вас" in t)
        and not sto_parts_dept
        and not op_new_car_block
    ):
        if _is_outbound(t):
            return "OTHER", "OTHER"
        return "STO", "STO_IN"

    # 0bv. Прочие: «Это Илья/…» от банка / счёт-НДС без Викинги·Заставная и без темы покупки авто — не ОП вх.
    if _is_non_dealer_op_homonym_bank_or_billing_intro(t):
        return "OTHER", "OTHER"

    # 0gl. Прочие: Викинги / Наталья+Лариса + B2B по запчастям у контрагента; «комплектация» в речи про деталь — не ОП.
    if _is_vikings_b2b_parts_supplier_call(t):
        return "OTHER", "OTHER"

    # 0gh. Прочие: быт / бытовая техника без темы автосалона — не ОП (ложное «комплектация», «цвет» не про авто).
    if _is_non_automotive_domestic_context(transcript):
        return "OTHER", "OTHER"

    # 0wpe. Прочие: самопредставление мастера-приёмщика в голове (до op_strong), без требования исходящего.
    if _is_sto_master_priyom_self_intro_early_other(transcript):
        return "OTHER", "OTHER"

    # 1sto_hand. СТО вх.: оф. дилер / админ в начале, затем Юлия Андреева / сервис — до op_strong («анастасия» и т.д.).
    if _is_sto_after_dealer_or_op_admin_handoff(transcript):
        return "STO", "STO_IN"

    # 1sto_line. СТО вх.: итмсервис/сервис + Юлия/Андреева в шапке — до OP gatekeeper (8964).
    if _is_sto_inbound_direct_service_line_opening(transcript):
        return "STO", "STO_IN"

    # 1sto_zast. СТО вх.: Заставная + диспетчер (STT «Бари»≈Дарья) + цена/состав ТО (9091).
    if _is_sto_inbound_zastavnaya_dispatcher_to_inquiry(transcript):
        return "STO", "STO_IN"

    # 1sto_exist. СТО вх.: клиент уже записывался на ТО, перезвон после звонка жене/другому (8963).
    if _is_inbound_client_existing_booking_after_misdirected_call(transcript):
        return "STO", "STO_IN"

    # ОП вх.: администратор / стойка → «переключаю вам» → менеджер ОП (8846); до op_strong и _speaker_not_op_manager.
    _admin_op_in = _is_op_inbound_admin_handoff_to_op_manager(transcript)
    if (
        _is_op_inbound_with_reception_gatekeeper(transcript)
        and (not _is_outbound(t) or _admin_op_in)
        and (not _has_op_outbound_surface_markers(transcript) or _admin_op_in)
        and _has_op_sales_conversation_substance(transcript)
        and not _sto_booking_intake_blocks_false_op_gatekeeper(transcript)
        and not _is_inbound_admin_transfer_to_used_cars_manager(transcript)
    ):
        if _has_sto_greeting_service_with_dispatcher_name(transcript) and _has_sto_substantive_service_intent(
            t
        ):
            return "STO", "STO_IN"
        return "OP", "OP_IN"

    # 1. ОП — проверка первой (маркеры: менеджер, покупка, комплектации, скидка, визитка и т.д.)
    op_transfer_strong = [
        "переключаю на менеджера", "перевожу на менеджера",
        "переключу на менеджера", "переведу на менеджера",
        "переключаю вас на менеджера", "перевожу вас на менеджера",
        "переключу вас на менеджера", "переведу вас на менеджера",
        "переключаю на отдел продаж", "перевожу на отдел продаж",
        "переключу на отдел продаж", "переведу на отдел продаж",
        "переключаю вас на отдел продаж", "перевожу вас на отдел продаж",
        "переключу вас на отдел продаж", "переведу вас на отдел продаж",
    ]
    op_manager_in_transfer = (
        "евгений", "евгения", "евгению", "илья", "илью", "анастасия", "анастасию", "андрей", "андрея",
        "захаров", "захарова", "евдокимов", "евдокимова", "калаев", "калаева", "краснощекова", "краснощёкова", "щеголев", "щеголева",
    )
    for phrase in op_transfer_strong:
        if phrase in t and any(m in t for m in op_manager_in_transfer):
            is_op_out = _is_op_outbound(t)
            return "OP", "OP_OUT" if is_op_out else "OP_IN"

    op_strong = [
        "это илья", "это евгений", "это анастасия", "это андрей",
        "менеджер захаров", "менеджер евдокимов", "менеджер краснощекова", "менеджер калаев", "менеджер щеголев",
        "калаев андрей",
        "андрей калаев",
        "менеджер евгений", "менеджер илья", "менеджер анастасия", "менеджер андрей",
        "менеджер отдела продаж", "менеджер отдела продажный",
        "отдел продаж", "отделе продаж", "отдел продажный", "дел продаж",
        "интересует черри", "интересует тенет",
        "интересует chery", "интересует tenet",
        "вас интересовал", "по поводу покупки", "что решили",
        # Покупка, комплектации, условия
        "покупку автомобиля рассмотреть", "покупку автомобиля",
        # «комплектаци» — только если не сервисный контекст (см. отдельную ветку в цикле)
        "комплектаци",
        "краснощекова", "краснощёкова", "анастасия",
        "какой цвет", "какие цвета", "цвета есть",
        "покупку в кредит", "в кредит или за наличные",
        "скидка максимальная", "максимальная скидка",
        "электронную визитку", "отправлю электронную визитку",
    ]
    for phrase in op_strong:
        if phrase == "комплектаци":
            if "комплектаци" not in t:
                continue
            if _service_context_blocks_complictaciya_op(t):
                continue
        elif phrase not in t:
            continue
        # «Отдел продаж» упомянут, но звонок по СТО (диспетчер, Юлия + ТО/диагностика) → СТО
        sto_overrides_op = (
            ("диспетчер" in t or "диспетчер сервиса" in t)
            and any(n in t for n in ["юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина"])
            and any(m in t for m in [
                "диагностик", "сервис интересует", "интересует сервис",
                "то-0", "то-5", "то -", "тэо", "тэо-", " сдать", "сдала ", "остановить работ",
                "стабилизатор", "просто работ", "свои будут",
            ])
        )
        if sto_overrides_op and phrase in ("отдел продаж", "отделе продаж", "отдел продажный", "дел продаж"):
            continue  # пропустить, пусть СТО обработает
        # Запись на ТО, «ранее обслуживались», замена масла — СТО, не ОП (комплектация для сервиса, не продажи)
        sto_to_reception = (
            ("ранее обслужив" in t or "по поводу то" in t or "по поводу тэо" in t or "то по счету" in t or "какое то по" in t)
            and any(m in t for m in ["замена масла", "замену масла", "техническое обслуживание", "запись на то", "записаться на то", "первое то", "второе то", "третье то"])
        )
        if sto_to_reception and phrase in ("комплектации", "комплектаци"):
            continue  # пропустить, пусть СТО обработает
        # «Комплектаци» в речи про деталь/гарантию/сигнализацию, не про покупку комплектации авто — исходящий СТО
        if phrase in ("комплектации", "комплектаци") and (
            "диспетчер сервиса" in t or "ассистент сервиса" in t
        ) and any(
            n in t for n in ["андреева", "юлия", "юлию", "плаксина", "гринкина", "гранкина", "александра", "дарья"]
        ) and any(
            h in t for h in (
                "гарантийн", "запчаст", "заказ наряд", "сигнализац", "штатн",
            )
        ):
            continue
        # Лицензии ПО / Автомаркет / Abton: «по поводу покупки лицензии», «комплектация» поставки — не ОП
        if phrase in ("по поводу покупки", "комплектации", "комплектаци") and _is_software_licensing_vendor_call(t):
            continue
        # ФИО клиента («Анастасия», реже фамилия ОП) совпало с маркером op_strong; линия СТО + ТО/запись — не ОП и не Прочие
        sto_assistant_line = (
            _has_sto_service_context(t)
            or "ассистент сервиса" in t
            or "диспетчер сервиса" in t
            or ("диспетчер" in t and any(n in t for n in ["юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина"]))
        )
        sto_to_intent = any(
            m in t
            for m in [
                "записаться на", "запись на то", "запись на сервис", "на то записаться",
                "можно на то", "второе то", "третье то", "первое то", "четвёртое то",
                "то-2", "то 2", "тэо", "техобслуживание", "замена масла", "замену масла",
                # исходящий СТО: гарантия / запчасти / согласование визита (без точной «записаться на то»)
                "гарантийн", "заказ наряд", "гарантийный заказ",
                "запчаст", "записываю", "запишу", "записать",
                "сдать автомобиль", "сдать авто",
                "стабилизатор", "просто работ", "свои будут", "поменять",
            ]
        )
        # Имя клиента «Анастасия»/«Краснощекова» совпало с маркером ОП; явный диспетчер/ассистент сервиса + имя СТО — не ОП.
        if phrase in ("анастасия", "краснощекова", "краснощёкова") and sto_assistant_line and (
            "диспетчер сервиса" in t or "ассистент сервиса" in t
        ) and any(
            n in t for n in ["андреева", "юлия", "юлию", "плаксина", "гринкина", "гранкина", "александра", "дарья"]
        ):
            continue
        if phrase in ("анастасия", "краснощекова", "краснощёкова") and sto_assistant_line and any(
            n in t for n in ["андреева", "юлия", "юлию", "плаксина", "гринкина", "гранкина", "александра", "дарья"]
        ) and sto_to_intent:
            continue
        if phrase in ("анастасия", "краснощекова", "краснощёкова") and (
            _should_classify_kuzov_as_other(t) or _is_body_shop_internal_coordination(t)
        ):
            continue
        if sto_assistant_line and _has_sto_substantive_service_intent(t):
            continue
        if _speaker_not_op_manager(transcript):
            return "OTHER", "OTHER"
        is_op_out = _is_op_outbound(t)
        return "OP", "OP_OUT" if is_op_out else "OP_IN"

    # 0m. Прочие: административные — уточнение email, контактов (нет ОП/СТО)
    admin_strong = ["адрес электронной почты", "официальной почты", "адрес почты"]
    if any(p in t for p in admin_strong):
        return "OTHER", "OTHER"

    # 0e. Прочие: автомобиль уже на сервисе (звонок о статусе/готовности)
    # Исключение: «автомобиль у нас на кого оформлен» — вопрос приёмки, не «авто у нас» (на сервисе)
    car_at_service = [
        "звоню по вашему автомобилю", "звоню по вашему авто",
        "работы закончили", "он готов", "машина готова",
        "авто у нас",
    ]
    if any(p in t for p in car_at_service):
        return "OTHER", "OTHER"
    # Исключение: «автомобиль у нас ранее обслуживали?», «на кого автомобиль оформлен» — вопрос приёмки СТО
    sto_reception = any(x in t for x in ["на кого оформлен", "на кого автомобиль оформлен", "на вас оформлен", "ранее обслужив"])
    if "автомобиль у нас" in t and not sto_reception:
        return "OTHER", "OTHER"

    # 0j. Прочие: СТО исходящий без диспетчеров/ассистентов — звонок о ремонте/диагностике (авто на сервисе)
    # «Дилерский центр Чери», называют клиента по имени, обсуждают датчики, проводку — нет ОП, нет диспетчеров
    if _is_outbound(t):
        no_sto_dispatcher = not any(p in t for p in ["диспетчер сервиса", "ассистент сервиса", "стажер дарья"])
        repair_report = any(p in t for p in [
            "датчик кислорода", "обрыв в проводке", "обрыв проводки",
            "по результатам диагностики", "по диагностике",
        ])
        no_op = not any(m in t for m in ["интересует", "тест-драйв", "комплектац", "хочу купить", "покупаю", "выставочн"])
        if no_sto_dispatcher and repair_report and no_op:
            return "OTHER", "OTHER"

    # 0f. Прочие: представляется мастер кузовного цеха (не приёмка, не продажи)
    if "мастер кузовного" in t:
        return "OTHER", "OTHER"

    # 0q. Прочие: кузовной (любое обсуждение кузовного цеха). Ранняя ветка — см. 0wk.
    if _should_classify_kuzov_as_other(t):
        return "OTHER", "OTHER"

    # 0r. Прочие: преимущественно страховой звонок (ОСАГО/КАСКО), не ремонт/гарантия на линии СТО
    sto_markers = _has_sto_service_context(t) and ("диспетчер" in t or "ассистент" in t or any(n in t for n in ["юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина", "александра", "дарья"]))
    sto_repair_or_warranty = any(
        p in t
        for p in [
            "гарант", "не работает", "ремонт", "диагностик", "слесар",
            "аккумулятор", "генератор", "техобслуживание", "записаться на",
            "запись на то", "на станц", "со станц",  # «со станции», «на станцию»
            "дилерств",  # окончание дилерства по марке, перенаправление в другой ДЦ
        ]
    )
    if sto_markers and any(p in t for p in ["осаго", "каско"]) and not sto_repair_or_warranty:
        return "OTHER", "OTHER"
    if any(p in t for p in ["окрашен", "арматурщик"]):
        return "OTHER", "OTHER"

    # 0h. Прочие: кузовной цех, покраска/красил (ремонт кузова, не стандартная приёмка СТО)
    if _kuzov_body_topic(t) and any(p in t for p in ["красил", "покраск", "порог", "краска"]):
        return "OTHER", "OTHER"

    # 0n. Прочие: кузовной + статус ремонта (в работе, сдавал, сколько делать)
    if _kuzov_body_topic(t) and any(p in t for p in ["в работе", "сдавал", "сколько делать"]):
        return "OTHER", "OTHER"

    # 0o. Прочие: восстановление, кузов, разбит (ремонт кузова, оценка ущерба)
    if any(p in t for p in ["восстановление", "по кузову", "разбит", "разбита"]):
        return "OTHER", "OTHER"

    # 0i. Прочие: кузовной + мастер/Павел/эксперт — обсуждение ремонта с мастером (не приёмка СТО, не ОП)
    # Исключение: мастерприемщик/мастер приёмщик — приёмщик СТО, заказ-наряд на ТО; при диспетчере+ТЭО — СТО
    if _kuzov_body_topic(t) and any(p in t for p in ["павел", "эксперт"]):
        return "OTHER", "OTHER"
    if _kuzov_body_topic(t) and "мастер" in t and "мастерприемщик" not in t and "мастер приёмщик" not in t and "мастер приемщик" not in t:
        return "OTHER", "OTHER"

    # 0l. Прочие: кузовной (+ STT «кузав…») + страховой/направление/осмотр — не приёмка слесарки СТО.
    if _kuzov_body_topic(t) and any(
        p in t
        for p in [
            "от страховой",
            "направление",
            "на осмотр",
            "страхов",
            "по страховому",
        ]
    ):
        return "OTHER", "OTHER"

    # 0a. Прочие: только перенос/подтверждение записи, без обсуждения выбора авто (не «запись на сервис/то» — это СТО)
    op_car_markers = ["интересует", "тест-драйв", "комплектац", "хочу купить", "покупаю", "выставочн"]
    record_phrase = any(p in t for p in ["по поводу записи", "запись идёт", "записан автомобиль", "записан авто"]) or ("запись на " in t and "запись на сервис" not in t and "запись на то" not in t)
    sto_record_context = (
        (_has_sto_service_context(t) and any(n in t for n in ["юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина", "диспетчер", "ассистент"]))
        or ("диспетчер" in t and any(n in t for n in ["юлия", "юлию", "андреева", "плаксина", "гринкина", "гранкина"]))
        or ("шиномонтаж" in t)
        or ("переобувк" in t)
        or (
            "на кого оформлен" in t
            or "автомобиль на кого оформлен" in t
            or "на вас автомобиль оформлен" in t
            or "на кого автомобиль оформлен" in t
            or "ранее обслужив" in t
            or "напомните фамилию" in t
            or "фамилию владельца" in t
            or "фамилия имя отчество" in t
            or "фио автомобиля" in t
            or "назовите фио" in t
            or "скажите фио" in t
        )
    )
    if record_phrase and not any(m in t for m in op_car_markers) and not sto_record_context:
        if not _is_sto_to_booking_live_dialog_not_secretary_misc(t):
            return "OTHER", "OTHER"

    # 0b. Прочие: обсуждение запчастей (наличие, под заказ), не запрос на ремонт/замену
    # Замена магнитолы может относиться и к СТО — не включать в parts_markers
    parts_markers = ["тормозные колодки", "колодки", "диски под заказ", "дисков нет", "запчасти", "замена будет"]
    sto_request = any(s in t for s in [
        "хочу заменить", "можно заменить", "нужна замена", "мне нужна замена",
        "хочу отремонтировать", "можно отремонтировать",
        "не работает", "не греет", "шумит", "дребезжит", "дребезжат", "продиагностировать",
        "замена масла", "замену масла", "записаться на то", "запись на то",  # ТО, масло — сервис, не только запчасти
        "шиномонтаж", "записаться на шиномонтаж", "записаться на сервис",
        "напомните фамилию", "фамилию владельца",  # протокол приёмки СТО
        "поменять", "можно ли у вас",  # «можно ли у вас поменять колодки»; STT: «колонки»
        "обслуживаем", "обслуживаетесь", "то делаем", "делаем то",
    ])
    if any(p in t for p in parts_markers) and not any(m in t for m in op_car_markers) and not sto_request:
        return "OTHER", "OTHER"

    # 0c. Прочие: выдача авто после ремонта (заберете, по автомобилю), не продажа
    if ("по автомобилю" in t and "заберете" in t) or ("заберете" in t and ("восстановим" in t or "работа" in t or "замена" in t)):
        return "OTHER", "OTHER"

    # 0g. Прочие: только логистика/прибытие — нет намерения купить авто или отдать на ремонт
    # Пример: «Приеду через 5 минут», «Ожидаем вас», «Поворачиваю на Дзержинского», «Чери кольцо пришло»
    op_intent = any(m in t for m in [
        "интересует", "тест-драйв", "комплектац", "хочу купить", "покупаю", "выставочн",
        "новый авто", "новый автомобиль", "с пробегом", "подержанн", "рассрочк", "трейд-ин",
    ])
    sto_intent = any(m in t for m in [
        "отремонтировать", "заменить", "продиагностировать", "записаться на сервис",
        "запись на сервис", "запись на то", "приёмка", "приемка", "не работает", "не греет",
        "шумит", "дребезж", "слесарн", "кузовн", "техобслуживание", "диагностик",
        "шиномонтаж", "напомните фамилию", "фамилию владельца",
    ])
    logistics_only = any(m in t for m in [
        "ожидаем вас", "приеду", "приеду через", "буду через", "минут буду", "минут я буду",
        "разворачиваюсь", "поворачиваю на", "кольцо пришло", "уже еду", "по пути",
    ])
    if logistics_only and not op_intent and not sto_intent:
        return "OTHER", "OTHER"

    # 1sto_oil. СТО вх. (НЕ_ТО): работы сервиса без явного регламентного ТО.
    if _is_sto_registry_other_service_work_without_scheduled_to(transcript):
        return "STO", "STO_IN"

    # 1sto_verify. СТО вх. (НЕ_ТО): уточнение/проверка уже существующей записи, не новая запись на ТО.
    if _is_service_dispatcher_booking_verification_registry_other(transcript):
        return "STO", "STO_IN"

    # 2. СТО входящие: сильные маркеры
    # Составные маркеры (по 2–3 критериям). Одно слово «сервис» недостаточно — нужны фразы контекста.
    # — викинги сервис + имя ассистента
    # — диспетчер сервиса | ассистент сервиса | (сервис-фразы + имя из СТО)
    sto_assistant_names = ["андреева", "плаксина", "гринкина", "гранкина", "александра", "юлия", "юлию", "дарья", "лилия", "лилию"]  # гранкина — ошибка STT, юлию/лилию — вин.пад.
    has_sto_name = any(n in t for n in sto_assistant_names)
    if not _speaker_not_sto_manager(transcript):
        if (_has_sto_service_context(t) and has_sto_name) or _has_sto_service_line_context(transcript):
            if _is_service_transfer_to_op_sales_successful_inbound(transcript):
                return "OP", "OP_IN"
            is_outbound = _is_outbound(t)
            if is_outbound and _is_sto_outbound_record_only(t):
                return "STO", "STO_OUT"
            if is_outbound:
                if _is_sto_outbound_service_invite(transcript):
                    return "STO", "STO_OUT"
                return "STO", "STO_OUT"  # исх. сервис без сценария ТО -> STO_OUT + НЕ_ТО
            if _has_sto_substantive_service_intent(t):
                return "STO", "STO_IN"

    sto_strong = [
        "стажер дарья",
        "диспетчер сервиса плаксина",
        "диспетчер сервиса андреева",
        "диспетчер сервиса гринкина",
        "диспетчер сервиса гранкина",
        "ассистент сервиса гринкина",
        "ассистент сервиса гранкина",
        "гринкина юлия",
        "гранкина юлия",
        "диспетчер сервиса александра",
        "диспетчер сервиса юлия",
        "викинги сервис",
        "записаться на сервис",
        "на сервис записаться",
        "запись на сервис",
        "со сервисом связаться",
        "с сервисом связаться",
        "с сервисом соединить",
        "с сервисом можно соединить",
        "соединить с сервисом",
        "с сервисным центром соединиться",
        "с сервисным центром",
        "отдел ремонта",
        "уточнить время записи",
        "на кого автомобиль оформлен",
        "на кого оформлен",
        "автомобиль на кого оформлен",
        "на вас автомобиль оформлен",
        "ранее обслуживали",
        "ранее обслуживались",
        "какие работы сделать",
        "какие работы необходимо",
        "фамилию владельца",
        "фамилия владельца",  # Фамилия владельца? (им.пад.)
        "скажите фамилию владельца",
        "напомните фамилию владельца",
        "напомните, пожалуйста, фамилию владельца",
        "напомните фамилию",
        "госномер",
        "гос номер",
        "запись на то",
        "записаться на то",
        "на то записаться",
        "на то хотел",
        "техническое обслуживание",
        "записаться на техническое обслуживание",
        "шиномонтаж",
        "записаться на шиномонтаж",
        "переобувк",
        "замену колес",
        "замену колёс",
        "замена колес",
        "замена колёс",
        "записаться на замену колес",
        "записаться на замену колёс",
        "сервисные кампании",
        "гарантийный случай",
        "гарантийного случая",  # по гарантийного случая — СТО
        "по гарантии",
        "гарантийн",  # гарантийный ремонт / гарантийные обязательства
        "переобуться",
        "развал",
        "обслуживаюсь",
        "обслуживается",
        # ТО (техобслуживание): сделать ТО, пройти ТО, первое/второе/третье ТО, ТО — цена. ТВ — ошибка STT
        "уточнить цену на то",
        "уточнить цену на тв",
        "стоимость то",
        "узнать стоимость то",
        "сделать то",
        "сделать тэо",
        "тэо сделать",
        "пройти то",
        "то можно делать",
        "первое то",
        "второе то",
        "третье то",
        "четвёртое то",
        "то -",
        "замена масла",
        "меняется масло",
        "приёмка слесарного цеха",
        "приемка слесарного цеха",
        "слесарный цех",
        "заменить масло",
        # Инициатива клиента
        "хочу отремонтировать", "можно отремонтировать",
        "хочу заменить", "можно заменить", "хочу поменять", "можно поменять",
        "хочу продиагностировать", "можно продиагностировать", "продиагностировать",
        "загнать",  # загнать на сервис/диагностику
        # Неисправность
        "не работает", "не греет", "шумит", "дребезжит", "дребезжат",
        # Клиент спрашивает в начале
        "это сервис", "это слесарный цех", "это сто", "это эстэо", "эстэо",
        # Ассистент/стажер спрашивает
        "как к вам обращаться", "как вам обращаться", "как могу обращаться",
        "как к вам могу обращаться", "как к вам обращаются",
        # Сотрудник отвечает — входящий (слушаю вас = звонят ему/ей)
        "слушаю вас",
        "слушаем",
        # Запись на сервис
        "плотная запись",
    ]
    for phrase in sto_strong:
        matched = phrase in t
        # «это сто» совпадает с «это стоматология» — требуем «сто» как отдельное слово
        if phrase == "это сто" and matched:
            matched = bool(re.search(r"\bэто\s+сто\b", t))
        if matched:
            if _speaker_not_sto_manager(transcript):
                return "OTHER", "OTHER"
            is_outbound = _is_outbound(t)
            if is_outbound and _is_sto_outbound_record_only(t):
                return "OTHER", "OTHER"
            if is_outbound:
                if _is_sto_outbound_service_invite(transcript):
                    return "STO", "STO_OUT"
                return "OTHER", "OTHER"
            if not _has_sto_substantive_service_intent(t):
                continue
            return "STO", "STO_IN"

    # 3. Исключения: кузовной, КСО — однозначно СТО
    sto_exclusive = [
        "кузовной ремонт", "кузовной цех", "покраск", "вмятин", "царапин", "дтп", "ксо ",
    ]
    for w in sto_exclusive:
        if w in t:
            is_outbound = _is_outbound(t)
            if is_outbound:
                return "OTHER", "OTHER"  # СТО исх. не выделяем — в Прочие
            return "STO", "STO_IN"

    # Исходящий ОП по лиду (сайт / CRM / Авито) — до весовых маркеров (11257: «запрос с Авито», T8).
    if _is_op_outbound_primary_site_lead(transcript):
        return "OP", "OP_OUT"

    # Взвешенные маркеры (избегаем двусмысленных: "сервис", "записаться" — могут быть в ОП)
    sto_markers = {
        "слесарн": 2, "ремонт авто": 2, "ремонт машин": 2, "техобслуживание": 2, "диагностик": 2,
        "масло ": 1, "фильтр": 1, "подвеск": 2, "тормоз": 1, "развал": 2,
        "шиномонтаж": 2, "приемк": 1, "приёмк": 1,
    }
    # Бренды (чери, тенет) сами по себе не маркеры — только в контексте (интересует, купить и т.п.)
    op_markers = {
        "комплектац": 2, "интересует": 1, "интересующ": 2, "хочу купить": 2, "покупаю": 2,
        "с пробегом": 2, "подержанн": 2, "новый автомобиль": 2, "новый авто": 2,
        "тест-драйв": 2, "тестовая поездка": 2, "когда покупать": 2, "когда покупка": 2,
        "новая модель": 2, "выставочн": 1, "авто в наличии": 2, "рассрочк": 1,
        "трейд-ин": 2, "приезжайте": 1, "смс": 1,
        "какие цвета": 2, "какие скидки": 2, "скидк": 1,
        "цвет": 1,  # в контексте выбора авто
    }
    sto_score = sum(weight for m, weight in sto_markers.items() if m in t)
    if re.search(r"\bдиагност", t):
        sto_score += 2
    op_hits = [
        m
        for m in op_markers
        if m in t
        and not (m == "покупаю" and _pokupaju_is_personal_shopping_not_vehicle_sale(t))
    ]
    op_score = sum(op_markers[m] for m in op_hits)
    weak_op_only_hits = {"цвет", "приезжайте"}
    # Слабые маркеры «цвет»/«приезжайте» не считаем достаточными для OP без других продуктовых сигналов.
    if op_hits and set(op_hits).issubset(weak_op_only_hits):
        op_score = 0

    # Отдел «Авто с пробегом» = Прочие (см. раннее правило 0bu); здесь — веса: с пробегом без ядра ОП Чери
    used_car_only = any(m in t for m in ["с пробегом", "подержанн", "пробегом", "авто с пробегом"])
    op_core = any(m in t for m in [
        "тест-драйв", "тест-дрейк", "когда покупать", "когда покупка", "комплектац",
        "хочу купить", "покупку автомобиля", "электронную визитку",
        "менеджер захаров", "менеджер евдокимов", "менеджер краснощекова", "менеджер калаев", "менеджер щеголев",
        "это илья", "это евгений", "это анастасия", "это андрей",
    ])
    if used_car_only and not op_core:
        op_score = 0  # авто с пробегом без признаков ОП (тест-драйв, когда покупка, менеджер) → Прочие

    # Доп. признаки СТО (уже проверены в sto_strong, но на случай частичного совпадения)
    if "ремонт" in t and "ремонт авто" not in t and "ремонт машин" not in t:
        sto_score += 1

    # Личные/прочие: минимум содержания
    if sto_score == 0 and op_score == 0:
        return "OTHER", "OTHER"

    # При низком счёте — не доверять маркерам, OTHER
    if max(sto_score, op_score) < 2:
        return "OTHER", "OTHER"

    # Определяем отдел: сначала ОП, затем СТО, остальное — Прочие
    if op_score >= 2:
        dept = "OP"
    elif sto_score >= 2:
        dept = "STO"
    else:
        dept = "OTHER"

    is_outbound = _is_outbound(t)
    if dept == "OP":
        if _speaker_not_op_manager(transcript):
            dept, ct = "OTHER", "OTHER"
        else:
            is_op_out = _is_op_outbound(t)
            ct = "OP_OUT" if is_op_out else "OP_IN"
    elif dept == "STO":
        if _speaker_not_sto_manager(transcript):
            dept, ct = "OTHER", "OTHER"
        elif is_outbound:
            if _is_sto_outbound_service_invite(transcript):
                ct = "STO_OUT"
            else:
                dept, ct = "OTHER", "OTHER"
        elif not _has_sto_substantive_service_intent(t):
            dept, ct = "OTHER", "OTHER"
        else:
            ct = "STO_IN"
    else:
        ct = "OTHER"

    return dept, ct


def _v2_substantive_sales_topic(low: str) -> bool:
    """Есть явная тема сделки/машины (не только «менеджер/дилер» в приветствии)."""
    if _is_op_promotional_contest_crm_other_low(low):
        return False
    if _is_op_marketing_sponsorship_charity_contact_other_low(low):
        return False
    markers = (
        "тест-драйв",
        "тест драйв",
        "комплектац",
        "комплектаци",
        "купить",
        "покупк",
        "кредит",
        "лизинг",
        "тенет",
        "чери",
        "черри",
        "chery",
        "тигго",
        "tiggo",
        "арризо",
        "дашинг",
        "бронь",
        "кассо",
        "госпрограмм",
        "обмен",
        "пробег",
        "интересует",
        "хочу ",
        "модель",
    )
    if any(p in low for p in markers):
        return True
    return _discount_in_vehicle_sale_context(low)


def _has_op_sales_conversation_substance(transcript: str) -> bool:
    """
    Есть смысл разговора с отделом продаж: менеджер ОП из справочника, перевод на ОП, обсуждение авто/сделки.
    Линия сервиса без этого (8897: такси, логистика) — не ОП.
    """
    low = (transcript or "").lower()
    if not low.strip():
        return False
    if _is_op_promotional_contest_crm_other_low(low):
        return False
    if _is_op_marketing_sponsorship_charity_contact_other_low(low):
        return False
    if _is_service_taxi_dispatch_other(low):
        return False
    if _is_sto_dealer_employee_outbound_service_call(transcript):
        return False
    if _has_sto_greeting_service_with_dispatcher_name(transcript) and _has_sto_substantive_service_intent(
        low
    ):
        return False
    if "менеджер отдела продаж" in low or "менеджера отдела продаж" in low:
        return True
    if _has_op_outbound_surface_markers(transcript):
        return True
    if re.search(
        r"(?:переведу|перевожу|переключу|переключаю|соединю)[^.!?]{0,100}"
        r"(?:менеджер|отдел\s+продаж|менеджера)",
        low,
    ):
        return True
    if any(len(n) >= 3 and n in low for n in _OP_MANAGER_NAMES) and any(
        p in low
        for p in (
            "менеджер отдела",
            "отдел продаж",
            "меня зовут",
            "это андрей",
            "это евгений",
            "это илья",
            "это анастасия",
            "переключ",
            "перевед",
        )
    ):
        return True
    return _v2_has_vehicle_purchase_substance(low)


def _v2_has_vehicle_purchase_substance(low: str) -> bool:
    """
    Общие признаки разговора про выбор/покупку/условия по автомобилю (сделка, не просто приём звонка на линии ОП).
    Не используем голый бренд в приветствии, общее «интересует» без темы машины и т.п. — чтобы не держать
    административные и прочие обращения в «ОП вх.» только из-за имени менеджера/дилера в начале.
    """
    if not (low or "").strip():
        return False
    if _is_internal_staff_dialogue_without_client_other(low):
        return False
    # «по стоимости» — и на СТО (ТО), и в ОП; не OP-маркер сам по себе.
    low = re.sub(r"\bпо\s+стоимости\b", " ", low)
    if _is_service_taxi_dispatch_other(low):
        return False
    try:
        from call_analytics.sto_booking_dimensions import jetour_brand_mentioned as _jetour_brand_mentioned
    except ImportError:
        _jetour_brand_mentioned = lambda t: any(  # noqa: E731
            b in t for b in ("жетур", "житур", "jetour", "джитур", "джетур", "джтр", "дjtур")
        )
    jetour_in_text = _jetour_brand_mentioned(low)
    # «Интересует …» только в связке с авто/сделкой (не «интересует подтверждение / документ»)
    if (
        "интересует чер" in low
        or "интересует тиг" in low
        or "интересует арриз" in low
        or ("интересует даш" in low and not jetour_in_text)
        or "интересует jaecoo" in low
        or "интересует авто" in low
        or "интересует машин" in low
        or "интересует модел" in low
        or "интересует комплект" in low
        or "интересует кредит" in low
        or "интересует цен" in low
        or "интересует рассроч" in low
        or "интересует тест" in low
        or "интересует налич" in low
        or ("интересует скид" in low and _discount_in_vehicle_sale_context(low))
        or "интересует брон" in low
    ):
        return True
    # Намерение / авто / сделка
    if _test_drive_is_customer_sales_context(low):
        return True
    _purchase_topic_markers = (
        "комплектац",
        "покупку автомоб",
        "покупке автомоб",
        "покупку авто",
        "покупки авто",
        "купить автомоб",
        "купить авто",
        "приобрести автомоб",
        "приобрести авто",
        "оформить автомоб",
        "оформить авто",
        "кредит ",
        "кредит,",
        "кредит.",
        "лизинг",
        "рассроч",
        "трейд-ин",
        "trade-in",
        "трейдин",
        "госпрограмм",
        "бронь",
        "кассо",
        "скидк",
        "выставочн",
        "авто в наличи",
        "новый автомоб",
        "новое авто",
        "новая модель",
        "какую модель",
        "какая модель",
        "отправлю электронную визитку",
        "электронную визитку",
    )
    for p in _purchase_topic_markers:
        if p in low:
            if p == "комплектац" and _service_context_blocks_complictaciya_op(low):
                continue
            if p == "дашинг" and jetour_in_text:
                continue
            if p == "скидк" and _discount_is_service_to_credit_not_vehicle_sale(low):
                continue
            if p in ("кредит ", "кредит,", "кредит.") and _credit_mention_is_staff_reconciliation_not_vehicle_sale(
                low
            ):
                continue
            return True
    if any(
        p in low
        for p in (
            "хочу купить",
            "хочу приобрести",
            "хочу посмотреть",
            "хочу модел",
            "хочу автомоб",
            "хочу машин",
            "хочу тиг",
            "хочу чер",
        )
    ):
        return True
    if _has_new_vehicle_model_in_purchase_context(low):
        return True
    return False


def _kuzov_exempt_when_new_car_sales_markers(low: str) -> bool:
    """
    «Кузов» в речи про новую машину (цвет кузова, поколение, комплектация на сайте) —
    не считать причиной правила 0wk «→ Прочие», если в том же тексте уже есть маркеры продажи нового авто.
    Опирается на _v2_has_vehicle_purchase_substance и доп. связки (наличие/заказ + цена/оплата/бренд).
    """
    if not (low or "").strip():
        return False
    if _v2_has_vehicle_purchase_substance(low):
        return True
    supply = any(
        p in low
        for p in (
            "в наличии",
            "нет в наличии",
            "под заказ",
            "на заказ",
            "авто в налич",
            "машина в налич",
            "есть в наличии",
            "в наличие есть",
        )
    )
    deal = any(
        p in low
        for p in (
            "скидк",
            "комплектац",
            "за налич",
            "наличные",
            "кредит",
            "лизинг",
            "рассроч",
            "трейд-ин",
            "trade-in",
            "бронь",
            "модель",
        )
    )
    brand_or_new_car = any(
        p in low
        for p in (
            "чери",
            "chery",
            "тенет",
            "tenet",
            "тэнет",
            "тигго",
            "tiggo",
            "арризо",
            "arizo",
            "новый автомоб",
            "новое авто",
            "покупку автомоб",
            "покупка автомоб",
            "купить автомоб",
            "купить авто",
        )
    )
    return (supply and deal) or (supply and brand_or_new_car) or (deal and brand_or_new_car)


def _v2_gray_op_in_without_substance(transcript: str) -> bool:
    """
    Входящий «ОП вх.» по маркерам legacy, но без содержания про выбор или покупку автомобиля → Прочие.
    Работает и для длинных транскриптов (раньше срабатывало только для коротких).
    """
    low = (transcript or "").lower()
    if _v2_has_vehicle_purchase_substance(low):
        return False
    if not any(
        n in low
        for n in (
            "менеджер отдела продаж",
            "менеджер",
            "викинги",
            "официальн",
            "дилер",
            "отдел продаж",
            "отделе продаж",
        )
    ):
        return False
    return True


def _is_op_manager_service_consultation_without_vehicle_purchase_other(transcript: str) -> bool:
    """
    Разговор состоялся с менеджером ОП, но предмет — только сервис/ТО, не покупка.

    19113: администратор уточнил «по покупке нового автомобиля?», затем менеджер ОП
    объяснял, за сколько дней записываться на первое ТО и как работает ресепшен.
    Маршрутный вопрос администратора и название отдела не считаются сделкой.
    """
    low = (transcript or "").lower()
    if not low.strip() or _is_outbound(transcript):
        return False
    op_manager_line = bool(
        re.search(r"\bменеджер\w*\s+отдел\w*\s+продаж\w*\b", low)
        or (
            re.search(r"\b(?:соедин|перевед|переключ)\w*[^.!?]{0,100}\bотдел\w*\s+продаж\w*\b", low)
            and re.search(r"\b(?:андрей|илья|евгений|анастасия|захаров|щеголев)\b", low)
        )
    )
    if not op_manager_line:
        return False
    service_topic = bool(
        re.search(
            r"\bзапис\w*[^.!?]{0,80}\b(?:на\s+)?то\b|"
            r"\b(?:перв\w*|втор\w*|трет\w*)\s+то\b|"
            r"\bтехническ\w*\s+обслуживан\w*\b|"
            r"\bтехобслуживан\w*\b|"
            r"\bресепшен\b[^.!?]{0,100}\bзапис\w*|"
            r"\bза\s+сколько\s+дн\w*[^.!?]{0,100}\bзапис\w*",
            low,
            re.IGNORECASE,
        )
    )
    if not service_topic:
        return False
    sale_check = re.sub(
        r"\bпо\s+покупк\w*\s+(?:нов\w*\s+)?автомобил\w*\??",
        " ",
        low,
        flags=re.IGNORECASE,
    )
    sale_check = re.sub(
        r"\b(?:менеджер\w*\s+)?отдел\w*\s+продаж\w*\b",
        " ",
        sale_check,
        flags=re.IGNORECASE,
    )
    return not _v2_has_vehicle_purchase_substance(sale_check)


def _is_op_manager_accessories_consultation_without_vehicle_purchase_other(
    transcript: str,
) -> bool:
    """
    Разговор с менеджером ОП, но предмет — только аксессуары/допоборудование
    (поперечины, фаркоп, багажник и т.п.), без выбора/покупки автомобиля.

    24979: после перевода на менеджера обсуждаются только поперечины на крышу и
    сроки наличия у мастера по допоборудованию.
    """
    low = (transcript or "").lower()
    if not low.strip() or _is_outbound(transcript):
        return False
    op_manager_line = bool(
        re.search(r"\bменеджер\w*\s+отдел\w*\s+продаж\w*\b", low)
        or (
            re.search(
                r"\b(?:соедин|перевед|переключ)\w*[^.!?]{0,100}\bотдел\w*\s+продаж\w*\b",
                low,
            )
            and re.search(r"\b(?:андрей|илья|евгений|анастасия|захаров|щеголев)\b", low)
        )
    )
    if not op_manager_line:
        return False
    accessories_topic = bool(
        re.search(
            r"\b(?:аксессуар\w*|доп(?:олнительн\w*\s+)?оборудован\w*|допборуд\w*|"
            r"поперечин\w*|на\s+крыш\w*\s+попереч\w*|фаркоп\w*|багажник\w*|рейлинг\w*|"
            r"бокс\s+на\s+крыш\w*|с\s+дуг\w*|с\s+замк\w*)\b",
            low,
            re.I,
        )
    )
    if not accessories_topic:
        return False
    purchase_flow_markers = bool(
        re.search(
            r"\b(?:купить|покупк\w*|выбира\w*|подобра\w*|тест[-\s]?драйв|"
            r"кредит\w*|рассроч\w*|трейд[-\s]?ин|в\s+наличии\s+автомобил\w*|"
            r"стоимост\w*[^.!?]{0,40}\bавтомобил\w*)\b",
            low,
            re.I,
        )
    )
    if purchase_flow_markers:
        return False
    return True


def _sto_outbound_service_dispatcher_context(low: str) -> bool:
    """Линия диспетчера / ассистента сервиса (Викинги·Чери + роль или имя из справочника СТО)."""
    head = (low or "").lower()[:6500]
    head = re.sub(r"\b(дария|тария|дарье)\b", "дарья", head)
    # Исходящее уточнение регламентного ТО: STT часто без «диспетчер …» в начале («ранее общались по ТО», «уточню информацию»).
    if (
        any(b in head for b in ("викинг", "чери", "chery", "тенет", "тэнет", "tenet"))
        and ("техническое обслуживание" in head or "техобслуживание" in head)
        and (
            "мы с вами ранее общались" in head
            or bool(re.search(r"уточн\w*[^.!?]{0,140}информац", head))
        )
    ):
        return True
    if "диспетчер сервиса" in head or "ассистент сервиса" in head:
        return True
    if _sto_outbound_to_sched_reminder_vehicle(head) and any(
        b in head for b in ("викинг", "чери", "chery", "тенет", "тэнет", "tenet")
    ):
        return True
    sto_names = (
        "юлия",
        "юлию",
        "андреева",
        "плаксина",
        "гринкина",
        "гранкина",
        "александра",
        "дарья",
        "дарью",
        "лилия",
        "лилию",
    )
    if not any(n in head for n in sto_names):
        return False
    if ("викинги" not in head[:4000]) and ("чери" not in head[:3500]):
        return False
    if "диспетчер" in head[:4500] or "компани" in head[:1200] or "ассистент" in head[:4500]:
        return True
    if "меня зовут" in head[:900] and any(b in head[:900] for b in ("викинг", "чери", "компани")):
        return True
    return False


def _sto_outbound_car_already_at_service(low: str) -> bool:
    """Авто уже на территории сервиса / в работе — не сценарий «пригласить приехать на обслуживание»."""
    return any(
        p in low
        for p in (
            "автомобиль у нас на сервисе",
            "машина у нас на сервисе",
            "авто у нас в сервисе",
            "автомобиль на сервисе находится",
            "находится у нас на сервисе",
            "в ремонте на сервисе",
            "ждём на выдаче",
            "ожидает выдачи",
            "готов к выдаче",
            "ремонт окончен",
            "работы выполнены",
            "автомобиль у нас в работе",
        )
    )


def _sto_outbound_reschedule_after_failed_to(head: str) -> bool:
    """
    Исходящий перенос визита: договорённость о дате после срыва ТО или явное «планируем … визит».
    Нужен для explicit_outbound при _has_initial_sto_reception_greeting (иначе STO_OUT гасится).
    """
    h = (head or "").lower()
    if "планируем сейчас ваш визит" in h or bool(re.search(r"планируем[^.!?]{0,50}ваш визит", h)):
        return True
    return (
        "по вашему автомобилю" in h
        and "не получилось" in h
        and ("техническое обслуживание" in h or "техобслуживание" in h)
    )


def _sto_outbound_to_sched_reminder_vehicle(head: str) -> bool:
    """
    Исходящее напоминание о регламентном ТО по автомобилю клиента:
    «на вашем автомобиле подходит N-е ТО», «второе ТО приближается», сценарий Tiggo/Pro Max + ТО,
    «планируете проходить» и т.п. (не обязательно формулировка «на вашем автомобиле» — см. исходящие напоминания).
    """
    h = (head or "").lower()
    has_to = "техническое обслуживание" in h or "техобслуживание" in h
    if not has_to:
        return False
    # Исх. перезвон: уточнение 1-го/2-го регламентного ТО (ошибка отметки), без скрипта «планируете проходить».
    if any(b in h for b in ("викинг", "чери", "chery", "тенет", "тэнет", "tenet")):
        clarification_to = (
            "мы с вами ранее общались" in h
            or bool(re.search(r"уточн\w*[^.!?]{0,140}информац", h))
        ) and any(
            x in h
            for x in (
                "первое техническое",
                "второе техническое",
                "третье техническое",
                "по поводу то",
            )
        )
        if clarification_to:
            return True
    question_to = any(
        p in h
        for p in (
            "планируете проходить",
            "планируете пройти",
            "планируете его проходить",
            "будете проходить",
            "собираетесь проходить",
        )
    ) or bool(re.search(r"планируете[^.!?]{0,30}проходить", h)) or bool(
        re.search(r"будете[^.!?]{0,20}проходить", h)
    )
    anchor_vehicle = any(
        p in h
        for p in (
            "на вашем автомобиле",
            "на вашем авто",
            # STT: «на ваш автомобиль подходит…» (не «на вашем»)
            "на ваш автомобиль",
            "на ваш авто",
            "подходит третье",
            "подходит второе",
            "подходит первое",
            "подходит нулевое",
            "подходит четвёртое",
            "подходит четвертое",
            "третье техническое",
            "второе техническое",
            "первое техническое",
            "нулевое техническое",
            "нулевое техническое обслуживание",
        )
    )
    timing = any(
        p in h
        for p in (
            "приближается",
            "наступает",
            "подходит второе",
            "подходит третье",
            "подходит нулевое",
        )
    )
    model_script = any(
        p in h for p in ("тигго", "tiggo", "промакс", "про макс", "pro max", "тнт7", "tnt7", "тнт 7")
    )
    ord_to = ("второе" in h or "третье" in h or "первое" in h or "нулевое" in h)
    if question_to and (anchor_vehicle or (timing and ord_to)):
        return True
    if question_to and has_to and model_script:
        return True
    return False


def _sto_outbound_appointment_reminder(head: str) -> bool:
    """
    Исходящее напоминание/подтверждение УЖЕ существующей записи на сервис:
    диспетчер сервиса звонит клиенту перед приездом — «по записи звоню на завтра»,
    «звоню напомнить/подтвердить запись», «вы подъедете, актуально?».
    Это операционный сервис-звонок (не приём заявки), направление — исходящее.

    Триггеры ищем только в начале текста: иначе STT вставляет «звоню по записи на завтра»
    в середину входящего разговора о новой записи на ТО → ложный STO_OUT (7913).
    """
    h = (head or "").lower()
    h_tr = h[:720]
    triggers = (
        re.search(r"по\s+записи\s+звоню", h_tr),
        # 18096 STT: «я по записи уточнить звоню».
        re.search(r"по\s+записи[^.!?]{0,40}?звоню", h_tr),
        re.search(r"по\s+записи\s+уточн", h_tr),
        re.search(r"звоню\s+по\s+записи", h_tr),
        re.search(r"звоню\s+по\s+поводу\s+(?:вашей\s+)?записи", h_tr),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?напомнить", h_tr),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?напомина", h_tr),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?подтвердить", h_tr),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?подтверд\w*\s+(?:запис|приезд)", h_tr),
        re.search(r"\bзавтра\s+записан[аы]?\s+к\s+нам\b", h_tr),
        re.search(r"\bзаписан[аы]?\s+к\s+нам\s+на\b", h_tr),
        (
            "хотели уточнить" in h_tr
            and re.search(r"\bзаписан[аы]?", h_tr)
            and re.search(r"\bна\s+то\b", h_tr)
        ),
        re.search(r"\bзаписыва\w*\s+автомобил\w*\s+на\s+завтра\b", h_tr, re.I),
        # 18096: «на завтра … планировали автомобиль запись?»
        re.search(
            r"\b(?:на\s+)?завтра\b[^.!?]{0,80}\bпланировали\b[^.!?]{0,60}автомобил",
            h_tr,
            re.I,
        ),
        # 18107: «записывали на завтра, автомобиль …».
        re.search(r"\bзаписывали\s+на\s+завтра\b", h_tr),
        re.search(
            r"\bзаписывали\b[^.!?]{0,40}\bна\s+завтра\b[^.!?]{0,60}автомобил",
            h_tr,
            re.I,
        ),
        # 18153: «вы сегодня к нам записаны / записывались».
        re.search(r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записан[аы]?\b", h_tr),
        re.search(r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записывал", h_tr),
        (
            re.search(r"\bзавтра\b", h_tr)
            and re.search(r"\bтехническ\w+\s+обслуживан", h_tr, re.I)
            and re.search(r"\bактуальн", h_tr, re.I)
        ),
    )
    if not any(triggers):
        return False
    sto_intro = any(
        p in h
        for p in (
            "диспетчер сервиса",
            "администратор сервиса",
            "ассистент сервиса",
            "ассистент-сервис",
            "сервис-консультант",
            "сервисный консультант",
            "сервис консультант",
            "сервис-менеджер",
            "сервисный менеджер",
        )
    ) or (
        "диспетчер" in h_tr
        and ("викинг" in h_tr or "компани" in h_tr)
        and (_sto_dispatcher_or_assistant_named(h_tr) or "юлия" in h_tr or "андреева" in h_tr)
    )
    sto_topic = any(
        p in h
        for p in (
            "техническое обслуживание",
            "техобслуживание",
            "записаны на",
            "записаны к нам",
            "записывали на",
            "на сервис",
            "на ремонт",
            "на диагностик",
        )
    ) or bool(re.search(r"\bна\s+то\b", h_tr))
    confirm_visit = any(
        p in h_tr
        for p in (
            "подъедете",
            "подъедите",
            "ожидаем вас",
            "ожидаем",
            "ждём вас",
            "ждем вас",
            "приедете",
            "в силе",
            "вы будете",
            "вас ожидаем",
        )
    )
    if confirm_visit and (
        re.search(r"\bзавтра\s+записан[аы]?\s+к\s+нам\b", h_tr)
        or re.search(r"\bзаписан[аы]?\s+к\s+нам\s+на\b", h_tr)
        or re.search(r"\bзаписывали\s+на\s+завтра\b", h_tr)
        or re.search(r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записан", h_tr)
    ):
        return True
    return sto_intro or sto_topic


def _sto_service_line_role_stt_in_head(head: str) -> bool:
    """STT-роль линии диспетчера/ассистента СТО в начале звонка (12958: «чсистен сервиса»)."""
    h = (head or "").lower()
    if (
        "диспетчер" in h
        or "ассистент" in h
        or ("стажер" in h or "стажёр" in h)
        or re.search(r"\bспетр\b", h)
    ):
        return True
    if re.search(r"(?:чсистен|челюстисерв|итмсервис)\w*\s+сервис", h):
        return True
    if re.search(r"\b(?:викинг\w*|чери\w*)\b", h) and re.search(r"\bсервис\w*\b", h):
        return True
    return False


def _sto_dispatcher_or_assistant_named(head: str) -> bool:
    """Имя диспетчера/ассистента СТО из справочника (без «Викинги» в мусорном STT)."""
    h = (head or "").lower()
    if not _sto_service_line_role_stt_in_head(h):
        return False
    return any(
        n in h
        for n in (
            "дарья",
            "дарье",
            "дарью",
            "бари",  # STT: Дарья (9091)
            "юлия",
            "юлию",
            "юля",
            "андреева",
            "александра",
            "плаксина",
            "гринкина",
            "гранкина",
        )
    )


def _is_sto_inbound_zastavnaya_dispatcher_to_inquiry(transcript: str) -> bool:
    """
    СТО вх.: «Чери Викинги на Заставной, диспетчер …» + уточнение цены/состава ТО (9091: Бари≈Дарья).
    """
    if _is_outbound(transcript or ""):
        return False
    try:
        from text_normalization import normalize_text

        low = (normalize_text(transcript) or transcript).lower()
    except Exception:
        low = (transcript or "").lower()
    low = re.sub(r"\bдиспетчер\s+бари\b", "диспетчер сервиса дарья", low)
    head = low[:900]
    if not (
        "диспетчер" in head
        and "заставн" in head
        and any(b in head for b in ("викинг", "чери", "chery"))
    ):
        return False
    if not (_sto_dispatcher_stt_name_alias_in_head(head) or "диспетчер сервиса" in head):
        return False
    return _has_sto_substantive_service_intent(low) or any(
        p in low
        for p in (
            "техосмотр",
            "то один",
            "первое техническое",
            "по стоимости",
            "сколько стоит",
        )
    )


def _is_sto_outbound_site_lead_opening(text: str) -> bool:
    """
    Исходящий перезвон по заявке/лиду до OP inbound-guard:
    - сайт: «по вашей заявке звоню», «оставляли на сайте…» (8675);
    - CRM: «получили заявку…», «удобно говорить», запись на ТО (8684).
    """
    t = (text or "").lower()
    t = re.sub(r"\bилерский\s+центр\b", "дилерский центр", t)
    head = t[:4500]
    sto_line = (
        "диспетчер сервиса" in head
        or "ассистент сервиса" in head
        or _sto_dispatcher_or_assistant_named(head)
    )
    dealer = (
        "викинг" in head
        or "компания викинг" in head
        or "дилерск" in head
        or "дилский центр" in head
    )
    if not sto_line or not dealer:
        return False
    lead_call = ("по вашей заявке" in head or "по заявке" in head) and (
        "звоню" in head or "звоним" in head
    )
    site_lead = lead_call and (
        "оставляли на сайте" in head
        or "оставили на сайте" in head
        or (
            re.search(r"\bудобно\b", head[:1000])
            and ("пару минут" in head[:1000] or "оставляли на сайте" in head)
        )
    )
    crm_lead = (
        ("получили заявку" in head or "получили вашу заявку" in head or "заявку получили" in head)
        and ("удобно говорить" in head[:1500] or "удобно вам говорить" in head[:1500])
        and (
            "техническое обслуживание" in head
            or "записаться" in head
            or "хотели бы" in head
        )
    )
    return site_lead or crm_lead


def _sto_outbound_invite_by_lead_or_booking(head: str) -> bool:
    """
    Маркер «приглашение по заявке»: перезвон по заявке, клиент хотел записать авто / на сервис.
    Учитываем оба порядка слов STT («получили заявку» / «заявку получили»).
    """
    return any(
        p in head
        for p in (
            "получили заявку",
            "получили вашу заявку",
            "заявку получили",
            "мы заявку получили",
            "просто заявку получили",
            "мы просто заявку получили",
            "поступила заявка",
            "заявка поступила",
            "оставили заявку",
            "оставляли заявку",
            "вы оставляли заявку",
            "вы оставили заявку",
            "заявку оставляли",
            "вы заявку оставляли",
            "хотели бы записать",
            "хотели бы записаться",
            "хотели записать",
            "записать автомобиль",
            "записать авто",
            "мне передали",
        )
    )


def _sto_outbound_reminder_to_service(head: str) -> bool:
    """
    Маркер «напоминание о себе»: срок ТО, ранее обслуживались, приглашение пройти ТО.
    """
    return any(
        p in head
        for p in (
            "у нас обслуживались",
            "обслуживались у нас",
            "ранее обслуживались",
            "у вас обслуживались",
            "проходили то",
            "то проходили",
            "прошел год",
            "прошёл год",
            "по сроку",
            "по срокам",
            "нужно сделать то",
            "нужно пройти то",
            "приглашаем пройти то",
            "приглашаем пройти тэо",
            "пора проходить то",
            "напоминаем записаться на то",
            "ежегодное техническое",
            # 12021: исходящее приглашение на нулевое ТО после покупки.
            "звоним по автомобил",
            "нулевое техническое",
            "необходимо пройти нулев",
            "пригласить записаться",
            "хотим вам пригласить",
            "приглашаем записаться",
        )
    )


def _is_inbound_admin_transfer_to_sto_dispatcher_line(transcript: str) -> bool:
    """
    Входящий «админ -> диспетчер/ассистент сервиса»:
    в префиксе есть перевод на диспетчера/ассистента, затем представление линии СТО.
    Такой сценарий относим к STO_IN и не переопределяем в STO_OUT.
    """
    low = (transcript or "").lower()
    head = low[:2600]
    # Вариант 1: явный «переключу/переведу на диспетчера/ассистента».
    transfer_m = re.search(
        r"\b(?:переключ\w*|перевед\w*|перекоч\w*)\b[^.!?]{0,140}\b(?:на|к)\s+"
        r"(?:диспетчер\w*|ассистент\w*)(?:\s+сервис\w*)?\b",
        head,
        re.IGNORECASE,
    )
    # Вариант 2: «попробую переключить ... диспетчер/ассистент ...» без предлога «на».
    if not transfer_m:
        transfer_m = re.search(
            r"\b(?:переключ\w*|перевед\w*|перекоч\w*)\b[^.!?]{0,160}\b"
            r"(?:диспетчер\w*|ассистент\w*)(?:\s+сервис\w*)?\b",
            head,
            re.IGNORECASE,
        )
    if not transfer_m:
        # Вариант 3: администратор в начале + «попробую переключить», а затем отдельное
        # представление сервисной линии (Юлия/Андреева и т.п.).
        first_900 = head[:900]
        admin_intro = (
            "администратор" in first_900
            or ("официальный дилер" in first_900 and ("добрый день" in first_900 or "здравствуйте" in first_900))
        )
        # Вариант 5 (16688): клиент просит «соедините … техобслуживание», затем линия диспетчера.
        if (
            admin_intro
            and re.search(
                r"(?:\bсоедин\w*[^.!?]{0,80}(?:техобслуживан|на\s+техобслуживан|с\s+техобслуживан|сервис)"
                r"|\b(?:техобслуживан|с\s+техобслуживан)[^.!?]{0,80}\bсоедин\w*)",
                head[:1200],
                re.I,
            )
            and re.search(r"\bдиспетчер\s+сервис", head[:2200], re.I)
            and any(
                n in head[:2200]
                for n in (
                    "юлия",
                    "юлию",
                    "юля",
                    "андреева",
                    "александра",
                    "плаксина",
                    "гринкина",
                    "гранкина",
                    "дарья",
                    "дарью",
                )
            )
        ):
            return True
        sto_line_after_admin = bool(
            re.search(
                r"(?:викинг\w*|чери)[^.!?]{0,120}(?:диспетчер|ассистент|сервис)[^.!?]{0,120}"
                r"(?:юлия|юлию|юля|андреева|александра|плаксина|гринкина|гранкина|дарья|дарью)",
                head,
                re.IGNORECASE,
            )
        )
        # Вариант 4 (9173): STT «передус диспетчер запишет», «оставайтесь на линии».
        has_stt_admin_to_dispatcher = bool(
            re.search(
                r"\b(?:передус|перевед\w*|передам|переключ\w*|перекоч\w*)\b[^.!?]{0,100}\bдиспетчер",
                head,
                re.IGNORECASE,
            )
            or re.search(r"\bдиспетчер\w*\s+запиш\w*\b", head, re.IGNORECASE)
        )
        if (
            admin_intro
            and has_stt_admin_to_dispatcher
            and ("оставайтесь на линии" in head or "диспетчер" in head[:1400])
            and sto_line_after_admin
        ):
            return True
        has_transfer_verb = bool(
            re.search(
                r"\b(?:попробую\s+)?(?:переключ\w*|перевед\w*|перекоч\w*|перевож\w*|ревож\w*)\b",
                head,
            )
        )
        if not (admin_intro and has_transfer_verb):
            return False
        return sto_line_after_admin
    after = head[transfer_m.end() : transfer_m.end() + 700]
    if not re.search(
        r"\b(?:диспетчер|ассистент)(?:\s+сервиса)?\b[^.!?]{0,80}\b"
        r"(?:юлия|юлию|юля|андреева|александра|плаксина|гринкина|гранкина|дарья|дарью)\b",
        after,
        re.IGNORECASE,
    ):
        return False
    return True


def _sto_dispatcher_stt_name_alias_in_head(head: str) -> bool:
    """STT-имена диспетчера СТО: «Бари»≈Дарья, «спетр»≈стажёр Дарья (9091, 8444)."""
    h = (head or "").lower()
    if not ("диспетчер" in h or "стажер" in h or "стажёр" in h or re.search(r"\bспетр\b", h)):
        return False
    return bool(
        re.search(r"\b(?:бари|спетр|дарь\w*)\b", h)
        or any(n in h for n in ("дарья", "дарью", "юлия", "юлию", "андреева", "александра", "плаксина"))
    )


def _has_initial_sto_reception_greeting(transcript: str) -> bool:
    """Старт разговора с линии СТО: «Викинги/Чери, диспетчер/ассистент ..., здравствуйте»."""
    low = (transcript or "").lower()
    first_220 = low[:220]
    # «на Заставной» / заставн — якорь дилера, если STT вместо «Чери/Викинги» пишет «Чикингин» и т.п.
    has_brand = (
        "викинг" in first_220
        or "викин" in first_220
        or "чери" in first_220
        or "дилерск" in first_220
        or "заставн" in first_220
        or "черебикин" in first_220  # STT: Чери Викинги (8963)
        or re.search(r"\bчер\w*икин", first_220)
    )
    has_role = _sto_service_line_role_stt_in_head(first_220)
    has_name = any(
        n in first_220
        for n in (
            "юлия", "юлию", "юля", "юли", "андреева", "андреев",
            "александра", "плаксина", "гринкина", "гранкина", "дарья", "дарью", "лилия", "лилию",
        )
    ) or _sto_dispatcher_stt_name_alias_in_head(first_220)
    # «слушаю» без «вас» — часто ответ клиента после «удобно говорить?» (12021), не приём СТО.
    has_greeting = (
        "здравств" in first_220
        or "слушаю вас" in first_220
        or (
            re.search(r"\bслушаю\b", first_220)
            and has_brand
            and has_name
            and has_role
        )
        or re.search(r"\bдобрый\s+(?:день|вечер)\b", first_220, re.I)
    )
    return has_brand and has_role and has_name and has_greeting


def _is_sto_outbound_service_invite(transcript: str) -> bool:
    """
    СТО исходящий: исходящий контакт + линия диспетчера/ассистента СТО + смысл приглашения:
    заявка/запись, напоминание о ТО, либо субстантивный запрос на сервис (ТО, ремонт, гарантия…);
    авто ещё не на сервисе (см. _sto_outbound_car_already_at_service).
    """
    if not _is_outbound(transcript):
        return False
    low = (transcript or "").lower()
    if _is_inbound_admin_transfer_to_sto_dispatcher_line(transcript):
        return False
    if not _sto_outbound_service_dispatcher_context(low):
        return False
    if _suppress_sto_lead_echo_as_outbound(low[:1200]):
        return False
    if _sto_outbound_car_already_at_service(low):
        return False
    head = low[:6500]
    employee_phone_handoff = any(p in head for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS)
    explicit_outbound_sto = (
        _sto_outbound_invite_by_lead_or_booking(head)
        or _sto_outbound_reminder_to_service(head)
        or employee_phone_handoff
        or _sto_outbound_reschedule_after_failed_to(head)
        or _sto_outbound_to_sched_reminder_vehicle(head)
        or _is_sto_outbound_crm_name_call_convenience_opening(transcript)
    )
    # Если звонок начинается с приветствия линии СТО (принимающая сторона),
    # не поднимаем в STO_OUT без явного маркера перезвона сотрудника.
    if _has_initial_sto_reception_greeting(transcript) and not explicit_outbound_sto:
        return False
    if _sto_outbound_invite_by_lead_or_booking(head) or _sto_outbound_reminder_to_service(head):
        return True
    outreach = any(
        p in head
        for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS
        + (
            "оставили заявку",
            "хотели записаться",
            "хотели бы записаться",
            "записаться на сервис",
            "запись на сервис",
            "на сервис записаться",
            "запишем вас",
            "записать вас",
            "уточняем запись",
            "подтверждаем запись",
            "приехать на сервис",
            "удобное время",
            "на какое число",
            "на какой день",
            "техническое обслуживание",
            "ремонт по гарантии",
            "гарантийн",
            "диагностик",
            "провести то",
            "пройти то",
            "сделать то",
            " тэо ",
        )
    )
    if outreach or _has_sto_substantive_service_intent(low):
        return True
    return False


def _is_sto_out_short_callback_by_service_line(transcript: str) -> bool:
    """
    Короткий STO_OUT-перезвон от сервисной линии:
    диспетчер/ассистент СТО в начале + явные маркеры перезвона сотрудника («это … викинги …»,
    «по вашему автомобилю звоню», «звоню по …», «смотрю по базе» и т.д.) + сервисная суть.
    Не использовать голые «звоню вам», «в базе», подстроку «отметк» и широкое «по вашему автомобил» —
    без диаризации они дают ложный STO_OUT на входящих.
    """
    low = (transcript or "").lower()
    has_initial_greeting = _has_initial_sto_reception_greeting(transcript)
    has_eto_service_intro = bool(
        re.search(
            r"\bэто\b[^.!?]{0,90}\b(?:чери|викинг|сервис)\b[^.!?]{0,140}\b"
            r"(?:юлия|юлию|юля|андреева|александра|плаксина|гринкина|гранкина|диспетчер|ассистент)\b",
            (transcript or "").lower()[:3200],
            re.I,
        )
    )
    if not (has_initial_greeting or has_eto_service_intro):
        return False
    if _is_inbound_admin_transfer_to_sto_dispatcher_line(transcript):
        return False
    if _is_service_transfer_to_other_department(low) or _is_sto_service_dispatcher_internal_handoff_other(transcript):
        return False
    if not _has_sto_substantive_service_intent(low):
        return False
    head = low[:3200]
    # Входящий запрос клиента «соедините/переведите/можно связаться» — не callback сотрудника.
    if re.search(r"\b(?:можно\s+связат|соедините|переведите|переключите)\b", head):
        return False
    # Клиент перезванивает после звонка с линии («у меня звонок был от вас») — входящий, не short STO_OUT.
    if re.search(r"\b(?:у\s+меня\s+)?звонок\s+был\s+от\s+вас\b", head, re.I):
        return False
    # «По вашему автомобилю звоню» — перезвон сотрудника; не путать с «уточню по информации по вашему автомобилю».
    if re.search(
        r"\bпо\s+вашему\s+автомобил(?:ю|е)\s+(?:звоню|звонил|звоним|перезван)",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\bпо\s+вашему\s+авто\b[^.!?]{0,55}\b(?:звоню|звонил|звоним|перезван)",
        head,
        re.I,
    ):
        return True
    callback_markers = (
        # «звоню вам» не используем: после приветствия линии клиент может сказать «звоню вам из …».
        "звоню по",
        "это чери викинги",
        "это викинги",
        "это юлия",
        "это диспетчер",
        "удобно пару минут",
        "удобно пару минут буквально",
        "приобретали автомобиль",
        "в феврале месяце приобретали",
        "планируете обслуживаться",
        "заказывали",
        "обращались",
        "обращение получили",
        "смотрю по базе",
        # «в базе» убрано: ложное срабатывание на «у меня в базе cherry / chery» (приложение клиента).
        # «то подходит» / «подходит то» убрано: STT часто пишет «подходит ТО» как «подходит то» на входящем.
        # «отметк» убрано: подстрока слова «отметку» в сервисной книжке.
    )
    # «это викинги» / «это юлия» на входящем — подтверждение линии, не перезвон сотрудника.
    if _is_outbound(transcript) and any(m in head for m in callback_markers):
        return True
    # Паттерн «это … Викинги … имя» встречается и на входящем (сотрудник уточняет линию после приветствия).
    if not _is_outbound(transcript):
        return False
    # Короткий STO-callback: «Это Чери/Викинги ... Юлия/диспетчер ...» в стартовой фразе.
    return bool(
        re.search(
            r"\bэто\b[^.!?]{0,80}\b(?:чери|викинг|сервис)\b[^.!?]{0,120}\b"
            r"(?:юлия|юлию|юля|андреева|александра|плаксина|гринкина|гранкина|диспетчер|ассистент)\b",
            head,
            re.I,
        )
    )


def _is_sto_outbound_master_service_workcall(transcript: str) -> bool:
    """
    Жёсткий STO_OUT-оверрайд для сервисных исходящих:
    исходящий + самопредставление сервисной роли + предметный контекст работ/ТО,
    при отсутствии явных маркеров продуктового ОП-диалога.
    """
    low = (transcript or "").lower()
    if not _is_outbound(transcript):
        return False
    # Кузовной цех: CRM «заявка на ТО», суть — гарантийный/кузовной осмотр → широкий STO_IN (8491).
    if _is_kuzov_outbound_master_claim_callback_opening(transcript):
        return False
    if _is_inbound_admin_transfer_to_sto_dispatcher_line(transcript):
        return False
    service_intro_markers = (
        "мастер-приёмщик",
        "мастер приемщик",
        "мастер-приемщик",
        "диспетчер сервиса",
        "ассистент сервиса",
        "по вашему автомобилю звоню",
    )
    if not any(m in low for m in service_intro_markers):
        return False
    service_content_markers = (
        "то-",
        "тэо",
        "фильтр",
        "масло",
        "нормочас",
        "нормо-час",
        "работа",
        "стоимость работ",
        "цена работ",
        "запчаст",
        "прокладк",
        "ремонт",
        "диагностик",
        "согласовать",
    )
    service_hits = sum(1 for m in service_content_markers if m in low)
    if service_hits < 2:
        return False
    op_strong_markers = (
        "тест-драйв",
        "тест драйв",
        "комплектац",
        "покупк",
        "кредит",
        "скидк",
        "интересует автомобиль",
        "интересует чери",
        "интересует тенет",
        "новый автомобиль",
        "новый авто",
    )
    if any(m in low for m in op_strong_markers):
        return False
    return True


def _v2_preserve_sto_out_on_outbound_service_script(
    transcript: str, dept: str, call_type: str
) -> bool:
    """
    Префикс АТС («начинаем звонить») даёт source_callback_marker; при sto_score > op_score v2 тянул в STO_IN,
    хотя legacy уже вернул исходящий СТО (7855: «удобно говорить» + диспетчер/ассистент сервиса).
    """
    if (dept or "").strip().upper() != "STO" or (call_type or "").strip().upper() != "STO_OUT":
        return False
    if not _is_outbound(transcript):
        return False
    tl = (transcript or "").lower()
    h = tl[:4500]
    if _sto_outbound_appointment_reminder(tl[:1500]):
        return True
    if _is_sto_outbound_service_invite(transcript):
        return True
    if (
        ("удобно говорить" in h or "удобно вам говорить" in h or "удобно разговаривать" in h)
        and (
            "диспетчер сервиса" in h
            or "ассистент сервиса" in h
            or "ассистент-сервис" in h
            or _sto_dispatcher_or_assistant_named(h)
        )
    ):
        return True
    return False


def _v2_apply_product_rules(transcript: str, dept: str, call_type: str) -> Tuple[str, str]:
    """
    Правила v2 поверх legacy: исходящий ОП без маркеров исходящего контакта ОП → Прочие;
    «ОП вх.» без темы выбора/покупки авто → Прочие;
    СТО исходящее приглашение на сервис (STO_OUT); СТО вх. без смысла сервиса → Прочие.
    Общий исходящий — _is_outbound (не _is_op_outbound).
    """
    d = (dept or "").strip().upper()
    c = (call_type or "").strip().upper()
    tl = (transcript or "").lower()
    if _is_used_cars_department_other(transcript):
        return "OTHER", "OTHER"
    if _is_post_sale_vehicle_documents_other(transcript):
        return "OTHER", "OTHER"
    # 24979: после перевода на менеджера ОП обсуждают только аксессуары/допоборудование.
    if _is_op_manager_accessories_consultation_without_vehicle_purchase_other(transcript):
        return "OTHER", "OTHER"
    # 19113: менеджер ОП консультирует только о записи на ТО — не цикл покупки авто.
    if _is_op_manager_service_consultation_without_vehicle_purchase_other(transcript):
        return "OTHER", "OTHER"
    # Повторный follow-up ОП → Прочие:
    # 19027: входящий после недавнего визита и разговора в салоне.
    if _is_op_inbound_recent_dealer_contact_repeat(transcript):
        return "OTHER", "OTHER"
    # 1) подтверждённая повторность (контакт в прошлом + итог/статус);
    # 2) широкий набор маркеров follow-up («вы хотели», «смотрели у нас», …) — см. _is_op_repeat_followup_outbound
    #    (раньше функция была реализована, но не вызывалась, из-за чего «вы хотели к нам приехать» оставалось OP_OUT).
    if _is_op_confirmed_repeat_followup(transcript):
        return "OTHER", "OTHER"
    if _is_op_repeat_followup_outbound(transcript) and not _is_op_inbound_admin_handoff_to_op_manager(
        transcript
    ):
        if not _op_repeat_followup_blocks_primary_outbound_misc(transcript):
            return "OTHER", "OTHER"
    if _is_bring_own_oil_service_visit_consult_other(transcript):
        return "OTHER", "OTHER"
    # Перевод на менеджера ОП не состоялся (все заняты, только номер/перезвон) — Прочие,
    # даже если в начале есть маркеры «звонок с сайта» / «обратный звонок» (регрессия 10889).
    if _is_op_all_managers_busy_callback_only_misc(transcript):
        return "OTHER", "OTHER"
    if _is_op_failed_transfer_callback_only_misc(transcript):
        return "OTHER", "OTHER"
    if _is_credit_department_callback_without_sales_talk_other(transcript):
        return "OTHER", "OTHER"
    if _is_op_transfer_to_manager_dropped_before_sales_talk_misc(transcript):
        return "OTHER", "OTHER"
    if _is_op_reception_phone_callback_no_manager_misc(transcript):
        return "OTHER", "OTHER"
    if _is_avito_bridge_client_hung_up_before_op_talk_misc(transcript):
        return "OTHER", "OTHER"
    # 16700: исх. звонок с сайта (ОП), клиент уточняет сервис, перевод на сервис не состоялся — Прочие.
    if _is_op_outbound_site_callback_service_transfer_failed_other(transcript):
        return "OTHER", "OTHER"
    # 19588: администратор успешно перевёл входящий звонок менеджеру, после чего
    # состоялся полноценный цикл покупки (новый авто, цена, кредит, скидки, визит).
    # Слова «гарантия» и «защита» внутри презентации машины не должны уводить в СТО.
    if (
        _is_op_inbound_admin_handoff_to_op_manager(transcript)
        and _has_op_sales_conversation_substance(transcript)
        and _has_client_facing_op_sales_cycle(transcript)
        and not _is_classic_sto_inbound_dispatcher_booking_intake(transcript)
        and not _is_outbound(transcript)
    ):
        return "OP", "OP_IN"
    # Приоритет источника лида: «заказан обратный звонок / оставляли заявку на сайте».
    # Если после этого есть маркеры ОП или СТО, считаем это входящим обращением в нужный отдел.
    source_callback_marker = (
        _is_crm_ats_outbound_lead_prefix(transcript)
        or _is_op_dealer_ringback_intro(transcript)
        or "заказан обратный звонок" in tl
        or _is_site_lead_callback_phrase(transcript)
    )
    if source_callback_marker:
        op_score, sto_score = _site_callback_op_sto_scores(transcript, tl)
        if any(len(name) >= 3 and name in tl[:3500] for name in _OP_MANAGER_NAMES):
            op_score += 1
        if op_score > 0 or sto_score > 0:
            if sto_score > op_score:
                if _is_outbound(transcript):
                    # Перезвон по пропущенному («звоночек от вас был») + выбор сервиса — СТО вх., не исх. (10167).
                    if _client_chose_service_after_admin_routing(tl) and _is_op_dealer_ringback_intro(
                        transcript
                    ):
                        return "STO", "STO_IN"
                    return "STO", "STO_OUT"
                if not _v2_preserve_sto_out_on_outbound_service_script(transcript, d, c):
                    return "STO", "STO_IN"
            # Не занижать OP_OUT до OP_IN из‑за маркера сайта, если legacy уже дал исходящий ОП
            # с явным контактом менеджера (иначе ранний return мешает блоку OP_IN→OP_OUT ниже).
            if (
                d == "OP"
                and c == "OP_OUT"
                and _is_outbound(transcript)
                and _has_op_outbound_surface_markers(transcript)
            ):
                return "OP", "OP_OUT"
            # Лид/АТС/перезвон по пропущенному: дилер набирает клиента и тема — ОП → исходящий.
            if (
                _is_outbound(transcript)
                and op_score > sto_score
                and (
                    "менеджер отдела продаж" in tl
                    or any(len(name) >= 3 and name in tl[:5000] for name in _OP_MANAGER_NAMES)
                )
            ):
                return "OP", "OP_OUT"
            if op_score > sto_score:
                return "OP", "OP_IN"
            # Равенство op/sto: не поднимать до OP_IN только из-за слабого «лид-сайт»-маркера.
    if _is_sto_reception_handoff_non_op_other(transcript):
        return "OTHER", "OTHER"
    if _is_sto_out_short_callback_by_service_line(transcript):
        return "STO", "STO_OUT"
    # Кузовной исходящий по CRM-заявке: широкий STO_IN (гарантия/осмотр), не STO_OUT мастера СТО (8491).
    if (
        d == "OTHER"
        and _is_kuzov_outbound_master_claim_callback_opening(transcript)
        and _is_sto_inbound_warranty_booking_dialog(transcript)
    ):
        return "STO", "STO_IN"
    if _is_sto_outbound_master_service_workcall(transcript):
        return "STO", "STO_OUT"
    # Входящий через админа с успешным переводом на диспетчера/ассистента СТО:
    # даже если в начале просили номер на перезвон, при фактическом соединении это STO_IN.
    if (
        d == "OTHER"
        and _is_inbound_admin_transfer_to_sto_dispatcher_line(transcript)
        and _sto_outbound_service_dispatcher_context(tl)
        and _has_sto_substantive_service_intent(tl)
        and not _is_service_transfer_to_other_department(tl)
        and not _is_sto_service_dispatcher_internal_handoff_other(transcript)
    ):
        return "STO", "STO_IN"
    # Входящий СТО: линия диспетчера/ассистента + сервисная суть разговора.
    # Нужен как страховка, когда legacy дал OTHER из-за шумного STT.
    if (
        d == "OTHER"
        and not _is_outbound(transcript)
        and _sto_outbound_service_dispatcher_context(tl)
        and _has_sto_substantive_service_intent(tl)
        and not _is_service_transfer_to_other_department(tl)
        and not _is_sto_service_dispatcher_internal_handoff_other(transcript)
        and not _is_service_secretary_callback_misc(transcript)
        and not _is_sto_inbound_failed_target_contact_misc(tl)
    ):
        return "STO", "STO_IN"
    # СТО исходящее по заявке/CRM — до гарантийного STO_IN (9088: «мне передали… ТО» + неисправность).
    if _is_sto_outbound_service_invite(transcript):
        return "STO", "STO_OUT"
    # Входящий гарантийный сервисный диалог с атрибутами приёмки и согласованием визита
    # (даже если в приветствии не распозналась линия диспетчера/ассистента).
    if d == "OTHER" and not _is_outbound(transcript) and _is_sto_inbound_warranty_booking_dialog(transcript):
        return "STO", "STO_IN"
    # Входящий сервисный диалог записи с сутью работ и приёмкой без линии в STT (8240).
    if (
        d == "OTHER"
        and _is_sto_inbound_service_booking_reception_substantive_dialog(transcript)
        and not _is_service_transfer_to_other_department(tl)
        and not _is_sto_service_dispatcher_internal_handoff_other(transcript)
        and not _is_service_secretary_callback_misc(transcript)
        and not _is_sto_inbound_failed_target_contact_misc(tl)
    ):
        return "STO", "STO_IN"
    # ОП исходящий: менеджер ОП представился + явная фраза «передали ...» (контакт из CRM),
    # даже если в начале клиент отвечает «Да, слушаю вас». Не путать с входящим через администратора (перевод на ОП).
    op_out_head = (transcript or "").lower()[:2200]
    if (
        not _is_op_inbound_with_reception_gatekeeper(transcript)
        and ("менеджер отдела продаж" in op_out_head or re.search(r"\bменеджер\s+оп\b", op_out_head))
        and "передали" in op_out_head
        and any(x in op_out_head for x in ("викинг", "чери", "тенет", "заставн", "тольятт"))
    ):
        return "OP", "OP_OUT"
    # Входящий через администратора с переводом на менеджера ОП — это OP_IN, не OP_OUT,
    # кроме сценариев «дилер набирает по лиду/сайту/пропущенному» (АТС в начале или перезвон салона).
    if _is_op_inbound_with_reception_gatekeeper(transcript) and d == "OP" and c == "OP_OUT":
        head_ok = (transcript or "").lower()[:4500]
        if (
            _is_dialer_or_site_outbound_opening(transcript)
            or "заказан обратный звонок" in head_ok
            or _is_op_dealer_ringback_intro(transcript)
            or _is_site_lead_callback_phrase(transcript)
        ):
            pass
        else:
            return "OP", "OP_IN"
    # Важный продуктовый оверрайд: первый исходящий контакт менеджера ОП
    # («алло + имя клиента + это менеджер ОП/дилер + мне передали/вот звоню»).
    if (
        d == "OTHER"
        and _is_outbound(transcript)
        and _has_op_outbound_surface_markers(transcript)
        and (
            _has_op_crm_callback_markers(transcript)
            or _is_op_outbound_primary_site_lead(transcript)
        )
        and not _is_sto_outbound_service_invite(transcript)
    ):
        return "OP", "OP_OUT"
    # Первичный исходящий ОП без явной «заявки с сайта»:
    # «вы нам/сегодня звонили … вам [модель/авто] интересует».
    # Если дальше идёт содержательная продажная консультация, поднимаем из OTHER в OP_OUT.
    if (
        d == "OTHER"
        and _is_outbound(transcript)
        and _has_op_outbound_surface_markers(transcript)
        and re.search(
            r"\bвы\s+(?:сегодня\s+)?(?:уже\s+)?звонил\w*\b|\bвы\s+нам\s+звонил\w*\b",
            tl[:2400],
            re.I,
        )
        and _v2_has_vehicle_purchase_substance(tl)
        and (
            _has_new_vehicle_model_in_purchase_context(tl)
            or any(
                p in tl
                for p in (
                    "трейд-ин",
                    "трейд ин",
                    "кредит",
                    "рассрочк",
                    "комплектац",
                    "нового автомобил",
                    "покупк",
                )
            )
        )
        and not _is_op_repeat_followup_outbound(transcript)
        and not _is_sto_outbound_service_invite(transcript)
    ):
        return "OP", "OP_OUT"
    if _is_non_automotive_domestic_context(transcript) and d == "OP":
        return "OTHER", "OTHER"
    op_surface = _has_op_outbound_surface_markers(transcript)
    strong_site_op_outbound = (
        _is_dialer_or_site_outbound_opening(transcript)
        and any(p in tl for p in ("викинг", "чери", "тенет", "тэнет", "официальный дилер"))
        and (
            "менеджер отдела продаж" in tl
            or any(len(name) >= 3 and name in tl[:3500] for name in _OP_MANAGER_NAMES)
        )
        and sum(
            1
            for p in (
                "покупк",
                "интересует автомобиль",
                "интересует машина",
                "комплектац",
                "в наличии",
                "доплат",
                "скидк",
                "кредит",
                "за налич",
                "рассрочк",
            )
            if p in tl
        )
        >= 2
    )
    if d == "OP" and c == "OP_IN":
        # Админ → менеджер ОП, клиент сам звонит: не OP_OUT из‑за ложного «удобно»/исходящего (9231).
        if _is_op_inbound_with_reception_gatekeeper(transcript) or _is_op_inbound_admin_direct_client_call(
            transcript
        ):
            return "OP", "OP_IN"
        if _is_outbound(transcript):
            if op_surface or strong_site_op_outbound:
                return "OP", "OP_OUT"
            return "OTHER", "OTHER"
        if _v2_gray_op_in_without_substance(transcript):
            return "OTHER", "OTHER"
    if d == "OP" and c == "OP_OUT" and _is_op_confirmed_repeat_followup(transcript):
        return "OTHER", "OTHER"
    if d == "OP" and c == "OP_OUT" and not op_surface:
        return "OTHER", "OTHER"
    if d == "STO" and c == "STO_IN" and _is_service_secretary_callback_misc(transcript):
        return "STO", "STO_IN"
    if d == "STO" and c == "STO_IN" and _is_sto_inbound_failed_target_contact_misc(tl):
        return "STO", "STO_IN"
    if d == "STO" and c == "STO_IN" and not _has_sto_substantive_service_intent(tl):
        return "OTHER", "OTHER"
    if (
        d == "STO"
        and c == "STO_IN"
        and _is_outbound(transcript)
        and _is_sto_outbound_repeat_followup_opening(transcript)
    ):
        return "STO", "STO_OUT"
    if (
        d == "STO"
        and c == "STO_IN"
        and _is_outbound(transcript)
        and _is_sto_outbound_service_invite(transcript)
    ):
        return "STO", "STO_OUT"
    return d, c


def _has_minimum_op_signal(transcript: str) -> bool:
    """
    Единый gate для OP-классификации (и legacy, и v2):
    OP_* допускается только при наличии минимум 3 содержательных маркеров продаж.
    Звонки в ОЗЧ (запчасти/масла/цена/наличие) не должны попадать в OP_*.
    """
    try:
        from text_normalization import normalize_text

        transcript = normalize_text(transcript or "")
    except ImportError:
        transcript = transcript or ""
    transcript = _normalize_stt_classification_artifacts(transcript)
    low = transcript.lower()
    head = low[:3500]
    if not low.strip():
        return False
    if _is_service_taxi_dispatch_other(low):
        return False

    # Жесткий блок: запросы в ОЗЧ (масла/запчасти/наличие/цена) — не OP.
    if _is_vikings_b2b_parts_supplier_call(transcript):
        return False
    parts_dept_ctx = any(p in low for p in ("отдел запчаст", "отдел запасных", "запчаст"))
    parts_retail_ctx = any(
        p in low
        for p in (
            "масло",
            "луко",
            "допуск",
            "канистр",
            "литр",
            "сколько стоит",
            "цена",
            "в наличии",
            "можно купить",
        )
    )
    if parts_dept_ctx and parts_retail_ctx and not _has_op_sales_conversation_substance(transcript):
        return False

    # Минимум 3 маркера OP из согласованного списка.
    markers: list[bool] = []

    # 1) Явное упоминание отдела продаж.
    markers.append(bool(re.search(r"\bотдел\w*\s+продаж\b|\bпродаж\w*\s+отдел\b", head)))

    # 2) Менеджер из списка ОП ведет диалог.
    markers.append(
        any(len(name) >= 3 and name in head for name in _OP_MANAGER_NAMES)
        and bool(
            re.search(
                r"\b(?:меня\s+зовут|это|менеджер)\b",
                head,
            )
        )
    )

    # 3) Выяснение модели / интересующей машины.
    markers.append(
        bool(re.search(r"\b(?:какая|какой)\s+модел\w*|\bкакой\s+автомобил\w*\s+интерес", low))
        or _has_new_vehicle_model_in_purchase_context(low)
        or (
            any(m in low for m in ("tenet", "тенет", "тэнет", "tiggo", "тигго", "arrizo", "арризо", "t4", "t7", "t8", "t9"))
            and any(p in low for p in ("интерес", "покуп", "хочу", "рассматрива"))
        )
    )

    # 4) Выяснение, для кого автомобиль.
    markers.append(
        bool(
            re.search(
                r"\b(?:на\s+кого\s+(?:оформлен|автомобил)|для\s+кого\s+(?:авто|автомобил))",
                low,
            )
        )
    )

    # 5) Способ оплаты.
    markers.append(
        any(p in low for p in ("кредит", "за налич", "наличн", "рассрочк", "трейд-ин", "трейд ин"))
    )

    # 6) Наличие и какая машина.
    markers.append(
        ("в наличии" in low and bool(re.search(r"\b(?:какая|какой|какие)\b", low)))
        or ("в наличии" in low and _has_new_vehicle_model_in_purchase_context(low))
    )

    # 7) Срок покупки.
    markers.append(
        bool(
            re.search(
                r"\bкогда\s+(?:планиру\w*|хотите)\s+(?:покупк\w*|приобрест\w*)|\bв\s+ближайш\w+\s+врем",
                low,
            )
        )
    )

    # 8) Приглашение на тест-драйв.
    markers.append(
        "тест-драйв" in low
        and bool(re.search(r"\b(?:приезж|приеха|приглаш\w*|запиш\w*|запис\w*|пройти|посмотрет\w*)\b", low))
    )

    # 9) Приглашение в салон.
    markers.append(
        bool(
            re.search(
                r"\b(?:приглаша\w*\s+в\s+салон|приезжа\w*\s+в\s+салон|ждем\s+в\s+салон|ждём\s+в\s+салон)",
                low,
            )
        )
    )

    return sum(1 for m in markers if m) >= 3


def _apply_unified_op_gate(transcript: str, dept: str, call_type: str) -> Tuple[str, str]:
    d = (dept or "").strip().upper()
    c = (call_type or "").strip().upper()
    if d == "OP" and c in ("OP_IN", "OP_OUT") and not _has_minimum_op_signal(transcript):
        return "OTHER", "OTHER"
    return d, c


def classify_by_transcript_v2(transcript: str) -> Tuple[str, str]:
    """
    Главный движок v2: полный разбор — legacy (монолит), затем продуктовые правила дерева ОП/серая зона.
    """
    d, c = classify_by_transcript_legacy(transcript)
    d, c = normalize_call_type_result(d, c)
    d, c = _v2_apply_product_rules(transcript, d, c)
    # Раньше здесь четыре даунгрейда STO_IN→Прочие (диагностика без «узкого ТО», реестр «работ без ТО»,
    # уточнение записи, MyCherry). Продукт: широкий слой остаётся полноценным СТО при темах ТО, диагностики,
    # гарантии, замены, неисправности; рубрика СТО_ТО_* / её отсутствие — только infer_sto_to_rubric_type.
    if _is_service_dispatcher_cancellation_service_other(transcript):
        return _apply_unified_op_gate(transcript, "OTHER", "OTHER")
    # Восстановление широкого слоя СТО:
    # сервисные диалоги «запись/перенос/подтверждение/диагностика» остаются в STO_*,
    # а разделение на ТО/не ТО делает только узкий слой sto_to_rubric.
    if (d, c) == ("OTHER", "OTHER"):
        if _is_wrong_dealer_center_redirect_other(transcript):
            return _apply_unified_op_gate(transcript, "OTHER", "OTHER")
        # Промо-конкурс (велосипед/розыгрыш + CRM-тест-драйв): не восстанавливать в STO_* (15338).
        if _is_op_promotional_contest_crm_other(transcript):
            return _apply_unified_op_gate(transcript, "OTHER", "OTHER")
        tl = (transcript or "").lower()
        if _is_service_taxi_dispatch_other(tl):
            return _apply_unified_op_gate(transcript, "OTHER", "OTHER")
        # Короткие фрагменты без приветствия: клиент просит перенос уже существующей записи на ТО.
        # Это входящий сервисный сценарий, даже если нет явной шапки диспетчера.
        if (
            any(p in tl[:2200] for p in ("перенести то", "перенести запись", "перезапис"))
            and any(p in tl[:2600] for p in ("вы записаны к нам", "записан к нам", "записана к нам"))
        ):
            return _apply_unified_op_gate(transcript, "STO", "STO_IN")
        # Входящий перенос/уточнение уже существующей записи на ТО:
        # клиент сам инициирует изменение записи («перенести ТО», «вы записаны к нам»),
        # это широкий STO_IN (узкий слой дальше определит НЕ_ТО).
        if (
            _is_client_initiated_booking_change_after_greeting(transcript)
            and any(
                p in tl[:3500]
                for p in (
                    "перенести то",
                    "перенести запись",
                    "перезапис",
                    "записаны к нам",
                    "записан к нам",
                    "записан на",
                    "записана на",
                    "напомните фамилию",
                    "на то",
                    "техническое обслуживание",
                    "техобслуживание",
                )
            )
            and any(
                p in tl[:1600]
                for p in (
                    "я хотел",
                    "я хотела",
                    "подскажите",
                    "если это возможно",
                    "можно",
                )
            )
        ):
            return _apply_unified_op_gate(transcript, "STO", "STO_IN")
        # 9741: перенос сегодняшнего слота — сервисная линия, широкий STO_IN.
        head_tl = tl[:900]
        if (
            re.search(r"\bзаписал(?:ся|ись)\s+сегодня\b", head_tl)
            and re.search(r"\bна\s+\d{1,2}\s*[.:]?\s*\d{2}\b", head_tl)
            and any(
                p in head_tl
                for p in (
                    "не получается приехать",
                    "не получается подъехать",
                    "не получится",
                    "не смогу приехать",
                )
            )
            and re.search(r"\bперенес", tl[:4000])
            and not _speaker_not_sto_manager(transcript)
        ):
            return _apply_unified_op_gate(transcript, "STO", "STO_IN")
        if not _speaker_not_sto_manager(transcript) and (
            _has_sto_service_line_context(transcript) or _has_sto_service_context(tl[:6000])
        ):
            if _is_classic_sto_inbound_dispatcher_booking_intake(transcript):
                return _apply_unified_op_gate(transcript, "STO", "STO_IN")
            if (
                _has_initial_sto_reception_greeting(transcript)
                and not _is_outbound(transcript)
                and (
                    _is_client_initiated_after_greeting(transcript)
                    or _client_initiated_booking_phrase_in_opening(tl[:1000])
                )
            ):
                return _apply_unified_op_gate(transcript, "STO", "STO_IN")
            sto_out_like = (
                _is_outbound(tl)
                or _is_sto_outbound_service_invite(transcript)
                or _sto_outbound_to_sched_reminder_vehicle(tl)
                or _is_service_dispatcher_booking_verification_registry_other(transcript)
                or any(
                    p in tl[:4500]
                    for p in (
                        "по записи звоню",
                        "на завтра записан",
                        "на завтра записаны",
                        "запланирован автомобиль в работу",
                        "все ли в силе",
                        "всё ли в силе",
                        "приедете",
                    )
                )
            )
            if sto_out_like:
                return _apply_unified_op_gate(transcript, "STO", "STO_OUT")
            return _apply_unified_op_gate(transcript, "STO", "STO_IN")
    return _apply_unified_op_gate(transcript, d, c)


def classify_by_transcript(transcript: str) -> Tuple[str, str]:
    """
    Диспетчер: по умолчанию v2 (главный); при USE_LEGACY_CLASSIFY_TRANSCRIPT — только legacy.
    """
    try:
        from config import USE_LEGACY_CLASSIFY_TRANSCRIPT

        if USE_LEGACY_CLASSIFY_TRANSCRIPT:
            d, c = classify_by_transcript_legacy(transcript)
            return _apply_unified_op_gate(transcript, d, c)
    except ImportError:
        pass
    return classify_by_transcript_v2(transcript)


_SPEAKER_NAME_NOT_BEFORE_MENEDZHER = frozenset(
    {
        "там",
        "из",
        "для",
        "что",
        "это",
        "как",
        "где",
        "если",
        "тут",
        "тот",
        "ваш",
        "наш",
        "все",
        "всё",
        "или",
        "они",
        "она",
        "его",
        "ещё",
        "еще",
    }
)


def _extract_speaker_name(transcript: str) -> Optional[str]:
    """Извлекает имя говорящего: «стажер X», «менеджер X», «это X, компания», «меня зовут X», «X, менеджер»."""
    t = (transcript or "").lower()
    for pat in [
        # «что/это Евгений, мастер-приёмщик» — роль приёмки, не менеджер ОП по имени
        r"(?:это|что)\s+(захаров|евдокимов|краснощекова|краснощёкова|калаев|калаева|щеголев|евгений|евгения|илья|илью|анастасия|анастасию|андрей|андрея)\b"
        r"[^.!?]{0,120}?\bмастер[-\s]?(?:приёмщик|приемщик)\b",
        # Только известный стажёр СТО — иначе «стажер сервиса» давал бы ложное имя
        r"стажер\s+(дарья|дарью)\b",
        # STT часто теряет «сервиса»: «диспетчер Юлия» — всё ещё СТО
        r"(?:диспетчер|ассистент)\s+(?:сервиса\s+)?(юлия|юлию|юля|андреева|александра|плаксина|гринкина|гранкина|лилия|лилию)(?:\s+[а-яё]+)?\b",
        # «Чери Викинги Дарья», «Викинги Дарья» — приветствие линии (до «меня зовут Виталий» клиента)
        r"(?:чери\s+)?викинги\s+(?:чери\s+)?(дарья|дарью)\b",
        r"викинги\s+(?:на\s+)?(?:заставн\w*\s+)?(дарья|дарью)\b",
        # «менеджер отдела продаж Захаров Илья» / «… продажный …» — между «менеджер» и фамилией не должно ломать извлечение
        r"менеджер\s+(?:отдела\s+продаж(?:ный)?\s*)?(захаров|евдокимов|краснощекова|краснощёкова|калаев|калаева|щеголев|евгений|илья|анастасия|андрей)(?:\s+[а-яё]+)?",
        r"это\s+([а-яё]+)(?:\s+[а-яё]+)?,?\s+компания",
        r"меня\s+зовут\s+([а-яё]+)(?:\s+[а-яё]+)?",
        # STT: «меня Андрей зовут» (11123) — порядок слов не «меня зовут Андрей»
        r"меня\s+([а-яё]+)\s+зовут(?:\s+[а-яё]+)?",
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    # «X, менеджер» — только в начале; иначе «там менеджер» из середины разговора (11123)
    m = re.search(
        r"([а-яё]{3,}),?\s*менеджер\b(?:\s+отдела)?(?:\s+продаж)?",
        t[:1500],
        re.IGNORECASE,
    )
    if m:
        name = m.group(1).lower()
        if name not in _SPEAKER_NAME_NOT_BEFORE_MENEDZHER:
            return name
    return None


def _speaker_not_in_names(transcript: str, allowed: frozenset) -> bool:
    """True, если говорящий представился и его имя не в allowed."""
    name = _extract_speaker_name(transcript)
    if not name:
        return False
    parts = set(re.findall(r"[а-яё]{2,}", name)) | {name}
    return not bool(parts & allowed)


def _speaker_not_op_manager(transcript: str) -> bool:
    """True, если говорящий представился и НЕ входит в 4 менеджеров ОП."""
    t = (transcript or "").lower()
    head = t[:1000]
    # Админ перевёл на менеджера (Оксана и др.) — первый спикер не ОП, но разговор с ОП; не блокируем
    transfer_to_op = any(
        p in t
        for p in (
            "переключу на менеджера",
            "переключу на отдел продаж",
            "переключаю на менеджера",
            "переключаю на отдел продаж",
            "переведу на менеджера",
            "переведу на отдел продаж",
            "перевожу на менеджера",
            "перевожу на отдел продаж",
            "переключаю вам",
            "переключу вам",
            "перевожу вам",
            "переведу вам",
            "перевожу вас на менеджера",
            "переведу вас на менеджера",
            "переключаю вас на менеджера",
            "переключу вас на менеджера",
        )
    ) or bool(
        re.search(r"\bпереключ\w*\s+вам\b", t[:2800])
        or re.search(r"\bперевож\w*\s+вас\s+на\s+менеджер", t[:2800])
    )
    op_context = any(
        m in t
        for m in (
            "анастасия",
            "краснощекова",
            "краснощёкова",
            "захаров",
            "евдокимов",
            "калаев",
            "калаева",
            "щеголев",
            "андрей",
            "андрея",
            "илья",
            "илью",
            "евгений",
            "отдел продаж",
            "отдел продажный",
            "менеджер отдела",
        )
    )
    if transfer_to_op and op_context:
        return False
    if "менеджер отдела продаж" in head:
        return False
    if any(p in head for p in _STO_MASTER_PRIYOM_MARKERS):
        return True
    return _speaker_not_in_names(transcript, _OP_MANAGER_NAMES)


def _speaker_not_sto_manager(transcript: str) -> bool:
    """True, если говорящий представился и НЕ входит в 2 диспетчеров СТО + Дарья."""
    t = (transcript or "").lower()
    # «Меня зовут Андрей» — это клиент при входящем; в транскрипте есть СТО (Андреева, Викинги сервис)
    sto_context = ["андреева", "плаксина", "гринкина", "гранкина", "дарья", "юлия", "юлию", "юля", "лилия", "лилию", "диспетчер", "викинги сервис", "чери сервис", "викинги чери сервис", "диспетчер сервиса", "стажер дарья"]
    if "меня зовут" in t and any(s in t for s in sto_context):
        return False
    # Админ перевёл на диспетчера/сервис (Оксана и др.) — первый спикер не СТО, но разговор с СТО; не блокируем
    transfer_to_dispatcher = any(p in t for p in [
        "на диспетчера переключу", "переключу на диспетчера", "переключу вас на диспетчера", "переключу вас",
        "на диспетчера переключаю", "переключаю на диспетчера", "переключаю вас на диспетчера", "переключаю вас",
        "переведу вас", "переведу на диспетчера", "перевожу вас", "переведу на механик",
        "на сервис переключу", "переключу на сервис", "с сервисом соединить", "с сервисом можно соединить",
        "с сервисным центром соединиться", "с сервисным центром",
        "сейчас переведу", "сейчас переключу",  # «Сейчас переведу вас, ожидайте»
    ])
    if transfer_to_dispatcher and any(s in t for s in sto_context):
        return False
    # «Ассистент сервиса Викинги Чери» — извлекли «викинги» (компания), но в транскрипте есть Андреева/Юлия
    name = _extract_speaker_name(transcript)
    if name == "викинги" and any(s in t for s in ["андреева", "юлия", "юлию", "юля", "плаксина", "гринкина", "гранкина"]):
        return False
    # Явная запись на ТО + линия сервиса в тексте — не блокировать СТО из‑за неузнанного имени в STT.
    if _is_sto_to_booking_live_dialog_not_secretary_misc(t) and any(
        n in t
        for n in (
            "диспетчер",
            "ассистент сервиса",
            "стажер",
            "дарья",
            "дарии",
            "юлия",
            "юлию",
            "андреева",
            "плаксина",
            "гринкина",
            "гранкина",
            "александра",
        )
    ):
        return False
    return _speaker_not_in_names(transcript, _STO_MANAGER_NAMES)


def _is_op_lead_callback_intro(text: str) -> bool:
    """
    Перезвон по заявке/лиду (CRM): Викинги/автосалон/Тольятти + заявка/покупка авто.
    Менеджер может не называть своё имя — только компанию и суть звонка.
    """
    low = (text or "").lower()[:3500]
    dealer = (
        "викинги" in low
        or ("компания" in low and "тольятт" in low)
        or "автосалон" in low
        or "официальный дилер" in low
    )
    if not dealer:
        return False
    return any(
        p in low
        for p in (
            "заявка приходила",
            "заявка поступила",
            "заявку приходила",
            "к нам заявка",
            "к нам вот заявка",
            "приходила заявка",
            "поступила заявка",
            "интересует покупка автомобиля",
            "покупка автомобиля вас",
            "интересует покупка",
            "случайно нажали",
            "отправляли что-то",
            "отправляли что то",
            "перезваниваю по заявке",
            "по вашей заявке",
            "заявку оставляли",
            "оставляли заявку",
            # Перезвон менеджера по лиду: без слова «заявка» (часто в CRM-обзвоне)
            "мы с вами обсуждали",
            "мы с вами общались по",
            "обсуждали покупку автомобиля",
            "обсуждали покупку",
            "хотела узнать, ещё актуальн",
            "хотела узнать, еще актуальн",
            "хотел узнать, ещё актуальн",
            "хотел узнать, еще актуальн",
        )
    )


def _has_client_requested_sales_dept_in_opening(head: str) -> bool:
    """
    «Отдел продаж» как запрос клиента в opening, не фрагмент «менеджер отдела продаж»
    из самопредставления менеджера ОП (регрессия 13019: «отдела продаж» + «тэнет» в теле разговора).
    """
    h = head or ""
    for m in re.finditer(r"(?:отдел\w*\s+продаж|продаж\w*\s+отдел)", h):
        prefix = h[max(0, m.start() - 32) : m.start()]
        if re.search(r"менеджер\w*\s+$", prefix, re.I):
            continue
        return True
    return False


def _is_op_manager_self_intro_in_opening(head: str, *, limit: int = 900) -> bool:
    """Менеджер ОП уже представился в начале — не путать с запросом клиента в отдел продаж."""
    open_head = (head or "")[:limit]
    if not open_head.strip():
        return False
    if re.search(r"\bменеджер\s+отдела\s+продаж\b", open_head):
        return True
    if re.search(rf"\bэто\s+{_OP_MGR_NAME_RE}\b", open_head, re.I):
        return True
    if re.search(r"\bменя\s+[а-яё]{2,}\s+зовут\b", open_head) and (
        re.search(rf"\b{_OP_MGR_NAME_RE}\b", open_head, re.I)
        or re.search(r"\bменеджер\s+отдела\s+продаж\b", open_head)
    ):
        return True
    return False


def _is_op_inbound_client_or_staff_routing_frame(text: str) -> bool:
    """
    Рамка входящего ОП до разговора с менеджером ОП (не привязана к линии сервиса/стойки).

    1) Клиент просит отдел продаж: «отдел продаж, мне нужен тенет», «нужен отдел продаж» (STT).
    2) Сотрудник не-ОП удерживает или переводит: «ожидайте на линии»,
       «перевожу / переведу / переключу / переключаю» (без явного перевода только в сервис).
       «минуточку»/«минуту» не используем — шум STT (9002, 8964).
    """
    if _is_sto_outbound_site_lead_opening(text):
        return False
    if _is_sto_employee_outbound_phone_handoff_opening(text):
        return False
    if _is_ats_outbound_dial_robot_prefix(text):
        return False
    if _is_service_taxi_dispatch_other((text or "").lower()):
        return False
    t = (text or "").lower()
    head = t[:2000]
    head_transfer = t[:1200]
    if (
        _has_op_outbound_surface_markers(text)
        and not _is_op_inbound_admin_handoff_to_op_manager(text)
        and not (
            "диспетчер сервиса" in head
            or "ассистент сервиса" in head
            or _sto_dispatcher_or_assistant_named(head)
        )
    ):
        return False
    client_sales = bool(
        re.search(
            r"(?:отдел\w*\s+продаж|продаж\w*\s+отдел).{0,80}(?:мне\s+)?нужн",
            head,
        )
        or re.search(
            r"(?:мне\s+)?нужн\w*.{0,40}(?:отдел\w*\s+продаж|продаж)",
            head,
        )
        or (
            _has_client_requested_sales_dept_in_opening(head)
            and any(
                p in head
                for p in (
                    "мне нужен",
                    "мне нужна",
                    "нужен тенет",
                    "нужен тэнет",
                    "нужен чери",
                    "хочу",
                    "интересует",
                    "танет",
                    "тенет",
                    "тэнет",
                )
            )
        )
    )
    staff_hold = any(p in head for p in _OP_RECEPTION_HOLD_ROUTING_PHRASES)
    # «Оставайтесь на линии» у робота АТС — только исходящий дозвон, не рамка входящего ОП.
    if staff_hold and _is_ats_outbound_dial_robot_prefix(text):
        staff_hold = False
    if staff_hold and _sto_booking_intake_blocks_false_op_gatekeeper(text):
        staff_hold = False
    # «контрольный звонок … ожидайте» — исх. диспетчер, не удержание на линии ОП (11299).
    if staff_hold and re.search(r"\bконтрольн\w*\s+звонок\b", head):
        staff_hold = False
    staff_transfer = bool(
        re.search(
            r"\b(?:перевожу|переведу|переключу|переключаю|соединю|соединяю)\b",
            head_transfer,
        )
    )
    routed_to_service_only = bool(
        re.search(
            r"(?:перевод\w*|переключ\w*|соедин\w*)[^.!?\n]{0,50}[^.!?\n]{0,30}сервис",
            head,
        )
    ) and "отдел продаж" not in head and not client_sales
    staff_routing = (staff_hold or staff_transfer) and not routed_to_service_only
    return client_sales or staff_routing


def _is_op_inbound_with_reception_gatekeeper(text: str) -> bool:
    """
    Входящий в ОП через стойку: «Вы по какому вопросу?», клиент формулирует тему (покупка авто, встреча, оценка),
    затем подключается менеджер (Евгений и т.д.). Фраза «мы вчера с вами разговаривали» здесь — продолжение входящего,
    не исходящий перезвон (иначе _is_outbound срабатывает на «это … евгений» + «мы с вами»).
    """
    if _is_sto_outbound_site_lead_opening(text):
        return False
    if _is_sto_employee_outbound_phone_handoff_opening(text):
        return False
    if _is_ats_outbound_dial_robot_prefix(text):
        return False
    if _is_crm_ats_outbound_lead_prefix(text):
        head_ob = (text or "").lower()[:5000]
        if _client_chose_service_after_admin_routing(head_ob):
            return False
        if re.search(r"перевед\w+|переключ\w+|соедин\w+", head_ob) and (
            "диспетчер" in head_ob
            or "на диспетчер" in head_ob
            or "чинсервис" in head_ob
            or "на сервис" in head_ob
        ):
            return False
    head_gk = (text or "").lower()[:3000]
    if _has_op_outbound_surface_markers(text) and (
        re.search(r"заявк\w*\s+отправлял", head_gk)
        or "нас вот заявку" in head_gk
        or re.search(r"\bменя\s+[а-яё]{2,}\s+зовут\b", head_gk)
    ):
        return False
    if _is_op_inbound_client_or_staff_routing_frame(text):
        return True
    t = (text or "").lower()
    head = t[:1000]
    # Жёсткое правило: входящий через администратора/линию дилера со словами «переключаю/переведу на менеджера»
    # трактуем как OP_IN, даже если далее менеджер ОП представляется («это Андрей», «передали автомобиль…»).
    dealer_hostess_open = bool(
        re.search(r"\b(?:официальн\w*|специальн\w*)\s+(?:дижер|дилер)\b", head)
    )
    admin_transfer_to_manager = (
        (
            "администратор" in head
            or "ресепш" in head
            or "reception" in head
            or dealer_hostess_open
        )
        and any(
            p in head
            for p in (
                "переключаю",
                "переключу",
                "переведу",
                "перевожу",
                "соединю",
            )
        )
        and (
            "менеджер" in head
            or (
                "отдел продаж" in head
                and not _is_admin_sales_or_service_routing_question(head)
            )
        )
    )
    # STT: «Переключаю вам, Александр» (без «на менеджера») + далее менеджер ОП (8846).
    admin_transfer_to_client = bool(
        ("администратор" in head or "администратор салона" in head[:400])
        and re.search(r"\bпереключ\w*\s+вам\b", head)
        and any(
            m in t
            for m in (
                "менеджер отдела продаж",
                "меня зовут андрей",
                "это андрей",
                "захаров",
                "евдокимов",
                "калаев",
                "щеголев",
                "андрей",
            )
        )
    )
    if admin_transfer_to_manager or admin_transfer_to_client:
        return True

    q_marker = "вы по какому вопросу"
    if q_marker not in t:
        return False
    if not any(
        x in t
        for x in (
            "покупк",
            "автомобил",
            "машин",
            "встреч",
            "оценк",
            "тест-драйв",
            "тест драйв",
        )
    ):
        return False
    tail = t[t.find(q_marker) :]
    return any(
        m in tail
        for m in (
            "евгений",
            "илья",
            "анастасия",
            "андрей",
            "захаров",
            "евдокимов",
            "калаев",
            "калаева",
            "краснощек",
            "щеголев",
            "дилерский центр",
            "менеджер отдела продаж",
            "автосалон",
            "викинги",
        )
    )


def _is_inbound_op_sales_intent_after_non_op_line(transcript: str) -> bool:
    """
    Входящий: клиент попал на сервис/стойку, но тема — новый авто и перевод на менеджера ОП.
    Не относить к «Прочие» из-за ложного срабатывания CRM-маркеров без входящего намерения ОП.

    Слабые одиночные маркеры (например, «стоимост», «цена», «в наличии», «кредит») сами по себе
    не считаются достаточным признаком ОП: в речи диспетчера/приёмки СТО или отдела запчастей
    они часто описывают стоимость работ или цену запчасти, а не покупку автомобиля.
    Требуется либо явный перевод на менеджера ОП, либо буквальная сильная ОП-формулировка,
    либо несколько «весомых» ОП-маркеров одновременно. Дополнительно гасим срабатывание,
    если на линии явный сервисный диспетчер из справочника СТО и есть субстантивный сервисный
    смысл, а явного перевода в ОП нет.
    """
    low = (transcript or "").lower()[:9000]
    if _is_outbound(low):
        return False

    transfer_to_op = bool(
        re.search(r"перевед\w*[^.!?\n]{0,60}(?:менеджер\w*[^.!?\n]{0,30}отдел\w*\s+продаж|отдел\w*\s+продаж)", low)
        or re.search(r"переключ\w*[^.!?\n]{0,60}(?:менеджер\w*[^.!?\n]{0,30}отдел\w*\s+продаж|отдел\w*\s+продаж)", low)
    )

    explicit_op_phrases = (
        "менеджер отдела продаж",
        "менеджера отдела продаж",
        "соединить с менеджер",
        "переведу вас на менеджера",
        "переведу на менеджера отдела продаж",
        "переведу на менеджера",
        "переключу на менеджера",
        "переключу вас на менеджера",
        "интересует новый",
        "интересует автомобиль",
        "новый автомобиль",
        "новый авто",
        "интересует черри",
        "интересует тенет",
        "трейд-ин",
        "трейд ин",
        "по поводу покупки",
        "покупку автомобиля",
    )
    has_strong_op = transfer_to_op or any(p in low for p in explicit_op_phrases)

    weighty_op_markers = (
        "покупк",
        "комплектац",
        "в наличии",
        "доплат",
        "за налич",
        "кредит",
        "рассрочк",
    )
    weighty_count = sum(1 for p in weighty_op_markers if p in low)

    if not has_strong_op and weighty_count < 2:
        return False

    has_explicit_op_transfer_phrase = (
        "менеджер отдела продаж" in low or "менеджера отдела продаж" in low
    )
    sto_dispatcher_named = any(
        len(name) >= 4 and name in low for name in _STO_MANAGER_NAMES
    )
    if (
        not transfer_to_op
        and not has_explicit_op_transfer_phrase
        and sto_dispatcher_named
        and _has_sto_substantive_service_intent(low)
    ):
        return False

    return (
        "диспетчер сервиса" in low
        or ("вы попали" in low and "сервис" in low)
        or ("официальный дилер" in low and "администратор" in low)
        or (
            ("администратор салона" in low or "администратор саллона" in low)
            and any(p in low[:3000] for p in ("менеджер", "продажник", "отдел продаж"))
        )
        or "переведу вас на менеджера" in low
        or "переведу вас на менеджера отдела продаж" in low
        or transfer_to_op
    )


def _is_site_lead_callback_phrase(transcript: str) -> bool:
    """
    Маркер лида с сайта / CRM-формы: устойчивые формулировки.
    Нельзя использовать произвольное сочетание «заявк» + «сайт» — ложные попадания
    («зайду в заявку», «на другом сайте» в одном тексте дают ошибочный «лид с сайта»).
    """
    low = (transcript or "").lower().replace("ё", "е")
    if not low.strip():
        return False
    needles = (
        "оставляли заявку на сайте",
        "оставили заявку на сайте",
        "оставили заявку на нашем сайте",
        "оставляли заявку на нашем сайте",
        "оставили заявку на официальном сайте",
        "оставляли заявку на официальном сайте",
        "заполнили заявку на сайте",
        "заполняли заявку на сайте",
        "заполнил заявку на сайте",
        "оставили заявку через сайт",
        "оставляли заявку через сайт",
        "заявка с сайта",
        "заявку с сайта",
        "заявки с сайта",
        "по заявке с сайта",
        "звонок по заявке с сайта",
        "заявка на сайте",
        "заявку на сайте",
        "заявки на сайте",
    )
    if any(p in low for p in needles):
        return True
    if re.search(r"заявк\w*\s+(?:на|со|с)\s+(?:официальн\w*\s+)?сайт", low):
        return True
    if re.search(r"заявк\w*\s+через\s+сайт", low):
        return True
    return False


def _is_dialer_or_site_outbound_opening(text: str) -> bool:
    """
    В начале записи часто попадает служебный текст АТС/CRM: исходящий дозвон по заявке с сайта.
    Дальше админ говорит «переключу на менеджера» — это всё ещё исходящий, не входящий.
    """
    head = (text or "").lower()[:2500]
    return any(
        m in head
        for m in (
            "начинаем звонить",
            "начинаем набор",
            "начинаем дозвон",
            "ожидайте соединения",
            "ожидайте соединение",
            "набираем абонент",
            "звоним абонент",
            "идёт набор",
            "идет набор",
            "ожидайте ответа",
            "автодозвон",
            "звонок с сайта",
            "звонок по заявке с сайта",
            "заявка с сайта",
            "заявка на сайте",
            "заявка на тест-драйв",
            "заявка на тест драйв",
            "тест-драйв с сайта",
            "сайт тест-драйв",
            "дилерский сайт",
            "цель обращения",
        )
    )


def _is_crm_ats_outbound_lead_prefix(text: str) -> bool:
    """
    Префикс АТС/CRM: дилер набирает клиента («начинаем звонить», «позвонить клиенту»,
    «заказал обратный звонок»). Имеет приоритет над gatekeeper «админ → менеджер».
    """
    if not (text or "").strip():
        return False
    head = (text or "").lower()[:2800]
    if _is_dialer_or_site_outbound_opening(text):
        return True
    if re.search(r"заказал\w*\s+обратн\w*\s+звонок", head):
        return True
    if "заказан обратный звонок" in head or "заказан обратный" in head:
        return True
    if "позвонить клиенту" in head:
        return True
    return False


def _is_admin_sales_or_service_routing_question(low: str) -> bool:
    """Админ уточняет «отдел продаж или сервис?» — не выбор отдела продаж."""
    h = (low or "")[:4000]
    if re.search(r"\bпродаж\w*\s+или\s+сервис\b", h, re.I):
        return True
    # STT: «отдел продаж, сервис интересует/интересовал?» без «или» (10167, 11100).
    if re.search(r"\bотдел\w*\s+продаж\b", h, re.I) and re.search(
        r"\bсервис\w*\s+интерес\w*", h, re.I
    ):
        return True
    # STT: «отдел продаж, сервис … или уже созвонились?» (11100).
    if (
        re.search(r"\bотдел\w*\s+продаж\b", h, re.I)
        and re.search(r"\bсервис\b", h, re.I)
        and re.search(r"\bсозвонил\w*", h, re.I)
    ):
        return True
    return False


def _client_chose_service_after_admin_routing(low: str) -> bool:
    """После вопроса «продаж или сервис» клиент явно выбрал сервис."""
    h = (low or "")[:5000]
    if not _is_admin_sales_or_service_routing_question(h):
        return False
    if re.search(
        r"продаж\w*\s+или\s+сервис[^.?!]{0,120}[?.!]?\s*(?:угу[,.]?\s*)?(?:э-э[,]?\s*)?сервис\b",
        h,
        re.I,
    ):
        return True
    if re.search(r"интересует\?[^.?!]{0,60}\bсервис\b", h, re.I):
        return True
    # «сервис интересует?» → «меня интересует … сервис» (10167).
    if re.search(
        r"\bсервис\w*\s+интересует\?[^.?!]{0,160}(?:да[,]?\s*)?(?:меня\s+)?интересует[^.?!]{0,80}\bсервис\b",
        h,
        re.I,
    ):
        return True
    if re.search(
        r"(?:^|[?.!]\s*)(?:да[,]?\s*)?(?:меня\s+)?интересует[^.?!]{0,100}\bсервис\b",
        h,
        re.I,
    ):
        return True
    return False


def _site_callback_op_sto_scores(transcript: str, tl: str) -> Tuple[int, int]:
    """Баллы ОП/СТО для перезвона с сайта (без ложного «отдел продаж» из вопроса админа)."""
    head = (tl or "")[:5000]
    service_booking_ctx = _has_sto_substantive_service_intent(tl) and (
        any(
            x in tl
            for x in (
                "записал",
                "записали",
                "запись",
                "радиатор",
                "чистк",
                "пробег",
                "госномер",
                "напомните фамилию",
                "контрольный звонок",
            )
        )
        or re.search(r"\bто\s+необходимо\b", tl)
        or re.search(r"\bна\s+(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\w*\b", tl)
        or (re.search(r"\bсколько\b", tl) and re.search(r"\bто\b", tl))
    )
    op_markers = (
        "менеджер отдела продаж",
        "отдел продаж",
        "переведу на менеджера",
        "перевожу на менеджера",
        "переключу на менеджера",
        "переключаю на менеджера",
        "интересует автомобиль",
        "интересует машина",
        "покупк",
        "комплектац",
        "в наличии",
        "доплат",
        "скидк",
        "кредит",
        "за налич",
        "рассрочк",
    )
    sto_markers = (
        "диспетчер сервиса",
        "ассистент сервиса",
        "на сервис",
        "на диспетчер",
        "переведу на диспетчер",
        "перевожу на диспетчер",
        "переключу на диспетчер",
        "чинсервис",
        "записаться на то",
        "запись на то",
        "техническое обслуживание",
        "гарантийн",
        "ремонт",
        "диагностик",
        "мастер-приёмщик",
        "мастер приемщик",
        "мастер-приемщик",
        "кузов",
        "запчаст",
        "чистк радиатор",
        "чистка радиатор",
        "радиатор",
    )
    op_score = 0
    for p in op_markers:
        if p == "отдел продаж" and _is_admin_sales_or_service_routing_question(head):
            continue
        if p in tl:
            op_score += 1
    sto_score = sum(1 for p in sto_markers if p in tl) + (
        1 if _has_sto_substantive_service_intent(tl) else 0
    )
    if _client_chose_service_after_admin_routing(tl):
        sto_score += 2
    if re.search(r"перевед\w+|переключ\w+|соедин\w+", head) and (
        "диспетчер" in head or "на диспетчер" in head
    ):
        sto_score += 1
    return op_score, sto_score


def _is_op_dealer_ringback_intro(text: str) -> bool:
    """
    Дилер перезванивает клиенту по пропущенному / «был звонок».
    STT: «звоночек от вам был», «звоночек от вас был».
    """
    head = (text or "").lower()[:3500]
    if not head.strip():
        return False
    dealerish = any(
        p in head
        for p in (
            "официальн",
            "дилер",
            "администратор салона",
            "тенет",
            "тэнет",
            "tenet",
            "чери",
            "chery",
            "викинг",
            "салон",
        )
    )
    if not dealerish:
        return False
    if re.search(
        r"(?:звоноч\w*|пропущен\w*)\s+от\s+вас\s+был|"
        r"(?:звоноч\w*|пропущен\w*)\s+от\s+вам\s+был|"
        r"от\s+вас\s+был\s+(?:звонок|пропущ)|"
        r"заявоч\w*\s+от\s+вас\s+был\w*|"
        r"заявк\w*\s+от\s+вас\s+был\w*",
        head,
        re.I,
    ):
        return True
    # 10539: «пропущенный звонок» + менеджер дозванивается (STT без «от вас был»).
    if re.search(r"\bпропущен\w*\s+звонок\b", head, re.I):
        if re.search(r"\bзвоню\b", head, re.I) and re.search(
            r"(?:не\s+мог\w*|никак)\s+[^.!?]{0,40}дозвон|второй\s+день|третий\s+день",
            head,
            re.I,
        ):
            return True
    return False


def _has_eto_or_chto_op_name_intro(fragment: str) -> bool:
    """
    «это Илья» или приветствие / алло + «что Имя» (типичная ошибка STT вместо «это»).
    Не путать с маркером исходящего «что решили»; не считать любое «это » без имени из списка ОП.
    """
    if "что решили" in fragment:
        return False
    # «Ильянет» / «Ильясет» — типичное слияние «Илья» + Чери/Тенет в STT.
    _nm = (
        r"(?:илья|ильянет|ильясет|евгения|евгений|анастасию|анастасия|андрея|андрей|"
        r"захаров|евдокимов|краснощеков|краснощёков|калаев|калаева|щеголев)"
    )
    if re.search(rf"\bэто\s+{_nm}\b", fragment, re.I):
        return True
    if not re.search(rf"\bчто\s+{_nm}\b", fragment, re.I):
        return False
    return bool(
        re.search(
            rf"(?:здравствуйте|добрый\s+день|добрый\s+вечер)\b[^.\n]{{0,220}}?\bчто\s+{_nm}\b",
            fragment,
            re.I | re.DOTALL,
        )
        or re.search(
            rf"\bалло\s*[,.]?\s*(?:здравствуйте\s*[,.!?]?\s*)?что\s+{_nm}\b",
            fragment,
            re.I,
        )
    )


def _sto_client_callback_inbound_markers(low_fragment: str) -> bool:
    """
    Клиент перезванивает на линию СТО после пропущенного / звонка с линии — входящий,
    даже если запись начинается с приветствия диспетчера (трубку взяла линия первой).
    """
    if not (low_fragment or "").strip():
        return False
    lf = low_fragment.lower()
    # «Перезваниваю» без уточнения часто говорит и сотрудник («перезваниваю по заявке»).
    # Клиент — только рядом с маркерами пропущенного / звонка с линии.
    if re.search(
        r"\bперезваниваю\b[^.!?]{0,160}\b(?:от\s+вас|звонок\s+был|пропущ|был\s+звонок)\b",
        lf,
        re.I,
    ):
        return True
    if re.search(r"\b(?:хотел|хотела)\s+перезвонить\b", lf) or re.search(r"\bперезвонить\s+хотел", lf):
        return True
    if re.search(r"\b(?:звонок|пропущен\w*|пропуск)\s+был\s+от\s+вас\b", lf):
        return True
    if re.search(r"\bу\s+меня\s+(?:был\s+)?(?:звонок|пропущ)\w*\s+от\s+вас\b", lf):
        return True
    if re.search(r"\bот\s+вас\s+был\s+(?:звонок|пропущ)", lf):
        return True
    # 17180: «мне звонили вы пропущенные» / «пропущенные … я на запись» — клиент перезванивает.
    if re.search(r"\bмне\s+звонил\w*[^.!?]{0,50}пропущ", lf[:1200], re.I):
        return True
    if re.search(r"\bзвонил\w*\s+вы\s+пропущ", lf[:1200], re.I):
        return True
    if re.search(r"\bпропущенн\w*\b", lf[:900], re.I) and re.search(
        r"\b(?:я\s+на\s+запись|на\s+запись)\b",
        lf[:900],
        re.I,
    ):
        return True
    if re.search(r"\bвы\s+звонили\s+(?:мне|нам)\b", lf):
        if re.search(r"\bвы\s+звонили\s+нам\b", lf) and _sto_employee_outbound_vy_zvonili_nam_opening(lf[:1200]):
            pass
        else:
            return True
    if re.search(r"\b(?:не\s+)?успел\w*\s+взять\s+трубку\b", lf):
        return True
    return False


def _has_client_name_outbound_script_marker(text: str) -> bool:
    """
    Единый маркер исходящего скрипта:
    имя клиента + приветствие + представление сотрудника + вопрос про удобство разговора
    и/или инициатива сотрудника («вы хотели/звонили/интересовались/были/спрашивали»).
    """
    head = (text or "").lower()[:4500]
    head_800 = head[:800]
    if not head.strip():
        return False
    # Иначе «вы звонили» в реплике клиента даёт ложный исходящий при входящем перезвоне на СТО.
    if _sto_client_callback_inbound_markers(head):
        return False
    greets = ("здравствуйте", "добрый день", "добрый вечер")
    convenience = (
        "удобно вам говорить",
        "удобно говорить",
        "удобно вам разговаривать",
        "удобно разговаривать",
        "удобно, да",
        "удобно да",
        "да, удобно",
        "удобно?",
    )
    staff_intro = (
        "это ",
        "диспетчер",
        "ассистент",
        "компания викинги",
        "викинги",
        "чери",
        "меня зовут",
        "официальный дилер",
        "тэнет",
        "тенет",
    )
    # «вы хотели записаться» в середине приёмки — вопрос диспетчера, не исходящий скрипт (8508).
    initiative_early = (
        "вы хотели",
        "вы интересовались",
        "вы были",
        "вы спрашивали",
        "звонок от вас был",
        "хотели уточнить",
    )
    initiative_anywhere = (
        "звоню по поводу",
        "звоню по вашему автомобил",
        "передали заяв",
        "передали вашу заяв",
        "заявку оставляли",
        "вы заявку оставляли",
        "оставляли заявку",
    )
    has_greet = any(g in head for g in greets)
    # «Девушка, здравствуйте» — обращение клиента к диспетчеру, не исходящий скрипт «Иван, здравствуйте» (11927).
    has_client_name_call = bool(
        re.search(
            r"\b(?!девушк(?:а|и|у|е|ой)?\b|молод(?:ой|ая)\s+человек\b)"
            r"[а-яё]{2,}(?:\s+[а-яё]{2,}){0,2}\s*,\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b[а-яё]{2,}(?:\s+[а-яё]{2,}){0,2}\s*,\s*ещ[её]\s+раз\s+здравствуйте\b",
            head,
            re.I,
        )
    )
    # «Администратор салона Анастасия, здравствуйте» — самопредставление стойки, не «Иван, здравствуйте» (16688).
    if re.search(
        r"\bадминистратор(?:\s+салона)?\s+[а-яё]{3,15}\s*,\s*(?:здравствуйте|добрый\s+(?:день|вечер))\b",
        head_800,
        re.I,
    ):
        has_client_name_call = False
    # 16724 / 16829: «Юлия, здравствуйте» после «диспетчер сервиса Юлия» / «диспетчер сервиса. Юлия.» —
    # клиент отвечает, не исходящий скрипт.
    if has_client_name_call:
        m_client_greet = re.search(
            r"\b(?!девушк(?:а|и|у|е|ой)?\b|молод(?:ой|ая)\s+человек\b)"
            r"([а-яё]{2,15})\s*,\s*(?:здравствуйте|добрый\s+(?:день|вечер))\b",
            head,
            re.I,
        )
        if m_client_greet:
            cg_name = m_client_greet.group(1).lower()
            if cg_name in _STO_MANAGER_NAMES or cg_name in _OP_MANAGER_NAMES:
                if re.search(
                    rf"\b(?:диспетчер\s+сервис\w*|ассистент\s+сервис\w*)[.\s,]+{re.escape(cg_name)}\b",
                    head_800[:500],
                    re.I,
                ) or re.search(rf"\bменя\s+зовут\s+{re.escape(cg_name)}\b", head_800[:500], re.I):
                    has_client_name_call = False
    has_intro = any(x in head for x in staff_intro)
    has_convenience = any(x in head_800 for x in convenience)
    # STT без «вы»: «интересовались автомобилем …, всё верно?» — исходящий CRM (8583).
    crm_interest_verify = bool(
        re.search(r"\bинтересовал\w*\s+автомобил", head_800, re.I)
        and re.search(r"\b(?:всё|все)\s+верно\b|\bверно\s*\?", head_800, re.I)
    )
    has_initiative = (
        not _is_dispatcher_to_booking_question_not_outbound(head_800)
        and (
            (
                not _is_op_inbound_admin_or_avito_handoff(text)
                and any(x in head_800 for x in initiative_early)
            )
            or any(x in head for x in initiative_anywhere)
            or crm_interest_verify
        )
    )
    return (has_client_name_call and has_intro and has_convenience) or (has_greet and has_intro and has_initiative)


def _allo_addressed_name_is_outbound_client(name_fragment: str) -> bool:
    """True, если после «Алло» похоже на обращение к клиенту по имени (исходящий), а не на приветствие."""
    token = ((name_fragment or "").strip().lower().split() or [""])[0]
    if not token or token == "алло":
        return False
    if token in _ALLO_NOT_CLIENT_NAME_TOKENS:
        return False
    if token in _STO_MANAGER_NAMES or token in _OP_MANAGER_NAMES:
        return False
    return True



def _sto_outbound_logic_variant() -> int:
    """
    1 = opening_role (рабочий), 2 = legacy-маркеры (откат).
    Переключение: VIKINGI_STO_OUTBOUND_VARIANT=2
    """
    raw = (os.environ.get("VIKINGI_STO_OUTBOUND_VARIANT") or "1").strip()
    return 2 if raw == "2" else 1


def _sto_opening_has_client_name_address(head: str) -> bool:
    """Обращение к клиенту в открытии: «Алло, Имя…» / «Имя, добрый день» (только первые фразы)."""
    # Не смотреть перевод на мастера («Алло, Владимир…») в середине звонка (16395).
    h = (head or "").lower()[:240]
    if re.search(
        r"\bалло[,.!?]?\s+[а-яё]{2,}(?:\s+[а-яё]{2,})?\s*"
        r"(?:\?|,\s*(?:да\s*)?\?|,\s*(?:(?:ещ[её]\s+раз\s+)?(?:здравствуйте|добрый\s+(?:день|вечер))|ещ[её]\s+раз))",
        h,
        re.I,
    ):
        # «Алло, здравствуйте» без имени — не набор клиента.
        if re.search(r"\bалло[,.!?]?\s+здравствуйте\b", h, re.I):
            return False
        return True
    # «Иван, добрый день, компания Викинги…» — только в самом начале head.
    head_early = h[:180]
    if re.search(
        r"^(?:[^а-яё]{0,40})?(?:алло[,.!]?\s*)?[а-яё]{2,}(?:\s+[а-яё]{2,})?\s*,\s*"
        r"(?:(?:ещ[её]\s+раз\s+)?(?:добрый\s+(?:день|вечер)|здравствуйте))\b",
        head_early,
        re.I,
    ):
        token_m = re.search(
            r"^(?:[^а-яё]{0,40})?(?:алло[,.!]?\s*)?([а-яё]{2,})",
            head_early,
            re.I,
        )
        token = (token_m.group(1).lower() if token_m else "")
        if token in {
            "девушка",
            "женщина",
            "мужчина",
            "господин",
            "госпожа",
            "викинги",
            "чери",
            "диспетчер",
            "ассистент",
            "компания",
        }:
            return False
        return True
    return False


def _sto_opening_starts_as_reception_line(head: str) -> bool:
    """Открытие с представления линии СТО, без «Алло, клиент»."""
    h = re.sub(r"^[^а-яёa-z0-9]+", "", (head or "").lower()[:220])
    if re.match(r"алло\b", h):
        return False
    return bool(
        re.match(r"(?:чери\s+)?викинг", h)
        or re.match(r"викинг", h)
        or re.match(r"чери\s+викинг", h)
        or re.match(r"(?:диспетчер|ассистент)\s+сервис", h)
        or re.match(r"дилерск\w*\s+центр", h)
    )


def _sto_opening_has_dealer_staff(head: str) -> bool:
    h = (head or "").lower()[:900]
    dealer = (
        "викинг" in h
        or "чери" in h
        or "дилерск" in h
        or "компания викинг" in h
    )
    staff = (
        "диспетчер" in h
        or "ассистент" in h
        or _sto_dispatcher_or_assistant_named(h)
    )
    return bool(dealer and staff)


def _sto_opening_outbound_cliche(head: str) -> bool:
    """Исходящие клише диспетчера в открытии (канон opening_role; включает бывшие 16497/16555)."""
    h = (head or "").lower()[:1000]
    return bool(
        re.search(r"\bудобно\s+(?:говорить|разговаривать)", h, re.I)
        or re.search(r"\bудобно\s*\?", h, re.I)
        or re.search(r"\bперезванива\w*", h[:800], re.I)
        or re.search(r"\bвы\s+оставлял\w*\s+заявк", h, re.I)
        or re.search(r"\bзвонил\w*[^.!?]{0,100}\bхотел\w*", h, re.I)
        or re.search(r"\bпроконсультир\w*\s+хотел\w*", h, re.I)
        or re.search(r"\bна\s+то\s+хотел\w*", h, re.I)
        or re.search(r"\bхотел\w*\s+запис", h, re.I)
        or re.search(r"\bэто\s+компания\s+викинг", h, re.I)
        or re.search(
            r"\bпо\s+техническ\w+\s+обслуживан\w*[^.!?]{0,60}хотел\w*",
            h,
            re.I,
        )
    )


def _sto_opening_inbound_reception(head: str) -> bool:
    """Линия СТО принимает входящий: «Викинги… диспетчер… здравствуйте/слушаю вас»."""
    h = (head or "").lower()[:700]
    # Набор клиенту («Алло, Имя») — не приём.
    if re.search(r"\bалло\b", h[:120], re.I) and _sto_opening_has_client_name_address(h[:500]):
        return False
    # 20421: «Викинги ... слушаю вас» без явного «диспетчер/ассистент» —
    # всё равно входящая линия приёмки, не исходящий набор.
    if _sto_opening_starts_as_reception_line(h) and re.search(r"\bслуша(?:ю)?\s+вас\b", h[:220], re.I):
        return True
    if _sto_opening_starts_as_reception_line(h) and _sto_opening_has_dealer_staff(h):
        return True
    if ("слушаю вас" in h[:450] or "слуша вас" in h[:450] or re.search(r"\bслуша(?:ю)?\s+вас\b", h[:450])):
        if "диспетчер" in h[:550] or "ассистент" in h[:550] or _sto_dispatcher_or_assistant_named(h[:550]):
            return True
    if re.search(
        r"\b(?:диспетчер|ассистент)\s+сервис\w*[^.!?]{0,60}(?:здравствуйте|добрый\s+(?:день|вечер)|слушаю)\b",
        h[:500],
        re.I,
    ) and ("викинг" in h[:500] or "чери" in h[:500]):
        if not (re.search(r"\bалло\b", h[:100], re.I) and _sto_opening_has_client_name_address(h[:400])):
            return True
    return False


def _sto_opening_outbound_dial(head: str) -> bool:
    """
    Сотрудник набирает клиента: имя + Викинги/диспетчер (+ клише).
    Канон variant1; бывшие call_id-хелперы (16497/16501/16555) сюда сведены.
    """
    h = (head or "").lower()[:1000]
    # Приём линии («Викинги… диспетчер… здравствуйте/слушаю») — не исходящий набор.
    if _sto_opening_starts_as_reception_line(h):
        return False
    # CRM: «Алло, И.О.? … удобно говорить» / «ещё раз здравствуйте» (12021, 16501).
    if _is_sto_outbound_crm_name_call_convenience_opening(h):
        return True
    name = _sto_opening_has_client_name_address(h)
    staff_dealer = _sto_opening_has_dealer_staff(h)
    cliche = _sto_opening_outbound_cliche(h)
    if name and staff_dealer:
        return True
    if staff_dealer and cliche and (
        name
        or re.search(r"\bэто\s+компания\s+викинг", h)
        or re.search(r"\bперезванива\w*", h[:800])
    ):
        return True
    return False


def _sto_opening_role(text: str) -> str:
    """
    Роль открытия СТО-звонка по первым фразам.
    Возвращает: outbound_dial | inbound_reception | ambiguous
    """
    raw = text or ""
    # Клиентский перезвон на пропущенный — входящий по направлению.
    low_raw = raw.lower()
    if _sto_client_callback_inbound_markers(low_raw[:5000]):
        return "inbound_reception"
    t = _normalize_stt_classification_artifacts(raw).lower()
    t = re.sub(r"\bилерский\s+центр\b", "дилерский центр", t)
    head = t[:900]
    # Админ → перевод на СТО/ОП — не чистая форма набора/приёма.
    if _is_inbound_admin_transfer_to_sto_dispatcher_line(raw) or _is_op_inbound_admin_or_avito_handoff(raw):
        return "ambiguous"
    out = _sto_opening_outbound_dial(head)
    inn = _sto_opening_inbound_reception(head)
    if out and not inn:
        return "outbound_dial"
    if inn and not out:
        return "inbound_reception"
    if out and inn:
        # Конфликт: старт с представления линии → входящий; иначе набор клиента.
        if _sto_opening_starts_as_reception_line(head) and not re.search(r"\bалло\b", head[:120], re.I):
            return "inbound_reception"
        return "outbound_dial" if _sto_opening_has_client_name_address(head) else "inbound_reception"
    return "ambiguous"


def _is_outbound_variant1_opening_role(text: str) -> bool:
    """Вариант 1 (рабочий): направление по opening_role; ambiguous → legacy variant2."""
    raw = text or ""
    if _is_ats_outbound_dial_robot_prefix(raw):
        return True
    if _is_op_dealer_ringback_intro(raw):
        return True
    if _is_crm_ats_outbound_lead_prefix(raw) and not _sto_client_callback_inbound_markers(
        (raw or "").lower()[:5000]
    ):
        return True
    role = _sto_opening_role(raw)
    if role == "outbound_dial":
        return True
    if role == "inbound_reception":
        return False
    return _is_outbound_variant2_legacy(raw)


def _is_outbound(text: str) -> bool:
    """Признаки исходящего звонка (менеджер звонит клиенту). Рабочий путь — variant1."""
    if _sto_outbound_logic_variant() == 2:
        return _is_outbound_variant2_legacy(text)
    return _is_outbound_variant1_opening_role(text)


def _is_outbound_variant2_legacy(text: str) -> bool:
    """Вариант 2 (откат): прежняя цепочка маркеров исходящего. Не вызывать напрямую — через _is_outbound."""
    raw = text or ""
    # Префикс АТС («оставайтесь на линии») — до нормализации: иначе STT-мусор отрезается (15383).
    if _is_ats_outbound_dial_robot_prefix(raw):
        return True
    text = _normalize_stt_classification_artifacts(raw)
    t = text.lower()
    # STT: «…илерский центр» вместо «дилерский центр»
    t = re.sub(r"\bилерский\s+центр\b", "дилерский центр", t)
    if _is_ats_outbound_dial_robot_prefix(text):
        return True
    # Перезвон салона по пропущенному («звоночек от вас был»): дилер набирает клиента — исходящий,
    # даже если клиент упоминает «пропущенный от вас» (11100).
    if _is_op_dealer_ringback_intro(text):
        return True
    # Входящий перезвон клиента на линию СТО (пропущенный от вас и т.п.) — не исходящий сотрудника.
    if _sto_client_callback_inbound_markers(t[:5000]):
        return False
    # 12476 / 16769: клиент после заявки; «заявку получили» от диспетчера — не исходящий перезвон.
    if _suppress_sto_lead_echo_as_outbound(t[:1200]):
        return False
    # Входящий СТО: после «... слушаю вас» клиент ссылается на прошлый звонок
    # («мы ранее звонили», «вы говорили/обещали ...») — не исходящий сотрудника.
    head_inbound_ref = t[:1400]
    if (
        "слушаю вас" in head_inbound_ref[:240]
        and re.search(
            r"\b(?:мы|я)\b[^.!?]{0,80}\b(?:ранее|до этого|дня\s+\w+\s+назад)?[^.!?]{0,80}\bзвонил(?:и|а)?\b",
            head_inbound_ref,
            re.I,
        )
        and any(p in head_inbound_ref for p in ("вы говорили", "вы обещали", "по поводу записи"))
    ):
        return False
    if _is_inbound_client_existing_booking_after_misdirected_call(text):
        return False
    # СТО исх.: «передали телефон, звонили, слушаю» — до OP gatekeeper и «минуточку» в теле разговора (8570).
    head_sto_redial_early = t[:1200]
    if _is_sto_employee_outbound_phone_handoff_opening(text) and (
        _sto_dispatcher_or_assistant_named(head_sto_redial_early)
        or "диспетчер сервиса" in head_sto_redial_early
    ):
        return True
    # СТО исх.: «Алло, Данила?» / «Алло, Кристина, ещё раз здравствуйте» / «…ещё раз:» (8826, 8851, 15679).
    head_sto_name_call = t[:1200]
    if re.search(
        r"\bалло[.!?,]?\s+[а-яё]{2,15}(?:\s+[а-яё]{2,15})?"
        r"(?:\s*\?|,\s*да\s*\?|,\s*ещ[её]\s+раз\s+(?:здравствуйте|добрый\s+(?:день|вечер))"
        r"|,\s*ещ[её]\s+раз\s*:?"
        r"|\s+ещ[её]\s+раз\b)",
        t[:1500],
        re.I,
    ):
        if (
            "диспетчер сервиса" in head_sto_name_call
            or "диспетчерск" in head_sto_name_call
            or re.search(r"диспетчер\s+сервис\w", head_sto_name_call)
            or "ассистент сервиса" in head_sto_name_call
            or _sto_dispatcher_or_assistant_named(head_sto_name_call)
        ) and ("викинг" in head_sto_name_call or "компания викинг" in head_sto_name_call):
            return True
    # СТО исх.: «Удобно, да?» / «Удобно?» — скрипт диспетчера при перезвоне (8854, 10568).
    head_sto_udobno = t[:2500]
    if re.search(r"\bудобно,?\s*(?:да\s*)?\?", head_sto_udobno, re.I):
        if (
            "диспетчер" in head_sto_udobno
            or "ассистент сервиса" in head_sto_udobno
            or _sto_dispatcher_or_assistant_named(head_sto_udobno)
        ) and ("викинг" in head_sto_udobno or "компания викинг" in head_sto_udobno):
            if _sto_outbound_invite_by_lead_or_booking(head_sto_udobno) or re.search(
                r"\bалло,?\s+[а-яё]{2,20},?\s*да\s*\?", head_sto_udobno, re.I
            ):
                return True
    if re.search(r"\bперезванива\w*\s+вам\b", head_sto_name_call, re.I):
        if (
            "диспетчер" in head_sto_name_call
            or "ассистент сервиса" in head_sto_name_call
            or _sto_dispatcher_or_assistant_named(head_sto_name_call)
        ) and ("викинг" in head_sto_name_call or "компания викинг" in head_sto_name_call):
            return True
    first = t[:600]
    intro_pref = t[:1200]
    first_150 = t[:150]
    # Входящий: клиент → администратор/стойка → перевод на менеджера ОП. Такие тексты содержат и «передали …»,
    # и «это Андрей, менеджер…» после перевода — не считаем исходящим до прочих маркеров АТС/лида.
    if _is_sto_outbound_site_lead_opening(text):
        return True
    if _is_op_outbound_crm_test_drive_followup_redial(text):
        return True
    # Префикс АТС/CRM («начинаем звонить», «заказал обратный звонок») — до admin_handoff (9801).
    if _is_crm_ats_outbound_lead_prefix(text):
        if not _sto_client_callback_inbound_markers(t[:5000]):
            return True
    if _is_op_inbound_admin_handoff_to_op_manager(text):
        return False
    if _is_op_inbound_admin_direct_client_call(text):
        return False
    if _is_op_inbound_admin_or_avito_handoff(text):
        if not _is_ats_outbound_dial_robot_prefix(text) and not _is_crm_ats_outbound_lead_prefix(text):
            return False
    # 16497: «это компания Викинги … диспетчер … перезваниваю на ТО хотели» — до classic inbound
    # (иначе «записать»/«замену масла» в открытии гасят исходящий).
    if _is_sto_outbound_company_dispatcher_perezanivayu_to_opening(text):
        return True
    # 16555: «Алло. Имя, добрый день, компания Викинги … звонили … хотели» — до classic inbound
    # (иначе «проконсультир*» клиента гасит исходящий).
    if _is_sto_outbound_company_dispatcher_zvonili_to_consult_opening(text):
        return True
    # Классический входящий на диспетчера — до CRM-маркера исходящего ОП (11927, 12958).
    if _is_classic_sto_inbound_dispatcher_booking_intake(text):
        return False
    # Исходящий ОП по лиду («меня … зовут», «заявку отправляли») — до gatekeeper: иначе «соединю» в хвосте (8890).
    # Не путать с исх. СТО «по заявке звоню» + диспетчер сервиса (8852).
    head_op_out = t[:2800]
    if _has_op_outbound_surface_markers(text) and not (
        "диспетчер сервиса" in head_op_out
        or "ассистент сервиса" in head_op_out
        or _sto_dispatcher_or_assistant_named(head_op_out)
    ):
        return True
    # CRM-исходящий до gatekeeper: иначе «ожидайте» в «контрольный звонок … ожидайте» даёт ложный OP_IN (11299).
    if _has_client_name_outbound_script_marker(text):
        return True
    hq_early = t[:4500]
    if (
        ("удобно говорить" in hq_early or "удобно разговаривать" in hq_early or "удобно вам говорить" in hq_early)
        and (
            "диспетчер сервиса" in hq_early
            or "ассистент сервиса" in hq_early
            or "ассистент-сервис" in hq_early
            or _sto_dispatcher_or_assistant_named(hq_early)
            or ("меня зовут" in hq_early[:900] and ("викинг" in hq_early[:900] or "компания викинг" in hq_early[:900]))
        )
    ):
        return True
    if re.search(r"\bобращени\w*\s+(?:ваше\s+)?получил", hq_early) and (
        "удобно разговаривать" in hq_early
        or "удобно говорить" in hq_early
        or _has_client_name_outbound_script_marker(text)
    ):
        return True
    if _is_op_inbound_with_reception_gatekeeper(text):
        return False
    if _is_dialer_or_site_outbound_opening(t):
        return True
    if _is_op_dealer_ringback_intro(text):
        return True
    # Исходящий СТО-перезвон по заявке: отдельный приоритетный паттерн.
    if _has_initial_sto_reception_greeting(text):
        h1200 = t[:1200]
        if "получили вашу заявку" in h1200 or "получили вашу заявку на сервис" in h1200:
            return True
    # Исходящее напоминание/подтверждение уже сделанной записи на сервис:
    # «по записи звоню на завтра», «звоню напомнить/подтвердить» от диспетчера сервиса.
    if _sto_outbound_appointment_reminder(t[:1500]):
        return True
    head_co = t[:4500]
    # Перезвон по заявке (сайт/лид): «удобно говорить вас/вам» + Викинги/дилерский +
    # «пришла заявка» ИЛИ «по заявке» + «звоню» (STT: «по заявке вашей звоню»).
    _ut = "удобно говорить" in head_co or "удобно разговаривать" in head_co
    _vh = "викинг" in head_co or "дилерск" in head_co
    _lead_come = "пришла заявка" in head_co or "пришла заявк" in head_co
    _lead_call = ("по заявке" in head_co or "по вашей заявке" in head_co or "заявке вашей" in head_co) and (
        "звоню" in head_co or "звоним" in head_co
    )
    _resched_visit = _sto_outbound_reschedule_after_failed_to(head_co)
    _to_sched_vehicle = _sto_outbound_to_sched_reminder_vehicle(head_co)
    # 8675: «Удобно? Пару минут?» + «по вашей заявке звоню, оставляли на сайте…» — исходящий лид с сайта.
    _ut_lead = _ut or (
        _lead_call
        and re.search(r"\bудобно\b", head_co[:1000])
        and (
            "пару минут" in head_co[:1000]
            or "оставляли на сайте" in head_co
            or "оставили на сайте" in head_co
        )
    )
    if _ut_lead and (_vh or _sto_dispatcher_or_assistant_named(head_co)) and (
        _lead_come or _lead_call or _resched_visit or _to_sched_vehicle
    ):
        return True
    # Перезвон сотрудника с уточнением по регламентному ТО («ранее общались» + какое ТО / стоимость) + бренд дилера.
    if re.search(r"мы\s+с\s+вами\s+ранее\s+общались", head_co, re.I):
        if any(
            p in head_co
            for p in (
                "техническое обслуживание",
                "первое техническое",
                "второе техническое",
                "третье техническое",
            )
        ) and any(b in t for b in ("викинг", "чери", "chery", "тенет", "тэнет", "tenet")):
            return True
    # Исходящее СТО: «Это компания Викинги», диспетчер сервиса + напоминание о записи (перезвон накануне визита).
    if (
        re.search(r"\bэто\s+компания\s+викинг", head_co, re.I)
        and "диспетчер" in head_co
        and (
            re.search(r"\bвы\s+записывали\b", head_co, re.I)
            or re.search(r"\bзаписывали\s+автомобиль\b", head_co, re.I)
            or "звоню по записи" in head_co[:720]
        )
    ):
        return True
    # СТО исх.: перезвон по переданному номеру (STT «спетр Дарья» без «диспетчер», 8444).
    head_sto_redial = t[:1200]
    if _is_sto_employee_outbound_phone_handoff_opening(text) and (
        "дарья" in head_sto_redial or _sto_dispatcher_or_assistant_named(head_sto_redial)
    ):
        return True
    # Кузовной цех: исходящий по CRM-заявке, обращение к клиенту по имени-отчеству (8491).
    if _is_kuzov_outbound_master_claim_callback_opening(text):
        return True
    # СТО исх.: перезвон по пропущенному («вы нам/нас звонили?») — до inbound-guard:
    # иначе «фамилию подскажите» диспетчера даёт ложный client_initiated (14525).
    if _sto_employee_outbound_vy_zvonili_nam_opening(t[:1200]):
        return True
    # Узкий inbound-guard: после приветствия первым формулирует запрос клиент
    # («у меня машина...», «мне нужно...», «подскажите...»), без явной инициативы
    # сотрудника «перезваниваю / мне передали / по заявке».
    if _is_client_initiated_after_greeting(text) and not _has_explicit_outbound_manager_initiative(text):
        return False
    if _is_client_initiated_booking_change_after_greeting(text):
        return False
    # Ранний inbound-guard для СТО: линия уже представилась как принимающая.
    if _has_initial_sto_reception_greeting(text):
        head_early = t[:4500]
        head_redial = t[:720]
        suppress_lead_echo = _suppress_sto_lead_echo_as_outbound(t[:1200])
        lead_echo_markers = (
            ()
            if suppress_lead_echo
            else (
                "получили вашу заявку",
                "получили вашу заявку на сервис",
                "мы заявку получили",
                "заявку получили",
                "получили заявку",
            )
        )
        explicit_sto_redial = any(p in head_early for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS) or any(
            p in head_early
            for p in (
                "звоню вам по",
                "перезваниваю по",
                *lead_echo_markers,
                "подтверждаем запись",
                "запись подтверждена",
                "запись подтверждаете",
                "заказывали обратный звонок",
                "по вашей заявке",
                "оставляли на сайте",
                "оставили на сайте",
            )
        ) or ("звоню по записи" in head_redial) or _has_client_name_outbound_script_marker(head_early)
        if not explicit_sto_redial:
            if not _is_sto_outbound_site_lead_opening(text):
                return False
    if _is_inbound_admin_transfer_to_sto_dispatcher_line(text):
        return False
    # Лид с Авито / мостовой звонок: передача клиента на линию дилера, затем менеджер ОП — входящий (не ОП исх.).
    head_bridge = t[:3200]
    if (
        ("компания авито" in head_bridge or "компания авит" in head_bridge or "клиент на линии" in head_bridge)
        and ("передаю вас" in head_bridge or "переключайте" in head_bridge or "переключаю вас" in head_bridge)
    ):
        return False
    # Администратор переводит клиента в начале звонка — входящий. Только по первым ~600 символам:
    # иначе «переключу» как подстрока «переключусь» в конце длинного транскрипта ломает ОП исх.
    in_transfer = ["переключаю вас", "как вас представить"]
    if any(m in first for m in in_transfer):
        return False
    if re.search(r"переключу(?!сь)", first):
        return False
    # Перезвон линии СТО клиенту: передали телефон / номер после обращения клиента — исходящий
    head_early = t[:4500]
    if (
        any(m in head_early for m in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS)
        and (
            "диспетчер сервиса" in head_early
            or "ассистент сервиса" in head_early
            or "диспетчер" in head_early and "сервис" in head_early
        )
        and ("викинги" in head_early or "чери" in head_early)
    ):
        return True
    # Входящий СТО: линия уже ответила (диспетчер/ассистент + имя из справочника).
    # Делаем этот guard ДО проверки OP surface, чтобы фразы клиента «звоню вам» не
    # переводили входящий сервисный звонок в исходящий.
    sto_manager_answers_early = (
        ("диспетчер" in first_150 or "ассистент сервиса" in first_150)
        and any(
            n in first_150
            for n in [
                "юлия", "юлию", "юля", "юли", "андреева", "андреев",
                "плаксина", "гринкина", "гранкина", "александра", "дарья",
            ]
        )
        and ("викинги" in first_150 or "чери" in first_150)
    )
    if sto_manager_answers_early:
        employee_phone_handoff_early = any(p in head_early for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS)
        suppress_lead_echo_early = _suppress_sto_lead_echo_as_outbound(t[:1200])
        lead_echo_early = (
            ()
            if suppress_lead_echo_early
            else (
                "получили вашу заявку",
                "получили вашу заявку на сервис",
                "мы заявку получили",
                "заявку получили",
                "пришла заявка",
                "пришла заявк",
            )
        )
        employee_explicit_outbound_early = any(
            p in head_early
            for p in (
                "звоню вам по",
                "звоню по записи",
                "перезваниваю по",
                "перезваниваю вам",
                "удобно, да",
                *lead_echo_early,
                "подтверждаем запись",
                "запись подтверждена",
                "запись подтверждаете",
                "заказывали обратный звонок",
                "хотели бы на сервис записать",
                "планируем сейчас ваш визит",
                "планируете проходить",
                "планируете пройти",
                "будете проходить",
                "на вашем автомобиле",
            )
        ) or _has_client_name_outbound_script_marker(head_early)
        if not employee_phone_handoff_early and not employee_explicit_outbound_early:
            return False
    if _has_op_outbound_surface_markers(t):
        return True
    head_wide = t[:3200]
    # «…это/что/то Андрей, менеджер отдела продаж» — исходящий ОП (окно >600: мусор/STT в начале файла).
    if re.search(r"\bменеджер\s+отдела\s+продаж\b", head_wide) and re.search(
        r"\b(?:это|что|то)\s+(андрей|илья|евгений|анастасию|анастасия|захаров|евдокимов|краснощек|калаев|калаева|щеголев)\b",
        head_wide,
    ):
        return True
    # «Михаил, здравствуйте … это/что/то Андрей» — менеджер зовёт клиента по имени (без «алло»).
    if re.search(
        r"[а-яё]{2,}\s*,\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер).{0,650}?\b(?:это|что|то)\s+(андрей|илья|евгений|анастасия)\b",
        head_wide,
        re.DOTALL,
    ):
        return True
    # «Дмитрий, это Андрей…» — обращение к клиенту по имени и самопредставление без приветствия между (перезвон из CRM).
    if re.search(
        r"[а-яё]{2,}\s*,\s*это\s+(?:илья|евгения|евгений|анастасию|анастасия|андрея|андрей|"
        r"захаров|евдокимов|краснощеков|краснощёков|краснощек|краснощёк|калаев|калаева|щеголев)\b",
        head_wide,
        re.IGNORECASE,
    ):
        return True
    # «Виталий, добрый день. Анастасия, автосалон Викинги…» — исходящий ОП (клиент по имени → приветствие → имя менеджера; без «это»)
    if (
        "автосалон" in head_wide
        or "викинги" in head_wide
        or "дилерск" in head_wide
        or re.search(r"\bофициальн\w*\s+дилер\b", head_wide)
    ) and re.search(
        r"[а-яё]{2,}\s*,\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер)\s*[\.,!]?\s*"
        r"\b(евгений|илья|анастасия|андрей|захаров|евдокимов|краснощек|краснощёк|калаев|калаева|щеголев)\b",
        head_wide,
        re.DOTALL,
    ):
        return True
    if _is_op_lead_callback_intro(text):
        return True
    # «Викинги Чери, диспетчер сервиса Юлия. Алло?» — менеджер отвечает на входящий, не зовёт клиента
    # «Алло? Алло.» — проверка связи, не «Алло, [имя клиента]»
    sto_manager_answers = (
        ("диспетчер" in first_150 or "ассистент сервиса" in first_150)
        and any(
            n in first_150
            for n in [
                "юлия", "юлию", "юля", "юли", "андреева", "андреев",
                "плаксина", "гринкина", "гранкина", "александра", "дарья",
            ]
        )
        and ("викинги" in first_150 or "чери" in first_150)
    )
    if sto_manager_answers:
        # Перезвон по переданному номеру — формулировка сотрудника, не ответ клиента «вы звонили»
        employee_phone_handoff = any(p in head_early for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS)
        employee_explicit_outbound = any(
            p in head_early
            for p in (
                "звоню вам по",
                "звоню по записи",
                "перезваниваю по",
                "перезваниваю вам",
                "получили вашу заявку",
                "получили вашу заявку на сервис",
                "мы заявку получили",
                "заявку получили",
                "подтверждаем запись",
                "запись подтверждена",
                "запись подтверждаете",
                "заказывали обратный звонок",
            )
        )
        # Если звонок стартует с приветствия диспетчера/ассистента СТО (принимающая сторона),
        # считаем входящим по умолчанию; исходящим — только при явных маркерах перезвона сотрудника.
        if not employee_phone_handoff and not employee_explicit_outbound:
            return False
    out_markers = [
        "звоню по поводу", "звоню вам", "звонил вам", "перезваниваю",
        "звоню там", "звоню с официального", "звоню из официального",
        "звоню по записи", "звоню узнать", "вас интересовал",
        "по поводу покупки", "что решили",
        "вчера созванивались", "созванивались",
        "звонили", "передали телефон", "звонила", "звонил ранее",
        "оставляли телефончик", "оставили телефон",
        "удобно разговаривать", "удобно говорить", "получили заявку",
        "провести техническое обслуживание",
        "вы звонили", "по вашему обращению",
        "связываюсь с вами", "связываюсь по",
        "мы с вами общались", "хотел узнать планы",
        "вчера с вами",
        "вчера мы с вами",
        "по машине общались",
        "по автомобилю общались",
        "звонок по заявке клиента", "заказывали обратный звонок",
        # СТО исх.: подтверждение записи (стажёр/линия звонит клиенту)
        "запись подтверждена",
        "запись подтверждаете",
        "к нас записаны",
        "к нам записаны",
        "заявку получили",
        "мы заявку получили",
        "просто заявку получили",
    ]
    # Маркеры исходящего часто после длинного преамбула STT; 600 символов недостаточно.
    head_out = t[:4000]
    head_out_800 = head_out[:800]
    for m in out_markers:
        if m in head_out:
            # «Перезвоню вам» — сотрудник обещает перезвонить (входящий), не «звоню вам» (исходящий)
            if m == "звоню вам" and "перезвоню вам" in head_out:
                continue
            # «перезвоню там» содержит подстроку «звоню там» — клиент, не исходящий (9173).
            if m == "звоню там" and re.search(r"\bперезвон\w*\s+там\b", head_out, re.I):
                continue
            # «вы звонили / звонили» учитываем только в начале разговора.
            if m in ("вы звонили", "звонили") and m not in head_out_800:
                continue
            # Входящий СТО: клиент ссылается на прошлый контакт
            # («мы/я ранее звонили ... вы говорили ...»), это не исходящий звонок сотрудника.
            if m == "звонили" and re.search(
                r"\b(?:мы|я)\b[^.!?]{0,60}\b(?:ранее|до этого|вчера|сегодня|дня\s+\w+\s+назад)?[^.!?]{0,60}\bзвонил(?:и|а)?\b",
                head_out_800,
                re.I,
            ):
                if any(p in head_out_800 for p in ("вы говорили", "вы обещали", "по поводу записи")):
                    continue
            # 8963: «вы жене позвонили» при «я на ТО записывался» — входящий клиент, не исходящий.
            if m == "звонили" and re.search(r"\bзаписывал", head_out_800) and (
                "позвонили" in head_out_800 or "жена" in head_out_800 or "муж" in head_out_800
            ):
                continue
            return True
    # «Это/что Илья» (менеджер ОП) в начале — часто исходящий перезвон
    if _has_eto_or_chto_op_name_intro(first) and any(
        n in first for n in ["илья", "евгений", "анастасия", "андрей", "захаров", "евдокимов", "калаев", "калаева", "краснощеков", "щеголев"]
    ):
        if (
            "мы с вами" in first
            or "хотел узнать" in first
            or "звоню" in first
            or "перезвон" in first
            or "отзвон" in intro_pref
        ):
            return True
    # «Это/что [любой из менеджеров ОП], Викинги. Звоню...» — ОП исходящий
    if _has_eto_or_chto_op_name_intro(first) and "викинги" in first and "звоню" in first:
        for name in _OP_MANAGER_NAMES:
            if name in first:
                return True
    # «Вы интересуетесь Чери/Тенет/моделью» — инициатива менеджера, перезвон
    if "вы интересуетесь" in first:
        op_car_refs = (
            "чери", "chery", "тенет", "tenet",
            "т1", "т2", "дашинг", "икс70", "икс 70",
            "тигго4", "тигго 4", "тигго7", "тигго 7", "тигго8", "тигго 8", "тигго9", "тигго 9",
            "арризо", "аризо", "arrizo",
        )
        if any(ref in first for ref in op_car_refs):
            return True
    # «[Имя] беспокоит, Дилерский центр Чери» — звонит сотрудник дилерского центра
    if "беспокоит" in first and "дилерский центр" in first:
        return True
    # «Алло, [Имя Отчество], Дилерский центр» — зовут клиента по имени, исходящий
    # Исключение: «диспетчер сервиса Юлия. Алло?» — менеджер отвечает, имя может быть от клиента
    if "алло" in first[:100] and ("дилерский центр" in first or "викинги" in first):
        if not sto_manager_answers and re.search(r"алло[.,]?\s*[а-яё]+\s+[а-яё]+(?:вич|вна|ович|овна)", first):
            return True
    # «Алло, [имя]!» + «менеджер отдела продаж» — менеджер ОП звонит клиенту, исходящий
    if "менеджер отдела продаж" in first and "алло" in first[:150]:
        allo_name_m = re.search(r"алло[.,]?\s+([а-яё]{3,}(?:\s+[а-яё]+)?)\s*[,?!]", first[:150])
        if allo_name_m:
            name = allo_name_m.group(1).split()[0].lower()
            if name != "алло" and name not in _OP_MANAGER_NAMES:
                return True
    # «Алло, [имя клиента]!» + «диспетчер/ассистент сервиса» — диспетчер/ассистент СТО звонит клиенту, исходящий
    # Имя после «Алло» — клиент (не из справочника: юлия, александра, дарья, андреева и т.д.)
    if ("диспетчер сервиса" in first or "ассистент сервиса" in first) and "алло" in first[:200]:
        if "слушаю вас" not in first[:200]:
            allo_name_m = re.search(r"алло[.,]?\s+([а-яё]{3,}(?:\s+[а-яё]+)?)\s*[,?!]", first[:200])
            if allo_name_m and _allo_addressed_name_is_outbound_client(allo_name_m.group(1)):
                return True
    # «Алло, Елена?» — зовут клиента по имени (без отчества), исходящий
    # Исключение: «Алло? Алло.» — проверка связи; «Юлия. Алло?» — менеджер отвечает (имя — не клиент)
    allo_name_m = re.search(r"алло[.,]?\s+([а-яё]{3,})\s*[,?]", first[:120])
    if allo_name_m and ("викинги" in first or "дилерский" in first):
        if "слушаю вас" in first[:200]:
            pass
        elif _allo_addressed_name_is_outbound_client(allo_name_m.group(1)):
            return True
        else:
            # 13398: «Алло. Здравствуйте, кузовной цех…» — исходящий, не обращение по имени.
            allo_tok = allo_name_m.group(1).split()[0].lower()
            if allo_tok in ("здравствуйте", "здравствуй") and re.search(r"^[\s.!?]*алло", first):
                return True
    # Повторный исходящий ОП: «Алло» + обращение к клиенту + «добрый день ещё раз» + «что/это снова» + имя менеджера из ОП
    op_mgr_repeat = (
        "евгений",
        "евгения",
        "илья",
        "анастасия",
        "андрей",
        "захаров",
        "евдокимов",
        "калаев",
        "калаева",
        "краснощеков",
        "краснощёков",
        "краснощекова",
        "краснощёкова",
        "щеголев",
    )
    if (
        "алло" in first_150
        and ("добрый день еще раз" in first or "добрый день ещё раз" in first or "добрый вечер еще раз" in first or "добрый вечер ещё раз" in first)
        and re.search(r"(?:что|это)\s+снова\b", first)
        and any(m in first for m in op_mgr_repeat)
    ):
        return True
    return False


def _is_client_promised_callback_not_employee_outbound(head: str) -> bool:
    """Клиент обещает перезвонить («гляну, перезвоню там»), не исходящий сотрудника (9173)."""
    h = (head or "").lower()
    if not re.search(r"\bперезвон\w*\b", h):
        return False
    if re.search(r"\bперезвон\w*\s+вам\b", h) and not any(
        p in h for p in ("гляну", "посмотрю", "проверю", "машину", "сяду")
    ):
        return False
    return any(
        p in h
        for p in (
            "гляну",
            "посмотрю",
            "проверю",
            "машину",
            "сяду",
            "перезвоню там",
            "я перезвоню",
        )
    )


def _has_explicit_outbound_manager_initiative(text: str) -> bool:
    head = (text or "").lower()[:2400]
    head_800 = head[:800]
    suppress_lead_echo = _suppress_sto_lead_echo_as_outbound(head[:1200])
    # Не считать «перезваниваю» маркером исходящего, если это клиентский контекст.
    # Пример: «я к вам... мне вчера звонили... я перезваниваю...».
    client_callback_context = any(
        p in head
        for p in (
            "я к вам",
            "мне вчера звонили",
            "я перезваниваю",
        )
    )
    has_outbound_callback_marker = any(
        p in head
        for p in (
            "перезваниваю",
            "перезвоню",
        )
    ) and not client_callback_context and not _is_client_promised_callback_not_employee_outbound(head)
    if any(p in head[:800] for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS):
        return True
    if _is_sto_employee_outbound_phone_handoff_opening(text):
        return True
    base_outbound_markers = [
        "мне передали",
        "передали вас",
        "звоню вам по",
        "звоню по записи",
        "звоню по вашей заявке",
        "по вашей заявке",
        "подтверждаем запись",
        "заказывали обратный звонок",
        "вот звоню",
        "звоню по поводу",
        "звоню по вашему автомобил",
        "получили вашу заявку",
        "получили вашу заявку на сервис",
        "получили по поводу записи",
    ]
    if not suppress_lead_echo:
        base_outbound_markers.extend(
            ("мы заявку получили", "заявку получили", "получили заявку")
        )
    base_outbound = any(p in head for p in base_outbound_markers) or re.search(
        r"\bобращени\w*\s+(?:ваше\s+)?получил", head
    )
    # «вы хотели/были/спрашивали» только в начале — иначе ложный исходящий на вопросах диспетчера (8508).
    early_employee_initiative = (
        not _is_dispatcher_to_booking_question_not_outbound(head_800)
        and any(
            p in head_800
            for p in (
                "вы хотели",
                "вы спрашивали",
                "вы были",
                "интересовались",
                "звонок от вас был",
            )
        )
    )
    # «вы звонили» далеко в середине/конце разговора часто относится к входящему сценарию.
    vy_zvonili_early = "вы звонили" in head_800
    return base_outbound or has_outbound_callback_marker or vy_zvonili_early or early_employee_initiative


def _is_client_initiated_after_greeting(text: str) -> bool:
    """
    Входящий паттерн: после стартового приветствия первой идёт клиентская инициатива темы.
    Примеры: «У меня машина...», «Подскажите...», «Мне нужно...», «Я хотел узнать...».
    """
    first = (text or "").lower()[:1400]
    if not first:
        return False
    if any(p in first[:700] for p in STO_EMPLOYEE_PHONE_HANDOFF_MARKERS):
        return False
    if _is_sto_employee_outbound_phone_handoff_opening(text):
        return False
    if not re.search(r"\b(?:алло|здравствуйте|добрый\s+день|добрый\s+вечер)\b", first[:320], re.I):
        return False
    return any(
        p in first
        for p in (
            " у меня машина",
            "у меня авто",
            "у меня автомобиль",
            "мне нужно",
            "мне надо",
            "подскажите",
            "скажите пожалуйста",
            "я хотел узнать",
            "я хотела узнать",
            "не горит",
            "не работает",
            "можно купить",
            "сколько стоит",
            "сколько будет стоить",
            "сколько будет",
            "запишите меня",
            "записаться можно",
            # Входящий: клиент первым озвучивает запись на ТО (7913).
            "записаться на то",
            "записаться на т о",
            "хотел бы я",
            "хотела бы я",
            "оставлял заявку",
            "оставляла заявку",
            "оставил заявку",
            "оставила заявку",
            "у меня такой вопрос",
        )
    ) or (
        # «на то хотел(а)» — клиент; не путать с исх. «перезваниваю на ТО хотели» (16497).
        bool(re.search(r"\bна\s+то\s+хотел(?:а)?\b", first, re.I))
        and not re.search(r"\bперезванива\w*\b", first[:700], re.I)
    ) or _suppress_sto_lead_echo_as_outbound(first)


def _is_client_initiated_booking_change_after_greeting(text: str) -> bool:
    """
    Приоритетный входящий паттерн: после приветствия клиент сам инициирует
    уточнение/перенос/отмену уже существующей записи.
    """
    first = (text or "").lower()[:2600]
    if not first:
        return False
    if not re.search(r"\b(?:алло|здравствуйте|добрый\s+день|добрый\s+вечер)\b", first[:320], re.I):
        return False
    return any(
        p in first
        for p in (
            "я записан",
            "я записана",
            "записан был",
            "записана была",
            "хотел отменить запись",
            "хотела отменить запись",
            "отменить запись",
            "перенести запись",
            "мне надо перенести",
            "не помню на какой день",
            "мне звонили подтвердить",
            "я перезваниваю",
            "на то записывался",
            "записывался",
            "позвонили",
        )
    )


def _is_sto_outbound_record_only(text: str) -> bool:
    """
    СТО исходящий, но только подтверждение/перенос записи — относим к Прочие.
    Диспетчер звонит, называет клиента по имени, уточняет время — без обсуждения ремонта/диагностики.
    """
    t = (text or "").lower()
    record_phrases = [
        "запись на завтра", "звоню по записи", "уточнить в силе", "всё ли в силе",
        "записываю", "освободилась", "могу предложить", "запись на ",
    ]
    service_content = [
        "ремонт", "диагностик", "не работает", "не греет", "шумит", "дребезж",
        "сделать то", "пройти то", "замена масла", "приёмка", "приемка", "слесарн",
        "продиагностировать", "хочу отремонтировать", "хочу заменить", "отремонтировать", "заменить масло",
    ]
    has_record = any(p in t for p in record_phrases)
    has_service = any(s in t for s in service_content)
    return has_record and not has_service


def _manager_uses_client_name(text: str) -> bool:
    """
    Менеджер сразу называет клиента по имени (без «как к вам обращаться»).
    В OP_OUT менеджер знает имя из CRM, в OP_IN клиент сам представляется.
    Клиента могут звать как менеджера (Илья, Андрей и т.п.) — не исключаем имена.
    Исключаем только контекст самопредставления: «менеджер», «викинги», «автосалон».
    """
    first = (text or "")[:500].lower()
    self_intro_words = {"викинги", "менеджер", "автосалон", "отдел", "продаж"}
    # «[Имя], звоню вам» — обращение к клиенту (клиент может иметь любое имя, в т.ч. Илья, Андрей)
    for m in re.finditer(r"(?:^|[.!?]\s*)([а-яё]{2,}(?:\s+[а-яё]{2,})?),?\s*(?:звоню|перезваниваю)", first):
        name = m.group(1).strip()
        words = set(name.split())
        if words and not (words & self_intro_words) and len(name) >= 4:
            return True
    # «здравствуйте, [Имя],» — только если дальше «звоню» (исключить «здравствуйте, Илья, Викинги»)
    if "звоню" in first or "перезваниваю" in first:
        for m in re.finditer(r"(?:здравствуйте|добрый день|добрый вечер),?\s*([а-яё]{2,}(?:\s+[а-яё]{2,})?)\s*[,.]", first):
            name = m.group(1).strip()
            words = set(name.split())
            if words and not (words & self_intro_words) and len(name) >= 4:
                return True
    return False


def _op_in_client_initiative(text: str) -> bool:
    """Инициатива клиента: вопрос/запрос в начале — входящий."""
    first = (text or "")[:400].lower()
    in_markers = [
        "это отдел продаж", "отдел продаж?", "продажи?",
        "машину купить хочу", "машину хочу купить", "хочу машину купить",
        "авто купить", "машину купить", "купить машину",
        "подскажите", "хотел спросить", "у вас есть",
        "записаться на тест-драйв", "тест-драйв когда",
    ]
    return any(m in first for m in in_markers)


def _is_op_outbound(text: str) -> bool:
    """
    ОП исходящий: первичный/CRM-контакт менеджера ОП (_has_op_outbound_surface_markers)
    плюс признаки исходящего (_is_outbound) или явный контекст перезвона/первичного контакта.
    """
    low = (text or "").lower()
    has_surface = _has_op_outbound_surface_markers(text)
    if not has_surface:
        return False
    # Админ/Авито → передача на ОП при _is_outbound=False: не ОП исх. (ложное «андрей» в первых 600 символах + greeting).
    if _is_inbound_op_sales_intent_after_non_op_line(text):
        return False
    if "менеджер отдела продаж" not in low[:900] and any(p in low for p in _STO_MASTER_PRIYOM_MARKERS):
        return False
    first = (text or "")[:600].lower()
    head_op = (text or "").lower()[:3500]
    # Перезвон по заявке (имя менеджера может отсутствовать) — раньше «подскажите» в реплике менеджера
    if _is_op_lead_callback_intro(text):
        return True
    # Инициатива клиента в начале — входящий
    if _op_in_client_initiative(text):
        return False
    if re.search(r"\bменеджер\s+отдела\s+продаж\b", head_op) and re.search(
        r"\b(?:это|что|то)\s+(андрей|илья|евгений|анастасию|анастасия|захаров|евдокимов|краснощек|калаев|калаева|щеголев)\b",
        head_op,
    ):
        return True
    if re.search(
        r"[а-яё]{2,}\s*,\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер).{0,650}?"
        r"\b(?:это|что|то)\s+(?:андрей|илья|евгений|анастасия)(?:\s+|\s*,\s*)?(?:викинг|викинги|чери|тенет|тэнет)?\b",
        head_op,
        re.DOTALL,
    ):
        return True
    # Лог АТС «начинаем звонить…» + админ/перевод/ОП — исходящий по заявке (не сбрасывать из‑за «переключу»).
    if _is_dialer_or_site_outbound_opening(text) and any(
        m in head_op
        for m in (
            "менеджер отдела продаж",
            "анастасия",
            "илья",
            "евгений",
            "андрей",
            "захаров",
            "евдокимов",
            "калаев",
            "калаева",
            "краснощек",
            "щеголев",
            "администратор",
            "диана",
            "официальный дилер",
            "викинги",
            "заставн",
            "тест-драйв",
            "тест драйв",
            "чери",
            "тенет",
        )
    ):
        return True
    # «Алло, [имя клиента]!» + «менеджер отдела продаж» — менеджер ОП звонит клиенту
    if "менеджер отдела продаж" in first and "алло" in first[:150]:
        allo_m = re.search(r"алло[.,]?\s+([а-яё]{3,})\s*[,?!]", first[:150])
        if allo_m:
            name = allo_m.group(1).lower()
            if name != "алло" and name not in _OP_MANAGER_NAMES:
                return True
    # Приветствие + имя менеджера ОП
    greeting = any(g in first for g in ["добрый день", "добрый вечер", "здравствуйте", "алло"])
    op_names_nominative = (
        "евгений", "илья", "анастасия", "андрей",
        "захаров", "евдокимов", "калаев", "краснощеков", "краснощёков", "щеголев",
    )
    has_op_manager = any(n in first for n in op_names_nominative)
    if greeting and has_op_manager:
        return True
    # «Вы интересуетесь Чери/Тенет/…» — перезвон по заявке
    if "вы интересуетесь" in first:
        op_car_refs = (
            "чери", "chery", "тенет", "tenet",
            "т1", "т2", "дашинг", "икс70", "тигго4", "тигго7", "тигго8", "тигго9", "арризо", "аризо",
        )
        if any(ref in first for ref in op_car_refs):
            return True
    # Менеджер сразу называет клиента по имени
    if _manager_uses_client_name(text):
        return True
    return False
