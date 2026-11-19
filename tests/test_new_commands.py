"""Новые команды целиком: фраза → обработчик → ответ (русский и казахский)."""
import datetime
import os
from unittest.mock import MagicMock

import pytest

from actions import Reply
import finder
import i18n
import winsys
from test_winsys import session

NOW = datetime.datetime(2026, 11, 14, 10, 30)


@pytest.fixture(autouse=True)
def _lang():
    i18n.set_lang("ru")


@pytest.fixture
def frozen(monkeypatch):
    """Фиксированное «сейчас», чтобы ответы про время не зависели от часов."""
    class FakeDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 14, 10, 30)
    monkeypatch.setattr(datetime, "datetime", FakeDT)
    return FakeDT


# ======================================================================== напоминания
def test_reminder_in_one_phrase(actions, frozen):
    r = actions.handle("напомни завтра в девять позвонить маме")
    assert r.text == "Напомню завтра в 09:00: позвонить маме."
    assert [x["text"] for x in actions.reminders.upcoming()] == ["позвонить маме"]


def test_reminder_with_time_first(actions, frozen):
    assert "через" not in actions.handle("через десять минут напомни выключить плиту").text
    assert actions.reminders.upcoming()[0]["when"] == "2026-11-14T10:40"


def test_reminder_asks_for_missing_time_then_remembers_text(actions, frozen):
    r = actions.handle("напомни позвонить маме")
    assert r.text == i18n.t("reminder_ask_when") and r.action == "ask"
    assert actions.handle("завтра в 9").text == "Напомню завтра в 09:00: позвонить маме."


def test_reminder_asks_for_missing_text(actions, frozen):
    assert actions.handle("напомни завтра в 9").text == i18n.t("reminder_ask_text")
    assert "выпить таблетку" in actions.handle("выпить таблетку").text


def test_reminder_answer_expires_after_30_seconds(actions, frozen):
    actions.handle("напомни позвонить маме")
    actions.awaiting["until"] = 0
    assert actions.handle("завтра в 9") is None            # время ответа вышло — это уже обычная фраза


def test_reminder_in_the_past_is_refused(actions, frozen):
    assert actions.handle("сегодня в 8 утра напомни выпить воды").text == i18n.t("reminder_past")
    assert actions.reminders.upcoming() == []


def test_list_and_cancel_reminders(actions, frozen):
    assert actions.handle("какие у меня напоминания").text == i18n.t("reminder_none")
    actions.handle("напомни завтра в 9 позвонить маме")
    actions.handle("напомни в пятницу в 18:30 тренировка")
    text = actions.handle("какие у меня напоминания").text
    assert text.startswith("Напоминаний: 2") and "позвонить маме" in text
    assert actions.handle("отмени напоминание про маму").text == "Отменено напоминаний: 1."
    assert actions.handle("отмени все напоминания").text == "Отменено напоминаний: 1."
    assert actions.handle("отмени все напоминания").text == i18n.t("reminder_cancel_none")


def test_kazakh_reminder(actions, frozen):
    i18n.set_lang("kk")
    r = actions.handle("ертең сағат тоғызда анама қоңырау шалуды еске сал")
    assert r.text == "Еске саламын — ертең 09:00: анама қоңырау шалуды."
    assert "анама" in actions.handle("қандай еске салуларым бар").text
    assert actions.handle("еске салуларды өшір").text.endswith("1.")


# ======================================================================== рутины
def test_routine_is_saved_and_runs_the_action(actions):
    saved = actions.handle("когда я скажу привет делай который час")
    assert saved.text == "Запомнено: на «привет» выполню «который час»."
    assert actions.handle("привет").text.startswith("Сейчас ")


