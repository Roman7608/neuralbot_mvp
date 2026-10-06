"""
Извлечение и нормализация марок автомобилей из транскрипта.

Использует списки популярных марок и моделей для распознавания
и исправления опечаток Whisper.
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class CarBrandExtractor:
    """Извлекает и нормализует марки автомобилей из текста."""

    # (каноническая марка, варианты для сопоставления в lowercase)
    CAR_BRANDS: list[Tuple[str, list[str]]] = [
        # Дилерские марки (Чери, Тенет)
        ("Chery", ["chery", "чери", "чири", "шерри", "чhr", "чhр"]),
        ("Tenet", [
            "tenet", "tent", "tennet", "tenant", "tnt",
            "тенет", "тэнет", "тенеет", "тэнт", "тент", "тинет", "теннет",
            "тенеед", "тенёт", "тенат", "тонат", "танет", "тенит", "тэнит", "тэнэт",
        ]),
        # Китайские марки (GAC, JAC, Changan и др.)
        ("JAC", ["jac", "жак", "джак"]),
        ("GAC", ["gac", "гак", "джиэйси"]),
        ("Changan", ["changan", "чанган", "чанъань", "чангэн"]),
        ("FAW", ["faw", "фав"]),
        ("Dongfeng", ["dongfeng", "дунгфенг", "дунфэн", "донгфенг"]),
        ("Baic", ["baic", "баик", "бэйк", "beic"]),
        ("Lifan", ["lifan", "лифан", "лифэн"]),
        ("Voyah", ["voyah", "воя", "воях"]),
        ("Belgee", ["belgee", "белджи", "белги"]),
        ("Kaya", ["kaya", "кая", "кайа"]),
        ("Tank", ["tank", "танк"]),
        ("Haval", ["haval", "хавал", "хаваль"]),
        ("Omoda", ["omoda", "омода"]),
        ("Jaecoo", ["jaecoo", "джаку", "жакку", "яку"]),
        ("Exeed", ["exeed", "эксид"]),
        ("Jetour", [
            "jetour", "jetor", "jettour", "jaytur",
            "джетоур", "джеторт", "джетур", "джитур", "джтур", "джытур",
            "жетур", "житур",
        ]),
        ("XCite", ["xcite", "эксит", "икссайт", "эксайт", "excite"]),
        ("Soueast", ["soueast", "соист", "соуист", "соуиэст"]),
        ("Kmewstar", ["kmewstar", "ньюстар", "ньюст+ар", "кмьюстар", "кьюстар"]),
        # Популярные
        ("Toyota", ["toyota", "тойота", "таёта", "тоёта"]),
        ("Nissan", ["nissan", "ниссан", "нисан"]),
        ("Datsun", [
            "datsun", "датсун", "дацун", "дацсун", "датсан", "гадцун", "гадсун",
        ]),
        ("Hyundai", ["hyundai", "хёндай", "хендай", "хиундай", "hundai"]),
        ("Kia", ["kia", "киа", "кия"]),
        (
            "Volkswagen",
            [
                "volkswagen",
                "vw",
                "фольксваген",
                "вольксваген",
                "фольцваген",
                # Частые STT-искажения («Фольксваген Тигуан» -> «Шваган Тигуан» и т.п.)
                "шваган",
                "фолксваген",
                "волксваген",
                "volkswageн",
            ],
        ),
        ("Skoda", ["skoda", "шкода"]),
        ("Ford", ["ford", "форд"]),
        ("Chevrolet", ["chevrolet", "шевроле", "шеви"]),
        ("Mazda", ["mazda", "мазда"]),
        ("Honda", ["honda", "хонда"]),
        ("Renault", ["renault", "рено", "ренов"]),
        ("Peugeot", ["peugeot", "пежо", "пёжо"]),
        ("Citroen", ["citroen", "citroën", "ситроен", "ситрон"]),
        ("BMW", ["bmw", "бмв", "биэмв", "бэмв", "бэмвэй", "бэмвей", "бэмвейк", "бмвей"]),
        ("Mercedes", ["mercedes", "мерседес", "мерс", "мерседес-бенц"]),
        ("Audi", ["audi", "ауди"]),
        ("Lada", ["lada", "лада"]),
        ("VAZ", ["vaz", "ваз"]),
        ("Zhiguli", ["zhiguli", "жигули", "жигуль"]),
        ("GAZ", ["gaz", "газ"]),
        ("Volga", ["volga", "волга"]),
        ("Moskvich", ["moskvich", "москвич"]),
        ("UAZ", ["uaz", "уаз"]),
        ("Mitsubishi", ["mitsubishi", "мицубиси", "митсубиши"]),
        ("Subaru", ["subaru", "субару"]),
        ("Lexus", ["lexus", "лексус", "лексусс"]),
        ("Infiniti", ["infiniti", "infinit", "finit", "инфинити", "инфинитти"]),
        ("Volvo", ["volvo", "вольво"]),
        ("Geely", ["geely", "джили", "гели"]),
        ("Genesis", ["genesis", "дженезис", "генезис"]),
        ("Cupra", ["cupra", "купра"]),
        ("SsangYong", ["ssangyong", "ссанъён", "ссангйонг"]),
    ]

    # Плоский список канонических марок для fuzzy-поиска
    _CANONICAL_LIST = [b[0] for b in CAR_BRANDS]
    _VARIANT_TO_CANONICAL: dict[str, str] = {}
    for canonical, variants in CAR_BRANDS:
        for v in variants:
            _VARIANT_TO_CANONICAL[v.lower().strip()] = canonical

    # Популярные модели (для распознавания). Включаем латинские и русские варианты —
    # STT часто выдаёт русскую транслитерацию («Тигуан», «Кашкай»). normalize_model
    # ищет совпадение через difflib, поэтому достаточно одной формы на модель + основных
    # русских вариантов для частотных кейсов.
    CAR_MODELS = [
        # Nissan
        "PATHFINDER", "ПАТФАЙНДЕР",
        "QASHQAI", "КАШКАЙ", "КАШКАИ",
        "X-TRAIL", "ИКСТРЕЙЛ", "ХТРЕЙЛ",
        "TERRANO", "ТЕРРАНО",
        "MURANO", "МУРАНО",
        "PATROL", "ПАТРУЛЬ",
        "ALMERA", "АЛМЕРА",
        "SENTRA", "СЕНТРА",
        "NOTE", "НОУТ",
        "JUKE", "ДЖУК",
        "TIIDA", "ТИИДА",
        # Datsun
        "ON-DO", "ONDO", "ОН-ДО", "ОНДО", "ON DO", "ОН ДО",
        "MI-DO", "MIDO", "МИ-ДО", "МИДО", "MI DO", "МИ ДО",
        # Toyota
        "CAMRY", "КЭМРИ", "КАМРИ",
        "COROLLA", "КОРОЛЛА",
        "RAV4", "РАВ4", "РАВ ЧЕТЫРЕ",
        "LAND CRUISER", "ЛЕНД КРУЗЕР", "ЛЭНД КРУЗЕР",
        "PRADO", "ПРАДО",
        "HIGHLANDER", "ХАЙЛЕНДЕР",
        "HARRIER", "ХАРРИЕР",
        "C-HR", "СИХР",
        "HILUX", "ХАЙЛЮКС",
        "FORTUNER", "ФОРТУНЕР",
        "AVENSIS", "АВЕНСИС",
        "AURIS", "АУРИС",
        "YARIS", "ЯРИС",
        # Hyundai
        "SOLARIS", "СОЛЯРИС",
        "CRETA", "КРЕТА",
        "TUCSON", "ТУССАН", "ТУКСОН",
        "SANTA FE", "САНТА ФЕ", "САНТАФЕ",
        "ELANTRA", "ЭЛАНТРА",
        "SONATA", "СОНАТА",
        "IX35", "АЙ ИКС 35",
        "ACCENT", "АКЦЕНТ",
        "I30", "АЙ 30",
        "PALISADE", "ПАЛИСАД",
        "GETZ", "ГЕТЦ",
        # KIA
        "RIO", "РИО",
        "SPORTAGE", "СПОРТЕЙДЖ",
        "SORENTO", "СОРЕНТО",
        "OPTIMA", "ОПТИМА",
        "CERATO", "СЕРАТО",
        "CEED", "СИИД",
        "PICANTO", "ПИКАНТО",
        # Peugeot
        "408", "3008", "5008", "2008",
        "PARTNER", "ПАРТНЕР",
        # Renault
        "LOGAN", "ЛОГАН",
        "DUSTER", "ДАСТЕР",
        "SANDERO", "САНДЕРО",
        "KAPTUR", "КАПТЮР",
        "ARKANA", "АРКАНА",
        "MEGANE", "МЕГАН",
        # Volkswagen
        "TIGUAN", "ТИГУАН", "ТИПУАН",
        "POLO", "ПОЛО",
        "PASSAT", "ПАССАТ",
        "GOLF", "ГОЛЬФ",
        "TOUAREG", "ТУАРЕГ",
        "JETTA", "ДЖЕТТА",
        "CADDY", "КЭДДИ",
        "TERAMONT", "ТЕРАМОНТ",
        "AMAROK", "АМАРОК",
        # Skoda
        "OCTAVIA", "ОКТАВИЯ",
        "RAPID", "РАПИД",
        "KODIAQ", "КОДИАК",
        "KAROQ", "КАРОК",
        "KAMIQ", "КАМИК",
        "SUPERB", "СУПЕРБ",
        "YETI", "ЕТИ", "ЙЕТИ",
        "FABIA", "ФАБИЯ",
        # Audi
        "Q7", "Q5", "Q3", "Q8",
        "A3", "A4", "A6", "A8",
        # BMW
        "X1", "X2", "X3", "X4", "X5", "X6", "X7",
        "1 SERIES", "3 SERIES", "5 SERIES", "7 SERIES",
        # Mercedes
        "C-CLASS", "E-CLASS", "S-CLASS", "A-CLASS", "B-CLASS",
        "GLA", "GLB", "GLC", "GLE", "GLS",
        # Honda
        "CR-V", "ЦРВ", "СРВ",
        "ACCORD", "АККОРД",
        "CIVIC", "СИВИК",
        "PILOT", "ПИЛОТ",
        "FIT", "ФИТ",
        "JAZZ", "ДЖАЗ",
        # Mazda
        "CX-5", "СИИКС 5", "ЦИКС 5",
        "CX-30",
        "CX-9",
        "MAZDA3", "МАЗДА 3",
        "MAZDA6", "МАЗДА 6",
        # Subaru
        "FORESTER", "ФОРЕСТЕР",
        "OUTBACK", "АУТБЕК",
        "XV",
        "IMPREZA", "ИМПРЕЗА",
        # Mitsubishi
        "OUTLANDER", "АУТЛЕНДЕР",
        "PAJERO", "ПАДЖЕРО",
        "ASX", "АСХ",
        "LANCER", "ЛАНСЕР",
        "L200",
        # Lexus
        "RX", "NX", "GX", "LX", "ES", "IS", "UX",
        # Infiniti — приоритетная марка дилера. Добавлены варианты с пробелом и без.
        "FX", "QX50", "QX55", "QX60", "QX70", "QX80",
        "QX 50", "QX 55", "QX 60", "QX 70", "QX 80",
        "FX35", "FX37", "FX45", "FX50",
        "EX25", "EX35", "EX37",
        "M25", "M35", "M37", "M45",
        "JX35",
        # Geely
        "COOLRAY", "КУЛРЕЙ",
        "ATLAS", "АТЛАС",
        "MONJARO", "МОНЖАРО",
        # Haval
        "JOLION", "ДЖОЛИОН",
        "F7",
        "DARGO", "ДАРГО",
        "H6",
        # Chery — модельный ряд (приоритетная марка дилера Викинги).
        # Многословные имена работают благодаря N‑граммам в extract_car_info.
        "TIGGO", "ТИГГО",
        # STT-обломки: лат. t/кирил. т + «иг» ≈ Tiggo.
        "TИG", "ТИG",
        "TIGGO 4", "TIGGO 7", "TIGGO 8", "TIGGO 9",
        "ТИГГО 4", "ТИГГО 7", "ТИГГО 8", "ТИГГО 9",
        "TIGGO 4 PRO", "TIGGO 7 PRO", "TIGGO 8 PRO", "TIGGO 9 PRO",
        "ТИГГО 4 ПРО", "ТИГГО 7 ПРО", "ТИГГО 8 ПРО", "ТИГГО 9 ПРО",
        "TIGGO 4 PRO MAX", "TIGGO 7 PRO MAX", "TIGGO 8 PRO MAX", "TIGGO 9 PRO MAX",
        "ТИГГО 4 ПРО МАКС", "ТИГГО 7 ПРО МАКС", "ТИГГО 8 ПРО МАКС", "ТИГГО 9 ПРО МАКС",
        "TIGGO 7 L", "TIGGO 8 PLUS", "ТИГГО 7 Л", "ТИГГО 8 ПЛЮС",
        # «Чери N» — маркетинговое имя (= Tiggo N). Часто звучит без слова «Тигго».
        "CHERY 4", "CHERY 7", "CHERY 8", "CHERY 9",
        "ЧЕРИ 4", "ЧЕРИ 7", "ЧЕРИ 8", "ЧЕРИ 9",
        "ARRIZO", "АРРИЗО",
        "ARRIZO 8", "АРРИЗО 8",
        "EXEED", "ЭКСИД",
        # Tenet — перебрендирование Chery, тот же модельный ряд под именами T4/T7/T8/T9.
        # T4/T7/T8/T9 — короткие, обрабатываются прямым совпадением (см. normalize_model).
        "T4", "T7", "T8", "T9", "Т4", "Т7", "Т8", "Т9",
        "T4L", "Т4Л", "T4 L", "Т4 Л",
        "TENET T4", "TENET T7", "TENET T8", "TENET T9",
        "TENET T4L", "ТЕНЕТ Т4Л",
        "CHERY T4", "CHERY T4L", "ЧЕРИ Т4", "ЧЕРИ Т4Л",
        "ТЕНЕТ Т4", "ТЕНЕТ Т7", "ТЕНЕТ Т8", "ТЕНЕТ Т9",
        "ТЕНЕТ 4", "ТЕНЕТ 7", "ТЕНЕТ 8", "ТЕНЕТ 9",
        "ТЭНЕТ 4", "ТЭНЕТ 7", "ТЭНЕТ 8", "ТЭНЕТ 9",
        "TENET 4", "TENET 7", "TENET 8", "TENET 9",
        # Jetour — приоритетная марка для распознавания (на ТО клиент звонит,
        # хотя дилер Jetour не обслуживает; марка должна корректно ложиться в карточку).
        "DASHING", "ДЭШИНГ", "ДЕШИНГ",
        "X70", "ИКС70",
        "X70 PLUS", "ИКС70 ПЛЮС", "ИКС 70 ПЛЮС",
        "X90", "ИКС90", "ИКС 90",
        "X90 PLUS", "ИКС90 ПЛЮС", "ИКС 90 ПЛЮС",
        "T1", "Т1",
        "T2", "Т2",
        "TRAVELLER", "ТРАВЕЛЛЕР", "ТРЕВЕЛЛЕР",
        # Tank / Voyah
        "TANK 300", "ТАНК 300",
        "TANK 500", "ТАНК 500",
        "DREAM",
        "FREE",
        # Changan
        "CS35", "CS55", "CS75", "UNI-K", "UNI-T", "UNI-V",
    ]

    # Маппинг марок для TTS (правильное произношение с ударениями)
    BRAND_TTS_MAP = {
        "NISSAN": "Нисс+ан",
        "TOYOTA": "Той+ота",
        "HONDA": "Х+онда",
        "MAZDA": "М+азда",
        "SUBARU": "Суб+ару",
        "MITSUBISHI": "Митсуб+иси",
        "HYUNDAI": "Хёнд+ай",
        "KIA": "К+иа",
        "BMW": "Бээмв+э",
        "MERCEDES": "Мерс+едес",
        "AUDI": "А+уди",
        "VOLKSWAGEN": "Фольксв+аген",
        "PEUGEOT": "Пеж+о",
        "CITROEN": "Ситро+ен",
        "RENAULT": "Рен+о",
        "FORD": "Ф+орд",
        "CHEVROLET": "Шеврол+е",
        "CHERY": "Ч+ери",
        "TENET": "Т+енет",
        "VOLVO": "В+ольво",
        "FIAT": "Ф+иат",
        "LADA": "Л+ада",
        "UAZ": "У+аз",
        "SKODA": "Шк+ода",
        "SEAT": "С+еат",
        "SOUEAST": "Со+ист",
        "KMEWSTAR": "ньюст+ар",
    }

    # Маппинг моделей для TTS
    MODEL_TTS_MAP = {
        # Nissan
        "PATHFINDER": "Патф+айндер",
        "QASHQAI": "Кашк+ай",
        "X-TRAIL": "Икстр+ейл",
        "TERRANO": "Терр+ано",
        "MURANO": "Мур+ано",
        "PATROL": "П+атруль",
        "ALMERA": "Алм+ера",
        # Toyota
        "CAMRY": "К+эмри",
        "COROLLA": "Кор+олла",
        "RAV4": "Рав чет+ыре",
        "LAND CRUISER": "Ленд Круз+ер",
        "PRADO": "Пр+адо",
        # Hyundai
        "SOLARIS": "Сол+ярис",
        "CRETA": "Кр+ета",
        "TUCSON": "Тусс+ан",
        "SANTA FE": "Санта Ф+е",
        "ELANTRA": "Эл+антра",
        # KIA
        "RIO": "Р+ио",
        "SPORTAGE": "Спорт+ейдж",
        "SORENTO": "Сор+енто",
        # Peugeot
        "408": "чет+ыреста вос+емь",
        "3008": "тр+и тысячи вос+емь",
        "5008": "п+ять тысяч вос+емь",
        "2008": "дв+е тысячи вос+емь",
        # Renault
        "LOGAN": "Лог+ан",
        "DUSTER": "Д+астер",
        "SANDERO": "Санд+еро",
        "KAPTUR": "Капт+ур",
        # Skoda
        "OCTAVIA": "Окт+авия",
        "RAPID": "Р+апид",
        # Audi
        "Q7": "Ку семь",
        "Q5": "Ку пять",
        "A4": "А чет+ыре",
        "A6": "А шест+ь",
        # BMW
        "X7": "Икс семь",
        "X5": "Икс пять",
        "X3": "Икс три",
        "X1": "Икс один",
        # Chery / Tenet
        "TIGGO": "Т+игго",
        "DASHING": "Д+эшинг",
        "X70": "Икс семьдесят",
        "T4": "T чет+ыре",
        "T7": "T сем+ь",
        "T8": "T в+осемь",
        "T9": "T д+евять",
        "Т4": "T чет+ыре",
        "Т7": "T сем+ь",
        "Т8": "T в+осемь",
        "Т9": "T д+евять",
    }

    def __init__(self):
        """Инициализирует экстрактор марок."""
        logger.info("CarBrandExtractor инициализирован")

    @staticmethod
    def _canonicalize_model_latin(model: Optional[str]) -> Optional[str]:
        """
        Канонизирует модель к латинице.

        Требование проекта: нормализованная марка/модель хранится и передается
        только латиницей. Здесь переводим частые кириллические формы и
        STT-варианты в единый латинский canonical.
        """
        if not model:
            return None
        m = str(model).strip()
        if not m:
            return None
        up = m.upper()
        # Короткие линейки Tenet/Chery.
        up = re.sub(r"\bТ([4789])Л\b", r"T\1L", up)
        up = re.sub(r"\bТ([4789])\b", r"T\1", up)

        word_map = {
            "ТИГГО": "TIGGO",
            "АРРИЗО": "ARRIZO",
            "ПРО": "PRO",
            "МАКС": "MAX",
            "ПЛЮС": "PLUS",
            "Л": "L",
            "ЭЛЬ": "L",
            "ОНДО": "ON-DO",
            "ОН-ДО": "ON-DO",
            "МИДО": "MI-DO",
            "МИ-ДО": "MI-DO",
            "ТИГУАН": "TIGUAN",
            "ТИПУАН": "TIGUAN",
            "ПАТФАЙНДЕР": "PATHFINDER",
            "КАШКАЙ": "QASHQAI",
            "КАШКАИ": "QASHQAI",
            "ИКСТРЕЙЛ": "X-TRAIL",
            "ХТРЕЙЛ": "X-TRAIL",
            "ТЕРРАНО": "TERRANO",
            "МУРАНО": "MURANO",
            "ПАТРУЛЬ": "PATROL",
            "АЛМЕРА": "ALMERA",
            "СЕНТРА": "SENTRA",
            "НОУТ": "NOTE",
            "ДЖУК": "JUKE",
            "ТИИДА": "TIIDA",
        }
        for src, dst in word_map.items():
            up = re.sub(rf"\b{re.escape(src)}\b", dst, up)

        up = re.sub(r"\bON\s+DO\b", "ON-DO", up)
        up = re.sub(r"\bMI\s+DO\b", "MI-DO", up)
        up = re.sub(r"\s+", " ", up).strip()
        return up or None

    @staticmethod
    def _normalize_stt_chery_tiggo_shards(text: str) -> str:
        """STT Chery/Tenet/Tiggo: полный нормализатор + голосовые дополнения."""
        from dialog.chery_tenet_stt_normalize import normalize_chery_tenet_car_stt

        return normalize_chery_tenet_car_stt(text)

    @staticmethod
    def _unify_leading_latin_a_before_cyrillic(text: str) -> str:
        """
        STT: «Nissan Aльмера» — латинская A перед кириллицей; для сравнения с «АЛМЕРА» в списке моделей.
        """
        if not text:
            return text
        out: list[str] = []
        for i, c in enumerate(text):
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if c == "A" and nxt and "\u0400" <= nxt <= "\u04ff":
                out.append("А")
            elif c == "a" and nxt and "\u0400" <= nxt <= "\u04ff":
                out.append("а")
            else:
                out.append(c)
        return "".join(out)

    def normalize_brand(self, brand_text: str) -> Optional[str]:
        """
        Нормализует марку автомобиля, исправляя опечатки.
        
        Сначала проверяется точное совпадение по вариантам, затем
        нечёткий поиск (difflib) по каноническим названиям.
        
        :param brand_text: Текст с маркой (может быть с опечатками)
        :return: Каноническая марка или None
        """
        if not brand_text or not brand_text.strip():
            return None

        text = brand_text.strip().lower()
        
        # Точное совпадение по вариантам
        if text in self._VARIANT_TO_CANONICAL:
            return self._VARIANT_TO_CANONICAL[text]
        
        # Нечёткий поиск по каноническим названиям
        import difflib
        
        matches = difflib.get_close_matches(
            text, self._CANONICAL_LIST, n=1, cutoff=0.6
        )
        if matches:
            logger.info(f"Марка '{brand_text}' приведена к '{matches[0]}'")
            return matches[0]

        return None

    def _model_exact(self, model_text: str) -> Optional[str]:
        """Только прямое совпадение по списку (без fuzzy). Безопасно для N‑грамм."""
        if not model_text or not model_text.strip():
            return None
        text = model_text.strip().upper()
        if text in self.CAR_MODELS:
            return text
        return None

    def normalize_model(self, model_text: str) -> Optional[str]:
        """
        Нормализует модель автомобиля. Прямое совпадение, для длинных строк — fuzzy.

        ВНИМАНИЕ: для multi‑word фрагментов (N‑граммы из extract_car_info) использовать
        _model_exact, потому что difflib даёт ложные substring‑совпадения
        ('ЧЕРИ ТИГГО 7 ПРО' → 'ТИГГО 7 ПРО').
        """
        if not model_text or not model_text.strip():
            return None

        text = model_text.strip().upper()

        # Прямое совпадение.
        if text in self.CAR_MODELS:
            if text in ("TИG", "ТИG"):
                return "TIGGO"
            return text

        # Для коротких (≤3 символов) и многословных строк — только прямое совпадение,
        # difflib не подходит (дёт ложные совпадения по подстрокам).
        if len(text) <= 3 or " " in text:
            return None

        # Нечёткий поиск только для длинных однословных строк по ОДНОСЛОВНЫМ кандидатам:
        # многословные модели (с пробелом, типа «ЧЕРИ 9», «ТИГГО 7 ПРО МАКС») должны
        # совпадать только через N‑граммы в extract_car_info — иначе difflib даёт
        # ложное «ЧЕРИ» → «ЧЕРИ 9».
        import difflib

        single_word_models = [m for m in self.CAR_MODELS if " " not in m]
        matches = difflib.get_close_matches(text, single_word_models, n=1, cutoff=0.7)
        if matches:
            logger.info(f"Модель '{model_text}' распознана как '{matches[0]}'")
            return matches[0]

        return None

    def extract_car_info(self, text: str) -> Optional[Tuple[str, Optional[str]]]:
        """
        Извлекает марку и модель из текста.

        Алгоритм:
          1) марка ищется пословно (любая последовательность букв в любой позиции);
          2) модель ищется N‑граммами (4→3→2→1 слов): многословные модели типа
             «TIGGO 7 PRO MAX» или «ТЕНЕТ 4» имеют приоритет над однословными.

        :param text: Текст с упоминанием автомобиля
        :return: Кортеж (марка, модель) или None
        """
        if not text or not text.strip():
            return None

        text = self._unify_leading_latin_a_before_cyrillic(text.strip()).lower()
        # STT/склейки Datsun On-Do: «гадцунон...», «datsunondo», «датсун он до» и т.п.
        compact = re.sub(r"[^a-zа-яё0-9]+", "", text.lower())
        if (
            re.search(r"(?:datsun|д[ао]т?с?ун|дац[с]?ун|гадцун|гадсун)", compact)
            and re.search(r"(?:ondo|ондо)", compact)
        ):
            text = f"{text} datsun on-do"
        text = re.sub(
            r"(?<![\w])(?:datsun|датсун|дацун|дацсун|датсан|гадцун|гадсун)\s*[-_/.,;: ]*\s*(?:on\s*[- ]?\s*do|ондо)(?![\w])",
            "datsun on-do",
            text,
            flags=re.IGNORECASE,
        )
        # STT: «Анальмера/Альмера/Эльмера» -> Nissan Almera.
        text = re.sub(
            r"(?<![\w])(?:анальмера|альмера|эльмера)(?![\w])",
            "almera",
            text,
            flags=re.IGNORECASE,
        )
        # STT Jetour T1: «Джеторт один» / «Джетоур Т1».
        text = re.sub(
            r"(?<![\w])(?:джетоур|джеторт)\s+(?:один|[тt]\s*1)(?!\d)",
            "jetour T1",
            text,
            flags=re.IGNORECASE,
        )
        # STT: «Тенат?7», «Тонат*7», «Тенат Т7» → Tenet T7.
        text = re.sub(
            r"(?<![\w])(?:тенат|тонат)\s*[^а-яёa-z0-9]*\s*(?:[тt]\s*)?7(?!\d)",
            "tenet T7",
            text,
            flags=re.IGNORECASE,
        )
        # STT: «тенет семь» / «tenet семь» / «Тэнет. | Семь.» -> Tenet T7.
        text = re.sub(
            r"(?<![\w])(?:tenet|тенет|тэнет|тенат|тонат)(?:[\s\.,;:!?|]*)сем(?:ь|ёрк\w*)(?![\w])",
            "tenet T7",
            text,
            flags=re.IGNORECASE,
        )
        # STT: «тенет восемь» / «tenet восемь» -> Tenet T8.
        text = re.sub(
            r"(?<![\w])(?:tenet|тенет|тэнет|тенат|тонат)(?:[\s\.,;:!?|]*)восем\w*(?![\w])",
            "tenet T8",
            text,
            flags=re.IGNORECASE,
        )
        # STT: «тенет четыре» / «tenet четыре» -> Tenet T4.
        text = re.sub(
            r"(?<![\w])(?:tenet|тенет|тэнет|тенат|тонат)(?:[\s\.,;:!?|]*)четыр\w*(?![\w])",
            "tenet T4",
            text,
            flags=re.IGNORECASE,
        )
        # STT 23311: «Chr Te7PMак» / mixed-script варианты -> Chery Tiggo 7 Pro Max.
        text = re.sub(
            r"(?<![\w])chr\s*te\s*7\s*p(?:r|р)?\s*[mм]\s*[aа]\s*[kк]\w*(?![\w])",
            "chery tiggo 7 pro max",
            text,
            flags=re.IGNORECASE,
        )
        text = self._normalize_stt_chery_tiggo_shards(text)
        punct = ".,;:!?\"'«»()[]"
        words: list[str] = []
        for raw in text.upper().split():
            w = raw.strip(punct)
            if w:
                words.append(w)
        brand = None
        model = None

        # Ищем марку
        for word in words:
            normalized = self.normalize_brand(word)
            if normalized:
                brand = normalized
                break

        # Ищем модель N‑граммами: 4→3→2→1 слов. Многословные имеют приоритет:
        # «Чери Тигго 7 Про Макс» → «ТИГГО 7 ПРО МАКС», а не одиночное «ТИГГО».
        # Для N≥2 используем только прямое совпадение (difflib даёт ложные substring‑матчи).
        for n in (4, 3, 2):
            if model:
                break
            for i in range(len(words) - n + 1):
                chunk = " ".join(words[i : i + n])
                normalized = self._model_exact(chunk)
                if normalized:
                    model = normalized
                    break

        # Однословный fallback (с прямым совпадением и fuzzy для длинных строк).
        if not model:
            for word in words:
                normalized = self.normalize_model(word)
                if normalized:
                    model = normalized
                    break

        # STT конкретного ответа «Nissan Terrano»: «Типап/Тирап/Типан/Tipan».
        # Только при явно названной марке Nissan, чтобы не спутать с Volkswagen Tiguan.
        if brand == "Nissan" and re.search(
            r"(?<![\w])[tт][iи]\s*[pрп][aа][nнп](?![\w])",
            text,
            re.IGNORECASE,
        ):
            model = "TERRANO"

        # Если найдена только модель без марки, пытаемся определить марку по модели
        if not brand and model:
            # Модели BMW
            if model in ["X7", "X5", "X3", "X1", "X6", "X4", "X2"]:
                brand = "BMW"
            # Модели Audi
            elif model in ["Q7", "Q5", "Q3", "A4", "A6", "A8"]:
                brand = "Audi"
            # Модели Mercedes
            elif model in ["GLE", "GLC", "GLS", "E-Class", "C-Class", "S-Class"]:
                brand = "Mercedes"
            # Модели Peugeot
            elif model in ["408", "3008", "5008", "2008"]:
                brand = "Peugeot"
            # Модели Nissan
            elif model in ["QASHQAI", "X-TRAIL", "PATHFINDER", "TERRANO", "ALMERA", "MURANO", "JUKE", "NOTE", "SENTRA", "TIIDA"]:
                brand = "Nissan"
            # Модели Datsun
            elif model in ["ON-DO", "ONDO", "ОН-ДО", "ОНДО", "ON DO", "ОН ДО", "MI-DO", "MIDO", "МИ-ДО", "МИДО", "MI DO", "МИ ДО"]:
                brand = "Datsun"
            # Модели Toyota
            elif model in ["CAMRY", "COROLLA", "RAV4", "LAND CRUISER", "PRADO"]:
                brand = "Toyota"
            # Модели Hyundai
            elif model in ["SOLARIS", "CRETA", "TUCSON", "SANTA FE"]:
                brand = "Hyundai"
            # Модели KIA
            elif model in ["RIO", "SPORTAGE", "SORENTO"]:
                brand = "Kia"
            # Модели Renault
            elif model in ["LOGAN", "DUSTER", "SANDERO", "KAPTUR"]:
                brand = "Renault"
            # Модели Skoda
            elif model in ["OCTAVIA", "RAPID"]:
                brand = "Skoda"
            # Модели Volkswagen
            elif model in ["TIGUAN", "ТИГУАН", "ТИПУАН"]:
                brand = "Volkswagen"
            # Модели Chery (в т.ч. Dashing / X70 — единая линейка дилера).
            # «ТИГГО 8», «TIGGO 7 PRO» и т.п. приходят из N‑грамм — проверяем префикс.
            elif model in ("DASHING", "X70") or (
                model
                and (model.startswith("TIGGO") or model.startswith("ТИГГО"))
            ):
                brand = "Chery"

        if brand:
            if brand in ("Chery", "Tenet") and model in ("TIGUAN", "ТИГУАН", "ТИПУАН"):
                model = None
            # N-грамма «TENET T7» уже содержит марку; в результате храним
            # единообразно brand=Tenet, model=T7 без дублирования «Tenet TENET T7».
            if brand == "Tenet" and model:
                tenet_model = re.sub(
                    r"^(?:TENET|ТЕНЕТ|ТЭНЕТ)\s+",
                    "",
                    model,
                    count=1,
                    flags=re.IGNORECASE,
                )
                tenet_compact = re.sub(r"\s+", "", tenet_model.upper())
                if tenet_compact in ("T4L", "Т4Л"):
                    model = "T4L"
                elif tenet_model in ("T4", "T7", "T8", "T9", "Т4", "Т7", "Т8", "Т9"):
                    model = "T" + tenet_model[-1]
            if brand == "Chery" and model:
                chery_model = re.sub(
                    r"^(?:CHERY|ЧЕРИ|ЧЕРРИ)\s+",
                    "",
                    model,
                    count=1,
                    flags=re.IGNORECASE,
                )
                chery_compact = re.sub(r"\s+", "", chery_model.upper())
                if chery_compact in ("T4L", "Т4Л"):
                    model = "T4L"
                elif chery_compact in ("T4", "Т4"):
                    model = "T4"
            if brand == "Jetour" and model in ("T1", "Т1"):
                model = "T1"
            if brand == "Datsun" and model:
                datsun_model = re.sub(r"\s+", "", model.upper())
                if datsun_model in ("ONDO", "ОНДО"):
                    model = "ON-DO"
                elif datsun_model in ("MIDO", "МИДО"):
                    model = "MI-DO"
            model = self._canonicalize_model_latin(model)
            return (brand, model)
        return None

    def format_for_tts(self, brand: str, model: Optional[str] = None) -> str:
        """
        Форматирует марку и модель для TTS (с правильными ударениями).
        
        :param brand: Марка автомобиля (каноническая)
        :param model: Модель автомобиля (опционально)
        :return: Строка для TTS
        """
        parts = []
        
        # Используем каноническую марку для поиска в маппинге
        brand_tts = self.BRAND_TTS_MAP.get(brand.upper(), brand)
        parts.append(brand_tts)
        
        if model:
            model_tts = self.MODEL_TTS_MAP.get(model.upper())
            if not model_tts:
                model_tts = re.sub(
                    r"^(TIGGO|ТИГГО)\b",
                    "Т+игго",
                    model,
                    count=1,
                    flags=re.IGNORECASE,
                )
            parts.append(model_tts)
        
        return " ".join(parts)
