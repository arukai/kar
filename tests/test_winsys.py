"""Системные функции Windows, проверенные на подменах: реестр, микшер, Steam, горячие клавиши, интернет."""
import os
import socket
import sys

import pytest

import winsys


# ------------------------------------------------------------------ поддельный реестр
class FakeKey:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeReg:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_SET_VALUE, REG_SZ = 2, 1

    def __init__(self, data=None):
        self.data = data or {}   # (root, path) → {имя: значение}

    def _path(self, root, path):
        return (root, path.lower())

    def OpenKey(self, root, path, *a):
        if self._path(root, path) not in self.data:
            raise FileNotFoundError(path)
        return FakeKey(self, self._path(root, path))

    CreateKey = lambda self, root, path: self._create(root, path)  # noqa: E731

    def _create(self, root, path):
        self.data.setdefault(self._path(root, path), {})
        return FakeKey(self, self._path(root, path))

    def QueryValueEx(self, key, name):
        values = self.data[key.path]
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def SetValueEx(self, key, name, _r, _t, value):
        self.data[key.path][name] = value

    def DeleteValue(self, key, name):
        if name not in self.data[key.path]:
            raise FileNotFoundError(name)
        del self.data[key.path][name]


MAIN = os.path.abspath("main.py")


def test_autostart_enable_disable_roundtrip():
    reg = FakeReg()
    assert not winsys.autostart_enabled(MAIN, reg)
    assert winsys.set_autostart(True, MAIN, reg)
    assert winsys.autostart_enabled(MAIN, reg)
    value = reg.data[("HKCU", winsys.RUN_KEY.lower())]["KAR"]
    assert value.startswith('"') and value.endswith(f'"{MAIN}"')
    assert winsys.set_autostart(False, MAIN, reg)
    assert not winsys.autostart_enabled(MAIN, reg)


def test_autostart_respects_task_manager_switch_and_clears_it():
    reg = FakeReg()
    winsys.set_autostart(True, MAIN, reg)
    approved = ("HKCU", winsys.APPROVED_KEY.lower())
    reg.data[approved] = {"KAR": bytes([3]) + bytes(11)}      # «выключено» в диспетчере задач
    assert not winsys.autostart_enabled(MAIN, reg)
    winsys.set_autostart(True, MAIN, reg)                      # повторное включение снимает пометку
    assert winsys.autostart_enabled(MAIN, reg)
    reg.data[approved] = {"KAR": bytes([2]) + bytes(11)}      # «включено»
    assert winsys.autostart_enabled(MAIN, reg)


def test_autostart_ignores_other_copy_of_kar():
    reg = FakeReg({("HKCU", winsys.RUN_KEY.lower()): {"KAR": '"pythonw.exe" "D:\\\\other\\\\main.py"'}})
    assert not winsys.autostart_enabled(MAIN, reg)


def test_autostart_uses_pythonw(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Python312\python.exe")
    assert winsys.autostart_command("m.py").startswith('"C:\\Python312\\pythonw.exe"')


# ------------------------------------------------------------------ микрофон
CONSENT = winsys.MIC_CONSENT.lower()


@pytest.mark.parametrize("data,blocked", [
    ({}, False),
    ({("HKLM", CONSENT): {"Value": "Allow"}}, False),
    ({("HKLM", CONSENT): {"Value": "Deny"}}, True),
    ({("HKCU", CONSENT): {"Value": "Deny"}}, True),
    ({("HKCU", CONSENT + "\\nonpackaged"): {"Value": "Deny"}}, True),
    ({("HKCU", CONSENT): {"Value": "Allow"}, ("HKCU", CONSENT + "\\nonpackaged"): {"Value": "Allow"}}, False),
])
def test_mic_privacy(data, blocked):
    assert winsys.mic_privacy_blocked(FakeReg(data)) is blocked


@pytest.mark.parametrize("error,key", [
    ("Error opening InputStream: Device unavailable [PaErrorCode -9985]", "mic_busy"),
    ("Error querying device -1", "mic_error"),
    ("Invalid device [PaErrorCode -9996]", "no_mic"),
    (None, "mic_error"),
])
def test_diagnose_mic_by_error(error, key):
    assert winsys.diagnose_mic(error, FakeReg()) == key


def test_privacy_block_wins_over_other_errors():
    blocked = FakeReg({("HKLM", CONSENT): {"Value": "Deny"}})
    assert winsys.diagnose_mic("Device unavailable", blocked) == "mic_blocked"


# ------------------------------------------------------------------ громкость программ
class FakeVolume:
    def __init__(self, level=0.5, muted=False):
        self.level, self.muted = level, muted

    def GetMasterVolume(self):
        return self.level

    def GetMute(self):
        return self.muted

    def SetMasterVolume(self, v, _ctx):
        self.level = v

    def SetMute(self, m, _ctx):
        self.muted = bool(m)


class Raw:
    def __init__(self, **kw):
        self.SimpleAudioVolume = FakeVolume(**kw)


def session(name, level=50, muted=False):
    raw = Raw(level=level / 100, muted=muted)
    return {"name": name, "volume": level, "muted": muted, "playing": True, "raw": raw}


def test_change_app_volume_step_level_mute():
    a = session("Spotify", 50)
    assert winsys.change_app_volume([a], step=-15)[0][:2] == ("Spotify", 35)
    assert a["raw"].SimpleAudioVolume.level == pytest.approx(0.35)
    assert winsys.change_app_volume([a], level=130)[0][1] == 100       # потолок
    assert winsys.change_app_volume([a], step=-500)[0][1] == 0         # пол
    winsys.change_app_volume([a], mute=True)
    assert a["raw"].SimpleAudioVolume.muted


def test_louder_unmutes_a_muted_app():
    a = session("chrome", 40, muted=True)
    winsys.change_app_volume([a], step=15)
    assert not a["raw"].SimpleAudioVolume.muted


# ------------------------------------------------------------------ Steam
def test_steam_library_is_read(tmp_path):
    steam = tmp_path / "Steam"
    lib2 = tmp_path / "GamesLib"
    for lib, items in ((steam, [(730, "Counter-Strike 2", 4), (228980, "Steamworks Common Redistributables", 4),
                                (100, "Half Installed", 2)]), (lib2, [(440, "Team Fortress 2", 4)])):
        (lib / "steamapps").mkdir(parents=True)
        for app_id, name, flags in items:
            (lib / "steamapps" / f"appmanifest_{app_id}.acf").write_text(
                f'"AppState"\n{{\n\t"appid"\t\t"{app_id}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"{flags}"\n}}\n')
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "1"\n {\n  "path"\t\t"' + str(lib2).replace("\\", "\\\\") + '"\n }\n}\n')
    games = dict(winsys.steam_apps(str(steam)))
    assert games == {"Counter-Strike 2": "steam://rungameid/730", "Team Fortress 2": "steam://rungameid/440"}