def test_routine_with_a_chain_is_saved_not_executed(actions):
    """Раньше фраза резалась по запятой на шаги, и вместо создания рутины открывался Telegram."""
    opened = []
    actions.routes = [(p, (lambda c, m: opened.append(c) or Reply("ok")) if fn.__name__ == "open" else fn)
                      for p, fn in actions.routes]
    saved = actions.handle("когда я скажу рабочий режим, открой телеграм и ютуб")
    assert opened == [] and "рабочий режим" in saved.text
    assert actions.routines["рабочий режим"] == "открой телеграм и ютуб"
    actions.handle("рабочий режим")                         # сама рутина открывает ОБЕ программы
    assert opened == ["открой телеграм", "открой ютуб"]


def test_expand_and():
    from actions import Actions
    assert Actions.expand_and("открой телеграм и ютуб") == ["открой телеграм", "открой ютуб"]
    assert Actions.expand_and("запусти хром, телеграм и стим") == ["запусти хром", "запусти телеграм", "запусти стим"]
    assert Actions.expand_and("открой ютуб") == ["открой ютуб"]
    assert Actions.expand_and("найди борщ и плов") == ["найди борщ и плов"]       # поиск не режем


def test_routine_cannot_call_itself(actions):
    actions.handle("когда я скажу громче делай громче")
    assert "громче" not in actions.routines             # фраза и действие совпадают — рутина не создаётся


def test_routine_forget_and_list(actions):
    assert actions.handle("какие у меня рутины").text == i18n.t("routine_empty")
    actions.handle("когда я скажу привет делай который час")
    assert "привет" in actions.handle("какие у меня рутины").text
    assert actions.handle("забудь рутину привет").text == "Рутина «привет» удалена."
    assert actions.handle("забудь рутину привет").text == i18n.t("routine_none")


def test_kazakh_routine(actions):
    i18n.set_lang("kk")
    assert "жұмыс" in actions.handle("жұмыс деп айтсам, сағат неше істе").text
    assert actions.handle("жұмыс").text.startswith("Қазір ")


# ======================================================================== файлы
@pytest.fixture
def with_files(actions, tmp_path):
    for rel in ("Documents/отчет по физике.docx", "Desktop/Договор.pdf"):
        p = tmp_path / "files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    shown, opened = [], []
    actions.finder = finder.FileFinder(roots=lambda: [str(tmp_path / "files")], wider=lambda: [],
                                       reveal=shown.append, opener=opened.append)
    actions.shown, actions.opened = shown, opened
    return actions


def test_find_then_show_then_open(with_files):
    a = with_files
    assert a.handle("покажи его").text == i18n.t("file_first")
    r = a.handle("найди файл отчёт по физике")
    assert "отчет по физике.docx" in r.text and "Documents" in r.text
    assert a.handle("покажи его").text == i18n.t("file_shown") and a.shown[0].endswith("физике.docx")
    assert "отчет по физике.docx" in a.handle("открой его").text and a.opened


def test_find_by_extension_and_not_found(with_files):
    assert "Договор.pdf" in with_files.handle("найди договор.pdf").text
    assert with_files.handle("найди файл несуществующий").text == i18n.t("file_none", q="несуществующий")


def test_file_words_do_not_hijack_web_search(with_files):
    """«найди рецепт борща» — обычный поиск, а не поиск файла."""
    import re
    import actions as A
    c = A.norm("найди рецепт борща")
    hits = [fn.__name__ for pattern, fn in with_files.routes if re.search(pattern, c)]
    assert hits[0] == "search" and "find_file" not in hits


def test_open_him_without_search_is_not_swallowed(actions):
    assert actions.open_found("открой его", None) is None


def test_kazakh_file_flow(with_files):
    i18n.set_lang("kk")
    assert "Договор.pdf" in with_files.handle("договор файлын тап").text
    assert with_files.handle("оны көрсет").text == i18n.t("file_shown")


# ======================================================================== громкость программ
@pytest.fixture
def mixer(actions, monkeypatch):
    sessions = [session("Spotify", 50), session("chrome", 80), session("Discord", 60)]
    monkeypatch.setattr(winsys, "app_sessions", lambda: sessions)
    actions.sessions = sessions
    return actions


