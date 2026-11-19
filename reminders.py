"""Напоминания с датой и временем. «Напомни завтра в девять позвонить маме», «через десять минут напомни выключить
плиту», «в пятницу в 18:30 напомни про тренировку». Хранятся в reminders.json и переживают перезапуск.
Разбор времени — на русском; казахские фразы приводит к нему kazakh.py. Идея — из capri-desktop (NOTICE.md)."""
import datetime
import json
import os
import re
import tempfile
import threading

import i18n

MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
          "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
WEEKDAYS = {"понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3, "пятница": 4, "пятницу": 4,
            "суббота": 5, "субботу": 5, "воскресенье": 6}
UNIT_RE = re.compile(r"^(секунд\w*|сек|минут\w*|мин|час\w*|ч|дн\w*|день|сутки|суток|недел\w*)$")
PERIOD_RE = re.compile(r"\b(утра|утром|дня|днем|вечера|вечером|ночи|ночью)\b")
DEFAULT_HOUR = {"утром": 9, "днем": 14, "вечером": 19, "ночью": 23}


def _number(words):
    """Строго: '10' / 'десять' / 'двадцать пять' / 'полтора' → число; всё остальное (в том числе «три яблока»,
    «9 позвонить») → None. actions.parse_number здесь не подходит: он ищет число в любом месте фразы."""
    words = words.strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", words):
        return float(words.replace(",", "."))
    if words in ("полтора", "полторы"):
        return 1.5
    from actions import NUM
    tokens = words.split()
    if not tokens or any(t not in NUM for t in tokens):
        return None
    total, stage = 0, 0  # 0 начало, 1 сотни, 2 десятки, 3 «-надцать», 4 единицы
    for t in tokens:
        v = NUM[t]
        if v == 0:
            return 0.0 if len(tokens) == 1 else None
        if v >= 100:
            kind = 1
        elif v >= 20:
            kind = 2
        elif v >= 10:
            kind = 3
        else:
            kind = 4
        if (kind == 1 and stage > 0) or (kind in (2, 3) and stage > 1) or (kind == 4 and stage > 2):
            return None  # «десять двадцать», «пять три»: так числа не говорят
        stage, total = kind, total + v
    return float(total)


def _unit_seconds(unit, count):
    if unit.startswith(("сек",)):
        return count
    if unit.startswith(("мин",)):
        return count * 60
    if unit.startswith(("час", "ч")):
        return count * 3600
    if unit.startswith(("дн", "ден", "сут")):
        return count * 86400
    return count * 604800  # неделя


def _duration(tokens, i):
    """Продолжительность из слов начиная с i: «десять минут», «полтора часа», «два с половиной часа»,
    «час», «полчаса». Возвращает (секунды, индекс после). Лишние слова вроде «три яблока» не съедает."""
    total, pos = 0.0, i
    while pos < len(tokens):
        if tokens[pos] == "полчаса":
            total, pos = total + 1800, pos + 1
            continue
        j = pos
        words = []
        while j < len(tokens) and not UNIT_RE.match(tokens[j]) and len(words) < 4:
            words.append(tokens[j])
            j += 1
        if j >= len(tokens) or not UNIT_RE.match(tokens[j]):
            break
        half = 0.0
        if words[-2:] == ["с", "половиной"]:
            words, half = words[:-2], 0.5
        count = 1.0 if not words else _number(" ".join(words))
        if count is None:
            break
        total += _unit_seconds(tokens[j], count + half)
        pos = j + 1
        if pos < len(tokens) and tokens[pos] == "и":
            pos += 1
            continue
    if total and pos > i and tokens[pos - 1] == "и":
        pos -= 1
    return total, pos