def test_steam_missing_or_broken_is_empty(tmp_path):
    assert winsys.steam_apps(str(tmp_path / "nope")) == []
    broken = tmp_path / "Steam" / "steamapps"
    broken.mkdir(parents=True)
    (broken / "appmanifest_1.acf").write_text("не манифест")      # без appid и name
    assert winsys.steam_apps(str(tmp_path / "Steam")) == []


# ------------------------------------------------------------------ горячие клавиши
@pytest.mark.parametrize("combo,expected", [
    ("ctrl+alt+k", (winsys.MOD_CONTROL | winsys.MOD_ALT, ord("K"))),
    ("Ctrl + Shift + 5", (winsys.MOD_CONTROL | winsys.MOD_SHIFT, ord("5"))),
    ("win+f9", (winsys.MOD_WIN, 0x78)),
])
def test_parse_hotkey(combo, expected):
    assert winsys.parse_hotkey(combo) == expected


@pytest.mark.parametrize("combo", ["k", "ctrl+alt", "ctrl+alt+enter", "", "ctrl+f25"])
def test_parse_hotkey_rejects_bad(combo):
    with pytest.raises(ValueError):
        winsys.parse_hotkey(combo)


class FakeUser32:
    def __init__(self, busy=()):
        self.registered, self.busy = {}, set(busy)

    def RegisterHotKey(self, hwnd, i, mods, vk):
        if vk in self.busy:
            return 0
        self.registered[i] = (mods, vk)
        return 1


def test_hotkeys_register_dispatch_and_report_busy():
    u = FakeUser32(busy={ord("M")})
    fired = []
    hk = winsys.GlobalHotkeys({"talk": "ctrl+alt+k", "mute": "ctrl+alt+m", "bad": "oops"}, fired.append, user32=u)
    assert hk.register() == ["mute", "bad"]                 # занята другой программой / записана неверно
    assert all(m & winsys.MOD_NOREPEAT for m, _ in u.registered.values())
    hk.handle(1)
    hk.handle(99)                                            # чужой id игнорируется
    assert fired == ["talk"]


# ------------------------------------------------------------------ медиаклавиши и интернет
class FakeKeys:
    def __init__(self):
        self.events = []

    def keybd_event(self, vk, scan, flags, extra):
        self.events.append((vk, flags))


def test_media_key_presses_and_releases():
    u = FakeKeys()
    winsys.media_key("next", u)
    assert u.events == [(0xB0, 0), (0xB0, 2)]


def test_internet_status(monkeypatch):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: Conn())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("x",)])
    assert winsys.internet_status()[0] == "ok"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("dns")))
    assert winsys.internet_status()[0] == "dns"
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert winsys.internet_status()[0] == "off"


def test_media_control_falls_back_to_keys(monkeypatch):
    import media
    pressed = []
    monkeypatch.setattr(media, "_control_winrt", lambda *a, **k: (_ for _ in ()).throw(ImportError("no winrt")))
    monkeypatch.setattr(winsys, "media_key", pressed.append)
    assert media.control("pause") == (True, None, None)
    assert media.control("next") == (True, None, None)
    assert pressed == ["play_pause", "next"]