def test_app_quieter_and_louder(mixer):
    assert mixer.handle("сделай спотифай тише").text == "Spotify: громкость 35 процентов."
    assert mixer.handle("сделай хром громче").text == "Chrome: громкость 95 процентов."


def test_app_mute_and_unmute(mixer):
    assert mixer.handle("выключи звук в хроме").text == "Звук в Chrome выключен."
    assert mixer.sessions[1]["raw"].SimpleAudioVolume.muted
    assert mixer.handle("включи звук в хроме").text == "Звук в Chrome включён."


def test_app_level_by_digits_and_words(mixer):
    assert mixer.handle("громкость дискорд 30").text == "Discord: громкость 30 процентов."
    assert mixer.handle("громкость спотифай двадцать пять").text == "Spotify: громкость 25 процентов."


def test_unknown_app_explains_itself(mixer):
    assert "steam" in mixer.handle("сделай стим тише").text.lower() or "стим" in mixer.handle("сделай стим тише").text


def test_master_volume_still_works(actions, monkeypatch):
    import actions as A
    monkeypatch.setattr(A, "get_volume", lambda: 40)
    set_to = []
    monkeypatch.setattr(A, "set_volume", set_to.append)
    for phrase in ("громкость 70", "громче", "сделай музыку тише", "громкость на пятьдесят"):
        assert actions.handle(phrase) is not None
    assert set_to == [70, 50, 30, 50]


def test_kazakh_app_volume(mixer):
    i18n.set_lang("kk")
    assert mixer.handle("Spotify дыбысын азайт").text == "Spotify: дыбыс деңгейі 35 пайыз."
    assert mixer.handle("хромның дыбысын өшір").text == "Chrome дыбысы өшірілді."
    assert mixer.handle("Discord дыбыс 30").text == "Discord: дыбыс деңгейі 30 пайыз."


# ======================================================================== заметки, проверки, диктовка
def test_notes_flow(actions):
    assert actions.handle("что ты обо мне знаешь").text == i18n.t("note_none")
    assert actions.handle("запомни, что я учусь в 10 классе").text == "Запомнено: я учусь в 10 классе."
    assert "10 классе" in actions.handle("что ты обо мне знаешь").text
    assert actions.handle("забудь всё обо мне").text == i18n.t("note_clear")
    assert actions.notes.items() == []


def test_remember_site_is_not_confused_with_notes(actions):
    assert actions.notes.items() == []
    actions.handle("запомни этот сайт как музыка")        # старая команда по-прежнему про сайты
    assert actions.notes.items() == []


def test_kazakh_notes(actions):
    i18n.set_lang("kk")
    assert actions.handle("мен 10 сыныпта оқимын деп есте сақта").text.startswith("Есте сақталды")
    assert "10 сыныпта" in actions.handle("мен туралы не білесің").text


@pytest.mark.parametrize("state,key", [("ok", "net_ok"), ("dns", "net_dns"), ("off", "net_off")])
def test_check_internet(actions, monkeypatch, state, key):
    monkeypatch.setattr(winsys, "internet_status", lambda: (state, 42))
    assert actions.handle("проверь интернет").text == i18n.t(key, ms=42)


def test_kazakh_check_internet(actions, monkeypatch):
    i18n.set_lang("kk")
    monkeypatch.setattr(winsys, "internet_status", lambda: ("off", 5))
    assert actions.handle("интернетті тексер").text == i18n.t("net_off")


def test_dictation_and_mic_check_return_engine_actions(actions):
    assert actions.handle("включи диктовку").action == "dictation_on"
    assert actions.handle("выключи диктовку").action == "dictation_off"
    assert actions.handle("стоп диктовка").action == "dictation_off"
    assert actions.handle("проверь микрофон").action == "mic_check"
    i18n.set_lang("kk")
    assert actions.handle("диктовканы қос").action == "dictation_on"
    assert actions.handle("микрофонды тексер").action == "mic_check"