def _clock(tokens, i):
    """Время суток после «в»/«к»: «9:30», «9 30», «девять тридцать», «в пять часов». → (час, минуты, сколько слов)."""
    if i >= len(tokens):
        return None
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", tokens[i])
    if m:
        return int(m.group(1)), int(m.group(2)), 1
    for size in (3, 2, 1):  # самое длинное число, которое подходит как час
        h = _number(" ".join(tokens[i:i + size]))
        if h is not None and h == int(h) and 0 <= h <= 24 and len(tokens[i:i + size]) == size:
            used, minutes = size, 0
            if i + used < len(tokens) and re.match(r"^час\w*$", tokens[i + used]):
                used += 1
            for msize in (3, 2, 1):  # минуты: ноль пять / тридцать / 45 / «пять минут»
                chunk = tokens[i + used:i + used + msize]
                if len(chunk) < msize:
                    continue
                n = _number(" ".join(chunk[1:] if chunk[0] == "ноль" else chunk))
                after = tokens[i + used + msize:i + used + msize + 1]
                explicit = bool(after) and bool(re.match(r"^минут\w*$", after[0]))
                if n is not None and n == int(n) and 0 <= n <= 59 and (n >= 10 or chunk[0] == "ноль" or explicit):
                    used += msize + (1 if explicit else 0)
                    minutes = int(n)
                    break
            return int(h), minutes, used
    return None


def parse_when(text, now):
    """(datetime | None, остаток текста). None, если времени в фразе нет."""
    tokens = text.lower().replace("ё", "е").split()
    drop = set()  # индексы слов, которые относятся ко времени
    when = None

    # 1. «через …»
    if "через" in tokens:
        i = tokens.index("через")
        secs, end = _duration(tokens, i + 1)
        if secs:
            drop.update(range(i, end))
            when = now + datetime.timedelta(seconds=secs)

    if when is None:
        date, hour, minute = None, None, 0
        for i, tok in enumerate(tokens):  # день
            if tok in ("сегодня", "завтра", "послезавтра"):
                date = now.date() + datetime.timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[tok])
                drop.add(i)
            elif tok in WEEKDAYS:
                ahead = (WEEKDAYS[tok] - now.weekday()) % 7
                date = now.date() + datetime.timedelta(days=ahead)
                drop.update({i} | ({i - 1} if i and tokens[i - 1] in ("в", "во", "на", "следующий", "следующую",
                                                                          "ближайший", "ближайшую") else set()))
            elif tok in MONTHS and i and re.fullmatch(r"\d{1,2}", tokens[i - 1]):
                day, month = int(tokens[i - 1]), MONTHS[tok]
                try:
                    date = datetime.date(now.year, month, day)
                    if date < now.date():
                        date = datetime.date(now.year + 1, month, day)
                except ValueError:
                    return None, _note_text(text.lower().replace("ё", "е"))
                drop.update({i, i - 1} | ({i - 2} if i > 1 and tokens[i - 2] in ("на", "в", "к") else set()))
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok):
                try:
                    date = datetime.date.fromisoformat(tok)
                    drop.add(i)
                except ValueError:
                    pass
        for i, tok in enumerate(tokens):  # время суток
            if i in drop:
                continue
            if tok in ("в", "к", "на", "около") or re.fullmatch(r"\d{1,2}:\d{2}", tok):
                start = i + 1 if tok in ("в", "к", "на", "около") else i
                got = _clock(tokens, start)
                if got:
                    hour, minute, used = got
                    drop.update(range(i if tok in ("в", "к", "на", "около") else start, start + used))
                    break
            if tok in ("полдень", "полночь") and i and tokens[i - 1] in ("в", "к"):
                hour, minute = (12, 0) if tok == "полдень" else (0, 0)
                drop.update({i, i - 1})
                break
        period = None
        for i, tok in enumerate(tokens):
            if PERIOD_RE.fullmatch(tok):
                period = tok
                drop.add(i)
        if hour is not None and period:
            if period in ("вечера", "вечером") and hour < 12:
                hour += 12
            elif period in ("дня", "днем") and hour < 12 and hour != 0:
                hour = hour + 12 if hour < 6 else hour
            elif period in ("ночи", "ночью"):
                hour = 0 if hour == 12 else hour + 12 if hour >= 6 else hour
            elif period in ("утра", "утром") and hour == 12:
                hour = 0
        elif hour is None and period in DEFAULT_HOUR:
            hour = DEFAULT_HOUR[period]
        if hour is None and date is None:
            return None, _note_text(text.lower().replace("ё", "е"))
        hour = 9 if hour is None else min(hour, 23)
        base = date or now.date()
        when = datetime.datetime.combine(base, datetime.time(hour, minute))
        if date is None and when <= now:
            when += datetime.timedelta(days=1)       # «в девять», когда уже десять, — это завтра
        elif date is not None and tokens and when <= now and any(t in WEEKDAYS for t in tokens):
            when += datetime.timedelta(days=7)       # «в понедельник», когда он сегодня и время прошло
    rest = [t for i, t in enumerate(tokens) if i not in drop]
    return when, _note_text(" ".join(rest))


