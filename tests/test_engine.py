"""Движок: решение «ответ / поиск / не понял», кнопки окна, смена языка."""
import json
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import i18n  # noqa: E402


@pytest.fixture
def engine(tmp_path, monkeypatch):
    import actions as A
    A.AppIndex.build = lambda self: None
    import main
    monkeypatch.setattr(main, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(main, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = json.loads(json.dumps(main.DEFAULTS))
    cfg.update(local_ai=False, greet_on_start=False, language="kk")
    i18n.set_lang("kk")
    e = main.KarEngine(cfg)
    e.speaker.say = lambda text: False          # без звука
    e.actions.web = lambda url: e.opened.append(url)
    e.opened = []
    return e


def fake_local(engine, answer):
    import local_ai

    class Local:
        def ask(self, q):
            return answer
    engine.local = Local()
    engine.actions.local = engine.local
    return local_ai


def test_greeting_does_not_open_google_when_ollama_is_off(engine):
    """Раньше при выключенной Ollama «привет» открывал поиск в браузере."""
    fake_local(engine, None)                      # None = Ollama недоступна
    reply = engine._answer("привет")
    assert engine.opened == []
    assert reply.text in i18n.TEXT["kk"]["unknown"] + [i18n.TEXT["kk"]["dont_know"]]


def test_question_still_searches_when_ollama_is_off(engine):
    fake_local(engine, None)
    engine._answer("как приготовить плов")
    assert engine.opened and "google" in engine.opened[0]


def test_model_asks_for_search(engine):
    import local_ai
    fake_local(engine, local_ai.SEARCH)
    engine._answer("привет")                      # модель сама попросила поиск
    assert engine.opened


def test_model_answer_is_used(engine):
    fake_local(engine, "Астана.")
    assert engine._answer("столица казахстана").text == "Астана."
    assert engine.opened == []


def test_arm_makes_next_phrase_a_search(engine):
    engine.arm_next("search")
    assert engine.arm == "search" and engine.cur_state == "listening"
    engine._process(("text", "қазақ тілі"))
    assert engine.arm == ""
    assert any("%D2%9B" in u or "қазақ" in u for u in engine.opened)   # «қазақ» в запросе


def test_stop_button_stops_then_disconnects_mic(engine):
    engine.set_state("idle")
    engine.stop_or_disconnect()
    assert engine.mic_off and engine.cur_state == "off"
    engine.stop_or_disconnect()
    assert not engine.mic_off and engine.cur_state == "idle"


def test_stop_button_cancels_pending_and_arm(engine):
    engine.pending_marker = None
    engine.actions.pending = lambda: None
    engine.arm_next("open")
    engine.stop_or_disconnect()
    assert engine.actions.pending is None and engine.arm == "" and not engine.mic_off


def test_wake_words(engine):
    assert all(engine.has_wake(x) for x in ("кар открой ютуб", "қар ашшы", "кәр, сағат неше"))
    assert not any(engine.has_wake(x) for x in ("как дела", "карта москвы", "картинки"))
    engine.cfg["use_wake_word"] = False
    assert not engine.has_wake("кар открой ютуб")


def test_strip_wake(engine):
    assert engine.strip_wake("Кар, открой ютуб") == "открой ютуб"
    assert engine.strip_wake("открой ютуб кар") == "открой ютуб"


def test_echo_filter(engine):
    engine.speech_words = {"алматы", "қазір", "ашық"}
    assert engine.is_echo("алматы қазір ашық")
    assert not engine.is_echo("кар стоп")


def test_chat_history_is_saved_and_limited(engine, tmp_path):
    engine.cfg["history_size"] = 3
    for i in range(5):
        engine.log_chat("user", f"cmd {i}")
    assert [h["text"] for h in engine.history] == ["cmd 2", "cmd 3", "cmd 4"]
    assert json.load(open(tmp_path / "history.json", encoding="utf-8"))[-1]["text"] == "cmd 4"
    engine.clear_history()
    assert engine.history == [] and not os.path.exists(tmp_path / "history.json")


def test_language_switch_updates_everything(engine, monkeypatch):
    monkeypatch.setattr(engine, "_start_listener", lambda: None)
    engine.update_settings({"language": "ru", "voice_speed": 200})
    assert i18n.lang() == "ru" and engine.cfg["language"] == "ru"
    assert engine.speaker.language == "ru" and engine.speaker.wpm == 200
    assert engine.stt_lang == "ru-RU"
    engine.set_language("kk")
    assert engine.stt_lang == "kk-KZ" and engine.speaker.voice.startswith("kk-KZ")


def test_speed_maps_to_edge_rate(engine):
    engine.speaker.wpm = 160
    assert engine.speaker.rate == "+0%"
    engine.speaker.wpm = 240
    assert engine.speaker.rate == "+50%"
    engine.speaker.wpm = 80
    assert engine.speaker.rate == "-50%"


def test_voice_preview_uses_unsaved_settings(engine, monkeypatch):
    import main
    used = []

    class FakeSpeaker:
        def __init__(self, cfg):
            used.append(cfg)

        def say(self, text):
            used.append(text)

        def stop(self):
            pass
    monkeypatch.setattr(main, "Speaker", FakeSpeaker)
    engine.preview_voice({"language": "kk", "voice_kk": "kk-KZ-DauletNeural", "voice_speed": 200})
    time.sleep(0.2)
    assert used[0]["voice_kk"] == "kk-KZ-DauletNeural" and used[0]["voice_speed"] == 200
    assert used[1] == i18n.TEXT["kk"]["preview_text"]
    assert engine.cfg["voice_speed"] == 160            # сохранённые настройки не тронуты


# ======================================================================== «мозг»: команда или ответ
class FakeBrain:
    available = True
    kind = "nvidia"

    def __init__(self, route=("answer", "ответ"), error=None):
        self.route_result, self.error, self.calls = route, error, []

    def route(self, text, notes=""):
        self.calls.append(("route", text, notes))
        if self.error:
            raise self.error
        return self.route_result

    def ask(self, text, notes=""):
        self.calls.append(("ask", text, notes))
        return "прямой ответ"


def with_brain(engine, brain):
    engine.cloud = brain
    return engine


def test_brain_turns_free_speech_into_a_command(engine):
    brain = with_brain(engine, FakeBrain(route=("cmd", "который час"))).cloud
    reply = engine._answer("сағат қанша болып қалды екен")
    assert reply.text.startswith("Қазір ") or reply.text.startswith("Сейчас ")
    assert brain.calls[0][:2] == ("route", "сағат қанша болып қалды екен")


def test_brain_answer_is_spoken_and_notes_are_sent(engine):
    brain = with_brain(engine, FakeBrain(route=("answer", "Астана."))).cloud
    engine.actions.notes.add("мен Алматыда тұрамын")
    assert engine._answer("столица казахстана").text == "Астана."
    assert "Алматыда" in brain.calls[0][2]                  # заметки о пользователе дошли до ИИ


def test_brain_command_that_does_nothing_says_unknown(engine):
    with_brain(engine, FakeBrain(route=("cmd", "несуществующая команда абракадабра")))
    reply = engine._answer("что-то странное")
    assert reply.text in i18n.TEXT["kk"]["unknown"]


def test_brain_errors_are_spoken_not_raised(engine):
    from cloud_ai import CloudError
    with_brain(engine, FakeBrain(error=CloudError("ai_rate")))
    assert engine._answer("привет").text == i18n.t("ai_rate")


def test_route_can_be_switched_off(engine):
    engine.cfg["ai_route"] = False
    brain = with_brain(engine, FakeBrain()).cloud
    assert engine._answer("привет").text == "прямой ответ"
    assert brain.calls[0][0] == "ask"


def test_api_key_in_settings_creates_the_right_brain(engine, monkeypatch):
    monkeypatch.setattr(engine, "_start_listener", lambda: None)
    assert engine.brain() is None
    engine.update_settings({"ai_key": "nvapi-test"})
    assert engine.brain().kind == "nvidia" and engine.actions.ai is engine.brain()
    engine.update_settings({"ai_key": ""})
    assert engine.brain() is None


def test_old_claude_key_needs_enable_flag(engine, monkeypatch):
    import ai as ai_module
    monkeypatch.setattr(ai_module.AI, "__init__", lambda self, cfg: setattr(self, "client", object()) or
                        setattr(self, "anthropic", None) or setattr(self, "history", []))
    engine.cfg["enable_ai"] = False
    engine._make_brains()
    assert engine.brain() is None                            # ключ есть, галочки нет — не используем
    engine.cfg["enable_ai"] = True
    engine._make_brains()
    assert engine.brain() is not None


# ======================================================================== диктовка
@pytest.fixture
def typing(monkeypatch):
    import automation
    typed = []
    monkeypatch.setattr(automation, "restore_external", lambda: True)
    monkeypatch.setattr(automation, "foreground_is_own", lambda: False)
    monkeypatch.setattr(automation, "input_text", typed.append)
    return typed


def test_dictation_types_what_is_said_until_stopped(engine, typing):
    from actions import Reply
    engine._finalize(Reply("ok", "dictation_on"))
    assert engine.dictation and engine.cur_state == "listening"
    engine.on_final("привет мир", b"")
    job = engine.jobs.get_nowait()
    assert job[0] == "dictate"
    engine.cfg["use_google_stt"] = False
    engine._process(job)
    assert typing == ["привет мир "]
    engine.on_final("стоп диктовка", b"")                    # заканчивает диктовку, а не печатается
    assert engine.jobs.get_nowait() == ("text", "выключи диктовку")
    engine._finalize(Reply("ok", "dictation_off"))
    assert not engine.dictation


def test_dictation_stops_in_kazakh_too(engine, typing):
    from actions import Reply
    engine._finalize(Reply("ok", "dictation_on"))
    engine.on_final("диктовканы тоқтат", b"")
    assert engine.jobs.get_nowait()[0] == "text"


def test_dictation_refuses_when_there_is_nowhere_to_type(engine, monkeypatch):
    import automation
    from actions import Reply
    monkeypatch.setattr(automation, "restore_external", lambda: False)
    monkeypatch.setattr(automation, "foreground_is_own", lambda: True)
    reply = engine._finalize(Reply("ok", "dictation_on"))
    assert reply.text == i18n.t("dictation_no_window") and not engine.dictation


def test_dictation_keeps_the_mic_state_while_on(engine, typing):
    from actions import Reply
    engine._finalize(Reply("ok", "dictation_on"))
    engine.awake_until = 0
    engine.tick()
    assert engine.cur_state == "listening"                    # окно прослушивания не «закрывается»


# ======================================================================== напоминания в такте, микрофон
def test_due_reminder_is_spoken_and_notified(engine):
    import datetime
    engine.actions.reminders.add(datetime.datetime.now() - datetime.timedelta(minutes=1), "выпить воды")
    popups = []
    engine.notify.connect(lambda title, text: popups.append((title, text)))
    engine.tick()
    assert popups == [("KAR", "выпить воды")]
    job = engine.jobs.get_nowait()
    assert job == ("say", i18n.t("reminder_due", text="выпить воды"))
    engine.tick()                                              # второй раз не повторяется
    assert engine.jobs.empty()


def test_mic_error_gives_a_specific_reason(engine, monkeypatch):
    import winsys
    monkeypatch.setattr(winsys, "mic_privacy_blocked", lambda reg=None: True)
    engine.on_listener_error("whatever")
    assert engine.jobs.get_nowait() == ("say", i18n.t("mic_blocked"))
    monkeypatch.setattr(winsys, "mic_privacy_blocked", lambda reg=None: False)
    engine.on_listener_error("Device unavailable [PaErrorCode -9985]")
    assert engine.jobs.get_nowait() == ("say", i18n.t("mic_busy"))
    engine.on_listener_error("x", kind="model")
    assert engine.jobs.get_nowait() == ("say", i18n.t("model_broken"))


def test_mic_check_text(engine, monkeypatch):
    import winsys
    monkeypatch.setattr(winsys, "mic_privacy_blocked", lambda reg=None: False)
    assert engine.mic_check_text() == i18n.t("no_mic")        # слушатель не запущен
    engine.listener = type("L", (), {"active_name": "Realtek Mic"})()
    assert engine.mic_check_text() == i18n.t("mic_ok", t="Realtek Mic")
    monkeypatch.setattr(winsys, "mic_privacy_blocked", lambda reg=None: True)
    assert engine.mic_check_text() == i18n.t("mic_blocked")


def test_autostart_goes_through_winsys(engine, monkeypatch):
    import winsys
    calls = []
    monkeypatch.setattr(winsys, "set_autostart", lambda on, path: calls.append((on, os.path.basename(path))) or True)
    monkeypatch.setattr(winsys, "autostart_enabled", lambda path: True)
    assert engine.autostart_enabled() and engine.set_autostart(False)
    assert calls == [(False, "main.py")]


def test_hotkeys_failure_is_reported(engine, monkeypatch):
    import winsys

    class Fake:
        failed = ["mute"]

        def __init__(self, bindings, callback):
            self.bindings = bindings

        def start(self):
            pass
    monkeypatch.setattr(winsys, "GlobalHotkeys", Fake)
    engine.start_hotkeys()
    assert engine.jobs.get_nowait() == ("say", i18n.t("hotkey_busy", t="ctrl+alt+m"))
    engine.cfg["use_hotkeys"] = False
    engine.start_hotkeys()
    assert engine.jobs.empty()


def test_echo_filter_tolerates_recognition_errors(engine):
    engine.speech_words = {"сейчас", "в", "алматы", "плюс", "восемнадцать", "градусов"}
    assert engine.is_echo("в алматы плюс восемнадцать градус")      # слово исказилось
    assert not engine.is_echo("кар открой ютуб")


def test_barge_in_is_off_by_default_so_speaker_is_not_heard(engine):
    assert engine.cfg["barge_in_enabled"] is False
    assert engine.cfg["echo_tail_seconds"] >= 0.5
