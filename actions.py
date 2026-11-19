"""Команды: сайты, приложения, громкость, яркость, диагностика ПК и т.д."""
import ctypes
import datetime
import difflib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass

import psutil

import automation
import media
import finder
import i18n
import kazakh
import notes
import pcctl
import reminders
import winsys
from i18n import line, lst, dur, t as T

log = logging.getLogger("kar")
NO_WINDOW = 0x08000000


@dataclass
class Reply:
    text: str
    action: str | None = None    # "hide" / "quit"


def norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s\.\-:/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


TR = dict(zip("абвгдезийклмнопрстуфхцы", "abvgdeziyklmnoprstufhcy"))
TR.update({"ё": "e", "ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ь": "", "э": "e",
           "ю": "yu", "я": "ya"})


def translit(s):
    return "".join(TR.get(c, c) for c in s)


NUM = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
       "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
       "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
       "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
       "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
       "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}
NUM.update(kazakh.NUMBERS_RAW)   # казахские числительные: «дыбыс елу»
NUM.update(kazakh.NUMBERS)


def parse_number(text):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total = None
    for w in text.split():
        if w in NUM:
            total = (total or 0) + NUM[w]
        elif total is not None:
            break
    if total is not None:
        return total
    if re.search(r"максимум|на полную|на всю", text):
        return 100
    if re.search(r"половин", text):
        return 50
    if re.search(r"минимум", text):
        return 0
    return None


def run_ps(cmd, timeout=60):
    full = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$ErrorActionPreference='SilentlyContinue'; " + cmd)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


def ps_json(cmd):
    out = run_ps(cmd + " | ConvertTo-Json -Depth 3 -Compress")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


# ---------------- громкость / яркость ----------------

def _endpoint():
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume():
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(v):
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(max(0, min(100, v)) / 100, None)
    if v > 0:
        ep.SetMute(0, None)


def set_mute(on):
    _endpoint().SetMute(1 if on else 0, None)


def get_brightness():
    import screen_brightness_control as sbc
    return sbc.get_brightness()[0]


def set_brightness(v):
    import screen_brightness_control as sbc
    sbc.set_brightness(max(0, min(100, v)))


# ---------------- поиск приложений ----------------

PROTECTED_PROCS = {"explorer", "python", "pythonw", "svchost", "csrss", "winlogon", "dwm", "lsass", "services",
                   "system", "smss", "wininit", "fontdrvhost", "sihost", "taskhostw", "ctfmon", "runtimebroker",
                   "searchhost", "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "ollama",
                   "ollama app", "conhost", "audiodg"}
SKIP_WORDS = ("uninstall", "удален", "удалить", "деинсталл", "readme", "help", "справка", "documentation",
              "license", "support", "поддержк", "feedback", "release notes", "website")


# как браузеры называют вслух → по чему узнать в списке браузеров Windows (название ключа или exe)
BROWSER_WORDS = [
    (r"(гугл )?хром\w*|chrome", "chrome"),
    (r"яндекс\w*|yandex", "yandex"),
    (r"опер\w*|opera", "opera"),
    (r"(фаер|файр|файер)фокс\w*|мозил+\w*|firefox", "firefox"),
    (r"(майкрософт )?эдж\w*|edge", "edge"),
    (r"брейв\w*|brave", "brave"),
    (r"вивальди|vivaldi", "vivaldi"),
]
BROWSER_NAMES = {"chrome": "Google Chrome", "yandex": "Яндекс Браузер", "opera": "Opera", "firefox": "Firefox",
                 "edge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi"}
# «… в хроме», «… через яндекс браузер», «… в браузере опера» — только в конце фразы
BROWSER_SUFFIX = re.compile(r"\s+(в|через)\s+(браузер\w*\s+)?(?P<b>" + "|".join(p for p, _ in BROWSER_WORDS)
                            + r")(\s+браузер\w*)?$")


def installed_browsers():
    """{"chrome": путь к exe, ...} — браузеры из списка Windows («Приложения по умолчанию»)."""
    import winreg
    found = {}
    for hive, path in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet")):
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            try:
                cmd = winreg.QueryValue(key, name + r"\shell\open\command")
            except OSError:
                continue
            m = re.match(r'^\s*"?([^"]+?\.exe)', cmd)
            if not m or not os.path.isfile(m.group(1)):
                continue
            label = (name + " " + os.path.basename(m.group(1))).lower()
            for _, k in BROWSER_WORDS:
                if k in label or (k == "edge" and "msedge" in label):
                    found.setdefault(k, m.group(1))
    return found


def find_browser(name):
    """«хром» / «яндекс браузер» / путь к exe → путь к exe браузера или None, если такого нет."""
    if not name:
        return None
    if os.path.isfile(name):
        return name
    n = norm(name)
    for pattern, k in BROWSER_WORDS:
        if re.fullmatch(rf"({pattern})( браузер\w*)?|браузер\w* ({pattern})", n):
            return installed_browsers().get(k)
    return None


def installed_programs():
    """[(название, путь к exe)] из «Программ и компонентов» Windows: значок программы обычно и есть её exe."""
    import winreg
    out = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
            except OSError:
                continue
            values = {}
            for field in ("DisplayIcon", "InstallLocation", "UninstallString"):
                try:
                    values[field] = str(winreg.QueryValueEx(sub, field)[0])
                except OSError:
                    values[field] = ""
            exe = re.sub(r",\s*-?\d+$", "", values["DisplayIcon"]).strip().strip('"')
            base = os.path.basename(exe).lower()
            if exe.lower().endswith(".exe") and "unins" not in base and "setup" not in base and os.path.isfile(exe):
                out.append((norm(name), exe))
                continue
            # значок — деинсталлятор (так у Steam): ищем рядом exe с названием программы, steam → steam.exe
            folder = values["InstallLocation"].strip().strip('"')
            if not os.path.isdir(folder):
                m = re.match(r'^"?([^"]+?\.exe)', values["UninstallString"] or exe)
                folder = os.path.dirname(m.group(1)) if m else ""
            guess = os.path.join(folder, re.sub(r"[^\w\-]", "", name) + ".exe") if folder else ""
            if guess and os.path.isfile(guess):
                out.append((norm(name), guess))
    return out


class AppIndex:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()

    def build(self):
        items = {}
        try:
            for app in ps_json("Get-StartApps | Select-Object Name,AppID"):
                name = norm(app.get("Name") or "")
                if name and not any(w in name for w in SKIP_WORDS):
                    items[name] = ("appid", app.get("AppID"))
        except Exception:
            log.exception("Get-StartApps")
        home = os.path.expanduser("~")
        dirs = [os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop"),
                os.path.join(os.environ.get("PUBLIC", ""), "Desktop")]
        for d in dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith((".lnk", ".url", ".appref-ms")):
                        name = norm(os.path.splitext(f)[0])
                        if name and not any(w in name for w in SKIP_WORDS):
                            # ярлык надёжнее идентификатора из Get-StartApps: у программ вроде Discord
                            # (установщик Squirrel) запуск по идентификатору ничего не открывает
                            if items.get(name, ("appid",))[0] != "file":
                                items[name] = ("file", os.path.join(root, f))
        # установленные программы из реестра — на случай, если ярлыка в «Пуске» нет (бывает у Steam)
        for name, exe in installed_programs():
            if name not in items and not any(w in name for w in SKIP_WORDS):
                items[name] = ("file", exe)
        try:  # игры Steam: ярлыка в «Пуске» у многих нет
            for title, url in winsys.steam_apps():
                name = norm(title)
                if name and name not in items and not any(w in name for w in SKIP_WORDS):
                    items[name] = ("file", url)
        except Exception:
            log.exception("Игры Steam")
        with self.lock:
            self.items = items
        self.ready.set()
        log.info("Найдено приложений: %d", len(items))

    @staticmethod
    def _sound(s):
        s = re.sub(r"\s", "", translit(s.lower()))
        for a, b in (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("w", "v"), ("x", "ks"), ("z", "s")):
            s = s.replace(a, b)
        return s

    @staticmethod
    def _skeleton(s):
        """Согласные без гласных: «cloud», «claude», «клод» → «kld». Ловит созвучные названия."""
        return re.sub(r"[aeiouy]", "", AppIndex._sound(s))

    @staticmethod
    def sounds_like(a, b):
        """Созвучны, если совпадают согласные и первый звук: «роблокс» = «roblox», но «запрет» ≠ «aseprite»."""
        sk = AppIndex._skeleton(a)
        return len(sk) >= 3 and sk == AppIndex._skeleton(b) and AppIndex._sound(a)[:1] == AppIndex._sound(b)[:1]

    def candidates(self, query, aliases, limit=4):
        """Подходящие приложения, лучшие первыми: [(оценка, название, цель)]."""
        q = norm(query)
        variants = {q, translit(q)}
        if q in aliases:
            variants.add(norm(aliases[q]))
        with self.lock:
            items = list(self.items.items())
        scored = {}
        for name, target in items:
            best = 0.0
            for v in variants:
                if v == name:
                    score = 1.0
                elif len(v) >= 3 and re.search(r"(^|\s)" + re.escape(v), name):
                    score = 0.93 - 0.003 * len(name)
                else:
                    score = difflib.SequenceMatcher(None, v, name).ratio()
                    first = name.split()[0] if name.split() else name
                    if self.sounds_like(v, name):
                        score = max(score, 0.97)  # звучит как всё название: «роблокс» = «roblox»
                    elif self.sounds_like(v, first):
                        score = max(score, 0.86)  # звучит так же: «клод/cloud» ~ «claude»
                best = max(best, score)
            if best >= 0.72 and best > scored.get(name, (0,))[0]:
                scored[name] = (best, target)
        ranked = sorted(((s, n, t) for n, (s, t) in scored.items()), key=lambda x: -x[0])
        return ranked[:limit]

    def find(self, query, aliases):
        found = self.candidates(query, aliases, 1)
        return (found[0][1], found[0][2]) if found else None

    @staticmethod
    def launch(target):
        kind, value = target
        if kind == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"], creationflags=NO_WINDOW)
            return
        os.startfile(value)

    def launch_best(self, query, options):
        """Запустить лучший вариант; если Windows его не открыла (например, Roblox.lnk — «операция
        отменена»), пробуем похожие ярлыки с тем же началом (Roblox Player), а в конце — через проводник,
        как при двойном клике. options: [(оценка, имя, цель)]. Возвращает имя запущенного или None."""
        first = options[0]
        q = first[1].split()[0] if first[1].split() else ""  # «roblox» из найденного «roblox», а не «роблокс»
        with self.lock:
            similar = [(1.0, n, t) for n, t in self.items.items() if n != first[1] and q and n.startswith(q)
                       and not re.search(r"studio|uninstall|update", n)]
        for _, name, target in [first] + sorted(similar, key=lambda x: len(x[1])):
            try:
                self.launch(target)
                return name
            except OSError as e:
                log.info("%s не запустился (%s)", name, e)
        if first[2][0] == "file":
            subprocess.Popen(["explorer.exe", first[2][1]], creationflags=NO_WINDOW)
            return first[1]
        return None


# ---------------- диагностика ----------------

def _dir_size(path, limit=200000):
    total, n = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            n += 1
            if n > limit:
                return total
    return total


def analyze_pc():
    """Возвращает (список проблем, полный текстовый отчёт)."""
    issues, lines = [], []
    add = lines.append
    now = datetime.datetime.now()
    add(T("rep_title", d=f"{now:%d.%m.%Y %H:%M}"))
    add("=" * 60)

    cpu = psutil.cpu_percent(interval=1.5)
    mem = psutil.virtual_memory()
    add(T("rep_cpu", v=cpu, n=psutil.cpu_count()))
    add(T("rep_mem", p=mem.percent, u=mem.used / 2**30, t=mem.total / 2**30))
    if cpu > 85:
        issues.append(T("is_cpu", v=cpu))
    if mem.percent > 85:
        issues.append(T("is_mem", v=mem.percent))

    up = now - datetime.datetime.fromtimestamp(psutil.boot_time())
    add(T("rep_uptime", d=up.days, h=up.seconds // 3600))
    if up.days >= 7:
        issues.append(T("is_uptime", d=up.days))

    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        add(T("rep_battery", p=bat.percent, c=T("rep_charging") if bat.power_plugged else ""))

    add("\n" + T("rep_disks"))
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        free_gb = u.free / 2**30
        add(T("rep_disk_row", m=part.mountpoint, fs=part.fstype, f=free_gb, t=u.total / 2**30, p=100 - u.percent))
        if u.percent > 90 or free_gb < 10:
            issues.append(T("is_disk", l=part.mountpoint[0], g=free_gb))

    for d in ps_json("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus"):
        add(T("rep_drive", n=d.get("FriendlyName"), h=d.get("HealthStatus"), s=d.get("OperationalStatus")))
        if str(d.get("HealthStatus")) not in ("Healthy", "0"):
            issues.append(T("is_health", n=d.get("FriendlyName")))

    tmp = os.environ.get("TEMP", "")
    if tmp:
        size = _dir_size(tmp) / 2**30
        add(T("rep_temp", v=size))
        if size > 3:
            issues.append(T("is_temp", v=size))

    add("\n" + T("rep_dev_h"))
    bad = ps_json("Get-PnpDevice -PresentOnly | Where-Object {$_.Status -eq 'Error'} | Select-Object FriendlyName,Class")
    for d in bad:
        add(f"  {d.get('FriendlyName')} ({d.get('Class')})")
    if not bad:
        add(T("rep_none"))
    else:
        issues.append(T("is_dev", n=len(bad)))

    add("\n" + T("rep_events_h"))
    groups = ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} | Group-Object ProviderName | "
                     "Sort-Object Count -Descending | Select-Object -First 12 Count,Name")
    total_err = sum(int(g.get("Count", 0)) for g in groups)
    for g in groups:
        add(f"  {g.get('Count'):>4}  {g.get('Name')}")
    if not groups:
        add(T("rep_none"))
    if total_err > 30:
        issues.append(T("is_events", n=total_err))

    add("\n" + T("rep_last_h"))
    for e in ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} -MaxEvents 8 | Select-Object "
                     "@{n='T';e={$_.TimeCreated.ToString('dd.MM HH:mm')}},ProviderName,Id,"
                     "@{n='M';e={if($_.Message){$_.Message.Substring(0,[Math]::Min(220,$_.Message.Length))}}}"):
        msg = re.sub(r"\s+", " ", str(e.get("M") or ""))
        add(f"  [{e.get('T')}] {e.get('ProviderName')} #{e.get('Id')}: {msg}")

    crashes = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=41;"
                     "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    bsod = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=1001;"
                  "ProviderName='Microsoft-Windows-WER-SystemErrorReporting';"
                  "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    crashes = int(crashes) if crashes.isdigit() else 0
    bsod = int(bsod) if bsod.isdigit() else 0
    add("\n" + T("rep_crash", c=crashes, b=bsod))
    if crashes:
        issues.append(T("is_crash", n=crashes))
    if bsod:
        issues.append(T("is_bsod", n=bsod))

    add("\n" + T("rep_top_h"))
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            pass
    for rss, name in sorted(procs, reverse=True)[:8]:
        add("  " + T("rep_mb", v=rss / 2**20, n=name))

    add("\n" + T("rep_startup_h"))
    for s in ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location"):
        add(f"  {s.get('Name')}  [{s.get('Location')}]")

    add("\n" + "=" * 60)
    add(T("rep_issues_h") if issues else T("rep_ok"))
    for i in issues:
        add(f"  • {i}")
    return issues, "\n".join(lines)