_FILLERS = re.compile(r"^(?:(?:напомни\w*|напоминани\w*|мне|пожалуйста|поставь|создай|добавь|что|чтобы|о|об|про|"
                       r"насчет|и|в|на|к|а)\b\s*)+")


def _note_text(text):
    text = re.sub(r"\bнапомни\w*\b|\bнапоминани\w*\b|\bпожалуйста\b", " ", text)
    text = _FILLERS.sub("", re.sub(r"\s+", " ", text).strip())
    return re.sub(r"\s+(?:и|в|на|к|а)$", "", text).strip(" ,.;")


_CANCEL_STOP = {"про", "насчет", "напоминание", "напоминания", "напоминаний", "все", "мои", "что", "чтобы",
                "еске", "салу", "туралы"}


def _same_word(a, b):
    """Слова совпадают по началу: «маму» ~ «маме», «хлеб» ~ «хлеба» (общее начало ≥ 3 букв, отличаются концом)."""
    common = len(os.path.commonprefix([a, b]))
    return common >= 3 and common >= min(len(a), len(b)) - 2


class Reminders:
    def __init__(self, path, clock=datetime.datetime.now):
        self.path, self.clock = path, clock
        self._lock = threading.Lock()
        self._next_when = min((r["when"] for r in self._load()), default=None)  # ближайшее, без чтения файла

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return [r for r in data if isinstance(r, dict) and "when" in r and "text" in r]
        except (OSError, ValueError):
            return []

    def _save(self, items):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # файл не бывает записан наполовину
        self._next_when = min((r["when"] for r in items), default=None)

    def add(self, when, text):
        with self._lock:
            items = self._load()
            item = {"id": max([r.get("id", 0) for r in items] + [0]) + 1, "when": when.isoformat(timespec="minutes"),
                    "text": text}
            self._save(items + [item])
            return item

    def upcoming(self):
        with self._lock:
            return sorted(self._load(), key=lambda r: r["when"])

    def cancel(self, query=None):
        """Убрать напоминания: все (запрос пустой) или те, где есть слово из запроса. → сколько убрано.
        Слова сравниваются по началу («маму» находит «маме»), служебные слова вроде «про» не учитываются."""
        with self._lock:
            items = self._load()
            words = [w for w in re.findall(r"\w+", (query or "").lower().replace("ё", "е"))
                     if len(w) > 2 and w not in _CANCEL_STOP]
            if not words:
                keep = []
            else:
                keep = [r for r in items if not any(_same_word(w, t) for w in words
                                                    for t in re.findall(r"\w+", r["text"].lower().replace("ё", "е")))]
            self._save(keep)
            return len(items) - len(keep)

    def remove(self, reminder_id):
        with self._lock:
            items = self._load()
            keep = [r for r in items if r.get("id") != reminder_id]
            self._save(keep)
            return len(items) - len(keep)

    def due(self, now=None):
        """Сработавшие напоминания (они удаляются). Пропущенные, пока KAR была выключена, тоже возвращаются."""
        now = (now or self.clock()).isoformat(timespec="minutes")
        with self._lock:
            items = self._load()
            if not any(r["when"] <= now for r in items):
                return []
            fired = [r for r in items if r["when"] <= now]
            self._save([r for r in items if r["when"] > now])
            return fired

    def has_due(self, now=None):
        """Дёшево: есть ли что-то на сейчас (вызывается несколько раз в секунду, файл не читается)."""
        return self._next_when is not None and self._next_when <= (now or self.clock()).isoformat(timespec="minutes")


def format_when(when, now=None):
    now = now or datetime.datetime.now()
    clock = when.strftime("%H:%M")
    if when.date() == now.date():
        return i18n.t("when_today", time=clock)
    if when.date() == now.date() + datetime.timedelta(days=1):
        return i18n.t("when_tomorrow", time=clock)
    return i18n.t("when_date", day=when.day, month=i18n.lst("months")[when.month - 1], time=clock)
