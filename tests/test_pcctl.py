"""Возможности из capri-desktop: батарея, диск, тема, Wi-Fi, флешки, окна, очистка, автозапуск, перерывы."""
import os
import re
from types import SimpleNamespace as NS

import pytest

import pcctl


# ---------------------------------------------------------------- чистая логика
def test_human_size():
    assert pcctl.human_size(0) == "0 Б"
    assert pcctl.human_size(5 * 1024 ** 2) == "5 МБ"
    assert pcctl.human_size(int(1.5 * 1024 ** 3)) == "1,5 ГБ"
    assert pcctl.human_size(120 * 1024 ** 3) == "120 ГБ"


@pytest.mark.parametrize("what,uri", [("блютуз", "ms-settings:bluetooth"), ("обновления", "ms-settings:windowsupdate"),
                                      ("звука", None), ("звук", "ms-settings:sound"), ("вай фай", "ms-settings:network-wifi")])
def test_settings_page(what, uri):
    got = pcctl.settings_page(what)
    assert (got[0] if got else None) == uri or (uri is None)


def test_battery_info():
    assert pcctl.battery_info(NS(sensors_battery=lambda: None)) is None
    assert pcctl.battery_info(NS(sensors_battery=lambda: NS(percent=79.6, power_plugged=True))) == (80, True)


def test_offscreen():
    screen = (0, 0, 1920, 1080)
    assert not pcctl.is_offscreen((100, 100, 800, 600), screen)
    assert pcctl.is_offscreen((2500, 100, 3200, 600), screen)    # монитор отключили
    assert pcctl.is_offscreen((-1900, 100, -1000, 600), screen)


def test_radio_script_and_result():
    on = pcctl.radio_script("Bluetooth", True)
    assert "'Bluetooth'" in on and "SetStateAsync('On')" in on
    assert "SetStateAsync('Off')" in pcctl.radio_script("WiFi", False)
    with pytest.raises(ValueError):
        pcctl.radio_script("Cellular", True)
    assert pcctl.radio_result("x\nOK\n") == "ok" and pcctl.radio_result("NONE") == "none" and pcctl.radio_result("") == "fail"


def test_removable_and_eject_script():
    parts = [NS(device="C:\\", opts="rw,fixed"), NS(device="E:\\", opts="rw,removable"), NS(device="E:\\", opts="removable")]
    assert pcctl.removable_drives(parts) == ["E"]
    assert 'ParseName("E:")' in pcctl.eject_script("e")
    with pytest.raises(ValueError):
        pcctl.eject_script("1")


def test_parse_scheme():
    out = "GUID схемы питания: 381b4222-f694-41f0-9685-ff5bb260df2e  (Сбалансированная)"
    assert pcctl.parse_scheme(out) == "381b4222-f694-41f0-9685-ff5bb260df2e"
    assert pcctl.parse_scheme("") is None


def test_old_files_and_delete(tmp_path):
    old, new = tmp_path / "old.tmp", tmp_path / "new.tmp"
    old.write_bytes(b"x" * 100)
    new.write_bytes(b"y" * 50)
    day = 86400
    os.utime(old, (1000, 1000))
    found = pcctl.old_files(str(tmp_path), days=2, now=1000 + 3 * day)
    assert [os.path.basename(p) for p, _ in found] == ["old.tmp"]
    assert pcctl.delete_files(found) == (1, 100)
    assert new.exists() and not old.exists()


# ---------------------------------------------------------------- автозапуск (реестр подменён)
class FakeReg:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, REG_BINARY = "HKCU", "HKLM", 1, 3

    def __init__(self):
        self.data = {("HKCU", pcctl.RUN_KEY): {"Discord": "d.exe", "Steam": "s.exe"},
                     ("HKLM", pcctl.RUN_KEY): {"Realtek": "r.exe"},
                     ("HKCU", pcctl.APPROVED_KEY): {"Steam": bytes([3]) + bytes(11)}}

    class _Key:
        def __init__(self, vals): self.vals = list(vals.items())
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def OpenKey(self, root, path, res=0, access=0):
        if (root, path) not in self.data:
            raise OSError
        return self._Key(self.data[(root, path)])

    CreateKey = OpenKey

    def EnumValue(self, key, i):
        if i >= len(key.vals):
            raise OSError
        n, v = key.vals[i]
        return n, v, 1

    def SetValueEx(self, key, name, _, kind, value):
        self.data[("HKCU", pcctl.APPROVED_KEY)][name] = value


def test_startup_entries_find_and_toggle():
    reg = FakeReg()
    entries = pcctl.startup_entries(reg)
    by = {e["name"]: e for e in entries}
    assert by["Discord"]["enabled"] and not by["Steam"]["enabled"] and by["Realtek"]["scope"] == "machine"
    assert pcctl.find_startup(entries, "дискорд") is None
    assert pcctl.find_startup(entries, "discord")["name"] == "Discord"
    pcctl.set_startup_enabled(reg, "Discord", False)
    assert reg.data[("HKCU", pcctl.APPROVED_KEY)]["Discord"][0] == 3
    assert {e["name"]: e["enabled"] for e in pcctl.startup_entries(reg)}["Discord"] is False


# ---------------------------------------------------------------- напоминания об отдыхе
def make_watcher(idle_now, enabled=True, busy=False):
    state = {"t": 0.0, "idle": idle_now, "said": []}
    w = pcctl.BreakWatcher(lambda worked: state["said"].append(worked), enabled=lambda: enabled,
                           idle=lambda: state["idle"], busy=lambda: busy, clock=lambda: state["t"],
                           after_minutes=120, rest_minutes=5, repeat_minutes=30)
    return w, state


