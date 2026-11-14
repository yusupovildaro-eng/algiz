"""Русское название и короткое продающее описание — без ИИ, по словарю.

Зарубежные магазины пишут названия по-английски («Draped tulle midi dress»). Здесь мы
распознаём тип вещи, длину, материал и детали (в названии и описании со страницы) и собираем:
  название:  «Платье миди из тюля с драпировкой»
  описание:  «Лёгкий воздушный тюль и эффектная драпировка. Подойдёт и для вечера, и для праздника…»
Если задан ANTHROPIC_API_KEY, тексты пишет Claude, а этот модуль — запасной вариант.
"""

from __future__ import annotations

import re

# (английский шаблон, русский шаблон, название, категория) — от частного к общему
TYPES: list[tuple[str, str, str, str]] = [
    (r"blazer dress", r"платье-пиджак", "Платье-пиджак", "clothes"),
    (r"shirt dress", r"платье-рубашк", "Платье-рубашка", "clothes"),
    (r"slip dress", r"платье-комбинац", "Платье-комбинация", "clothes"),
    (r"knit dress|sweater dress", r"платье-свитер", "Трикотажное платье", "clothes"),
    (r"\bdress\b", r"плать", "Платье", "clothes"),
    (r"jumpsuit|playsuit", r"комбинезон", "Комбинезон", "clothes"),
    (r"\bskirt\b", r"юбк", "Юбка", "clothes"),
    (r"trench", r"тренч", "Тренч", "clothes"),
    (r"puffer|down jacket", r"пуховик", "Пуховик", "clothes"),
    (r"\bcoat\b", r"пальто", "Пальто", "clothes"),
    (r"blazer", r"пиджак|жакет", "Пиджак", "clothes"),
    (r"bomber", r"бомбер", "Бомбер", "clothes"),
    (r"jacket", r"куртк", "Куртка", "clothes"),
    (r"waistcoat|\bvest\b|gilet", r"жилет", "Жилет", "clothes"),
    (r"cardigan", r"кардиган", "Кардиган", "clothes"),
    (r"hoodie", r"худи", "Худи", "clothes"),
    (r"sweatshirt", r"свитшот", "Свитшот", "clothes"),
    (r"sweater|jumper|pullover|\bknit\b", r"свитер|джемпер", "Свитер", "clothes"),
    (r"t-shirt|\btee\b", r"футболк", "Футболка", "clothes"),
    (r"\bpolo\b", r"поло", "Поло", "clothes"),
    (r"blouse", r"блуз", "Блуза", "clothes"),
    (r"\bshirt\b|overshirt", r"рубашк", "Рубашка", "clothes"),
    (r"bodysuit|corset|\btop\b", r"\bтоп\b|боди|корсет", "Топ", "clothes"),
    (r"jeans", r"джинс", "Джинсы", "clothes"),
    (r"trousers|\bpants\b", r"брюк", "Брюки", "clothes"),
    (r"shorts", r"шорт", "Шорты", "clothes"),
    (r"\bbag\b|handbag|tote|clutch|crossbody", r"сумк|клатч|шопер", "Сумка", "clothes"),
    (r"sneakers|trainers", r"кроссовк|кеды", "Кроссовки", "shoes"),
    (r"ankle boots|\bboots\b", r"ботин|сапог|ботильон", "Ботинки", "shoes"),
    (r"loafers", r"лофер", "Лоферы", "shoes"),
    (r"sandals", r"босонож|сандал", "Босоножки", "shoes"),
    (r"mules|slingback|heels|pumps|\bshoes\b", r"туфл|мюли", "Туфли", "shoes"),
    (r"setting spray", r"спрей-фиксатор|фиксирующ", "Спрей-фиксатор для макияжа", "beauty"),
    (r"palette", r"палетк", "Палетка теней", "beauty"),
    (r"lip gloss|lip bath|lip oil|lip lustre", r"блеск для губ", "Блеск для губ", "beauty"),
    (r"lipstick", r"помад", "Помада", "beauty"),
    (r"mascara", r"тушь", "Тушь", "beauty"),
    (r"powder|flawless finish", r"пудр", "Пудра", "beauty"),
    (r"foundation", r"тональн", "Тональный крем", "beauty"),
    (r"concealer", r"консилер", "Консилер", "beauty"),
    (r"blush", r"румян", "Румяна", "beauty"),
    (r"highlighter", r"хайлайтер", "Хайлайтер", "beauty"),
    (r"serum", r"сыворот", "Сыворотка", "beauty"),
    (r"moisturi[sz]er|\bcream\b", r"\bкрем\b", "Крем", "beauty"),
    (r"eau de parfum|eau de toilette|perfume|fragrance", r"парфюм|духи", "Парфюм", "beauty"),
    (r"\bkit\b|gift set", r"набор", "Набор", "beauty"),
    (r"dinner set|tableware set", r"сервиз", "Сервиз", "home"),
    (r"\bplates?\b", r"тарел", "Тарелки", "home"),
    (r"\bmugs?\b|\bcups?\b", r"чашк|кружк", "Чашки", "home"),
    (r"\bglass(?:es)?\b", r"бокал|стакан", "Бокалы", "home"),
    (r"\bbowls?\b", r"миск|пиал", "Пиалы", "home"),
]
LENGTH = [(r"\bmidi\b", r"миди", "миди"), (r"\bmaxi\b|long dress", r"макси", "макси"), (r"\bmini\b", r"мини", "мини")]
# материал: (en, ru, «из …», «как звучит в описании»)
MATERIAL = [
    (r"faux leather|vegan leather", r"эко-?кож", "экокожи", "мягкая экокожа"),
    (r"\bleather\b", r"\bкож", "кожи", "натуральная кожа"),
    (r"suede", r"замш", "замши", "бархатистая замша"),
    (r"tulle", r"тюл", "тюля", "лёгкий воздушный тюль"),
    (r"satin", r"атлас|сатин", "сатина", "мягкий блеск сатина"),
    (r"\bsilk\b", r"шёлк|шелк", "шёлка", "струящийся шёлк"),
    (r"chiffon", r"шифон", "шифона", "невесомый шифон"),
    (r"organza", r"органз", "органзы", "прозрачная органза"),
    (r"\blace\b", r"кружев", "кружева", "нежное кружево"),
    (r"linen", r"\bл[её]н|льн", "льна", "дышащий лён"),
    (r"cotton|poplin", r"хлоп|поплин", "хлопка", "приятный к телу хлопок"),
    (r"cashmere", r"кашемир", "кашемира", "нежный кашемир"),
    (r"\bwool\b", r"шерст", "шерсти", "тёплая шерсть"),
    (r"tweed", r"твид", "твида", "фактурный твид"),
    (r"velvet", r"бархат", "бархата", "глубокий бархат"),
    (r"denim", r"деним", "денима", "плотный деним"),
    (r"knit", r"трикотаж|вязан", "трикотажа", "уютная вязка"),
]
# детали: (en, ru, «с …», «как звучит в описании»)
DETAILS = [
    (r"drap", r"драпир", "драпировкой", "эффектная драпировка"),
    (r"crossover|wrap", r"на запах|запах", "запахом", "силуэт на запах подчёркивает талию"),
    (r"belt", r"пояс", "поясом", "пояс подчёркивает талию"),
    (r"pleat", r"плисс", "плиссировкой", "плиссировка красиво двигается"),
    (r"ruffle|frill", r"волан|оборк", "воланами", "воланы добавляют лёгкости"),
    (r"sequin", r"пайетк", "пайетками", "пайетки играют на свету"),
    (r"\bslit\b", r"разрез", "разрезом", "разрез добавляет лёгкости движениям"),
    (r"off[- ]the[- ]shoulder|off-shoulder", r"открыт\w* плеч", "открытыми плечами", "открытые плечи"),
    (r"v-neck|v neck", r"v-образн|v-вырез", "V-вырезом", "V-вырез вытягивает силуэт"),
    (r"puff sleeve|balloon sleeve", r"объ[её]мн\w* рукав", "объёмными рукавами", "объёмные рукава"),
    (r"\bbow\b", r"бант", "бантом", "бант — нежная деталь"),
    (r"embroider", r"вышив", "вышивкой", "аккуратная вышивка"),
    (r"floral|print", r"принт|цветоч", "принтом", "яркий принт"),
    (r"cut[- ]?out", r"вырез\w* на", "вырезами", "смелые вырезы"),
]
OCCASION = {
    "Платье": "Подойдёт и для вечера, и для праздника — а с жакетом и для офиса.",
    "Платье-пиджак": "Строгий пиджачный крой и женственный силуэт: в офис, на встречу и на ужин.",
    "Платье-рубашка": "Удобно на каждый день и легко собирается в образ с поясом или жакетом.",
    "Платье-комбинация": "Хороша сама по себе вечером и с объёмным свитером днём.",
    "Трикотажное платье": "Уютно и красиво — на каждый день в прохладный сезон.",
    "Комбинезон": "Готовый образ в одной вещи — остаётся только добавить обувь.",
    "Юбка": "Легко сочетается с топами, рубашками и свитерами.",
    "Тренч": "Вечная классика межсезонья: с джинсами, платьями и брюками.",
    "Пальто": "Вещь, которая собирает любой образ и служит не один сезон.",
    "Пуховик": "Тепло и легко — ваша защита от холодов.",
    "Пиджак": "Легко сочетается с джинсами, брюками и платьями — база на несколько сезонов.",
    "Бомбер": "Удобная база для повседневных образов.",
    "Куртка": "Легко сочетается с джинсами, брюками и платьями.",
    "Сумка": "Вместит всё нужное и завершит образ.",
    "Кроссовки": "Удобная пара на каждый день — и к джинсам, и к платью.",
    "Ботинки": "Надёжная пара на холодный сезон, к брюкам и платьям.",
    "Лоферы": "Классика, которая подходит и к офисным, и к повседневным образам.",
    "Босоножки": "Лёгкая пара для тёплых дней и вечерних выходов.",
    "Туфли": "Завершат и деловой, и вечерний образ.",
    "Спрей-фиксатор для макияжа": "Фиксирует макияж, чтобы он оставался свежим весь день.",
    "Палетка теней": "Готовые сочетания оттенков для дневного и вечернего макияжа.",
    "Блеск для губ": "Сочный цвет и сияние — губы выглядят ухоженными.",
    "Помада": "Цвет, который хочется носить каждый день.",
    "Тушь": "Подчеркнёт взгляд с первого нанесения.",
    "Пудра": "Выравнивает тон и убирает блеск — кожа выглядит гладкой.",
    "Тональный крем": "Ровный тон и естественный финиш.",
    "Набор": "Всё нужное в одном наборе — и себе, и в подарок.",
}
CATEGORY_OCCASION = {
    "clothes": "Легко вписывается в гардероб и сочетается с базовыми вещами.",
    "shoes": "Удобная пара, которая завершит и повседневный, и нарядный образ.",
    "beauty": "Любимый продукт для ежедневного макияжа и ухода.",
    "home": "Красиво смотрится на столе и отлично подходит в подарок.",
}
CLOSING = {
    "clothes": "Размеры ограничены — пишите, отложим для вас.",
    "shoes": "Размеры ограничены — пишите, отложим для вас.",
    "beauty": "Количество ограничено — пишите, отложим для вас.",
    "home": "Количество ограничено — пишите, отложим для вас.",
}

