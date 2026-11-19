"""Напоминания: разбор времени на русском и казахском, хранение, срабатывание."""
import datetime
import json

import pytest

import i18n
import kazakh
import reminders as R

NOW = datetime.datetime(2026, 11, 14, 10, 30)      # суббота


def when(text):
    w, rest = R.parse_when(text, NOW)
    return (w.strftime("%d.%m %H:%M") if w else None), rest


@pytest.mark.parametrize("phrase,expected", [
    ("через 10 минут позвонить маме", ("14.11 10:40", "позвонить маме")),
    ("через десять минут выключить плиту", ("14.11 10:40", "выключить плиту")),
    ("через час купить хлеб", ("14.11 11:30", "купить хлеб")),
    ("через полчаса созвон", ("14.11 11:00", "созвон")),
    ("через два с половиной часа встреча", ("14.11 13:00", "встреча")),
    ("через 2 часа 15 минут проверить почту", ("14.11 12:45", "проверить почту")),
    ("через 3 дня оплатить интернет", ("17.11 10:30", "оплатить интернет")),
    ("через минуту три раза хлопнуть", ("14.11 10:31", "три раза хлопнуть")),     # «три» — не часть времени
    ("завтра в 9 позвонить маме", ("15.11 09:00", "позвонить маме")),
    ("завтра в девять утра позвонить маме", ("15.11 09:00", "позвонить маме")),
    ("завтра в 21:30 фильм", ("15.11 21:30", "фильм")),
    ("сегодня вечером купить молоко", ("14.11 19:00", "купить молоко")),
    ("в 8 вечера тренировка", ("14.11 20:00", "тренировка")),
    ("в 8 утра зарядка", ("15.11 08:00", "зарядка")),          # 8 утра уже прошло → завтра
    ("в 11 ночи лечь спать", ("14.11 23:00", "лечь спать")),
    ("в двенадцать ночи проверить", ("15.11 00:00", "проверить")),
    ("в полдень обед", ("14.11 12:00", "обед")),
    ("в пятницу в 18:30 тренировка", ("20.11 18:30", "тренировка")),
    ("в понедельник встреча", ("16.11 09:00", "встреча")),
    ("послезавтра в десять двадцать врач", ("16.11 10:20", "врач")),
    ("5 декабря в 9 утра день рождения", ("05.12 09:00", "день рождения")),
    ("на 20 января в 12 дедлайн", ("20.01 12:00", "дедлайн")),      # январь уже следующего года
    ("в девять ноль пять созвон", ("15.11 09:05", "созвон")),
    ("в девять купить три яблока", ("15.11 09:00", "купить три яблока")),
])
def test_parse_when(phrase, expected):
    assert when(phrase) == expected


def test_no_time_means_none():
    assert when("купить хлеб") == (None, "купить хлеб")
    assert when("по понедельникам нет")[0] is None


def test_strict_numbers_do_not_eat_text():
    """Раньше число находилось в любом месте фразы: «9 позвонить» считалось числом."""
    assert R._number("десять двадцать") is None
    assert R._number("9 позвонить") is None
    assert R._number("двадцать пять") == 25
    assert R._number("полтора") == 1.5


KAZAKH = [
    ("ертең сағат 9-да анама қоңырау шалуды еске сал", "15.11 09:00", "анама қоңырау шалуды"),
    ("ертең сағат тоғызда дәрігерге бару керегін еске сал", "15.11 09:00", "дәрігерге бару керегін"),
    ("ертең сағат он екіде тапсырма еске сал", "15.11 12:00", "тапсырма"),
    ("10 минуттан кейін шәйнекті өшіруді еске сал", "14.11 10:40", "шәйнекті өшіруді"),
    ("жұмада сағат 18:30-да жаттығуды еске сал", "20.11 18:30", "жаттығуды"),
    ("5 желтоқсанда сағат 9-да туған күнді еске сал", "05.12 09:00", "туған күнді"),
    ("бүгін кешке сүт алуды еске сал", "14.11 19:00", "сүт алуды"),
]


@pytest.mark.parametrize("phrase,at,text", KAZAKH)
def test_kazakh_reminders(phrase, at, text):
    canon = kazakh.canon(phrase)
    assert canon.startswith("напомни")
    assert when(canon) == (at, text)


# ---------------------------------------------------------------- хранение
@pytest.fixture
def store(tmp_path):
    now = [NOW]
    return R.Reminders(str(tmp_path / "r.json"), clock=lambda: now[0]), now


def test_add_persists_and_lists_sorted(store, tmp_path):
    r, _ = store
    r.add(NOW + datetime.timedelta(hours=5), "позже")
    r.add(NOW + datetime.timedelta(hours=1), "раньше")
    assert [x["text"] for x in r.upcoming()] == ["раньше", "позже"]
    again = R.Reminders(str(tmp_path / "r.json"))         # «перезапуск»
    assert len(again.upcoming()) == 2


def test_due_fires_once_and_includes_missed(store):
    r, now = store
    r.add(NOW + datetime.timedelta(minutes=5), "скоро")
    assert not r.has_due()
    now[0] = NOW + datetime.timedelta(minutes=6)           # прошло время (в том числе пока KAR была выключена)
    assert r.has_due()
    assert [x["text"] for x in r.due()] == ["скоро"]
    assert r.due() == [] and not r.has_due()


def test_cancel_all_by_word_and_by_id(store):
    r, _ = store
    a = r.add(NOW + datetime.timedelta(hours=1), "позвонить маме")
    r.add(NOW + datetime.timedelta(hours=2), "купить хлеб")
    r.add(NOW + datetime.timedelta(hours=3), "зарядка")
    assert r.cancel("про маму") == 1                        # «маму» находит «маме»; «про» не учитывается
    assert r.remove(a["id"]) == 0                         # уже удалено
    assert r.cancel("про хлеб") == 1
    assert r.cancel(None) == 1 and r.upcoming() == []


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{ не json", encoding="utf-8")
    assert R.Reminders(str(path)).upcoming() == []


def test_format_when_is_localized():
    i18n.set_lang("ru")
    assert R.format_when(NOW.replace(hour=15), NOW) == "сегодня в 15:30"
    assert R.format_when(NOW + datetime.timedelta(days=1), NOW) == "завтра в 10:30"
    assert R.format_when(datetime.datetime(2026, 12, 5, 9, 0), NOW) == "5 декабря в 09:00"
    i18n.set_lang("kk")
    assert R.format_when(datetime.datetime(2026, 12, 5, 9, 0), NOW) == "5 желтоқсан, 09:00"
