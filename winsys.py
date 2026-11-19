"""Небольшие системные возможности Windows: автозапуск, диагностика микрофона, громкость отдельных программ,
игры Steam, глобальные горячие клавиши, медиаклавиши, проверка интернета.

Идеи взяты из открытого проекта capri-desktop (см. NOTICE.md), реализация своя. Все функции, которые трогают
Windows, принимают подмену (reg=, user32=, sessions=), чтобы их можно было проверить без Windows.
"""
import ctypes
import logging
import os
import re
import socket
import sys
import threading
import time

log = logging.getLogger("kar")


# ======================================================================== автозапуск
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
AUTOSTART_NAME = "KAR"


def _winreg():
    import winreg
    return winreg


def autostart_command(main_py):
    """Команда запуска без консольного окна: pythonw.exe main.py."""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(main_py)}"'


def autostart_enabled(main_py, reg=None):
    """Включён ли запуск вместе с Windows: запись в Run есть, и диспетчер задач её не выключил."""
    w = reg or _winreg()
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, RUN_KEY) as key:
            value = str(w.QueryValueEx(key, AUTOSTART_NAME)[0])
    except OSError:
        return False
    if os.path.normcase(os.path.abspath(main_py)) not in os.path.normcase(value):
        return False  # в Run записано что-то другое (например, другая копия KAR)
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, APPROVED_KEY) as key:
            data = w.QueryValueEx(key, AUTOSTART_NAME)[0]
            return not data or not (data[0] & 1)  # нечётный первый байт — выключено в диспетчере задач
    except OSError:
        return True


def set_autostart(on, main_py, reg=None):
    """Включить/выключить запуск с Windows. Возвращает True, если получилось."""
    w = reg or _winreg()
    try:
        with w.CreateKey(w.HKEY_CURRENT_USER, RUN_KEY) as key:
            if on:
                w.SetValueEx(key, AUTOSTART_NAME, 0, w.REG_SZ, autostart_command(main_py))
            else:
                try:
                    w.DeleteValue(key, AUTOSTART_NAME)
                except OSError:
                    pass
        try:  # старая пометка «выключено» из диспетчера задач мешала бы новой записи
            with w.OpenKey(w.HKEY_CURRENT_USER, APPROVED_KEY, 0, w.KEY_SET_VALUE) as key:
                w.DeleteValue(key, AUTOSTART_NAME)
        except OSError:
            pass
        return True
    except OSError as e:
        log.info("Автозапуск: %s", e)
        return False


# ======================================================================== микрофон
MIC_CONSENT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def mic_privacy_blocked(reg=None):
    """Windows запрещает микрофон в «Параметры → Конфиденциальность»: на весь компьютер, для пользователя
    или для классических приложений (а KAR — классическое)."""
    try:
        w = reg or _winreg()
    except ImportError:
        return False

    def value(root, path):
        try:
            with w.OpenKey(root, path) as key:
                return str(w.QueryValueEx(key, "Value")[0]).lower()
        except OSError:
            return None
    return "deny" in (value(w.HKEY_LOCAL_MACHINE, MIC_CONSENT), value(w.HKEY_CURRENT_USER, MIC_CONSENT),
                      value(w.HKEY_CURRENT_USER, MIC_CONSENT + r"\NonPackaged"))


def diagnose_mic(error=None, reg=None):
    """Ключ фразы i18n с понятной причиной, почему микрофон не слышен."""
    if mic_privacy_blocked(reg):
        return "mic_blocked"
    text = str(error or "")
    if re.search(r"9985|unavailable|exclusive|in use|busy", text, re.I):
        return "mic_busy"
    if re.search(r"9996|9997|no (default )?(input )?device|invalid device|not found", text, re.I):
        return "no_mic"
    return "mic_error"