_CYR = re.compile(r"[а-яё]", re.I)
_DUO = re.compile(r"\bduo\b|set of 2|\b2[- ]?pack\b|\bx ?2\b|2 шт|дуэт", re.I)


def _find(text: str, table, en_i: int = 0, ru_i: int = 1):
    low = text.lower()
    for row in table:
        if re.search(row[en_i], low) or re.search(row[ru_i], low):
            return row
    return None


def _find_all(text: str, table, limit: int):
    low = text.lower()
    out = [row for row in table if re.search(row[0], low) or re.search(row[1], low)]
    return out[:limit]


def detect_type(text: str) -> tuple[str, str] | None:
    """(«Платье», «clothes») — тип вещи и категория по названию на любом языке."""
    t = _find(text or "", TYPES)
    return (t[2], t[3]) if t else None


def russian_title(title: str, context: str = "") -> str:
    """Английское название магазина → русское. Русские названия не трогаем."""
    if not title or _CYR.search(title):
        return title
    t = _find(title, TYPES)
    if not t:
        return title
    name, cat = t[2], t[3]
    if cat == "beauty":  # линейку косметики оставляем как есть: «Палетка теней Pillow Talk Dreams — 2 шт»
        clean = re.sub(r"\b(duo|set of 2|luxury|kit)\b", "", title, flags=re.I)
        rest = re.sub(t[0], "", clean, flags=re.I)
        rest = re.sub(r"\s{2,}", " ", rest).strip(" -–|,")
        if len(rest.split()) < 2:  # «Collagen Lip Bath» — без типа название теряется
            rest = re.sub(r"\s{2,}", " ", clean).strip(" -–|,")
        out = f"{name} {rest}".strip()
        return out + (" — 2 шт" if _DUO.search(title) else "")
    parts = [name]
    ln = _find(title, LENGTH)
    if ln:
        parts.append(ln[2])
    if re.search(r"oversize|oversized|оверсайз", title, re.I):
        parts.append("оверсайз")
    mat = _find(title, MATERIAL)
    if mat:
        parts.append(f"из {mat[2]}")
    det = [d for d in _find_all(title, DETAILS, 2) if not (t[2] == "Платье-пиджак" and d[2] == "поясом")]
    if det:
        parts.append("с " + " и ".join(d[2] for d in det))
    return " ".join(parts)


def selling_text(title: str, category: str, context: str = "", sizes: bool = False) -> str:
    """2–3 коротких предложения: фактура/детали → где носить → «пишите, отложим»."""
    blob = f"{title}\n{context}"
    sentences = []
    feats = []
    mat = _find(blob, MATERIAL)
    if mat:
        feats.append(mat[3])
    feats += [d[3] for d in _find_all(blob, DETAILS, 2)]
    if feats:
        s = feats[0][:1].upper() + feats[0][1:]
        if len(feats) > 1:
            s += " и " + feats[1]
        sentences.append(s + ".")
    t = _find(blob, TYPES)
    occ = OCCASION.get(t[2]) if t else None
    sentences.append(occ or CATEGORY_OCCASION.get(category, CATEGORY_OCCASION["clothes"]))
    if _DUO.search(blob):
        sentences.append("Сразу две штуки — себе и в подарок.")
    sentences.append(CLOSING.get(category, CLOSING["clothes"]))
    return " ".join(sentences)