# ---------------- ютуб ----------------

BROWSERS = ("firefox", "chrome", "msedge", "opera", "browser", "brave", "vivaldi", "yandex")


def browser_playing():
    """True, если из браузера сейчас реально идёт звук (по индикатору громкости в микшере Windows)."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    for s in AudioUtilities.GetAllSessions():
        try:
            name = (s.Process.name() if s.Process else "").lower()
            if any(b in name for b in BROWSERS):
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                if meter.GetPeakValue() > 0.001:
                    return True
        except Exception:
            continue
    return False


def youtube_window():
    """Окно браузера с открытым YouTube (по заголовку активной вкладки)."""
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            if "youtube" in buf.value.lower():
                found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


_ensure_token = [0]  # номер последней проверки «заиграло ли видео»; новая команда отменяет старую


def cancel_ensure():
    _ensure_token[0] += 1


def ensure_playing(wait_load=7.0):
    """Браузеры блокируют автозапуск со звуком у вкладок, открытых программой. Ждём звук;
    если тишина — переключаемся на окно YouTube и жмём K (пуск/пауза в плеере), затем проверяем.
    Если за это время пришла новая команда — тихо выходим, чтобы не кликнуть посреди её работы."""
    _ensure_token[0] += 1
    token = _ensure_token[0]
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    deadline = time.time() + wait_load
    while time.time() < deadline:  # может, запустилось само (или идёт реклама)
        if _ensure_token[0] != token:
            return
        if browser_playing():
            log.info("Видео играет само")
            return
        time.sleep(0.5)
    if _ensure_token[0] != token:
        return
    hwnd = None
    for _ in range(10):  # заголовок вкладки может ещё не смениться на «… - YouTube»
        hwnd = youtube_window()
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        log.info("Окно YouTube не найдено, не нажимаю пуск")
        return
    u = ctypes.windll.user32
    u.keybd_event(0x12, 0, 0, 0)       # «пустой» Alt — без него Windows не даёт сменить активное окно
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.4)

    # Клик мышью по плееру — настоящее действие пользователя: и окно активирует, и фокус ставит в плеер,
    # и браузер считает это разрешением на звук. Точка (30% ширины, 35% высоты) попадает в плеер
    # и в обычном, и в широком режиме YouTube.
    from ctypes import wintypes
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    x, y = r.left + int((r.right - r.left) * 0.30), r.top + int((r.bottom - r.top) * 0.35)
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    u.SetCursorPos(x, y)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # левая кнопка вниз
    u.mouse_event(0x0004, 0, 0, 0, 0)  # вверх
    time.sleep(0.05)
    u.SetCursorPos(old.x, old.y)
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено кликом по плееру")
            return
    send_keys(["k"])  # запасной вариант: фокус уже в плеере
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено клавишей K")
            return
    log.info("Не удалось запустить видео: звука нет")

def youtube_first(query):
    """(id, название) первого обычного видео (не шортс) в поиске YouTube или None."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:
        log.info("Поиск на YouTube не удался: %s", e)
        return None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page)
    if not m:
        return None
    title = ""
    t = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', page[m.end():m.end() + 6000])
    if t:
        try:
            title = json.loads(f'"{t.group(1)}"')
        except ValueError:
            title = t.group(1)
    return m.group(1), title


_desc_cache = {}


def file_description(path):
    """Описание программы из свойств exe-файла (то, что показывает диспетчер задач),
    например «Steam Client WebHelper». Пустая строка, если описания нет."""
    if not path:
        return ""
    if path in _desc_cache:
        return _desc_cache[path]
    desc = ""
    try:
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if v.GetFileVersionInfoW(path, 0, size, buf):
                ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
                if v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                    lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
                    key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription"
                    sptr = ctypes.c_wchar_p()
                    if v.VerQueryValueW(buf, key, ctypes.byref(sptr), ctypes.byref(ln)) and ln.value:
                        desc = sptr.value or ""
    except Exception:
        pass
    _desc_cache[path] = desc
    return desc


def stem_name(name):
    """«с мамой» → «мам», «шахтеров» → «шахтер»: поиск Telegram ищет по началу слова, а падежные
    окончания из речи мешают найти «Мама» или «Шахтёры»."""
    out = []
    for w in name.split():
        if len(w) >= 5:
            s = re.sub(r"(ами|ями|ого|его|ому|ему|ов|ев|ей|ой|ом|ем|ым|им|ою|ею|ах|ях|ам|ям|ую|юю|ая|яя|ые|ие|"
                       r"ы|и|а|я|у|ю|е)$", "", w)
            w = s if len(s) >= 3 else w
        out.append(w)
    return " ".join(out)


