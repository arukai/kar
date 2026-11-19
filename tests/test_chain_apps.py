"""«Открой ворд и напиши привет», «открой спотифай и найди песню люблю»: цепочки с программами.
Windows и сами программы подменены заглушками — проверяется порядок шагов и то, куда идёт текст."""
import re

import pytest

import automation
import kazakh


@pytest.fixture
def chain(actions, monkeypatch):
    """actions с подменённым окружением: записываем, что запускали, печатали и нажимали."""
    import actions as A
    log = {"popen": [], "typed": [], "keys": [], "startfile": [], "focused": [], "play": 0, "titles": ["Документ1 - Word"]}
    monkeypatch.setattr(A.time, "sleep", lambda *_: None)
    monkeypatch.setattr(A.subprocess, "Popen", lambda argv, *a, **k: log["popen"].append(argv))
    monkeypatch.setattr(A.os, "startfile", lambda t: log["startfile"].append(t), raising=False)
    monkeypatch.setattr(A, "send_keys", lambda keys: log["keys"].append(list(keys)))
    monkeypatch.setattr(automation, "restore_external", lambda: None)
    monkeypatch.setattr(automation, "snapshot_windows", lambda: set())
    monkeypatch.setattr(automation, "foreground_is_own", lambda: False)
    monkeypatch.setattr(automation, "foreground_title", lambda: "")
    monkeypatch.setattr(automation, "input_text", lambda t: log["typed"].append(t))
    monkeypatch.setattr(automation, "wait_new_window", lambda before, timeout=20, process=None: 111)
    monkeypatch.setattr(automation, "window_title",
                        lambda h: log["titles"].pop(0) if len(log["titles"]) > 1 else log["titles"][0])
    monkeypatch.setattr(automation, "find_window", lambda kw: 111)
    monkeypatch.setattr(automation, "refocus", lambda ctx, timeout=6: log["focused"].append(ctx) or 111)
    monkeypatch.setattr(automation, "spotify_press_play", lambda timeout=14.0: log.__setitem__("play", log["play"] + 1) or True)
    monkeypatch.setattr(automation, "app_path",
                        lambda exe: r"C:\Office\WINWORD.EXE" if exe == "winword.exe" else None)
    actions.log = log
    actions.handle_one = lambda s: A.Reply("ок")  # обычный «открой X» здесь не проверяем
    return actions


def run(chain, phrase):
    import actions as A
    return chain.run_chain(automation.split_chain(A.norm(phrase)))


# ---------------------------------------------------------------- как режется фраза
@pytest.mark.parametrize("phrase,steps", [
    ("открой ворд и напиши привет", ["открой ворд", "напиши привет"]),
    ("открой ворд, напиши привет", ["открой ворд", "напиши привет"]),
    ("открой спотифай и найди песню люблю", ["открой спотифай", "найди песню люблю"]),
    ("открой спотифай и включи песню люблю", ["открой спотифай", "включи песню люблю"]),
    ("открой спотифай потом поставь песню люблю", ["открой спотифай", "поставь песню люблю"]),
])
def test_split(phrase, steps):
    import actions as A
    assert automation.split_chain(A.norm(phrase)) == steps


@pytest.mark.parametrize("phrase", ["включи музыку", "поставь таймер на 5 минут", "включи свет в спальне"])
def test_include_verb_alone_is_not_a_chain(phrase):
    import actions as A
    assert automation.split_chain(A.norm(phrase)) is None


# ---------------------------------------------------------------- Word
def test_word_opens_blank_document_and_types_without_enter(chain):
    r = run(chain, "открой ворд и напиши привет")
    assert chain.log["popen"] == [[r"C:\Office\WINWORD.EXE", "/w"]]   # /w — сразу пустой документ
    assert chain.log["typed"] == ["привет"]
    assert ["enter"] not in chain.log["keys"]                          # в документе строку не переносим
    assert "Word" in r.text and "привет" in r.text


def test_word_asks_for_enter_only_when_told(chain):
    run(chain, "открой ворд и напиши привет и отправь")
    assert ["enter"] in chain.log["keys"]


def test_word_start_screen_gets_ctrl_n(chain):
    chain.log["titles"] = ["Word", "Документ1 - Word"]
    run(chain, "открой ворд и напиши привет")
    assert ["ctrl", "n"] in chain.log["keys"]
    assert chain.log["typed"] == ["привет"]


def test_word_missing_falls_back_and_says_so(chain, monkeypatch):
    monkeypatch.setattr(automation, "app_path", lambda exe: None)
    r = run(chain, "открой ворд и напиши привет")
    assert chain.log["popen"] == [["notepad"]]
    assert "Word" in r.text and chain.log["typed"] == ["привет"]


def test_word_window_never_appears_nothing_is_typed(chain, monkeypatch):
    monkeypatch.setattr(automation, "wait_new_window", lambda *a, **k: None)
    r = run(chain, "открой ворд и напиши привет")
    assert chain.log["typed"] == []
    assert r.text


def test_open_word_without_typing_is_plain_open(chain):
    assert chain._doc_app("открой ворд", ["открой ворд", "открой эксель"]) is None
    assert chain._doc_app("открой ворд", ["открой ворд", "напиши привет"]) == "word"


# ---------------------------------------------------------------- Spotify
def test_spotify_find_song_only_searches(chain):
    r = run(chain, "открой спотифай и найди песню люблю")
    assert chain.log["startfile"] == ["spotify:search:" + "%D0%BB%D1%8E%D0%B1%D0%BB%D1%8E"]
    assert chain.log["play"] == 0
    assert "люблю" in r.text


def test_spotify_include_song_presses_play(chain):
    r = run(chain, "открой спотифай и включи песню люблю")
    assert chain.log["startfile"][0].startswith("spotify:search:")
    assert chain.log["play"] == 1
    assert "люблю" in r.text


def test_spotify_play_failure_is_reported_honestly(chain, monkeypatch):
    monkeypatch.setattr(automation, "spotify_press_play", lambda timeout=14.0: False)
    r = run(chain, "открой спотифай и включи песню люблю")
    assert "Нажмите" in r.text or "басыңыз" in r.text


def test_spotify_autoplay_can_be_turned_off(chain):
    chain.cfg["spotify_autoplay"] = False
    run(chain, "открой спотифай и включи песню люблю")
    assert chain.log["play"] == 0


@pytest.mark.parametrize("phrase,play", [
    ("включи песню люблю в спотифай", True), ("найди песню люблю в spotify", False),
    ("включи spotify песню change of tanks", True), ("в этом spotify ищи lil peep", False),
])
def test_single_phrase_spotify(chain, phrase, play):
    import actions as A
    chain.__dict__.pop("handle_one")     # здесь нужен настоящий разбор
    r = chain.handle_one(A.norm(phrase))
    assert r is not None and chain.log["startfile"]
    assert chain.log["play"] == (1 if play else 0)


# ---------------------------------------------------------------- любая другая программа
def test_unknown_app_then_typing_goes_to_its_new_window(chain):
    run(chain, "открой калькулятор2 и напиши привет")
    assert 111 in chain.log["focused"] and chain.log["typed"] == ["привет"]
