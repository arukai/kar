"""Тексты: во всех ключах есть оба языка и совпадают подстановки."""
import re

import i18n


def test_same_keys_in_both_languages():
    assert set(i18n.TEXT["ru"]) == set(i18n.TEXT["kk"])


def test_same_placeholders():
    bad = []
    for key, ru in i18n.TEXT["ru"].items():
        kk = i18n.TEXT["kk"][key]
        if isinstance(ru, list) or isinstance(kk, list):
            continue
        if set(re.findall(r"\{(\w+)", ru)) != set(re.findall(r"\{(\w+)", kk)):
            bad.append(key)
    assert not bad, bad


def test_every_key_used_in_code_exists():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    used = set()
    for name in ("actions.py", "automation.py", "media.py", "main.py", "ui.py", "entries_dialog.py"):
        src = open(os.path.join(root, name), encoding="utf-8").read()
        used |= set(re.findall(r'\b(?:line|t|T)\(\s*"([a-z_0-9]+)"', src))
    used = {k for k in used if not k.endswith("_")}   # «st_» + состояние собирается в коде
    used |= {"scroll_end", "scroll_top", "sent", "typed", "no_mic", "model_broken"}
    used |= {"st_" + s for s in ("idle", "listening", "processing", "speaking", "error", "off")}
    missing = sorted(k for k in used if k not in i18n.TEXT["ru"])
    assert not missing, missing


def test_formats_do_not_crash():
    i18n.set_lang("kk")
    assert "Алматы" in i18n.t("weather", city="Алматы", temp="+18", desc="ашық", wind="3")
    assert i18n.dur(3725) == "1 сағ 2 мин 5 сек"
    i18n.set_lang("ru")
    assert i18n.dur(90) == "1 мин 30 сек"
    assert i18n.weather_text(61) == "дождь"
