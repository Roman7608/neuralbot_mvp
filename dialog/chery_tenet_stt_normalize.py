"""
STT-нормализация марок и моделей Chery / Tenet для голосового бота.

Переиспользует ``call_analytics.sto_booking_dimensions._normalize_text`` (сотни
правил по реальным звонкам: 4/7/8/9, Pro, Max, L, латиница+кириллица, Tenet T4…)
и добавляет голосовые дополнения, ещё не попавшие в аналитику.
"""

from __future__ import annotations

import re


def normalize_chery_tenet_car_stt(text: str) -> str:
    """Текст реплики → форма, удобная для ``CarBrandExtractor.extract_car_info``."""
    raw = _pre_normalize_voice_chery_tenet(text or "")
    try:
        from call_analytics.sto_booking_dimensions import _normalize_text

        t = _normalize_text(raw)
    except Exception:
        t = raw.lower().replace("ё", "е")
        t = re.sub(r"\s+", " ", t).strip()
    return _normalize_voice_chery_tenet_extras(t)


def _pre_normalize_voice_chery_tenet(text: str) -> str:
    """
    До ``_normalize_text``: иначе агрессивное ``чер*`` в аналитике съедает
    «черитик» → «chery», теряя модель (звонок 822062731896).
    """
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[-–—/]", " ", t)
    # STT: «Сери ...» / «Шери ...» ~= Chery.
    t = re.sub(r"\bсери\b", " чери ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bшери\b", " чери ", t, flags=re.IGNORECASE)
    # STT: «иг4ро» / «иг 4ро» / «иг 7ро» ~= Tiggo N Pro.
    t = re.sub(r"\bиг\s*([4789])\s*р[оo]\b", r" тигго \1 про ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bиг\s*([4789])\s*пр[oо]\b", r" тигго \1 про ", t, flags=re.IGNORECASE)
    # STT: «иг7» / «иг 7» ~= Tiggo 7.
    t = re.sub(r"\bиг\s*([4789])\b", r" тигго \1 ", t, flags=re.IGNORECASE)
    # Mixed-script "Cеry"/"Чherry" -> Chery.
    t = re.sub(r"\bc[еe]ry\b", " chery ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчherry\b", " chery ", t, flags=re.IGNORECASE)
    # STT: «Черитик» / «черетига» / близкие варианты -> Chery Tiggo.
    t = re.sub(
        r"\b(?:черетига|черетиgа|черитига|чиретига|cheretiga|cherytiga)\b",
        " чери тигго ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 26027: «ЧереЧга» ≈ Chery Tiggo; «чере» (кроме «через») ≈ Chery.
    t = re.sub(r"\bчеречга\b", " чери тигго ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчерега\b", " чери тигго ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчере\b", " чери ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b7\s*pr[oо][\wа-яё]*\b",
        " 7 про ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Чели тига 8ProMaxс» и близкие варианты.
    t = re.sub(r"\bчели\b", " чери ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(?:чери|чели)\s+тига\s*([4789])\s*pro\s*max[сc]?\b",
        r" чери тигго \1 про макс ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bтига\s*([4789])\s*pro\s*max[сc]?\b",
        r" тигго \1 про макс ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bтига\s*([4789])\b", r" тигго \1 ", t, flags=re.IGNORECASE)
    # Голосовой STT: Tenet T7 → «Тенот Т7» / смешанное кириллица+латиница «тэn т7».
    t = re.sub(r"\bтенот\b", " tenet ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bт[эе]n\b", " tenet ", t, flags=re.IGNORECASE)
    # Созвучные формы бренда Tenet (кириллица/mixed-script).
    t = re.sub(r"\bт[эе]н[эе]т\b", " tenet ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bt[эе]н[эе]т\b", " tenet ", t, flags=re.IGNORECASE)
    # Mixed-script: «NтT7» / «nтt 7» -> Tenet T7.
    t = re.sub(
        r"\bn\s*[тt]\s*[tт]\s*7\b",
        " tenet t7 ",
        t,
        flags=re.IGNORECASE,
    )
    # Max/voice кейсы: «Тенет, а 4ре», «Tenet девятый».
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тенат|тонат)\s*,?\s*а\s*4ре\b",
        " tenet t4 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b(?:tenet|тенет|тэнет|тенат|тонат)\s+девят\w*\b", " tenet t9 ", t, flags=re.IGNORECASE)
    # Max/voice: после STT бренд уже «tenet», модель словом — «семь/восемь/четыре».
    # Иначе «Тэнет. | Семь.» → «tenet семь» и в MAX уходит «Тенет Семь» вместо T7.
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тенат|тонат)(?:[\s\.,;:!?|]*)сем(?:ь|ёрк\w*)\b",
        " tenet t7 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тенат|тонат)(?:[\s\.,;:!?|]*)восем\w*\b",
        " tenet t8 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тенат|тонат)(?:[\s\.,;:!?|]*)четыр\w*\b",
        " tenet t4 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «tenet/chery t4l» (склейка марки из диалога + ответ модели).
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тенат|тонат)\s*t\s*4\s*l\b",
        " tenet t4l ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри|cherry)\s*t\s*4\s*l\b",
        " chery t4l ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчеритик\s+гасим\w*\b", " чери тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчеритик\w*\b", " чери тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтикслим\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    # STT: «тикгасин» / «тиггасин» ≈ Tiggo 7 (fuzzy иначе → Тигуан VW).
    t = re.sub(r"\bтикгасин\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтикгасен\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтиггасин\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигга\s*([4789])\b", r" тигго \1 ", t, flags=re.IGNORECASE)
    # STT: «тигга сем» / «тигго сем» — «семь» ≈ 7 (звонок 822238642104).
    t = re.sub(r"\bтигга\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигго\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтиг\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигры\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигри\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    # STT: «игга семь» / «игга сем» — «тигга» без «т» (голосовой бот, запись на ТО).
    t = re.sub(r"\bигга\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bигга\s*([4789])\b", r" тигго \1 ", t, flags=re.IGNORECASE)
    # STT: «иgа 7 Пr Макс» / mixed-script -> Tiggo 7 Pro Max.
    t = re.sub(
        r"\bи[gг]а\s*([4789])\s*[пp]\s*[rр]\s*макс\w*\b",
        r" тигго \1 про макс ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «иgа 7 Пr» -> Tiggo 7 Pro.
    t = re.sub(
        r"\bи[gг]а\s*([4789])\s*[пp]\s*[rр]\b",
        r" тигго \1 про ",
        t,
        flags=re.IGNORECASE,
    )
    # Сильные обрезки STT: «ери ига компромат» / «черевзигасонпромат»
    # ~= «чери тигго 7 про макс».
    t = re.sub(
        r"\b(?:ч?ери)\s+и[gг]а\s+ком?прома[тд]\w*\b",
        " чери тигго 7 про макс ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчеревзигасонпрома[тд]\w*\b",
        " чери тигго 7 про макс ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 22832: «восьмерка гибрид» -> Chery Tiggo 8 Hybrid.
    t = re.sub(r"\bвосьм[её]рк\w*\s+гибрид\w*\b", " чери тигго 8 гибрид ", t, flags=re.IGNORECASE)
    # STT 22833: «Чирри восьмерка» / «Чирри 8» -> Chery Tiggo 8.
    t = re.sub(r"\bчир+ри\s+(?:восьм[её]рк\w*|8)\b", " чери тигго 8 ", t, flags=re.IGNORECASE)
    # STT 22841: «Чечерег 8еь» / «Четыре г восемь» -> Chery Tiggo 8.
    t = re.sub(r"\bчечер[еа]г\s*8\w*\b", " чери тигго 8 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчетыр[её]\s+г\s+восем\w*\b", " чери тигго 8 ", t, flags=re.IGNORECASE)
    # STT: «Чири Кига 7л» ≈ Chery Tiggo 7 L.
    t = re.sub(
        r"\b(?:чери|чири|черри)\s+киг+а\s*7\s*(?:л|l|эль|ель)\b",
        " чери тигго 7 л ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:чери|чири|черри)\s+киг+а\s*7\b",
        " чери тигго 7 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «кигга» / «эль кигга с7» — «к» вместо «т», «эль» ≈ Chery.
    t = re.sub(
        r"\bэль\s+(?:кигга|игга|тигга)\s*(?:с|c)?\s*([4789])\b",
        r" чери тигго \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bкигга\s*(?:с|c)?\s*([4789])\b", r" тигго \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bкигга\s+сем\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bкигга\b", " тигго ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bигга\b", " тигго ", t, flags=re.IGNORECASE)
    # STT: «Пигатин» ≈ Chery Tiggo 7 (повторная реплика клиента).
    t = re.sub(r"\bпигат\w*\b", " тигго 7 ", t, flags=re.IGNORECASE)
    # STT: «Чери г4» / «чери г 4» ≈ Chery Tiggo 4 (обрыв «Тигго»).
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+г\s*([4789])\b",
        r" чери тигго \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+g\s*([4789])\b",
        r" чери тигго \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 23465: «CherTg 4 New» / «cher tg 4 new» -> Chery Tiggo 4.
    t = re.sub(
        r"\bcher\s*tg\s*([4789])\s*(?:new|нью)?\b",
        r" чери тигго \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 25950: «Чирокс? ильтромакс» -> Chery Tiggo 7 Pro Max.
    t = re.sub(
        r"\bчирокс\b[?.!,;:\s]*ильтромакс\b",
        " чери тигго 7 про макс ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bильтромакс\b", " чери тигго 7 про макс ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчирокс\b", " чери ", t, flags=re.IGNORECASE)
    # STT 16335: «Терига 8» ≈ Chery Tiggo 8.
    t = re.sub(r"\bтерига\s*([4789])\b", r" чери тигго \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтерига\b", " чери тигго ", t, flags=re.IGNORECASE)
    return t


def _normalize_voice_chery_tenet_extras(t: str) -> str:
    """Дополнения голосового сценария записи на ТО (поверх sto_booking_dimensions)."""
    t = t or ""
    t = re.sub(r"[-–—/]", " ", t)
    # В аналитике «промакс» часто склеено, для модели нужен «про макс».
    t = re.sub(r"\bпромакс\b", "про макс", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпромарс\b", "про макс", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигр\s*([4789])\b", r"тигго \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпроo\b", "про", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпро\s+про\b", "про", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпро\s+max\b", "pro max", t, flags=re.IGNORECASE)
    t = re.sub(r"\btg\s+pro\b", "pro", t, flags=re.IGNORECASE)
    # STT: «тиг сем» / «тиг 7» без «го».
    t = re.sub(
        r"\b(?:chery|чери)\s+тиг\s*([4789])\b",
        r"чери тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери)\s+тигга\s*([4789])\b",
        r"чери тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    # STT 11237: «Чери чига 7л» / «Чери ... 7 л» -> Chery Tiggo 7 L.
    # Нормализация суффикса L после 7: эл/эль/л/ль/l (с пробелом или без).
    t = re.sub(
        r"\b(?:chery|чери|черри|чири)\s+чиг+а\s*7\s*(?:эл|эль|л|ль|l)\b",
        "чери тигго 7 л",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+чиг+а\s*([4789])\b",
        r"чери тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри|чири)\b(?:\s+[a-zа-яё0-9-]{1,8}){0,2}\s+7\s*(?:эл|эль|л|ль|l)\b",
        "чери тигго 7 л",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bтиг\s*([4789])\b", r"тигго \1", t, flags=re.IGNORECASE)
    # STT: «7 эль» / «7 эл» / «7 ль» / «7 l» / «7эл» в конце — Tiggo 7 L.
    t = re.sub(
        r"\b(?:chery|чери|черри|чири)\s+(?:tiggo|тигго)\s*7\s*(?:эл|эль|л|ль|l)\b",
        "чери тигго 7 л",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tiggo|тигго)\s*7\s*(?:эл|эль|л|ль|l)\b",
        "тигго 7 л",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «тига се 7/4», «тигго се 7/4» -> Tiggo 7 / Tiggo 4.
    t = re.sub(
        r"\b(?:chery|чери)\s+(?:тига|тигго|tiggo)\s*(?:се|эс|se|ce|cе|сe)\s*([47])\b",
        r"чери тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:тига|тигго|tiggo)\s*(?:се|эс|se|ce|cе|сe)\s*([47])\b",
        r"тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «тенет се 4/7» -> Tenet T4/T7.
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тенат|тонат)\s*(?:се|эс|se|ce|cе|сe)?\s*(?:[тt]\s*)?([47])\b",
        r"tenet t\1",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «тигго л7» / «tiggo l7» / «тигго эль 7» / «тигго эл7».
    t = re.sub(
        r"\b(?:tiggo|тигго)\s*(?:эл|эль|л|ль|l)\s*([4789])\b",
        r"тигго \1 л",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «chery л7 / эл7» после агрессивной нормализации «чер*» в аналитике.
    t = re.sub(
        r"\b(?:chery|чери)\s*(?:эл|эль|л|ль|l)\s*([4789])\b",
        r"chery tiggo \1 л",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Тигга с7» / «Tigga c7» / «Кигга с7» — латиница «c» + цифра.
    t = re.sub(
        r"\b(?:tiggo|тигго|тигга|tigga|кигга|игга)\s+(?:с|c)\s*([4789])\b",
        r"тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:кигга|игга)\s*([4789])\b",
        r"тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчерри\b", "чери", t, flags=re.IGNORECASE)
    # STT: «чери г4» на случай, если пре-нормализация не сработала.
    t = re.sub(
        r"\b(?:chery|чери)\s+г\s*([4789])\b",
        r"чери тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери)\s+g\s*([4789])\b",
        r"чери тигго \1",
        t,
        flags=re.IGNORECASE,
    )
    # Word digits for Tiggo model line.
    t = re.sub(r"\b(?:chery|чери)\s+(?:tiggo|тигго)\s+четыр\w*\s+про\b", "chery tiggo 4 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:chery|чери)\s+(?:tiggo|тигго)\s+сем\w*\s+про\b", "chery tiggo 7 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:chery|чери)\s+(?:tiggo|тигго)\s+восем\w*\s+про\b", "chery tiggo 8 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:chery|чери)\s+(?:tiggo|тигго)\s+девят\w*\s+про\b", "chery tiggo 9 pro", t, flags=re.IGNORECASE)
    # Bare spoken digits (без «про»): «тигго девять» / STT-обломок «тигго дев» / склейка «дев девять».
    t = re.sub(
        r"\b(?:chery|чери|чири)\s+(?:tiggo|тигго|тига)\s+дев(?:\s+девят\w*)?\b",
        "chery tiggo 9",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tiggo|тигго|тига)\s+дев(?:\s+девят\w*)?\b",
        "tiggo 9",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|чири)\s+(?:tiggo|тигго|тига)\s+девят\w*\b",
        "chery tiggo 9",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tiggo|тигго|тига)\s+девят\w*\b",
        "tiggo 9",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b(?:chery|чери)\s+([4789])\s+про\s+макс\b", r"chery tiggo \1 pro max", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:chery|чери)\s+([4789])\s+pro\s+max\b", r"chery tiggo \1 pro max", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:chery|чери)\s+(?:tiggo|тигго)\s*([4789])\s+pro\s+max\b", r"chery tiggo \1 pro max", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(?:chery|чери)\s+(?:tiggo|тигго)\s*([4789])\s*(?:pro|про)\s*(?:max|макс)\b",
        r"чери тигго \1 про макс",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s+", " ", t).strip()
    return t