def test_break_after_two_hours_then_not_too_often():
    w, st = make_watcher(0)
    w.tick()                                  # начали работать
    st["t"] = 119 * 60
    assert not w.tick()
    st["t"] = 121 * 60
    assert w.tick() and len(st["said"]) == 1
    st["t"] = 140 * 60
    assert not w.tick()                       # ещё не прошло полчаса
    st["t"] = 152 * 60
    assert w.tick()


def test_real_break_resets_counter():
    w, st = make_watcher(0)
    w.tick()
    st["t"] = 100 * 60
    st["idle"] = 6 * 60                       # отходил на 6 минут
    w.tick()
    st["idle"] = 0
    st["t"] = 101 * 60
    w.tick()
    st["t"] = 201 * 60                        # со времени перерыва прошло только 100 минут
    assert not w.tick()


def test_no_nag_when_disabled_or_fullscreen():
    for kw in ({"enabled": False}, {"busy": True}):
        w, st = make_watcher(0, **kw)
        w.tick()
        st["t"] = 200 * 60
        assert not w.tick() and st["said"] == []


# ---------------------------------------------------------------- обработчики
def call(actions, phrase):
    import actions as A
    c = A.norm(phrase)
    for pattern, fn in actions.routes:
        m = re.search(pattern, c)
        if m:
            return fn(c, m)


def test_battery_and_ram_and_disk_replies(actions, monkeypatch):
    import actions as A
    monkeypatch.setattr(A.psutil, "sensors_battery", lambda: NS(percent=64, power_plugged=False), raising=False)
    assert "64%" in call(actions, "сколько заряда батареи").text
    monkeypatch.setattr(A.psutil, "virtual_memory", lambda: NS(available=4 * 1024 ** 3, total=16 * 1024 ** 3, percent=75))
    r = call(actions, "сколько оперативной памяти")
    assert "4 ГБ" in r.text and "16 ГБ" in r.text and "75%" in r.text
    monkeypatch.setattr(A.psutil, "disk_usage", lambda p: NS(free=50 * 1024 ** 3, total=200 * 1024 ** 3))
    r = call(actions, "сколько места на диске д")
    assert "D" in r.text and "50 ГБ" in r.text


def test_theme_toggle_writes_registry(actions, monkeypatch):
    import winsys
    writes = {}
    reg = FakeReg()
    reg.REG_DWORD = 4
    reg.data[("HKCU", pcctl.THEME_KEY)] = {"AppsUseLightTheme": 1}
    reg.SetValueEx = lambda key, name, _, kind, value: writes.__setitem__(name, value)
    monkeypatch.setattr(winsys, "_winreg", lambda: reg)
    monkeypatch.setattr(pcctl, "set_light_theme", lambda r, light, user32=None: writes.update(light=light))
    r = call(actions, "включи темную тему")
    assert writes["light"] is False and "тёмная" in r.text.lower() or "қараңғы" in r.text.lower()


def test_radio_reports_missing_and_ok(actions, monkeypatch):
    import actions as A
    monkeypatch.setattr(A, "run_ps", lambda cmd, timeout=60: "NONE")
    assert call(actions, "включи блютуз").action == "fail"
    monkeypatch.setattr(A, "run_ps", lambda cmd, timeout=60: "OK")
    assert call(actions, "выключи вайфай").action is None


def test_cleanup_and_update_need_confirmation(actions, monkeypatch, tmp_path):
    import actions as A
    monkeypatch.setenv("TEMP", str(tmp_path))
    (tmp_path / "a.tmp").write_bytes(b"x" * 10)
    os.utime(tmp_path / "a.tmp", (1000, 1000))
    monkeypatch.setattr(A, "run_ps", lambda cmd, timeout=60: "")
    r = call(actions, "освободи место")
    assert r.action == "ask" and actions.pending and (tmp_path / "a.tmp").exists()   # пока ничего не удалено
    actions.pending()
    assert not (tmp_path / "a.tmp").exists()
    monkeypatch.setattr(A.shutil, "which", lambda n: None)
    assert call(actions, "обнови программы").action == "fail"


def test_snap_and_keys(actions, monkeypatch):
    import actions as A
    keys = []
    monkeypatch.setattr(A, "send_keys", lambda k: keys.append(list(k)))
    call(actions, "прижми окно влево")
    call(actions, "окно вправо")
    call(actions, "скриншот области")
    call(actions, "запиши экран")
    assert keys == [["win", "left"], ["win", "right"], ["win", "shift", "s"], ["win", "alt", "r"]]


def test_startup_commands(actions, monkeypatch):
    import winsys
    reg = FakeReg()
    monkeypatch.setattr(winsys, "_winreg", lambda: reg)
    assert "Discord" in call(actions, "что запускается с windows").text
    assert call(actions, "убери discord из автозапуска").action is None
    assert reg.data[("HKCU", pcctl.APPROVED_KEY)]["Discord"][0] == 3
    assert call(actions, "убери realtek из автозапуска").action == "fail"   # для всех пользователей — нужен админ
    assert call(actions, "убери скайп из автозапуска").action == "fail"


def test_breaks_toggle_saves_setting(actions):
    saved = {}
    actions.save_setting = lambda k, v: saved.__setitem__(k, v)
    call(actions, "выключи напоминания об отдыхе")
    assert saved == {"health_breaks": False} and actions.cfg["health_breaks"] is False