# ======================================================================== громкость отдельных программ
def app_sessions():
    """Программы, у которых есть свой ползунок в микшере Windows: [{'name', 'volume', 'muted', 'playing', 'raw'}].
    Программа появляется в микшере только после того, как издала звук."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    out = []
    for s in AudioUtilities.GetAllSessions():
        proc = s.Process
        if not proc:
            continue
        try:
            peak = s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue()
        except Exception:
            peak = 0.0
        vol = s.SimpleAudioVolume
        out.append({"name": re.sub(r"\.exe$", "", proc.name(), flags=re.I), "volume": round(vol.GetMasterVolume() * 100),
                    "muted": bool(vol.GetMute()), "playing": peak > 0.001, "raw": s})
    return out


def change_app_volume(matches, level=None, step=None, mute=None):
    """Изменить громкость найденных сессий: level — точное значение, step — на сколько сдвинуть, mute — вкл/выкл.
    Возвращает [(имя, итоговая громкость, выключен ли звук)]."""
    done = []
    for s in matches:
        vol = s["raw"].SimpleAudioVolume
        now = s["volume"]
        if level is not None:
            now = level
        elif step is not None:
            now = now + step
        now = max(0, min(100, now))
        if level is not None or step is not None:
            vol.SetMasterVolume(now / 100, None)
        if mute is not None:
            vol.SetMute(1 if mute else 0, None)
        elif now > 0 and (level is not None or step is not None) and s["muted"]:
            vol.SetMute(0, None)  # «громче» у заглушённой программы должно её включить
        done.append((s["name"], now, s["muted"] if mute is None else bool(mute)))
    return done


# ======================================================================== игры Steam
def steam_path(reg=None):
    try:
        w = reg or _winreg()
        with w.OpenKey(w.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return str(w.QueryValueEx(key, "SteamPath")[0]).replace("/", "\\")
    except (ImportError, OSError):
        return None


_STEAM_SKIP = re.compile(r"^(Steamworks Common Redistributables|Steam Linux Runtime\b.*|Proton\b.*)$", re.I)


def steam_apps(steam=None):
    """Установленные игры Steam: [(название, 'steam://rungameid/ID')]. В «Пуске» у многих игр ярлыка нет,
    поэтому читаем библиотеку Steam напрямую (appmanifest_*.acf)."""
    steam = steam or steam_path()
    if not steam or not os.path.isdir(steam):
        return []
    libraries = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as f:
            libraries += [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    except OSError:
        pass
    apps, seen = [], set()
    for lib in libraries:
        folder = os.path.join(lib, "steamapps")
        try:
            manifests = [f for f in os.listdir(folder) if f.startswith("appmanifest_") and f.endswith(".acf")]
        except OSError:
            continue
        for name in manifests:
            try:
                with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            app_id, title, state = (re.search(rf'"{k}"\s+"([^"]*)"', text) for k in ("appid", "name", "StateFlags"))
            if not app_id or not title or app_id.group(1) in seen:
                continue
            if state and state.group(1).isdigit() and not int(state.group(1)) & 4:
                continue  # не до конца установлена
            if _STEAM_SKIP.match(title.group(1)):
                continue
            seen.add(app_id.group(1))
            apps.append((title.group(1), "steam://rungameid/" + app_id.group(1)))
    return apps


# ======================================================================== медиаклавиши
VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2, "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD}


def press_key(vk, user32=None):
    """Нажать и отпустить клавишу. Медиаклавиши работают и без WinRT: их понимает любой плеер."""
    u = user32 or ctypes.windll.user32
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def media_key(action, user32=None):
    press_key(VK[action], user32)


# ======================================================================== глобальные горячие клавиши
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
DEFAULT_HOTKEYS = {"talk": "ctrl+alt+k", "mute": "ctrl+alt+m", "stop": "ctrl+alt+s", "hide": "ctrl+alt+h",
                   "type": "ctrl+alt+t"}


def parse_hotkey(text):
    """'ctrl+alt+k' → (модификаторы, код клавиши). ValueError, если сочетание непонятно."""
    mods, vk = 0, None
    for part in re.split(r"\s*\+\s*", (text or "").strip().lower()):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", part):
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"Неизвестная клавиша: {part!r}")
    if vk is None or not mods:
        raise ValueError("Нужна хотя бы одна клавиша-модификатор и одна обычная клавиша")
    return mods, vk


class GlobalHotkeys:
    """Горячие клавиши, которые работают из любого окна (RegisterHotKey) — со своим потоком сообщений."""

    def __init__(self, bindings, callback, user32=None):
        self.bindings, self.callback = bindings, callback
        self.u = user32 or ctypes.windll.user32
        self.ids = {}          # id → имя действия
        self.failed = []       # сочетания, которые уже заняты другой программой или записаны неверно
        self.thread_id = None
        self._ready = threading.Event()

    def register(self):
        for i, (name, combo) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse_hotkey(combo)
            except ValueError as e:
                log.info("Горячая клавиша %s: %s", name, e)
                self.failed.append(name)
                continue
            if self.u.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                self.ids[i] = name
            else:
                self.failed.append(name)  # занята другой программой
        return self.failed

    def handle(self, wparam):
        name = self.ids.get(int(wparam))
        if name:
            self.callback(name)

    def _loop(self):
        from ctypes import wintypes
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self.register()
        self._ready.set()
        msg = wintypes.MSG()
        while self.u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.handle(msg.wParam)
                except Exception:
                    log.exception("Горячая клавиша")
        for i in list(self.ids):
            self.u.UnregisterHotKey(None, i)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        self._ready.wait(3)

    def stop(self):
        if self.thread_id:
            self.u.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


# ======================================================================== интернет
def internet_status(timeout=2.0):
    """Как проверил бы человек: находит ли имена сайтов и доходит ли до публичного адреса.
    Возвращает 'ok' / 'dns' / 'off' и отклик в миллисекундах."""
    started = time.time()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo("www.google.com", 443)
        dns = True
    except OSError:
        dns = False
    try:
        t0 = time.time()
        with socket.create_connection(("1.1.1.1", 443), timeout=timeout):
            ms = round((time.time() - t0) * 1000)
        return ("ok" if dns else "dns"), ms
    except OSError:
        return "off", round((time.time() - started) * 1000)