def youtube_search_in_tab(text):
    """Поиск на YouTube в текущей вкладке через адресную строку (Ctrl+L): срабатывает, даже если страница
    ещё грузится и поле поиска не в фокусе."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    before = automation.window_title(hwnd)
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(text)
    for attempt in range(2):
        # адрес вводится, только если адресная строка действительно получила фокус (иначе буквы ушли бы
        # в страницу YouTube, где это горячие клавиши)
        if not media.navigate(hwnd, url):
            time.sleep(0.6)
            continue
        for _ in range(10):  # ждём, что вкладка ушла на страницу результатов
            time.sleep(0.4)
            title = automation.window_title(hwnd)
            if title != before and "youtube" in title.lower() and title.lower().startswith(text.lower()[:4]):
                return True
        log.info("Поиск на YouTube не сработал с %d-й попытки, заголовок: %s", attempt + 1, title)
        if not automation.is_foreground(hwnd):
            break  # окно потеряло фокус — второй раз вслепую не печатаем
    return False


def wiki_summary(query, sentences=2):
    """Первые предложения статьи русской Википедии по запросу, подготовленные для озвучки. None — не нашлось."""
    headers = {"User-Agent": "KarAssistant/1.0 (personal voice assistant)"}

    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        found = get("https://" + i18n.lang() + ".wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=1&srsearch="
                    + urllib.parse.quote(query))["query"]["search"]
        if not found:
            return None
        page = get("https://" + i18n.lang() + ".wikipedia.org/api/rest_v1/page/summary/"
                   + urllib.parse.quote(found[0]["title"].replace(" ", "_")))
    except Exception as e:
        log.info("Википедия недоступна: %s", e)
        return None
    if page.get("type") == "disambiguation":
        return None
    text = page.get("extract", "").replace("́", "")  # знаки ударения
    text = re.sub(r"\s*\([^()]*\)", "", text)       # транскрипции, даты и пр. в скобках — не для озвучки
    text = re.sub(r"\s*\[[^\]]*\]", "", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁӘҒҚҢӨҰҮҺІA-Z])", text.strip())
    out = " ".join(parts[:sentences]).strip()
    if len(out) > 300:
        out = out[:300].rsplit(" ", 1)[0] + "…"
    return out or None


# ---------------- папки ----------------

SHELL_FOLDERS = {  # имена значений в реестре User Shell Folders (учитывают перенос в OneDrive)
    "desktop": "Desktop", "documents": "Personal", "pictures": "My Pictures", "music": "My Music",
    "videos": "My Video", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}
KNOWN_FOLDERS = [  # (регулярка по названию, ключ папки или shell:-путь, как назвать вслух)
    (r"фото\w*|фотк\w*|изображени\w*|картинк\w*|снимк\w*", "pictures", "Изображения"),
    (r"документ\w*", "documents", "Документы"),
    (r"загрузк\w*|скачанн\w*|закачк\w*", "downloads", "Загрузки"),
    (r"рабоч\w+ стол\w*", "desktop", "Рабочий стол"),
    (r"музык\w*", "music", "Музыка"),
    (r"видео\w*", "videos", "Видео"),
    (r"скриншот\w*", "screenshots", "Скриншоты"),
    (r"(этот|мой) компьютер|компьютер", "shell:MyComputerFolder", "Этот компьютер"),
    (r"корзин\w*", "shell:RecycleBinFolder", "Корзина"),
]
DRIVE_LETTERS = {"ц": "C", "си": "C", "с": "C", "д": "D", "ди": "D", "де": "D", "е": "E", "и": "E",
                 "ф": "F", "эф": "F", "джи": "G", "г": "G", "же": "G"}


def known_dir(key):
    if key == "screenshots":
        return os.path.join(known_dir("pictures"), "Screenshots")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, SHELL_FOLDERS[key])[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), {"documents": "Documents", "pictures": "Pictures",
                                                      "music": "Music", "videos": "Videos",
                                                      "downloads": "Downloads"}.get(key, "Desktop"))


def find_folder(name, search=False):
    """(путь, название) по сказанному имени папки или None. search=True — искать и среди своих папок."""
    name = norm(name)
    drive = re.fullmatch(r"(диск|раздел) (?P<l>\S+)", name)
    if drive:
        letter = DRIVE_LETTERS.get(drive.group("l"), drive.group("l").upper())
        if len(letter) == 1 and os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\", f"диск {letter}"
    for pattern, key, label in KNOWN_FOLDERS:
        if re.fullmatch(pattern, name):
            path = key if key.startswith("shell:") else known_dir(key)
            if key.startswith("shell:") or os.path.isdir(path):
                return path, label
    if not search:
        return None
    return search_folder(name)


def search_folder(name, limit=40000):
    """Ищем папку по имени (с транслитом и нечётким сравнением) в домашних папках, до 4 уровней вглубь."""
    targets = {name, translit(name)}
    roots = [known_dir(k) for k in ("desktop", "documents", "downloads", "pictures", "videos", "music")]
    roots.append(os.path.expanduser("~"))
    best, best_score, seen = None, 0.0, 0
    for root in dict.fromkeys(roots):
        base_depth = root.rstrip("\\").count("\\")
        for cur, dirs, _ in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.lower() not in
                       ("appdata", "node_modules", "__pycache__", "site-packages", "windows")]
            if cur.count("\\") - base_depth >= 4:
                dirs[:] = []
            for d in dirs:
                seen += 1
                dn = norm(d)
                for t in targets:
                    score = 1.0 if dn == t else (0.9 if t in dn.split() or dn.startswith(t)
                                                 else difflib.SequenceMatcher(None, t, dn).ratio())
                    if score > best_score:
                        best, best_score = (os.path.join(cur, d), d), score
            if best_score == 1.0 or seen > limit:
                break
        if best_score == 1.0:
            break
    return best if best_score >= 0.8 else None


# ---------------- клавиши ----------------

VK = {"win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
      "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      "printscreen": 0x2C, "capslock": 0x14, "menu": 0x5D}
EXTENDED = {"win", "delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down", "menu"}
KEY_WORDS = {  # как клавиши произносят (Vosk пишет кириллицей, Google — часто латиницей)
    "win": "вин винд винда виндовс виндоус виндоуз виндос виндус виндо windows window win wind винды "
           "виндоуса виндовса",
    "ctrl": "контрол контроль контрл кнтрл ктрл контр ctrl control ctl",
    "alt": "альт алт alt", "shift": "шифт shift", "tab": "таб табуляция tab",
    "enter": "энтер ентер enter ввод return", "esc": "эскейп искейп эскейф эск esc escape",
    "space": "пробел space спейс", "backspace": "бэкспейс бекспейс backspace",
    "delete": "делит дилит делейт delete del дел", "insert": "инсерт insert", "home": "хоум home",
    "end": "энд end", "printscreen": "принтскрин printscreen prtsc", "capslock": "капслок capslock",
    "left": "влево лево налево left", "right": "вправо право направо right",
    "up": "вверх наверх up", "down": "вниз down", "menu": "меню",
}
WORD_TO_KEY = {w: k for k, ws in KEY_WORDS.items() for w in ws.split()}
LETTER_NAMES = {"эй": "a", "би": "b", "си": "c", "ди": "d", "и": "e", "эф": "f", "джи": "g", "эйч": "h",
                "ай": "i", "джей": "j", "кей": "k", "эл": "l", "эм": "m", "эн": "n", "оу": "o", "пи": "p",
                "кью": "q", "ар": "r", "эс": "s", "ти": "t", "ю": "u", "ви": "v", "дабл ю": "w",
                "дабл-ю": "w", "экс": "x", "икс": "x", "уай": "y", "игрек": "y", "зед": "z", "зет": "z",
                # по-русски: «контрол цэ», «вин дэ», и одиночные буквы «вин д»
                "а": "a", "бэ": "b", "б": "b", "цэ": "c", "ц": "c", "дэ": "d", "д": "d", "е": "e",
                "гэ": "g", "г": "g", "ха": "h", "ка": "k", "к": "k", "эль": "l", "л": "l", "м": "m",
                "н": "n", "о": "o", "пэ": "p", "п": "p", "эр": "r", "р": "r", "с": "s", "тэ": "t", "т": "t",
                "у": "u", "вэ": "v", "в": "v", "зэ": "z", "з": "z"}
SKIP_WORDS_KEYS = {"и", "плюс", "кнопку", "кнопка", "кнопки", "клавишу", "клавиши", "клавиша", "клавиш",
                   "сочетание", "сочетания", "комбинацию", "комбинация", "стрелку", "стрелка", "стрелочку",
                   "на", "клавиатуре", "одновременно", "мне", "пожалуйста", "plus", "and"}


def split_glued(word):
    """Google иногда склеивает сочетание в одно слово: «wintab», «альттаб», «ctrlshiftesc».
    Режем на известные названия клавиш (одиночная буква/цифра допустима только последней)."""
    names = sorted((w for w in WORD_TO_KEY if len(w) > 1), key=len, reverse=True)

    def go(rest):
        if not rest:
            return []
        for n in names:
            if rest.startswith(n):
                tail = go(rest[len(n):])
                if tail is not None:
                    return [WORD_TO_KEY[n]] + tail
        f = re.match(r"[fф](1[0-2]|[1-9])", rest)
        if f:
            tail = go(rest[f.end():])
            if tail is not None:
                return ["f" + f.group(1)] + tail
        if len(rest) == 1 and re.fullmatch(r"[a-z0-9]", rest):
            return [rest]
        return None
    return go(word)


def parse_keys(text):
    """«вин таб» / «ctrl shift esc» / «эф пять» → ['win', 'tab'] ... Возвращает (клавиши, нераспознанное)."""
    t = re.sub(r"[+\-_,.]", " ", norm(text))
    for a, b in (("пейдж ап", "pageup"), ("page up", "pageup"), ("пейдж даун", "pagedown"),
                 ("page down", "pagedown"), ("принт скрин", "printscreen"), ("print screen", "printscreen"),
                 ("капс лок", "capslock"), ("caps lock", "capslock"), ("дабл ю", "дабл-ю"),
                 ("бэк спейс", "backspace"), ("back space", "backspace")):
        t = t.replace(a, b)
    words = t.split()
    keys, i = [], 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in WORD_TO_KEY or w in VK:
            keys.append(WORD_TO_KEY.get(w, w))
        elif re.fullmatch(r"[fф]\d{1,2}", w):
            keys.append("f" + w[1:])
        elif w in ("эф", "ф", "f") and (nxt.isdigit() or nxt in NUM):
            keys.append(f"f{int(nxt) if nxt.isdigit() else NUM[nxt]}")
            i += 1
        elif w in LETTER_NAMES and not (w == "и" and keys and i + 1 < len(words)):
            keys.append(LETTER_NAMES[w])
        elif re.fullmatch(r"[a-z0-9]", w):
            keys.append(w)
        elif w in NUM and NUM[w] < 10:
            keys.append(str(NUM[w]))
        elif w not in SKIP_WORDS_KEYS:
            glued = split_glued(w)
            if not glued:
                return [], w
            keys += glued
        i += 1
    return keys, None


def send_keys(keys):
    """Зажимает клавиши по порядку и отпускает в обратном — так работают сочетания вроде Win+Tab."""
    u = ctypes.windll.user32
    codes = []
    for k in keys:
        if k in VK:
            code = VK[k]
        elif re.fullmatch(r"f\d{1,2}", k) and 1 <= int(k[1:]) <= 12:
            code = 0x6F + int(k[1:])
        else:
            code = ord(k.upper())
        codes.append((code, 1 if k in EXTENDED else 0))
    for code, ext in codes:
        u.keybd_event(code, 0, ext, 0)
        time.sleep(0.02)
    for code, ext in reversed(codes):
        u.keybd_event(code, 0, ext | 2, 0)  # 2 = KEYEVENTF_KEYUP
        time.sleep(0.02)


# ---------------- обработчик команд ----------------

REMEMBER_RE = (r"^(запомни|сохрани|добавь)( мне| себе)? (?P<what>(этот |эту |это |текущ\w+ |открыт\w+ )?"
               r"(сайт\w*|страниц\w*|вкладк\w*|ссылк\w*|программ\w*|приложени\w*|игр\w*))\s*(?P<name>.*)$")
FORGET_RE = (r"^(забудь|удали|убери)( мой| мою| из памяти)? (?P<what>сайт\w*|страниц\w*|ссылк\w*|программ\w*|"
             r"приложени\w*|игр\w*) (?P<name>.+)$")


# «когда я скажу X, отвечай Y», «на фразу X отвечай Y» — свой ответ; «забудь ответ на X»
REPLY_RE = (r"^((когда|если) (я )?(говорю|скажу|произнесу|спрошу)|на (фразу|слова|вопрос))\s+(?P<p>.+?)\s*,?\s+"
            r"(ты )?(отвечай|ответь|говори|скажи)( мне)?\s+(?P<r>.+)$")
FORGET_REPLY_RE = r"^(забудь|удали|убери) (свой |мой |этот )?ответ (на|для) (фразу |слова |вопрос )?(?P<p>.+)$"
# рутина: «когда я скажу X, сделай Y». Действие Y может быть цепочкой («открой телеграм и ютуб»), поэтому такую
# фразу нельзя резать на шаги раньше времени.
ROUTINE_A = (r"^(?:когда|если) (?:я )?(?:говорю|скажу|произнесу)\s+(?P<p>.+?)\s*,?\s+(?:ты )?"
             r"(?:делай|выполняй|запускай)\s+(?P<a>.+)$")
ROUTINE_B = (r"^(?:когда|если) (?:я )?(?:говорю|скажу|произнесу)\s+(?P<p>.+?)\s*,?\s+(?P<a>(?:открой|включи|запусти|"
             r"найди|поставь|сделай|закрой|выключи|напиши|нажми|проверь|прокрути|громче|тише|создай|покажи|сверни|"
             r"напомни)\b.+)$")


SOUND_WORDS = re.compile(r"\bзвук\b|громкост|\b(тише|потише|громче|погромче)\b")
GENERIC_QUERY = {"песню", "песни", "музыку", "музыка", "трек", "видео", "картинки", "что-нибудь",
                 "что нибудь", "информацию", "это", "его", "её"}  # «ищи песню» без названия — не запрос
# шаг цепочки в Spotify: «найди песню люблю» / «включи песню люблю»
SPOTIFY_STEP = (r"^(?P<v>найди|поищи|ищи|включи|поставь|играй|воспроизведи|запусти)\s+"
                r"(?:(?:песн\w*|трек\w*|музык\w*|композици\w*)\s+)?(?P<q>.+)$")
PLAY_START = r"^(включи|поставь|играй|воспроизведи|запусти)\b"  # с таким глаголом песню надо ещё и включить



class Actions:
    def __init__(self, cfg, base_dir, ai, save=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.ai = ai
        self.save = save  # save(раздел, название, значение или None) → текст ошибки или None
        self.browser_once = None  # браузер, названный в текущей команде («открой ютуб в хроме»)
        self.set_lists(cfg)
        self.index = AppIndex()
        threading.Thread(target=self.index.build, daemon=True).start()
        self.pending = None
        self.choice = None  # варианты приложений, из которых пользователь сейчас выбирает
        self.youtube_opened_at = 0.0  # когда KAR последний раз открывала YouTube
        self.media_paused = None      # какой плеер KAR поставила на паузу
        self.local = None
        self.pending_until = 0.0   # подтверждение («да») действует недолго
        self.timers = []           # [(threading.Timer, token)]
        self.notify = None         # notify(text): сказать вслух из фона (ставит main)
        self.save_setting = None   # save_setting(ключ, значение): записать настройку в config.json (ставит main)
        self.breaks = None
        self.notes = notes.Notes(os.path.join(base_dir, "notes.md"))
        self.reminders = reminders.Reminders(os.path.join(base_dir, "reminders.json"))
        self.finder = finder.FileFinder()
        self.awaiting = None       # недосказанное напоминание: {"text"|"when", "until"}
        self._in_routine = False
        self.routes = [
            (r"^(выход|выключись|отключись|заверши работу|завершить работу)$", self.quit),
            (r"^(спрячься|скройся|исчезни|пока|отбой|свободна)\b|"
             r"^(уйди|уходи|убирайся|свали|сгинь|испарись|скрой себя|убери себя)\b|"
             r"(уйди|уходи|убирайся|свали|пропади|исчезни) (с|из) (экрана|монитора|глаз)", self.hide),
            (REMEMBER_RE, self.remember_cmd),
            (FORGET_RE, self.forget_cmd),
            (REPLY_RE, self.reply_cmd),
            (FORGET_REPLY_RE, self.forget_reply_cmd),
            (r"^(отмени|отмена|отбой) (выключени\w*|перезагрузк\w*)$", self.abort_shutdown),
            (r"\b(таймер|будильник)\w*", self.timer),
            # --- рутины: «когда я скажу рабочий режим, открой телеграм и ютуб»
            (ROUTINE_A, self.routine_add),
            (ROUTINE_B, self.routine_add),
            (r"^(забудь|удали|убери) (рутину|сценарий|макрос) (?P<p>.+)$", self.routine_forget),
            (r"^(какие( у меня)? (рутины|сценарии)|покажи( мои)? (рутины|сценарии)|список рутин)$", self.routines_list),
            # --- напоминания
            (r"^(какие( у меня)? напоминани\w*|покажи( мои)? напоминани\w*|список напоминаний|что у меня запланировано)$",
             self.reminders_list),
            (r"^(отмени|удали|убери|забудь)( все| мои| все мои)? напоминани\w*( (о|про|об|насчет) (?P<t>.+))?$",
             self.reminders_cancel),
            (r"\bнапомни\w*\b", self.remind),
            # --- заметки обо мне
            (r"^(запомни|запиши|учти)\s*,?\s+что\s+(?P<t>.+)$", self.note_add),
            (r"^что ты (обо мне |про меня )?(знаешь|помнишь)( обо мне| про меня)?$", self.notes_show),
            (r"^(забудь (все|всё) (обо мне|что знаешь обо мне)|забудь меня)$", self.notes_clear),
            # --- файлы
            (r"^(покажи|выдели)( мне)?( его| ее| этот файл| файл| папку| где (он|она)| в проводнике)$", self.show_file),
            (r"^(открой|запусти) (его|ее|этот файл|найденный файл)$", self.open_found),
            (r"^(?:найди|найти|поищи|где)(?: мой| мою| мои| мое| этот)? (?:файл\w*|папк\w*|документ\w*)\s+(?P<q>.+)$",
             self.find_file),
            (r"^(?:найди|найти|поищи)\s+(?P<q>.+?)\s+(?:файл\w*|папк\w*|документ\w*)$", self.find_file),
            (r"^(?:найди|найти|поищи|где)\s+(?P<q>.*[\w\-]\.[a-z][a-z0-9]{1,4})$", self.find_file),
            # --- громкость отдельной программы (раньше общей громкости)
            (r"^(?:сделай|поставь|убавь|прибавь|приглуши)\s+(?:звук\s+(?:в|у|на)\s+)?(?P<app>.+?)\s+"
             r"(?P<dir>тише|потише|громче|погромче)$", self.app_volume),
            (r"^(?P<verb>выключи|отключи|заглуши|включи|верни)\s+звук\s+(?:в|у|на)\s+(?P<app>.+)$", self.app_mute),
            (r"^(?:громкость|звук)\s+(?:в\s+|у\s+)?(?!(?:на|до|в|минимум|максимум|пополам|половин)\b)(?P<app>[^\d]+?)\s+"
             r"(?:на\s+|до\s+)?(?P<n>\d+|[а-яеёa-z]+(?:\s[а-яеёa-z]+)?)\s*(?:процент\w*|%)?$", self.app_level),
            # --- проверки и диктовка
            (r"^(проверь|проверить) (интернет|соединение|связь|вай ?фай|wi ?fi)$|^(есть|работает) ли интернет$",
             self.check_internet),
            (r"^(проверь|проверить) микрофон$|^(ты )?(меня )?слышишь( меня)?$", self.check_mic),
            (r"^(включи|начни|запусти) диктовк\w*$|^режим диктовки$", self.dictation_on),
            (r"^(выключи|останови|закончи|прекрати|заверши) диктовк\w*$|^стоп диктовк\w*$", self.dictation_off),
            (r"^(какая |какова |скажи |узнай |расскажи |покажи )?(сейчас |сегодня |завтра )?"
             r"(погод\w*|прогноз погод\w*)( сейчас| сегодня| завтра)?( (в|во|на|для|по) (?P<city>.+))?$",
             self.weather),
            # «включи песню люблю в спотифай» — название песни стоит перед словом «спотифай»
            (r"^(?:найди|поищи|ищи|включи|поставь|играй|воспроизведи|запусти)\s+"
             r"(?:(?:песн\w*|трек\w*|музык\w*)\s+)?(?P<q>.+?)\s+(?:в|на|через)\s+(?:этом\s+)?"
             r"(?:спотифай\w*|spotify)$", self.spotify),
            (r"^(?:(?:включи|запусти|поставь|найди|ищи|поищи)\s+)?(?:(?:в|на)\s+)?(?:этом\s+)?"
             r"(?:спотифай\w*|spotify)\s+(?:(?:ищи|найди|включи|поставь|поищи)\s+)?"
             r"(?:(?:песн\w*|трек\w*|музык\w*)\s+)?(?P<q>.+)$", self.spotify),
            (r"что ты (умеешь|можешь)|как\w* (у тебя )?команд|(назови|перечисли|скажи) (\S+ )?команд|"
             r"список команд|помощь$", self.help),
            (r"(выключи|перезагрузи|перезапусти) (компьютер|пк|комп|систему)", self.power),
            (r"заблокируй (компьютер|пк|комп|экран)", self.lock),
            # --- забота о компьютере (идеи из capri-desktop)
            (r"^(?:открой|покажи|зайди в|запусти)\s+(?:мне\s+)?(?:настройк\w*|параметр\w*)\s+(?P<what>.+)$|"
             r"^(?:открой|покажи|зайди в)\s+(?P<what2>блютуз\w*|блютус\w*|bluetooth|вай ?фай|wi[ \-]?fi|"
             r"центр обновлени\w*|обновлени\w* windows)$", self.settings_page),
            (r"^(?:(?:скажи|покажи|проверь|узнай)\s+)?(?:сколько\s+|какой\s+|какая\s+)?"
             r"(?:(?:у меня|еще|осталось|сейчас)\s+)*(?:заряд\w*|батаре\w*|аккумулятор\w*|"
             r"процент\w* (?:заряд\w*|батаре\w*))(?:\s+(?:батаре\w*|аккумулятор\w*|осталось|сейчас|у меня|"
             r"ноутбук\w*)){0,3}$", self.battery_info),
            (r"^(?!.*(?:оператив|\bозу\b|\bрам\b)).*\b(?:сколько|скажи|покажи|проверь|узнай|много ли)\b.*"
             r"\b(?:мест\w*|памят\w*|свободн\w*)\b.*(?:\bдиск\w*|компьютер\w*|\bпк\b|комп\b|ноутбук\w*|\b[a-z]:)|"
             r"^(?:сколько|скажи сколько)\s+(?:у меня\s+)?(?:осталось\s+)?(?:свободного\s+)?места(?:\s+осталось)?$",
             self.disk_info),
            (r"(?:\b(?:сколько|скажи|покажи|проверь|узнай|какая)\b.*\b(?:оператив\w*|озу|рам|ram)\b)|"
             r"^(?:оператив\w*|озу|рам)$", self.ram_info),
            (r"^(?=.*\b(?:ip|айпи|ай пи)\b)(?:какой|скажи|покажи|узнай|проверь|мой|определи)\b.*$", self.ip_info),
            (r"^(?:включи|переключи|поставь|сделай|смени|поменяй)\s+(?:(?:на\s+)?(?P<w1>темн\w*|светл\w*)\s+"
             r"(?:тему|режим|оформление)|(?:тему|режим)\s+(?P<w2>темн\w*|светл\w*))$|"
             r"^(?:переключи|смени|поменяй)\s+(?:тему|режим)$", self.theme),
            (r"^(?P<v>включи|выключи|отключи|вруби|выруби)\s+(?P<k>вай ?фай|wi[ \-]?fi|вифи|блютуз\w*|блютус\w*|"
             r"bluetooth)$", self.radio),
            (r"^(?:безопасно\s+)?(?:извлеки|вытащи|отсоедини|отключи)\s+(?:безопасно\s+)?(?:флешк\w*|usb|юсб\w*|"
             r"накопител\w*|съемн\w* диск\w*)(?:\s+(?P<l>[a-zа-я]{1,2}))?$", self.eject),
            (r"^(?:прижми|переставь|сдвинь|поставь|прикрепи|поверни)\s+окно\s+(?:в\s+|на\s+|к\s+)?"
             r"(?P<d>в?лев\w*|в?прав\w*)(?:\s+(?:сторон\w*|половин\w*))?$|^окно\s+(?:в\s+|на\s+|к\s+)?"
             r"(?P<d2>в?лев\w*|в?прав\w*)$", self.snap_window),
            (r"^(?:разверни|раскрой|максимизируй)\s+(?:это\s+)?окно(?:\s+на весь экран)?$|^окно на весь экран$",
             self.maximize_window),
            (r"^(?:перенеси|перемести|отправь|переставь)\s+окно\s+на\s+(?:другой|второй|соседний)\s+"
             r"(?:монитор|экран)\w*$", self.other_monitor),
            (r"верни\w*\s+(?:потерян\w*|пропавш\w*)\s+окн\w*|потерял\w*\s+окн\w*|"
             r"окна\s+(?:за экраном|пропали|потерялись)|собери окна", self.lost_windows),
            (r"скриншот\w* (?:области|фрагмента|части)|снимок (?:области|фрагмента|части экрана)|"
             r"вырежи (?:область|фрагмент|часть экрана)|ножницы", self.snip),
            (r"^(?:запиши|записывай)\s+(?:мой\s+)?экран\w*$|^(?:начни|включи|останови|выключи|закончи|"
             r"прекрати|запусти)\s+запись\s+экран\w*$|^запись экрана$", self.record_screen),
            (r"\b(?:сохрани|запиши|сделай)\s+(?:клип|последн\w+\s+(?:\S+\s+)?секунд\w*)|^клип$", self.clip),
            (r"^(?:выключи|отключи|останови|закончи|прекрати)\s+игров\w*\s+режим\w*$|^стоп игровой режим$|"
             r"^обычный режим питания$", self.game_mode_off),
            (r"^(?:включи|запусти|вруби|активируй|давай)\s+игров\w*\s+режим\w*$|^игровой режим$",
             self.game_mode_on),
            (r"^(?:освободи|очисти|почисти|подчисти|убери)\s+(?:мне\s+)?(?:место(?: на диске)?|диск\w*|мусор\w*|"
             r"временные файлы|корзину|компьютер\w*|пк)$", self.clean_ask),
            (r"(?:что|какие\s+\S+)\s+(?:запускается|запускаются|стартует|стартуют|грузится|грузятся)\s+"
             r"(?:с|вместе с|при)\s+\S+|^(?:покажи\s+|какие\s+)?(?:программы\s+)?(?:в\s+)?автозапуск\w*$|что в автозапуске",
             self.startup_list),
            (r"^(?:убери|удали|отключи|выключи)\s+(?P<n>.+?)\s+(?:из|с)\s+(?:автозапуск\w*|автозагрузк\w*|"
             r"запуска windows)$|^(?:отключи|выключи)\s+автозапуск\s+(?P<n2>.+)$", self.startup_off),
            (r"^(?:добавь|включи|верни)\s+(?P<n>.+?)\s+(?:в|на)\s+(?:автозапуск\w*|автозагрузк\w*)$", self.startup_on),
            (r"^(?:обнови|обновить|проверь обновления|запусти обновление)\s+(?:все\s+)?(?:мои\s+)?"
             r"(?:программы|приложения|софт)$|^обнови (?:все|всё)$", self.update_apps),
            (r"^(?P<v>включи|вруби|верни|выключи|отключи|убери)\s+напоминани\w*\s+(?:об?|про)\s+(?:отдых\w*|"
             r"перерыв\w*)$", self.breaks_toggle),
            (r"громкост|громче|тише|(вкл|выкл|отключ|убер|верни|включ)\w* звук|звук (на|в) |без звука|замуть", self.volume),
            (r"яркост|ярче|темнее", self.brightness),
            (r"(проанализ|анализ|диагност|провер|просканир|сканир)\w*.*(пк|компьютер|комп|систем|ошибк)", self.analyze),
            # --- микрофон (выполняет main: там живёт слушатель) ---
            (r"(какой|какое) (сейчас )?(микрофон|устройство ввода)|что за микрофон|(через )?какой микрофон",
             lambda c, m: Reply("", "mic_info")),
            (r"(смени|поменяй|переключи|следующий|другой) (\S+ )?(микрофон|устройство ввода)",
             lambda c, m: Reply("", "mic_next")),
            # --- прокрутка страниц ---
            (r"\b(прокрут|пролист|листай|листани|полистай|скролл|скроль|спустись|поднимись|крути (вниз|вверх))\w*|"
             r"^(в |на |до )?(самый |самую |самого )?(низ|верх|конец страницы|начало страницы|низа|верха)$|"
             r"^(еще |ещё )?(ниже|выше)$", self.scroll),
            # --- мультимедиа: что сейчас играет (музыка, YouTube, плееры) ---
            (r"^(поставь |поставить )?(на )?пауз\w*( музык\w*| видео| трек| песн\w*| ролик)?$|"
             r"^(останови|приостанови|тормозни|стопни)( музык\w*| видео| трек| песн\w*| воспроизведение| ролик)?$|"
             r"^(выключи|стоп) (музык\w*|видео|трек|песн\w*|ролик)$", self.media_pause),
            (r"^(продолжи|продолжай|играй|воспроизведи|сними( с)? пауз\w*|сними)( музык\w*| видео| трек| песн\w*|"
             r" воспроизведение| ролик)?$|^(включи|запусти) (дальше|обратно|снова)$|"
             r"^(включи|запусти) (музык\w*|видео|трек|песн\w*) (обратно|снова|дальше)$", self.media_play),
            (r"^(следующ\w*|дальше|переключи|скипни|пропусти)( трек| песн\w*| видео| композици\w*| ролик)?$|"
             r"^(включи|давай) следующ\w*( трек| песн\w*| видео| ролик)?$", self.media_next),
            (r"^(предыдущ\w*|прошл\w*)( трек| песн\w*| видео| композици\w*| ролик)?$|"
             r"^(включи|верни) (предыдущ|прошл)\w*( трек| песн\w*| видео| ролик)?$", self.media_prev),
            (r"что (сейчас )?(играет|звучит|за (песн|трек|музык|видео)\w*)", self.media_info),
            (r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи)( видео| музык\w*| трек| песн\w*| ролик)? "
             r"(на|с|со) "
             r"(\d|полминут|полтор|начал|сначала|момент|отметк|тайминг|(\S+ ){0,2}(секунд|минут|час)|"
             r"(\S+ )?(ноль|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|\S+дцать|сорок|"
             r"пятьдесят))", self.media_seek),
            (r"^(перемотай|перемотать|промотай|мотни|отмотай|прокрути|верни\w*|вернись|пропусти|скипни|"
             r"перемотка|поставь|включи)( видео| музык\w*| трек| песн\w*| ролик)?( на| в| к| с)?"
             r"( тайминг\w*| отметк\w*| момент\w*| время| позици\w*)?\s*"
             r"(\d|полминут|полтор|начал|сначала|заново|вперед|назад|(\S+ ){0,2}(секунд|минут|час))", self.media_seek),
            (r"^(включи|начни|поставь|запусти|давай|перемотай|верни|играй)( видео| музык\w*| трек| песн\w*| ролик)?"
             r"( с| на| в)?( самого)? ?(сначала|начало|начала|заново|снова)$", self.media_seek),
            (r"^(нажми на поиск|нажми поиск|перейди в поиск|в поиск|поиск|поставь курсор в поиск)$",
             self.search_field),
            (r"^(нажми|нажать|зажми|жми|набери сочетание) (?P<k>.+)$", self.press),
            (rf"^({automation.TYPE_VERBS})\s+\S", self.type_cmd),
            (r"^(открой |зайди в |перейди в |выбери |найди )?(в )?(телеграм\w*|telegram|телег\w*|тг )?\s*"
             r"((?P<ord>{}) )?(чат\w*|chat|диалог\w*|переписк\w*|контакт\w*|беседу|беседа|группу|канал)"
             r"( с| со)?( (?P<name>.+))?$".format("|".join(automation.ORDINALS)), self.chat),
            # «открой телеграм папа» — запустить Telegram и сразу открыть чат с этим именем
            (r"^(открой|зайди в|перейди в|напиши в|запусти) (телеграм\w*|telegram|телег\w*|тг) (?P<name>(?!и |"
             r"напиши|первый|второй|третий).+)$", self.chat),
            (r"сверни (все|всё)|покажи рабочий стол", self.minimize_all),
            (r"\bсверни\b", self.minimize),
            (r"(закрой|закрыть|убери) (эту |текущую |\S+ )?вкладк", self.tab),
            (r"нов(ую|ая) вкладк|\b(верни|восстанови) (закрыт\w* )?вкладк|(следующ|предыдущ)\w* вкладк", self.tab),
            (r"^(создай|сделай|создать) (?P<what>.*(папк|файл|документ|таблиц).*)$", self.create),
            (r"^(найди|покажи|открой|загугли|погугли|поищи)( мне)?( в интернете| в гугле)? (фото\w*|фотк\w*|"
             r"картинк\w*|изображени\w*|пикч\w*|обои)( (с|со|про|о|об|на тему))? (?P<q>.+)$", self.images),
            (r"^(найди|поищи|загугли|погугли|расскажи|узнай|посмотри)( мне)?( в интернете| в гугле| в сети)? "
             r"(информаци\w*|инфу|инфо|что-нибудь|что нибудь|всё|все|сведения|данные|статью)( (о|об|про|по))? "
             r"(?P<q>.+)$", self.info),
            (r"^(открой|включи|покажи|найди|запусти|поставь|врубай|вруби)( мне)?( какое-нибудь| какой-нибудь| "
             r"какое нибудь| любое| любой| одно| одно)? (?P<kind>видео\w*|видос\w*|"
             r"ролик\w*|клип\w*|песн\w*|трек\w*|музык\w*)( на ютуб\w*| в ютуб\w*)?( (с|со|про|о|об|где|на тему))? "
             r"(?P<q>.+)$", self.video),
            (r"^(открой|включи|покажи|запусти|поставь)( мне)? (на|в) (ютуб\w*|youtube) (?P<q>.+)$", self.video),
            (r"^(найди|поищи|загугли|погугли|ищи)( в интернете| в гугле| в google)? (?P<q>.+)$", self.search),
            (r"^(открой|запусти|включи|зайди на|перейди на|покажи) (?P<t>.+)$", self.open),
            (r"^((сними|снять) (задачу|процесс\w*)|(заверши|завершить|убей|убить|останови|закрой) "
             r"(все )?(процесс\w*|задачу|задачи)|кильни|килльни|грохни|прибей) (?P<t>.+)$", self.kill_process),
            (r"что (сейчас )?(грузит|нагружает|жрет|ест|тормозит)|(какие|какой) процесс\w* (грузят|нагружают|жрут|"
             r"тормозят)|самые (тяжелые|прожорливые) процессы", self.top_processes),
            (r"^(закрой|заверши|выключи|убей) (?P<t>.+)$", self.close),
            (r"который час|сколько времени|сколько сейчас времени|время сейчас", self.time),
            (r"какое (сегодня )?число|какая (сегодня )?дата|какой сегодня день", self.date),
            (r"скриншот|скрин экрана|сделай скрин", self.screenshot),
        ]
        # команды, которым нужно окно под руками: окно KAR при них отдаёт фокус обратно
        self._needs_focus = {self.press, self.type_cmd, self.tab, self.minimize, self.minimize_all,
                             self.scroll, self.search_field, self.chat, self.close, self.snap_window, self.maximize_window,
                             self.other_monitor, self.snip, self.record_screen, self.clip}

    def handle(self, cmd):
        canon = kazakh.canon(cmd)   # «ютубты аш» → «открой ютуб»
        if canon:
            log.info("Казахская команда %r → %r", cmd, canon)
            cmd = canon
        c = norm(cmd)
        cancel_ensure()  # новая команда — старая проверка «заиграло ли видео» больше не нужна
        if self.awaiting:  # ждали от пользователя время или текст напоминания
            done = self._finish_reminder(c)
            if done:
                return done
        if c in self.routines and not self._in_routine:  # своя рутина: одна фраза — набор действий
            return self.run_routine(self.routines[c])
        if self.pending:
            action, self.pending = self.pending, None
            if time.time() <= self.pending_until:  # подтверждение живёт 20 секунд
                if re.match(r"^(да|давай|подтверждаю|конечно|ага|иә|иа|ия|жарайды|болады)\b", c):
                    return action()
                if re.match(r"^(нет|не надо|отмена|стоп|жоқ|жок|қажет емес|болдырма\w*)\b", c):
                    return Reply(line("cancel"))
        if self.stop_background():  # любая новая команда останавливает автопрокрутку
            if re.match(r"^(стоп|хватит|стой|остановись|достаточно|перестань|харэ|все|всё|тоқта\w*|токта\w*|"
                        r"болды|жеткілікті)\b", c):
                return Reply(line("scroll_stop"))
        if self.choice:  # ждём ответ на «какое открыть / закрыть?»
            options, self.choice = self.choice, None
            pick = self.resolve_choice(c, options)
            if pick is not None:
                name, act = options[pick]
                if callable(act):
                    return act()
                started = self.index.launch_best(name, [(1.0, name, act)])
                return Reply(line("launch", t=started) if started else line("app_missing", t=name))
            if re.match(r"^(никак\w*|ни одно|ни то|отмена|не надо|нет|ничего)\b", c):
                return Reply(line("cancel"))
            # ответ не про выбор — значит, это новая команда
        custom = self.custom_reply(c)  # своя фраза целиком — не делим её на цепочку команд
        if custom:
            return custom
        # «запомни … как X и Y» — название целиком, не цепочка команд
        whole = any(re.match(p, c) for p in (REMEMBER_RE, FORGET_RE, REPLY_RE, FORGET_REPLY_RE, ROUTINE_A, ROUTINE_B))
        steps = None if whole else automation.split_chain(c)
        if steps:
            try:
                return self.run_chain(steps)
            except Exception as e:
                log.exception("Цепочка %s", steps)
                return Reply(line("error", e=e))
        return self.handle_one(c)

    @staticmethod
    def resolve_choice(c, options):
        """«второе», «два», «клод», «первую» → номер варианта или None."""
        ords = [(r"перв\w*|один|одну|1|біріншісі|бірінші|бір", 0), (r"втор\w*|два|две|2|екіншісі|екінші|екі", 1),
                (r"трет\w*|три|3|үшіншісі|үшінші|үш", 2),
                (r"последн\w*", len(options) - 1)]
        for pattern, idx in ords:
            if re.search(rf"(^|\s)({pattern})(\s|$)", c) and idx < len(options):
                return idx
        words = re.sub(r"^(открой|запусти|давай|вот|это|ну)\s+", "", c)
        best, best_i = 0.0, None
        for i, (name, _) in enumerate(options):
            for v in {words, translit(words)}:
                s = 1.0 if v and v in name else difflib.SequenceMatcher(None, v, name).ratio()
                if AppIndex.sounds_like(v, name.split()[0] if name.split() else name):
                    s = max(s, 0.86)
                if s > best:
                    best, best_i = s, i
        return best_i if best >= 0.6 else None

    def web(self, url):
        """Открыть адрес: в браузере из команды («… в хроме»), из настройки "browser" или в браузере
        по умолчанию Windows."""
        exe = find_browser(self.browser_once) or find_browser(self.cfg.get("browser"))
        if exe:
            subprocess.Popen([exe, url])
        else:
            webbrowser.open(url)

    def handle_one(self, c):
        # «звук в хроме» — это программа, а не «открыть в браузере»: суффикс браузера там не отрезаем
        m = None if SOUND_WORDS.search(c) else BROWSER_SUFFIX.search(c)
        self.browser_once, missing = None, None
        if m:  # «открой ютуб в хроме» — браузер только для этой команды
            if find_browser(m.group("b")):
                self.browser_once = m.group("b")
            else:
                missing = next((BROWSER_NAMES[k] for p, k in BROWSER_WORDS if re.fullmatch(p, m.group("b"))),
                               m.group("b"))
            c = c[:m.start()].strip()
        try:
            r = self._handle_one(c)
        finally:
            self.browser_once = None
        if r and missing:
            r.text = line("browser_missing", b=missing) + " " + r.text
        return r

    def _handle_one(self, c):
        r = self.custom_reply(c)  # свои ответы — раньше команд и нейросети
        if r:
            return r
        for pattern, fn in self.routes:
            m = re.search(pattern, c)
            if m:
                try:
                    if fn in self._needs_focus:
                        automation.restore_external()
                    r = fn(c, m)
                except Exception as e:
                    log.exception("Команда %s", c)
                    return Reply(line("error", e=e))
                if r:
                    return r
        return None

    # --- свои сайты и программы ---
    def set_lists(self, cfg):
        """Сайты, программы и названия из настроек — при запуске и когда config.json поменяли."""
        self.sites = {norm(k): v for k, v in cfg.get("sites", {}).items()}
        self.apps = {norm(k): v for k, v in cfg.get("apps", {}).items()}
        self.aliases = {norm(k): v for k, v in cfg.get("app_aliases", {}).items()}
        self.replies = {norm(k): v for k, v in cfg.get("replies", {}).items() if v}
        self.routines = {norm(k): v for k, v in cfg.get("routines", {}).items() if v}

    def custom_reply(self, c):
        """Свой ответ на фразу из config.json → "replies" (точно или почти дословно) или None."""
        if not self.replies:
            return None
        if c in self.replies:
            return Reply(self.replies[c])
        close = difflib.get_close_matches(c, list(self.replies), n=1, cutoff=0.85)
        if close:
            return Reply(self.replies[close[0]])
        # фраза целиком внутри команды и лишнего немного: «ну как дела» → «как дела»
        for phrase in sorted(self.replies, key=len, reverse=True):
            if len(c) - len(phrase) <= 12 and re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", c):
                return Reply(self.replies[phrase])
        return None

    def remember(self, section, name, target):
        """Записать сайт / программу / свой ответ (section "sites" / "apps" / "replies") в config.json
        и сразу начать её узнавать. target=None — забыть. Возвращает текст ошибки или None."""
        name = norm(name)
        err = self.save(section, name, target) if self.save else None
        if err:
            return err
        book = {"sites": self.sites, "apps": self.apps, "replies": self.replies,
                "routines": self.routines}[section]
        stored = self.cfg.setdefault(section, {})
        if target is None:
            book.pop(name, None)
            stored.pop(name, None)
        else:
            book[name] = target
            stored[name] = target
        return None

    def remember_cmd(self, c, m):
        """«запомни этот сайт как музыка» — адрес открытой вкладки; «запомни эту программу как запрет» —
        программа, окно которой сейчас на экране."""
        name = re.sub(r"^(как|под названием|под именем|с названием|назови)\s+", "", (m.group("name") or "").strip())
        name = name.strip(" «»\"")
        site = re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what"))
        if not name:
            return Reply(line("remember_noname_site" if site else "remember_noname_app"))
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if site:
            url = media.page_url(hwnd)
            if not url:
                return Reply(line("remember_nosite"))
            err = self.remember("sites", name, url)
            short = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            return Reply(line("remember_fail", e=err) if err else line("remember_site", t=name, url=short))
        exe = automation.window_exe(hwnd)
        base = os.path.splitext(os.path.basename(exe or ""))[0].lower()
        if not exe or base in PROTECTED_PROCS or base == "applicationframehost":
            return Reply(line("remember_noapp"))
        err = self.remember("apps", name, exe)
        return Reply(line("remember_fail", e=err) if err else line("remember_app", t=name, app=base))

    def forget_cmd(self, c, m):
        name = norm(m.group("name"))
        section = "sites" if re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what")) else "apps"
        book = self.sites if section == "sites" else self.apps
        if name not in book:
            return Reply(line("forget_missing", t=name))
        err = self.remember(section, name, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=name))

    def reply_cmd(self, c, m):
        """«Когда я скажу „как дела“, отвечай „лучше всех“» — свой ответ на фразу."""
        phrase = m.group("p").strip(" ,")
        text = m.group("r").strip(" ,")
        text = text[:1].upper() + text[1:]
        if not re.search(r"[.!?…]$", text):
            text += "."
        err = self.remember("replies", phrase, text)
        return Reply(line("remember_fail", e=err) if err else line("reply_saved", p=phrase, r=text))

    def forget_reply_cmd(self, c, m):
        phrase = norm(m.group("p"))
        close = phrase if phrase in self.replies else next(
            iter(difflib.get_close_matches(phrase, list(self.replies), n=1, cutoff=0.85)), None)
        if not close:
            return Reply(line("forget_missing", t=phrase))
        err = self.remember("replies", close, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=close))

    def fallback(self, cmd, force_search=False):
        """Без ИИ: вопрос ищем в Google (если search_when_unsure не выключен), остальное — «не знаю команды»."""
        c = norm(cmd)
        if force_search or re.match(r"^(что|кто|как|какой|какая|какое|какие|почему|зачем|сколько|где|когда|куда|"
                     r"откуда|чем|чей|расскажи|объясни|скажи|қандай|қай|кім|неге|қанша|қайда|қалай|қашан)\b", c):
            if not self.cfg.get("search_when_unsure", True):
                return Reply(line("dont_know"))
            self.web("https://www.google.com/search?q=" + urllib.parse.quote(cmd))
            return Reply(line("search_q"))
        return Reply(line("unknown"))

    # --- команды ---
    def quit(self, c, m):
        return Reply(line("quit"), "quit")

    def hide(self, c, m):
        return Reply(line("hide"), "hide")

    # --- погода, таймер, Spotify ---
    def weather(self, c, m):
        city = (m.groupdict().get("city") or "").strip()
        city = re.sub(r"\s+(сейчас|сегодня|завтра)$", "", city).strip()
        city = city or (self.cfg.get("city") or "Алматы")
        lang = i18n.lang()

        def get(url):
            req = urllib.request.Request(url, headers={"User-Agent": "KarAssistant/1.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.loads(r.read().decode("utf-8"))
        try:
            place = None
            for cut in (0, 1, 2, 3):  # «в астане» → «астан», «алматыда» → «алматы»
                name = city[:len(city) - cut] if cut else city
                if cut and len(name) < 4:
                    break
                found = get("https://geocoding-api.open-meteo.com/v1/search?count=1&language=" + lang
                            + "&name=" + urllib.parse.quote(name)).get("results")
                if found:
                    place = found[0]
                    break
            if not place:
                return Reply(line("weather_city", t=city))
            data = get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
                       "&current=temperature_2m,weather_code,wind_speed_10m&wind_speed_unit=ms"
                       "&daily=temperature_2m_max,temperature_2m_min,weather_code&forecast_days=2&timezone=auto"
                       % (place["latitude"], place["longitude"]))
            now = data["current"]
        except Exception as e:
            log.info("Погода недоступна: %s", e)
            return Reply(line("weather_fail"))
        shown = place.get("name", "")
        if not re.search(r"[а-яәғқңөұүһі]", shown.lower()):
            shown = city[:1].upper() + city[1:]
        if re.search(r"\bзавтра\b", c):
            day = data["daily"]
            return Reply(line("weather_tomorrow", city=shown, lo=f"{day['temperature_2m_min'][1]:+.0f}",
                              hi=f"{day['temperature_2m_max'][1]:+.0f}",
                              desc=i18n.weather_text(day["weather_code"][1])))
        return Reply(line("weather", city=shown, temp=f"{now['temperature_2m']:+.0f}",
                          desc=i18n.weather_text(now["weather_code"]), wind=f"{now['wind_speed_10m']:.0f}"))

    def timer(self, c, m):
        if re.search(r"\b(отмени|отмена|сбрось|останови|убери|удали|отключи|выключи|стоп)\w*", c):
            n = len(self.timers)
            for tm, _ in self.timers:
                tm.cancel()
            self.timers.clear()
            return Reply(line("timer_cancel" if n else "timer_none"))
        text = re.sub(r"\b(поставь|включи|запусти|заведи|засеки|установи|сделай|таймер\w*|будильник\w*|на|через)\b",
                      " ", c)
        secs = media.parse_time(text)
        if secs and not re.search(r"час|минут|секунд|сек\b|\d[:.]\d", text):
            secs *= 60  # «таймер на 5» — пять минут
        if not secs or secs <= 0:
            return Reply(line("timer_ask"), "ask")
        label, token = dur(secs), object()

        def fire():
            self.timers[:] = [x for x in self.timers if x[1] is not token]
            try:
                import winsound
                winsound.Beep(880, 300)
                winsound.Beep(1175, 400)
            except Exception:
                pass
            if self.notify:
                self.notify(line("timer_done", t=label))
        tm = threading.Timer(secs, fire)
        tm.daemon = True
        tm.start()
        self.timers.append((tm, token))
        return Reply(line("timer_set", t=label))

    def abort_shutdown(self, c, m):
        self.pending = None
        subprocess.run(["shutdown", "/a"], creationflags=NO_WINDOW)
        return Reply(line("shutdown_abort"))

    def spotify(self, c, m):
        q = m.group("q").strip()
        if norm(q) in GENERIC_QUERY:
            return Reply(line("ask_query"))
        return self.spotify_go(q, play=bool(re.match(PLAY_START, c)))

    def spotify_go(self, q, play=False):
        """Поиск в Spotify; с play=True ещё и нажимает «Play» у первого результата."""
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(q))  # откроет поиск в приложении Spotify
        except OSError:
            self.web("https://open.spotify.com/search/" + urllib.parse.quote(q))
            return Reply(line("spotify", q=q))
        if not play or not self.cfg.get("spotify_autoplay", True):
            return Reply(line("spotify", q=q))
        if automation.spotify_press_play():
            return Reply(line("spotify_play", q=q))
        return Reply(line("spotify_manual", q=q))

    def _spotify_step(self, m, step):
        """Шаг цепочки после «открой спотифай»: «найди песню …» ищет, «включи песню …» ещё и запускает."""
        q = m.group("q").strip()
        if norm(q) in GENERIC_QUERY:  # «включи музыку» — просто продолжить воспроизведение
            return self.handle_one("продолжи") or Reply(line("done"))
        return self.spotify_go(q, play=bool(re.match(PLAY_START, step)))

    # ======================= напоминания =======================
    def remind(self, c, m):
        now = datetime.datetime.now()
        when, text = reminders.parse_when(c, now)
        if when is None:
            if not text:
                return Reply(line("reminder_ask_text"), "ask")
            self.awaiting = {"text": text, "until": time.time() + 30}
            return Reply(line("reminder_ask_when"), "ask")
        if not text:
            self.awaiting = {"when": when, "until": time.time() + 30}
            return Reply(line("reminder_ask_text"), "ask")
        return self._add_reminder(when, text, now)

    def _add_reminder(self, when, text, now):
        if when <= now:
            return Reply(line("reminder_past"))
        self.reminders.add(when, text)
        return Reply(line("reminder_set", when=reminders.format_when(when, now), text=text))

    def _finish_reminder(self, c):
        """Ответ на «Когда напомнить?» или «О чём напомнить?»."""
        wait, self.awaiting = self.awaiting, None
        if time.time() > wait["until"] or re.match(r"^(отмена|отбой|не надо|нет)\b", c):
            return None
        now = datetime.datetime.now()
        if "text" in wait:
            when, _ = reminders.parse_when(c, now)
            return self._add_reminder(when, wait["text"], now) if when else None
        return self._add_reminder(wait["when"], c, now)

    def reminders_list(self, c, m):
        items = self.reminders.upcoming()
        if not items:
            return Reply(line("reminder_none"))
        now = datetime.datetime.now()
        shown = "; ".join(f"{reminders.format_when(datetime.datetime.fromisoformat(r['when']), now)} — {r['text']}"
                          for r in items[:3])
        return Reply(line("reminder_list", n=len(items), items=shown))

    def reminders_cancel(self, c, m):
        n = self.reminders.cancel(m.groupdict().get("t"))
        return Reply(line("reminder_cancel", n=n) if n else line("reminder_cancel_none"))

    # ======================= рутины =======================
    @staticmethod
    def expand_and(action):
        """«открой телеграм и ютуб» → ['открой телеграм', 'открой ютуб']. Остальные действия — как есть."""
        m = re.match(r"^(открой|запусти|включи)\s+(.+)$", action.strip())
        if not m:
            return [action]
        objects = [o.strip() for o in re.split(r"\s*(?:,|\sи\s|\sпотом\s|\sзатем\s)\s*", m.group(2)) if o.strip()]
        return [f"{m.group(1)} {o}" for o in objects] if len(objects) > 1 else [action]

    def run_routine(self, action):
        self._in_routine = True  # рутина не может вызвать саму себя
        try:
            replies = [self.handle(step) for step in self.expand_and(action)]
        finally:
            self._in_routine = False
        replies = [r for r in replies if r]
        return replies[0] if len(replies) == 1 else Reply(line("done"))

    def routine_add(self, c, m):
        phrase, action = norm(m.group("p")), m.group("a").strip()
        if not phrase or not action or phrase == norm(action):
            return None
        err = self.remember("routines", phrase, action)
        return Reply(line("error", e=err)) if err else Reply(line("routine_saved", p=phrase, a=action))

    def routine_forget(self, c, m):
        phrase = norm(m.group("p"))
        if phrase not in self.routines:
            return Reply(line("routine_none"))
        err = self.remember("routines", phrase, None)
        return Reply(line("error", e=err)) if err else Reply(line("routine_forget", p=phrase))

    def routines_list(self, c, m):
        if not self.routines:
            return Reply(line("routine_empty"))
        return Reply(line("routine_list", items=", ".join(f"«{p}»" for p in list(self.routines)[:5])))

    # ======================= заметки обо мне =======================
    def note_add(self, c, m):
        fact = m.group("t").strip()
        return Reply(line("note_saved", t=fact)) if self.notes.add(fact) else Reply(line("done"))

    def notes_show(self, c, m):
        items = self.notes.items()
        return Reply(line("note_list", t="; ".join(items[-5:]))) if items else Reply(line("note_none"))

    def notes_clear(self, c, m):
        self.notes.clear()
        return Reply(line("note_clear"))

    # ======================= файлы =======================
    def find_file(self, c, m):
        query = m.group("q").strip()
        if not finder.wanted_name(query):
            return Reply(line("file_ask"), "ask")
        found = self.finder.find(query)
        if not found:
            return Reply(line("file_none", q=query))
        name = os.path.basename(found[0])
        folder = os.path.basename(os.path.dirname(found[0])) or os.path.dirname(found[0])
        if len(found) > 1:
            return Reply(line("file_found_more", name=name, folder=folder, n=len(found) - 1))
        return Reply(line("file_found", name=name, folder=folder))

    def show_file(self, c, m):
        ok = self.finder.show()
        if ok is None:
            return Reply(line("file_first"))
        return Reply(line("file_shown") if ok else line("file_gone"))

    def open_found(self, c, m):
        ok = self.finder.open()
        if ok is None:
            return None  # «открой его» без найденного файла — обычная команда
        return Reply(line("file_opened", name=os.path.basename(self.finder.last[0])) if ok else line("file_gone"))

    # ======================= громкость отдельной программы =======================
    GENERIC_SOUND = {"музыку", "музыка", "звук", "компьютер", "систему", "все", "всё", "видео", "ролик", "ролики"}

    def _proc_matches(self, proc, query):
        q = norm(query)
        names = {q, translit(q)}
        if q in self.aliases:
            names.add(norm(self.aliases[q]))
        p = norm(proc)
        return any(v and (v in p or (len(p) >= 3 and p in v) or AppIndex.sounds_like(v, p)) for v in names)

    def _change_app_volume(self, app, level=None, step=None, mute=None):
        try:
            sessions = winsys.app_sessions()
        except Exception:
            log.exception("Микшер")
            sessions = []
        matches = [s for s in sessions if self._proc_matches(s["name"], app)]
        if not matches:
            return Reply(line("app_vol_none", app=app))
        name, now, muted = winsys.change_app_volume(matches, level=level, step=step, mute=mute)[0]
        shown = name[:1].upper() + name[1:]
        if mute is not None:
            return Reply(line("app_vol_muted" if mute else "app_vol_unmuted", app=shown))
        return Reply(line("app_vol_set", app=shown, n=now))

    def app_volume(self, c, m):
        app, louder = norm(m.group("app")), m.group("dir").endswith(("громче",))
        if app in self.GENERIC_SOUND:  # «сделай музыку тише» — это общая громкость
            return self.volume("громче" if louder else "тише", m)
        return self._change_app_volume(app, step=15 if louder else -15)

    def app_mute(self, c, m):
        app = norm(m.group("app"))
        if app in self.GENERIC_SOUND:
            return self.volume("включи звук" if m.group("verb") in ("включи", "верни") else "выключи звук", m)
        return self._change_app_volume(app, mute=m.group("verb") not in ("включи", "верни"))

    def app_level(self, c, m):
        n = parse_number(m.group("n")) if re.fullmatch(r"\d+", m.group("n")) or m.group("n").split()[0] in NUM else None
        app = norm(m.group("app"))
        if n is None or app in NUM or app in self.GENERIC_SOUND:
            return None  # это не «громкость <программа> <число>»: пусть разберётся общая громкость
        return self._change_app_volume(app, level=max(0, min(100, n)))

    # ======================= проверки, диктовка =======================
    def check_internet(self, c, m):
        state, ms = winsys.internet_status()
        return Reply(line({"ok": "net_ok", "dns": "net_dns", "off": "net_off"}[state], ms=ms))

    def check_mic(self, c, m):
        return Reply(line("mic_ok", t="…"), "mic_check")  # текст подставит main: он знает, какой микрофон открыт

    def dictation_on(self, c, m):
        return Reply(line("dictation_on"), "dictation_on")

    def dictation_off(self, c, m):
        return Reply(line("dictation_off"), "dictation_off")

    # --- прокрутка ---
    def scroll(self, c, m):
        up = bool(re.search(r"\b(вверх|наверх|выше|верх\w*|начал\w*|поднимись|назад)\b", c))
        down = bool(re.search(r"\b(вниз|ниже|низ\w*|конец|дальше|спустись|вперед)\b", c))
        auto = re.search(r"\b(медленно|плавно|автоматически|сама|постепенно|прокручивай|листай сама)\b", c) \
            or re.fullmatch(r"(листай|полистай|прокручивай|скроль|скролль)( медленно| плавно)?", c)
        if not (up or down or auto) or re.search(r"секунд|минут|\bчас|\d[:.]\d\d|полминут", c):
            return None  # «прокрути на 30 секунд вперёд» — это перемотка видео, не страницы
        direction = -1 if up and not down else 1
        self.stop_background()
        if auto:
            fast = bool(re.search(r"быстр", c))
            self._autoscroll_stop = threading.Event()
            threading.Thread(target=self._autoscroll, args=(direction, 0.25 if fast else 0.6,
                                                             self._autoscroll_stop), daemon=True).start()
            return Reply(line("scroll_auto"))
        if re.search(r"\b(самый|самую|самого|до конца|в конец|в начало|до низа|до верха|конец страницы|"
                     r"начало страницы)\b|^(в |на |до )?(низ|верх|низа|верха)$", c):
            automation.scroll(200 * direction, delay=0.008)  # слишком быстро — щелчки колеса теряются
            return Reply(line("scroll_end" if direction > 0 else "scroll_top"))
        n = parse_number(c)
        screen = automation.screen_notches()
        if re.search(r"\b(немного|чуть|чуть-чуть|слегка|капельку)\b|^(еще )?(ниже|выше)$", c):
            notches = 3
        elif n and re.search(r"экран|страниц|раз", c):
            notches = min(n, 20) * screen
        else:
            notches = screen
        automation.scroll(notches * direction)
        return Reply(line("scroll"))

    def _autoscroll(self, direction, interval, stop):
        """Плавная прокрутка в окне, которое было активно в момент команды, — пока не скажут «стоп»."""
        hwnd = automation.target_window()
        end = time.time() + 300
        while not stop.is_set() and time.time() < end:
            if automation.target_window() != hwnd:
                break  # пользователь ушёл в другое окно — не крутим чужое
            automation.scroll(direction, hwnd=hwnd)
            stop.wait(interval)

    def autoscroll_active(self):
        ev = getattr(self, "_autoscroll_stop", None)
        return bool(ev and not ev.is_set())

    def stop_background(self):
        """Остановить фоновые действия (автопрокрутку, поиск файла). Вызывается и на «стоп» из main."""
        self.finder.stop()
        ev = getattr(self, "_autoscroll_stop", None)
        if ev and not ev.is_set():
            ev.set()
            return True
        return False

    # ======================= забота о компьютере (идеи из capri-desktop) =======================
    def settings_page(self, c, m):
        found = pcctl.settings_page(m.group("what") or m.group("what2"))
        if not found:
            return None
        uri, name = found
        os.startfile(uri)
        return Reply(line("pc_settings", t=name))

    def battery_info(self, c, m):
        info = pcctl.battery_info(psutil)
        if not info:
            return Reply(line("pc_nobattery"))
        percent, plugged = info
        return Reply(line("pc_battery", p=percent, c=line("pc_charging") if plugged else ""))

    @staticmethod
    def _drive_letter(c):
        default = (os.environ.get("SystemDrive") or "C:")[:1].upper()
        m = re.search(r"\bдиск\w*\s+(?P<l>[a-zа-я]{1,2})\b|\b(?P<l2>[a-z]):", c)
        if not m:
            return default
        raw = (m.group("l") or m.group("l2")).lower()
        return DRIVE_LETTERS.get(raw) or (raw.upper() if len(raw) == 1 and raw.isascii() else default)

    def disk_info(self, c, m):
        letter = self._drive_letter(c)
        info = pcctl.disk_info(psutil, letter)
        if not info:
            return Reply(line("pc_disk_missing", d=letter))
        free, total = info
        return Reply(line("pc_disk", d=letter, free=pcctl.human_size(free), total=pcctl.human_size(total)))

    def ram_info(self, c, m):
        free, total, used = pcctl.ram_info(psutil)
        return Reply(line("pc_ram", free=pcctl.human_size(free), total=pcctl.human_size(total), used=used))

    def ip_info(self, c, m):
        ip = pcctl.local_ip()
        return Reply(line("pc_ip", ip=ip) if ip else line("pc_ip_fail"))

    def theme(self, c, m):
        which = (m.groupdict().get("w1") or m.groupdict().get("w2") or "")
        reg = winsys._winreg()
        light = pcctl.get_light_theme(reg) if not which else which.startswith("светл")
        if not which:
            light = not light  # «переключи тему»
        try:
            pcctl.set_light_theme(reg, light)
        except OSError:
            return Reply(line("pc_fail"), "fail")
        return Reply(line("pc_theme_light" if light else "pc_theme_dark"))

    def radio(self, c, m):
        on = m.group("v").startswith(("включи", "вруби"))
        kind = "Bluetooth" if re.search(r"блют|bluetooth", m.group("k")) else "WiFi"
        name = "Bluetooth" if kind == "Bluetooth" else "Wi-Fi"
        result = pcctl.radio_result(run_ps(pcctl.radio_script(kind, on), timeout=30))
        if result == "none":
            return Reply(line("pc_radio_none", t=name), "fail")
        if result != "ok":
            return Reply(line("pc_fail"), "fail")
        return Reply(line("pc_radio_on" if on else "pc_radio_off", t=name))

    def eject(self, c, m):
        drives = pcctl.removable_drives(psutil.disk_partitions())
        raw = (m.group("l") or "").lower()
        letter = DRIVE_LETTERS.get(raw) or (raw.upper() if len(raw) == 1 and raw.isascii() else "")
        if letter:
            drives = [d for d in drives if d == letter]
        if not drives:
            return Reply(line("pc_eject_none"), "fail")
        if len(drives) > 1:
            return Reply(line("pc_eject_many", t=", ".join(drives)), "ask")
        out = run_ps(pcctl.eject_script(drives[0]), timeout=30)
        if "OK" not in out:
            return Reply(line("pc_fail"), "fail")
        return Reply(line("pc_eject", d=drives[0]))

    def snap_window(self, c, m):
        side = m.group("d") or m.group("d2")
        send_keys(["win", "left" if side.startswith(("лев", "влев")) else "right"])
        return Reply(line("done"))

    def maximize_window(self, c, m):
        send_keys(["win", "up"])
        return Reply(line("done"))

    def other_monitor(self, c, m):
        send_keys(["win", "shift", "right"])
        return Reply(line("done"))

    def lost_windows(self, c, m):
        n = pcctl.rescue_offscreen_windows()
        return Reply(line("pc_lost", n=n) if n else line("pc_lost_none"))

    def snip(self, c, m):
        send_keys(["win", "shift", "s"])
        return Reply(line("pc_snip"))

    def record_screen(self, c, m):
        send_keys(["win", "alt", "r"])  # запись через Xbox Game Bar: та же клавиша включает и останавливает
        return Reply(line("pc_record"))

    def clip(self, c, m):
        send_keys(["win", "alt", "g"])
        return Reply(line("pc_clip"))

    # --- игровой режим: план электропитания «Высокая производительность»
    def _powercfg(self, *args):
        r = subprocess.run(["powercfg", *args], capture_output=True, creationflags=NO_WINDOW, timeout=15)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    def game_mode_on(self, c, m):
        state = os.path.join(self.base_dir, "gamemode.json")
        if not os.path.exists(state):  # не затираем прежний план, если режим уже включён
            _, out = self._powercfg("/getactivescheme")
            with open(state, "w", encoding="utf-8") as f:
                json.dump({"scheme": pcctl.parse_scheme(out)}, f)
        code, _ = self._powercfg("/setactive", pcctl.HIGH_PERFORMANCE)
        if code != 0:
            return Reply(line("pc_fail"), "fail")
        names = {str(n).lower() for n in self.cfg.get("game_mode_close", []) if n}
        if names:  # закрываем только то, что человек сам назвал в config.json
            for p in psutil.process_iter(["name"]):
                try:
                    if (p.info["name"] or "").lower() in names:
                        p.terminate()
                except psutil.Error:
                    continue
        return Reply(line("pc_game_on"))

    def game_mode_off(self, c, m):
        state = os.path.join(self.base_dir, "gamemode.json")
        scheme = None
        try:
            with open(state, encoding="utf-8") as f:
                scheme = json.load(f).get("scheme")
        except (OSError, ValueError):
            pass
        code, _ = self._powercfg("/setactive", scheme or "SCHEME_BALANCED")
        if code != 0:
            return Reply(line("pc_fail"), "fail")
        try:
            os.remove(state)
        except OSError:
            pass
        return Reply(line("pc_game_off"))

    # --- очистка, автозапуск, обновления
    def clean_ask(self, c, m):
        folder = os.environ.get("TEMP") or tempfile.gettempdir()
        files = pcctl.old_files(folder, days=2)
        total = sum(size for _, size in files)

        def do():
            _, freed = pcctl.delete_files(files)
            try:
                run_ps("Clear-RecycleBin -Force", timeout=90)
            except Exception:
                log.info("Корзина не очистилась")
            return Reply(line("pc_clean_done", t=pcctl.human_size(freed)))
        self.pending = do
        self.pending_until = time.time() + 20
        return Reply(line("pc_clean_ask", t=pcctl.human_size(total)), "ask")

    def startup_list(self, c, m):
        entries = [e for e in pcctl.startup_entries(winsys._winreg()) if e["enabled"]]
        if not entries:
            return Reply(line("pc_startup_none"))
        names = [e["name"] for e in entries]
        shown = ", ".join(names[:8]) + (f" и ещё {len(names) - 8}" if len(names) > 8 and i18n.lang() == "ru"
                                        else f" және тағы {len(names) - 8}" if len(names) > 8 else "")
        return Reply(line("pc_startup_list", t=shown))

    def _startup_change(self, query, enabled):
        reg = winsys._winreg()
        entry = pcctl.find_startup(pcctl.startup_entries(reg), query)
        if not entry:
            return Reply(line("pc_startup_missing", t=query), "fail")
        if entry["scope"] != "user":  # для всех пользователей нужны права администратора
            return Reply(line("pc_startup_admin", t=entry["name"]), "fail")
        pcctl.set_startup_enabled(reg, entry["name"], enabled)
        return Reply(line("pc_startup_on" if enabled else "pc_startup_off", t=entry["name"]))

    def startup_off(self, c, m):
        return self._startup_change((m.group("n") or m.group("n2")).strip(), False)

    def startup_on(self, c, m):
        return self._startup_change(m.group("n").strip(), True)

    def update_apps(self, c, m):
        if not shutil.which("winget"):
            return Reply(line("pc_update_none"), "fail")

        def do():  # обновление идёт в отдельном окне: видно, что именно ставится
            subprocess.Popen('start "KAR" cmd /k winget upgrade --all --accept-source-agreements '
                             '--accept-package-agreements', shell=True)
            return Reply(line("pc_update_do"))
        self.pending = do
        self.pending_until = time.time() + 20
        return Reply(line("pc_update_ask"), "ask")

    # --- напоминания об отдыхе
    def breaks_toggle(self, c, m):
        on = m.group("v").startswith(("включи", "вруби", "верни"))
        self.cfg["health_breaks"] = on
        if self.save_setting:
            self.save_setting("health_breaks", on)
        return Reply(line("pc_breaks_on" if on else "pc_breaks_off"))

    def start_breaks(self):
        """Запускает фоновую проверку «давно ли вы за компьютером» (вызывает main)."""
        def nag(worked):
            if self.notify:
                self.notify(line("pc_break", t=dur(worked)))
        self.breaks = pcctl.BreakWatcher(nag, enabled=lambda: self.cfg.get("health_breaks", True),
                                         after_minutes=self.cfg.get("break_after_minutes", 120))
        self.breaks.start()

    # --- мультимедиа ---
    def _youtube_keys(self, keys_list):
        """Нажать горячие клавиши плеера YouTube в его окне (k — пауза, j/l — ±10 с, стрелки — ±5 с)."""
        hwnd = automation.find_window(("youtube",))
        if not hwnd or not automation.focus(hwnd):
            return False
        for keys in keys_list:
            send_keys(keys)
            time.sleep(0.12)
        return True

    def media_pause(self, c, m):
        if browser_playing() and automation.find_window(("youtube",)):
            # YouTube в браузере: системная пауза не всегда доходит — проверяем по звуку и жмём K
            media.control("pause")
            time.sleep(0.8)
            if browser_playing():
                self._youtube_keys([["k"]])
                time.sleep(0.8)
            if not browser_playing():
                self.media_paused = "youtube"
                return Reply(line("media_paused"))
        ok, desc, src = media.control("pause")
        if not ok:
            return Reply(line("media_none"))
        self.media_paused = src  # «продолжи» вернёт именно это, а не другой плеер
        return Reply(line("media_paused"))

    def media_play(self, c, m):
        if self.media_paused == "youtube" or (not self.media_paused and automation.find_window(("youtube",))
                                              and not media.now_playing()):
            def started():
                for _ in range(8):  # у трансляций звук появляется не сразу
                    time.sleep(0.5)
                    if browser_playing():
                        return True
                return False
            for b in BROWSERS:  # сначала системной командой — без переключения окон
                if media.control("play", source=f"{b}.exe")[0]:
                    break
            if started() or (self._youtube_keys([["k"]]) and started()):
                return Reply(line("media_play"))
            if self.media_paused == "youtube":  # не включаем вместо YouTube какой-то другой плеер
                return Reply(line("media_play_fail"))
        ok, desc, src = media.control("play", source=self.media_paused)
        if not ok:
            return Reply(line("media_none"))
        return Reply(line("media_play"))

    def media_next(self, c, m):
        ok, desc, _ = media.control("next")
        if not ok:
            return Reply(line("media_none"))
        time.sleep(0.8)  # даём плееру переключиться, чтобы назвать новый трек
        info = media.now_playing()
        return Reply(line("media_next", t=info[0]) if info and info[0] else line("done"))

    def media_prev(self, c, m):
        ok, desc, _ = media.control("prev")
        return Reply(line("done") if ok else line("media_none"))

    def media_info(self, c, m):
        info = media.now_playing()
        if not info or not info[0]:
            return Reply(line("media_none"))
        return Reply(line("media_info", t=info[0]))

    def media_seek(self, c, m):
        back = re.search(r"\bназад\b|\bотмотай|\bверни|\bвернись", c)
        fwd = re.search(r"\bвперед|\bпромотай|\bпропусти|\bскипни|\bмотни", c)
        text = re.sub(r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи|перемотай|перемотать|промотай|"
                      r"мотни|отмотай|прокрути|верни\w*|вернись|пропусти|скипни|перемотка)\s+", "", c)
        text = re.sub(r"\b(вперед|назад|на|с|со|в|к|видео|музык\w*|трек|песн\w*|ролик|момент\w*|отметк\w*|"
                      r"тайминг)\b", " ", text)
        secs = media.parse_time(text)
        groups = media.number_groups(text)
        to_start = re.search(r"начал|сначала|заново", c)
        if to_start:
            back = fwd = None  # «верни сначала» — это не «назад», а в самое начало
        absolute = to_start or not (back or fwd) and (
            re.match(r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи)", c)
            or re.search(r"\d[:.]\d\d|момент|отметк|тайминг|позици|\bс (\S+ )?\S+ (минут|секунд|час)", c)
            or len(groups) >= 2
            or (groups and groups[0] >= 100 and not re.search(r"секунд|минут|час", c)))  # «на 314» = 3:14
        if secs is None:
            if not (back or fwd):
                return None
            secs = 10  # «перемотай вперёд» без числа — на 10 секунд
        delta = None if absolute else (-secs if back else secs)
        yt = automation.find_window(("youtube",))
        np = media.now_playing()
        if yt and (browser_playing() or not np or any(b in (np[1] or "").lower() for b in BROWSERS)):
            # YouTube в браузере: Firefox не даёт перематывать через систему — клавиши плеера или ссылка с &t=
            if absolute:
                if media.youtube_url_seek(secs):
                    threading.Thread(target=ensure_playing, args=(5.0,), daemon=True).start()
                    return Reply(line("media_seek", t=media.fmt(secs)))
                return Reply(line("media_seek_fail"))
            n10, rest = divmod(int(abs(delta)), 10)
            keys = [["l" if delta > 0 else "j"]] * n10 + [["right" if delta > 0 else "left"]] * round(rest / 5)
            if self._youtube_keys(keys or [["right" if delta > 0 else "left"]]):
                return Reply(line("media_skip", t=media.fmt(abs(delta)), d=line("fwd") if delta > 0 else line("back")))
            return Reply(line("media_seek_fail"))
        ok, target, src = media.seek(seconds=secs) if absolute else media.seek(delta=delta)
        if target is None:
            return Reply(line("media_none"))
        return Reply(line("media_seek", t=media.fmt(target)) if ok else line("media_seek_fail"))

    # --- печать и цепочки ---
    def type_cmd(self, c, m):
        """«напиши привет» — печатает в активное окно и жмёт Enter (в чате это отправка)."""
        parsed = automation.parse_type(c)
        if not parsed:
            return None  # «напиши стих» — это просьба к нейросети, а не печать
        text, send = parsed
        in_search = False
        sm = re.match(r"^(?:в\s+)?(?:строк\w*\s+)?(?:поисков\w*\s+строк\w*|поиск\w*)\s+(?P<t>.+)$", text)
        if sm:  # «напиши в поиске X» — курсор в поиск, а не слова «в поиске» в текст
            in_search, text = True, sm.group("t").strip()
        title = automation.foreground_title().lower()
        recent_yt = time.time() - self.youtube_opened_at < 120
        if "youtube" in title or (recent_yt and automation.find_window(("youtube",))):
            # на YouTube «напиши X» = поиск X (печатать в саму страницу бессмысленно)
            if "youtube" not in title and not automation.wait_and_focus(("youtube",), timeout=4):
                return Reply(line("focus_fail"))
            youtube_search_in_tab(text)
            return Reply(line("yt", q=text))
        if automation.foreground_is_own():  # печатать некуда: активно окно самой KAR
            return Reply(line("focus_fail"), "fail")
        if in_search:
            automation.focus_search(automation.foreground_title())
            time.sleep(0.4)
        if send is None:
            send = self.cfg.get("type_press_enter", True)
        automation.input_text(text)
        if send:
            time.sleep(0.1)
            send_keys(["enter"])
        return Reply(line("sent" if send else "typed", t=text))

    def search_field(self, c, m):
        automation.focus_search(automation.foreground_title())
        return Reply(line("search_ready"))

    def chat(self, c, m):
        """«первый чат», «чат мама» — открывает чат в Telegram (запускает его, если надо)."""
        if not automation.find_window(("telegram",)):
            found = self.index.find("телеграм", {"телеграм": "telegram"}) if self.index.ready.wait(8) else None
            if not found:
                return Reply(line("app_missing", t="Telegram"), "fail")
            self.index.launch(found[1])
        index = automation.ORDINALS.get(m.groupdict().get("ord") or "", None)
        name = (m.groupdict().get("name") or "").strip() or None
        if name:
            name = re.sub(r"^(с|со|контакт\w*|чат\w*|chat|с контактом)\s+", "", name)
            name = stem_name(name)
        if not automation.telegram_open_chat(index=index or (None if name else 1), name=name):
            # чат не открылся — дальше по цепочке ничего не печатаем, чтобы не написать не туда
            return Reply(line("chat_not_found", t=name or f"№{index or 1}"), "fail")
        return Reply(line("chat_open", t=name or f"№{index or 1}"))

    def _doc_app(self, step, steps):
        """«открой ворд» в цепочке, где дальше что-то печатают → ключ приложения («word»), иначе None."""
        if not re.match(r"^(открой|запусти|включи|зайди в)\s", step):
            return None
        if not any(automation.parse_type(norm(x)) for x in steps):
            return None  # печатать нечего — открываем обычным способом
        for w in step.split():
            if w in automation.DOC_WORDS:
                return automation.DOC_WORDS[w]
        return None

    def _open_document(self, key):
        """Открывает Word (или то, что есть вместо него) с чистым документом и ждёт окно.
        Возвращает (hwnd или None, что сказать вслух)."""
        before = automation.snapshot_windows()
        options = []  # (название, команда, процессы окна)
        if key == "word":
            path = automation.app_path("winword.exe")
            if path:
                options.append(("Word", [path, "/w"], ("winword.exe",)))  # /w — сразу пустой документ
        soffice = automation.app_path("soffice.exe") if key == "word" else None
        if soffice:
            options.append(("LibreOffice Writer", [soffice, "--writer"], ("soffice.bin", "soffice.exe")))
        wordpad = automation.app_path("wordpad.exe")
        if wordpad:
            options.append(("WordPad", [wordpad], ("wordpad.exe",)))
        options.append((T("notepad_name"), ["notepad"], ("notepad.exe",)))
        for label, argv, procs in options:
            try:
                subprocess.Popen(argv)
            except OSError:
                log.info("Не запустилось: %s", argv)
                continue
            said = line("doc_fallback", t=label) if key == "word" and label != "Word" else line("launch", t=label)
            hwnd = automation.wait_new_window(before, timeout=45 if key == "word" else 20, process=procs)
            if not hwnd:
                return None, line("doc_timeout", t=label)
            if automation.is_start_screen(automation.window_title(hwnd)):
                send_keys(["ctrl", "n"])  # стартовый экран Word → пустой документ
                time.sleep(2.0)
                hwnd = automation.find_window(("winword",)) or hwnd
                automation.refocus(hwnd, timeout=4)
            time.sleep(1.0)  # окно приняло фокус, но документ ещё дорисовывается
            return hwnd, said
        return None, line("app_missing", t="Word")

    def run_chain(self, steps):
        """Выполняет шаги по очереди, держа в уме, в каком окне мы сейчас (Telegram, Word, Spotify, YouTube…)."""
        log.info("Цепочка: %s", steps)
        automation.restore_external()
        context = None   # ключевые слова заголовка окна (или hwnd), с которым работаем
        kind = None      # telegram / youtube / browser / doc / spotify / None
        said = []
        in_search = False  # курсор уже стоит в поле поиска
        for step in steps:
            s = norm(step)
            typed = automation.parse_type(s)
            if typed:
                text, send = typed
                if context and not automation.refocus(context, timeout=6):
                    # не смогли переключиться — ничего не печатаем, чтобы не написать в чужое окно
                    return Reply(" ".join(said + [line("focus_fail")]))
                if not context and automation.foreground_is_own():
                    return Reply(" ".join(said + [line("focus_fail")]))
                if kind == "youtube":
                    youtube_search_in_tab(text)
                    said.append(line("yt", q=text))
                    continue
                if kind in ("youtube", "browser"):
                    # на сайте «напиши X» = поиск X: сначала курсор в строку поиска, потом Enter
                    if not in_search:
                        automation.focus_search(automation.foreground_title())
                        time.sleep(0.4)
                    automation.input_text(text)
                    if send is not False:
                        time.sleep(0.1)
                        send_keys(["enter"])
                    said.append(line("web", q=text))
                    continue
                if send is None:
                    # в документе «напиши привет» не должно ещё и переносить строку
                    send = self.cfg.get("type_press_enter", True) and kind != "doc"
                automation.input_text(text)
                if send:
                    time.sleep(0.1)
                    send_keys(["enter"])
                said.append(line("sent" if send else "typed", t=text))
                continue
            if kind == "spotify":
                sp = re.match(SPOTIFY_STEP, s)
                if sp:  # «найди песню люблю» после «открой спотифай» — это поиск в Spotify, а не в YouTube
                    if context and not automation.refocus(context, timeout=6):
                        return Reply(" ".join(said + [line("focus_fail")]))
                    r = self._spotify_step(sp, s)
                    if r.action in ("fail", "ask"):
                        return Reply(" ".join(said + [r.text]))
                    said.append(r.text)
                    continue
            q = re.match(r"^(найди|поищи)\s+(?P<q>.+)$", s)
            if q and kind in ("youtube", "telegram"):
                if kind == "telegram":  # поиск в Telegram = открыть чат по имени
                    if not automation.telegram_open_chat(name=q.group("q")):
                        return Reply(" ".join(said + [line("focus_fail")]))
                else:
                    if not automation.refocus(context, timeout=6):
                        return Reply(" ".join(said + [line("focus_fail")]))
                    youtube_search_in_tab(q.group("q"))
                    said.append(line("yt", q=q.group("q")))
                continue
            if re.match(r"^(нажми на поиск|нажми поиск|поиск|в поиск|перейди в поиск)$", s):
                if context and not automation.refocus(context, timeout=6):
                    return Reply(" ".join(said + [line("focus_fail")]))
                automation.focus_search(automation.foreground_title())
                time.sleep(0.3)
                in_search = True
                continue
            doc = self._doc_app(s, steps)
            if doc:  # «открой ворд» и дальше печать: ждём окно с пустым документом
                hwnd, text = self._open_document(doc)
                if hwnd is None:
                    return Reply(" ".join(said + [text]))
                context, kind, in_search = hwnd, "doc", False
                said.append(text)
                continue
            # неизвестная программа, а дальше печать: запоминаем её новое окно
            opening = re.match(r"^(открой|запусти|включи)\s", s) and \
                not any(w in automation.APP_WINDOWS for w in s.split()) and \
                any(automation.parse_type(norm(x)) for x in steps)
            before = automation.snapshot_windows() if opening else None
            r = self.handle_one(s)
            if r is None:
                said.append(line("chain_skip", t=step))
                continue
            if r.action in ("fail", "ask"):  # шаг не удался или нужно уточнить — дальше не идём
                return Reply(" ".join(said + [r.text]))
            if before is not None:
                hwnd = automation.wait_new_window(before, timeout=20)
                if hwnd:
                    context, kind, in_search = hwnd, None, False
            words = s.split()
            for w in words:
                if w in automation.APP_WINDOWS:
                    context = automation.APP_WINDOWS[w]
                    kind = {"telegram": "telegram", "телеграм": "telegram", "телега": "telegram",
                            "ютуб": "youtube", "youtube": "youtube", "спотифай": "spotify",
                            "spotify": "spotify", "спотик": "spotify",
                            "ворд": "doc", "word": "doc", "вордпад": "doc",
                            "wordpad": "doc"}.get(w, "browser" if w == "браузер" else None)
            if re.search(r"\b(чат|диалог|переписк)", s):
                context, kind = ("telegram",), "telegram"
            if context and not isinstance(context, int) and re.match(r"^(открой|запусти|зайди|перейди)\b", s):
                slow = kind == "spotify"  # Spotify запускается долго
                automation.refocus(context, timeout=25 if slow else 10)
                time.sleep(2.5 if kind in ("youtube", "browser") else 1.5 if slow else 0.4)  # сайту нужно загрузиться
                in_search = False
        return Reply(" ".join(said) if said else line("done"))

    def help(self, c, m):
        return Reply(line("help"))

    def power(self, c, m):
        restart = m.group(1).startswith("перезаг") or m.group(1).startswith("перезап")
        flag = "/r" if restart else "/s"

        def do():
            subprocess.Popen(["shutdown", flag, "/t", "10"], creationflags=NO_WINDOW)
            return Reply(line("power_do_restart" if restart else "power_do_off"))
        self.pending = do
        self.pending_until = time.time() + 20
        return Reply(line("power_ask_restart" if restart else "power_ask_off"), "ask")

    def lock(self, c, m):
        ctypes.windll.user32.LockWorkStation()
        return Reply(line("lock"))

    def volume(self, c, m):
        if re.search(r"(выключи|выключить|отключи|убери|убрать)\w* звук|без звука|замуть", c):
            set_mute(True)
            return Reply(line("mute"))
        if re.search(r"(включи|включить|верни)\w* звук", c) and parse_number(c) is None:
            set_mute(False)
            return Reply(line("unmute"))
        n = parse_number(c)
        cur = get_volume()
        if re.search(r"громче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 10)
        elif re.search(r"тише|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 10)
        elif n is not None:
            new = n
        else:
            return Reply(line("vol_now", n=cur))
        new = max(0, min(100, new))
        set_volume(new)
        return Reply(line("vol_set", n=new))

    def brightness(self, c, m):
        try:
            cur = get_brightness()
        except Exception:
            return Reply(line("bright_fail"))
        n = parse_number(c)
        if re.search(r"ярче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 15)
        elif re.search(r"темнее|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 15)
        elif n is not None:
            new = n
        else:
            return Reply(line("bright_now", n=cur))
        new = max(0, min(100, new))
        try:
            set_brightness(new)
        except Exception:
            return Reply(line("bright_reject"))
        return Reply(line("bright_set", n=new))

    def analyze(self, c, m):
        issues, report = analyze_pc()
        folder = os.path.join(self.base_dir, "reports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        os.startfile(path)
        use_ai = self.cfg.get("enable_ai") and self.ai and self.ai.available
        spoken = self.ai.summarize_report(report) if use_ai else None
        if not spoken and self.local:
            spoken = self.local.summarize_report(report)
        if spoken:
            return Reply(spoken)
        if not issues:
            return Reply(line("scan_ok"))
        head = "; ".join(issues[:4])
        return Reply(line("scan_bad", n=len(issues), head=head))

    def search(self, c, m):
        q = m.group("q")
        if norm(q) in GENERIC_QUERY:
            return Reply(line("ask_query"))
        yt = re.search(r"(на|в) (ютубе|ютуб|youtube)", q)
        if yt:
            q = q.replace(yt.group(0), "").strip()
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
            return Reply(line("yt", q=q))
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        return Reply(line("web", q=q))

    def open(self, c, m):
        if re.search(r"ютуб|youtube", c):
            self.youtube_opened_at = time.time()  # следующее «напиши …» будет поиском на YouTube
        t = re.sub(r"^(мне |пожалуйста |программу |приложение )", "", m.group("t"))
        t = re.sub(r" пожалуйста$", "", t).strip()
        if t in ("звук",):
            return None
        site_mode = t.startswith("сайт ")
        if site_mode:
            t = t[5:].strip()
        else:
            explicit = re.match(r"^(папк\w*|каталог\w*|директори\w*)\s+(?P<n>.+)$", t)
            found = find_folder(explicit.group("n") if explicit else t, search=bool(explicit))
            if found:
                os.startfile(found[0])
                return Reply(line("folder_open", t=found[1]))
            if explicit:
                return Reply(line("folder_missing", t=explicit.group("n")))

        if not site_mode and t in self.apps:
            self._run(self.apps[t])
            return Reply(line("open", t=t))

        if t in self.sites:
            self.web(self.sites[t])
            return Reply(line("open", t=t))
        close = difflib.get_close_matches(t, list(self.sites), n=1, cutoff=0.82)
        if close:
            self.web(self.sites[close[0]])
            return Reply(line("open", t=close[0]))

        domain = re.sub(r"\s*(точка|dot)\s*", ".", t).replace(" ", "")
        if re.fullmatch(r"[\w\-]+(\.[\w\-]+)*\.[a-zа-я]{2,}", domain):
            self.web("https://" + domain)
            return Reply(line("open", t=domain))

        if not site_mode:
            self.index.ready.wait(timeout=8)
            options = self.index.candidates(t, self.aliases)
            if not options:
                return Reply(line("app_missing", t=t))
            top = options[0]
            close = [o for o in options[1:] if top[0] - o[0] < 0.1]
            if top[0] >= 1.0 or not close:  # одно явно подходит — запускаем
                started = self.index.launch_best(t, [top])
                return Reply(line("launch", t=started) if started else line("app_missing", t=t))
            # несколько похожих — спрашиваем, какое открыть
            self.choice = [(n, tg) for _, n, tg in [top] + close[:2]]
            names = [n.title() for n, _ in self.choice]
            ords = lst("ords")
            listed = ", ".join(f"{ords[i]} — {nm}" for i, nm in enumerate(names))
            return Reply(line("choose_app", t=listed), "ask")
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(t))
        return Reply(line("site_unknown", t=t))

    def kill_process(self, c, m):
        """«сними задачу steam client helper», «заверши процесс хром принудительно», «кильни дискорд»."""
        t = re.sub(r"^(все |всех )?(процесс\w*|задач\w*)\s+", "", m.group("t").strip())
        return self.close(c, None, target=t, force=bool(re.search(r"принудительн|насильн|жестк|совсем", c)))

    def close(self, c, m, target=None, force=False):
        t = target if target is not None else m.group("t").strip()
        force = force or bool(re.search(r"принудительн|насильн|жестк", c))
        t = re.sub(r"\s+(принудительно|насильно|жестко|совсем)$", "", t).strip()
        if re.fullmatch(r"(окно|это|это окно)", t):
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x73, 0, 0, 0)
            u.keybd_event(0x73, 0, 2, 0)
            u.keybd_event(0x12, 0, 2, 0)
            return Reply(line("closed_window"))
        q = norm(t)
        variants = {q, translit(q)}
        if q in self.aliases:
            variants.add(norm(self.aliases[q]))
        if q in self.apps:
            variants.add(os.path.splitext(os.path.basename(self.apps[q]))[0].lower())
        if self.index.ready.is_set():  # «закрой клод» → приложение Claude → процесс claude
            for _, app_name, _ in self.index.candidates(q, self.aliases, 2):
                variants.add(app_name.split()[0])
        variants = {v.replace(" ", "") for v in variants if len(v.replace(" ", "")) >= 3}

        words = [w for w in translit(q).split() if len(w) >= 3]  # для сравнения с описанием из диспетчера задач
        me = psutil.Process().username()
        best = {}  # имя процесса → (оценка, [процессы])
        for p in psutil.process_iter(["name", "pid", "username", "exe"]):
            name = (p.info["name"] or "").lower().removesuffix(".exe")
            # только свои программы: системные процессы и службы не трогаем никогда
            if not name or name in PROTECTED_PROCS or p.info["username"] != me or p.pid == os.getpid():
                continue
            score = 0.0
            for v in variants:
                if name == v:
                    s = 1.0
                elif name.startswith(v) or (len(name) >= 4 and v.startswith(name) and len(name) >= len(v) * 0.6):
                    s = 0.9  # «steam» не считается совпадением для «steamclienthelper»
                else:
                    s = difflib.SequenceMatcher(None, v, name).ratio()
                    if AppIndex.sounds_like(v, name):
                        s = max(s, 0.86)
                score = max(score, s)
            if score < 0.95 and words:
                # «steam client helper» → описание «Steam Client WebHelper» у steamwebhelper.exe
                desc = norm(file_description(p.info["exe"]))
                if desc:
                    if desc == " ".join(words):
                        score = 1.0
                    elif len(words) >= 2 and all(any(w in d for d in desc.split()) for w in words):
                        score = max(score, 0.92)
            if score >= 0.85:
                prev = best.get(name, (0.0, []))
                best[name] = (max(prev[0], score), prev[1] + [p])
        if not best:
            return Reply(line("not_running", t=t))
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
        top_name, (top_score, _) = ranked[0]
        close = [kv for kv in ranked[1:] if top_score - kv[1][0] < 0.1]
        if top_score < 1.0 and close:  # несколько похожих — спрашиваем, что закрыть
            self.choice = [(n, (lambda procs=procs, n=n: self._kill(n, procs, force))) for n, (_, procs) in
                           [ranked[0]] + close[:2]]
            ords = lst("ords")
            listed = ", ".join(f"{ords[i]} — {n}" for i, (n, _) in enumerate(self.choice))
            return Reply(line("choose_close", t=listed), "ask")
        return self._kill(top_name, best[top_name][1], force)

    @staticmethod
    def _kill(name, procs, force=False):
        """Завершить процессы: сначала вежливо (как «Снять задачу»), а если не закрылись за 3 секунды
        или просили принудительно — жёстко."""
        alive = []
        for p in procs:
            try:
                p.kill() if force else p.terminate()
                alive.append(p)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                return Reply(line("kill_denied", t=name))
        _, still = psutil.wait_procs(alive, timeout=3)
        if still:
            for p in still:
                try:
                    p.kill()
                except Exception:
                    pass
            _, still = psutil.wait_procs(still, timeout=2)
        if still:
            return Reply(line("kill_failed", t=name))
        return Reply(line("closed", names=name))

    def top_processes(self, c, m):
        """«Что грузит компьютер» — три процесса, которые больше всего едят процессор и память."""
        procs = list(psutil.process_iter(["name", "memory_info"]))
        for p in procs:
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        time.sleep(1.0)
        cpu, mem = {}, {}
        for p in procs:
            try:
                n = (p.info["name"] or "").removesuffix(".exe")
                if n.lower() in ("system idle process", "idle", ""):
                    continue
                cpu[n] = cpu.get(n, 0) + p.cpu_percent(None) / psutil.cpu_count()
                mem[n] = mem.get(n, 0) + p.info["memory_info"].rss / 2**20
            except Exception:
                pass
        top_cpu = sorted(cpu.items(), key=lambda kv: -kv[1])[:3]
        top_mem = sorted(mem.items(), key=lambda kv: -kv[1])[:3]
        c_txt = ", ".join(f"{n} {v:.0f} {line('unit_pct')}" for n, v in top_cpu if v >= 1) or line("nothing_heavy")
        m_txt = ", ".join(f"{n} {v / 1024:.1f} {line('unit_gb')}" if v >= 1024 else f"{n} {v:.0f} {line('unit_mb')}"
                          for n, v in top_mem)
        return Reply(line("top_procs", cpu=c_txt, mem=m_txt))

    def create(self, c, m):
        what = m.group("what")
        place_m = re.search(r"\s(на|в|во)\s+(рабоч\w+ стол\w*|документ\w*|загрузк\w*|фото\w*|изображени\w*|"
                            r"картинк\w*|музык\w*|видео|папк\w* \S+)$", what)
        place, place_name = known_dir("desktop"), line("where_desktop")
        if place_m:
            spot = find_folder(re.sub(r"^папк\w* ", "", place_m.group(2)), search=True)
            if not spot:
                return Reply(line("folder_missing", t=place_m.group(2)))
            place, place_name = spot[0], line("where_folder", p=spot[1])
            what = what[:place_m.start()]
        kind = re.match(r"(нов\w+ )?(?P<kind>папк\w*|текстов\w+ файл\w*|текстов\w+ документ\w*|"
                        r"документ\w*|файл\w*|таблиц\w*)( (с )?(именем|названием))?\s*(?P<name>.*)$", what)
        if not kind:
            return None
        k, name = kind.group("kind"), kind.group("name").strip()
        ext = ""
        dot = re.search(r"\s*(точка|dot|\.)\s*(?P<e>[a-zа-я0-9]+)$", name)
        if dot:  # «файл заметки точка txt»
            ext, name = "." + translit(dot.group("e")), name[:dot.start()].strip()
        if k.startswith("папк"):
            default = line("default_folder")
        elif k.startswith("таблиц"):
            default, ext = line("default_table"), ext or ".xlsx"
        elif k.startswith("документ") and not k.startswith("текст"):
            default, ext = line("default_doc"), ext or ".docx"
        else:
            default, ext = line("default_file"), ext or ".txt"
        name = re.sub(r'[<>:"/\\|?*]', "", name).strip() or default
        name = name[:1].upper() + name[1:]
        path = os.path.join(place, name + ext)
        n = 2
        while os.path.exists(path):  # ничего не перезаписываем
            path = os.path.join(place, f"{name} ({n}){ext}")
            n += 1
        if k.startswith("папк"):
            os.makedirs(path)
        else:
            open(path, "x", encoding="utf-8").close()  # пустые .docx/.xlsx Office открывает как новые
        return Reply(line("created", t=os.path.basename(path), where=place_name))

    def images(self, c, m):
        q = m.group("q").strip()
        if re.search(r"\bобои\b", c):
            q = "обои " + q
        self.web("https://www.google.com/search?tbm=isch&q=" + urllib.parse.quote(q))
        return Reply(line("images", q=q))

    def info(self, c, m):
        """Открывает поиск Google и вслух пересказывает начало статьи из Википедии, если она нашлась."""
        q = m.group("q").strip()
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        summary = wiki_summary(q)
        if summary:
            return Reply(line("info", t=summary))
        return Reply(line("web", q=q))

    def video(self, c, m):
        q = m.group("q").strip()
        q = re.sub(r"\s+(на|в) (ютуб\w*|youtube)$", "", q)  # «включи песню X на ютубе»
        q = re.sub(r"^(на|в) (ютуб\w*|youtube)\s*", "", q).strip()
        if not q or q in ("на", "в", "с", "про", "о"):  # «открой какое-нибудь видео на ютубе»
            q = random.choice(["интересное видео", "смешные моменты", "залипательное видео", "лучшие клипы"])
        kind = m.groupdict().get("kind") or ""
        search = q + (" песня" if re.match(r"песн|трек", kind) else "")
        found = youtube_first(search)
        if not found:  # не вышло достать первое видео — хотя бы откроем поиск
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(search))
            return Reply(line("yt", q=q))
        vid, title = found
        self.web(f"https://www.youtube.com/watch?v={vid}")
        threading.Thread(target=ensure_playing, daemon=True).start()
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) > 70:
            title = title[:67].rsplit(" ", 1)[0] + "…"
        return Reply(line("video", t=title) if title else line("video_untitled"))

    def tab(self, c, m):
        if re.search(r"нов(ую|ая) вкладк", c):
            send_keys(["ctrl", "t"])
            return Reply(line("tab_new"))
        if re.search(r"\b(верни|восстанови)", c):
            send_keys(["ctrl", "shift", "t"])
            return Reply(line("tab_restore"))
        if re.search(r"следующ", c):
            send_keys(["ctrl", "tab"])
            return Reply(line("done"))
        if re.search(r"предыдущ", c):
            send_keys(["ctrl", "shift", "tab"])
            return Reply(line("done"))
        n = max(1, min(20, parse_number(c) or 1))
        for _ in range(n):
            send_keys(["ctrl", "w"])
            time.sleep(0.12)
        return Reply(line("tab_closed", n=n) if n > 1 else line("tab_closed_one"))

    def minimize_all(self, c, m):
        send_keys(["win", "d"])
        return Reply(line("done"))

    def minimize(self, c, m):
        """«Сверни окно / вкладку / браузер» — сворачивает активное окно."""
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if hwnd:
            u.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return Reply(line("done"))

    def press(self, c, m):
        text = m.group("k")
        times = 1
        rep = re.search(r"\s(\S+) раз\w*$", text)
        if rep and parse_number(rep.group(1)):
            times, text = min(30, parse_number(rep.group(1))), text[:rep.start()]
        keys, unknown = parse_keys(text)
        if unknown:
            return Reply(line("key_unknown", t=unknown))
        if not keys:
            return None
        for _ in range(times):
            send_keys(keys)
            time.sleep(0.08)
        return Reply(line("pressed", t=" + ".join(k.upper() if len(k) == 1 else k.capitalize() for k in keys)))

    def time(self, c, m):
        return Reply(line("time", t=f"{datetime.datetime.now():%H:%M}"))

    def date(self, c, m):
        d = datetime.date.today()
        return Reply(line("date", t=f"{lst('days')[d.weekday()]}, {d.day} {lst('months')[d.month - 1]}"))

    def screenshot(self, c, m):
        from PIL import ImageGrab
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "KAR")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"screen_{datetime.datetime.now():%Y%m%d_%H%M%S}.png")
        ImageGrab.grab(all_screens=True).save(path)
        return Reply(line("screenshot"))

    def _run(self, target):
        if target.startswith(("http://", "https://")):
            self.web(target)
            return
        try:
            os.startfile(target)
        except OSError:
            subprocess.Popen(target, shell=True, creationflags=NO_WINDOW)
