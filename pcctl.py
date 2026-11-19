"""Забота о компьютере: то, что есть в capri-desktop, но не было в KAR. Идеи взяты оттуда, код написан заново.

Здесь только «чистая» логика и небольшие помощники: разбор вывода, подбор записей, сценарии PowerShell. Выполняет их и отвечает голосом actions.py. Благодаря этому модуль проверяется тестами
на любой ОС, а всё, что трогает Windows, принимает подмену (реестр, psutil, часы).
"""
import ctypes
import logging
import os
import re
import socket
import threading
import time

log = logging.getLogger("kar")

# ---------------------------------------------------------------- страницы «Параметров»
SETTINGS_PAGES = [  # (регулярка по названию, адрес ms-settings, как назвать вслух)
    (r"bluetooth|блютуз|блютус", "ms-settings:bluetooth", "Bluetooth"),
    (r"wi ?fi|вай ?фай|беспроводн", "ms-settings:network-wifi", "Wi-Fi"),
    (r"vpn|впн", "ms-settings:network-vpn", "VPN"),
    (r"сет\w*|интернет", "ms-settings:network", "сеть"),
    (r"звук\w*|аудио|динамик\w*", "ms-settings:sound", "звук"),
    (r"экран\w*|дисплей\w*|монитор\w*|разрешени\w*", "ms-settings:display", "экран"),
    (r"обновлени\w*|апдейт\w*|windows update|центр обновлени\w*", "ms-settings:windowsupdate", "обновления Windows"),
    (r"автозапуск\w*|автозагрузк\w*|запуск\w* с windows", "ms-settings:startupapps", "автозапуск"),
    (r"приложени\w*|программ\w*", "ms-settings:appsfeatures", "приложения"),
    (r"питани\w*|батаре\w*|сон\w*|энергосбережени\w*", "ms-settings:powersleep", "питание"),
    (r"уведомлени\w*", "ms-settings:notifications", "уведомления"),
    (r"хранилищ\w*|память диска|очистк\w*", "ms-settings:storagesense", "хранилище"),
    (r"принтер\w*|сканер\w*", "ms-settings:printers", "принтеры"),
    (r"мыш\w*|тачпад\w*", "ms-settings:mousetouchpad", "мышь"),
    (r"клавиатур\w*|ввод\w*", "ms-settings:typing", "клавиатура"),
    (r"дат\w* и врем\w*|врем\w*|часов\w* пояс\w*", "ms-settings:dateandtime", "дата и время"),
    (r"язык\w*|регион\w*|раскладк\w*", "ms-settings:regionlanguage", "язык и регион"),
    (r"камер\w*", "ms-settings:privacy-webcam", "камера"),
    (r"микрофон\w*", "ms-settings:privacy-microphone", "микрофон"),
    (r"конфиденциальност\w*|приватност\w*", "ms-settings:privacy", "конфиденциальность"),
    (r"обои|фон\w*|персонализаци\w*|оформлени\w*", "ms-settings:personalization", "персонализация"),
    (r"тем[аыуе]\w*|цвет\w*", "ms-settings:colors", "цвета"),
    (r"учетн\w* запис\w*|аккаунт\w*|пользовател\w*", "ms-settings:yourinfo", "учётная запись"),
    (r"специальн\w* возможност\w*", "ms-settings:easeofaccess", "специальные возможности"),
]


def settings_page(what):
    """«блютуз» → ('ms-settings:bluetooth', 'Bluetooth') или None."""
    w = (what or "").strip().lower().replace("ё", "е")
    w = re.sub(r"^(для|по|на|в|к)\s+", "", w)
    for pattern, uri, name in SETTINGS_PAGES:
        if re.fullmatch(pattern, w) or re.match(rf"^(?:{pattern})\b", w):
            return uri, name
    return None


# ---------------------------------------------------------------- объёмы
def human_size(n):
    """Байты → «1,5 ГБ» / «320 МБ» (с запятой, как говорят вслух)."""
    n = float(n)
    for unit, step in (("ГБ", 1024 ** 3), ("МБ", 1024 ** 2), ("КБ", 1024)):
        if n >= step:
            value = n / step
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return text.replace(".", ",").removesuffix(",0") + " " + unit
    return f"{int(n)} Б"


# ---------------------------------------------------------------- батарея, диск, память, адрес
def battery_info(psutil_mod):
    """(процент, на зарядке) или None, если батареи нет."""
    try:
        b = psutil_mod.sensors_battery()
    except Exception:
        return None
    if not b:
        return None
    return round(b.percent), bool(b.power_plugged)


def disk_info(psutil_mod, drive):
    """«C» → (свободно, всего) в байтах или None, если такого диска нет."""
    try:
        d = psutil_mod.disk_usage(drive.upper() + ":\\")
        return d.free, d.total
    except Exception:
        return None


def ram_info(psutil_mod):
    """(свободно, всего, процент занятой) в байтах."""
    m = psutil_mod.virtual_memory()
    return m.available, m.total, round(m.percent)


def local_ip(sock_factory=socket.socket):
    """Адрес компьютера в своей сети. Пакеты никуда не уходят: UDP-«соединение» только выбирает интерфейс."""
    s = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(1.0)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# ---------------------------------------------------------------- тёмная и светлая тема
THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def get_light_theme(reg):
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, THEME_KEY) as key:
            return bool(reg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return True


def set_light_theme(reg, light, user32=None):
    """Тема приложений и системы + сигнал «настройки изменились», чтобы окна перерисовались сразу."""
    with reg.CreateKey(reg.HKEY_CURRENT_USER, THEME_KEY) as key:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            reg.SetValueEx(key, name, 0, reg.REG_DWORD, 1 if light else 0)
    u = user32 or ctypes.windll.user32
    try:
        u.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 2, 3000, None)  # WM_SETTINGCHANGE
    except Exception:
        log.info("Сигнал о смене темы не отправился")
    return light


# ---------------------------------------------------------------- Wi-Fi и Bluetooth (радиомодули Windows)
_RADIO_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($task, $type) {
  $net = $asTask.MakeGenericMethod($type).Invoke($null, @($task))
  $net.Wait(-1) | Out-Null
  $net.Result
}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | Where-Object { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $r) { 'NONE'; return }
Await ($r.SetStateAsync('__STATE__')) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
'OK'
"""


def radio_script(kind, on):
    """Сценарий PowerShell: kind — «WiFi» или «Bluetooth»."""
    if kind not in ("WiFi", "Bluetooth"):
        raise ValueError(kind)
    return _RADIO_PS.replace("__KIND__", kind).replace("__STATE__", "On" if on else "Off")


def radio_result(output):
    """Вывод сценария → 'ok' / 'none' (нет такого модуля) / 'fail'."""
    out = (output or "").strip().splitlines()
    last = out[-1].strip() if out else ""
    return {"OK": "ok", "NONE": "none"}.get(last, "fail")


# ---------------------------------------------------------------- флешки
def removable_drives(partitions):
    """Буквы съёмных дисков из psutil.disk_partitions()."""
    letters = []
    for p in partitions:
        if "removable" in (getattr(p, "opts", "") or "").lower():
            letter = (p.device or p.mountpoint or "")[:1].upper()
            if letter and letter not in letters:
                letters.append(letter)
    return letters


def eject_script(letter):
    letter = re.sub(r"[^A-Za-z]", "", letter)[:1].upper()
    if not letter:
        raise ValueError("нужна буква диска")
    return (f'$d=(New-Object -comObject Shell.Application).Namespace(17).ParseName("{letter}:"); '
            f'if ($d) {{ $d.InvokeVerb("Eject"); "OK" }} else {{ "NONE" }}')


# ---------------------------------------------------------------- окна
def is_offscreen(rect, bounds, min_visible=60):
    """Окно «потерялось», если в границах всех экранов видно меньше min_visible пикселей по любой оси.
    rect и bounds — (left, top, right, bottom)."""
    l, t, r, b = rect
    bl, bt, br, bb = bounds
    visible_w = min(r, br) - max(l, bl)
    visible_h = min(b, bb) - max(t, bt)
    return visible_w < min_visible or visible_h < min_visible


def rescue_offscreen_windows(user32=None):
    """Возвращает на экран окна, которые остались за его границами (отключили монитор). → сколько вернули."""
    from ctypes import wintypes
    u = user32 or ctypes.windll.user32
    left, top = u.GetSystemMetrics(76), u.GetSystemMetrics(77)          # SM_X/YVIRTUALSCREEN
    bounds = (left, top, left + u.GetSystemMetrics(78), top + u.GetSystemMetrics(79))
    moved = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, buf, 256)
        if not buf.value:
            return True
        cloaked = ctypes.c_int(0)  # окна-призраки UWP «видимы», но скрыты системой
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
        except Exception:
            pass
        if cloaked.value:
            return True
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        if is_offscreen((r.left, r.top, r.right, r.bottom), bounds):
            w, h = r.right - r.left, r.bottom - r.top
            u.MoveWindow(hwnd, bounds[0] + 80, bounds[1] + 80, min(w, 1100), min(h, 700), True)
            moved.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    return len(moved)


# ---------------------------------------------------------------- игровой режим (план электропитания)
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def parse_scheme(output):
    """Вывод `powercfg /getactivescheme` → GUID плана или None."""
    m = GUID.search(output or "")
    return m.group(0) if m else None


HIGH_PERFORMANCE = "SCHEME_MIN"  # псевдоним плана «Высокая производительность»


# ---------------------------------------------------------------- очистка
def old_files(folder, days=2, limit=200000, now=None):
    """Файлы папки старше days суток: [(путь, размер)]. Папки и недоступные файлы пропускаются."""
    now = now or time.time()
    border = now - days * 86400
    found, seen = [], 0
    for root, _, files in os.walk(folder):
        for f in files:
            seen += 1
            if seen > limit:
                return found
            path = os.path.join(root, f)
            try:
                st = os.stat(path)
                if st.st_mtime < border:
                    found.append((path, st.st_size))
            except OSError:
                continue
    return found


def delete_files(paths):
    """Удаляет файлы; занятые и недоступные пропускает. → (сколько удалено, сколько байт освобождено)."""
    count = freed = 0
    for path, size in paths:
        try:
            os.remove(path)
            count += 1
            freed += size
        except OSError:
            continue
    return count, freed


# ---------------------------------------------------------------- автозапуск Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def _values(reg, root, path, flags=0):
    out = {}
    try:
        with reg.OpenKey(root, path, 0, reg.KEY_READ | flags) as key:
            i = 0
            while True:
                try:
                    name, value, _ = reg.EnumValue(key, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except OSError:
        pass
    return out


def startup_entries(reg):
    """Программы, которые запускаются с Windows: [{'name','command','enabled','scope'}]. scope: user / machine."""
    entries = []
    for scope, root in (("user", reg.HKEY_CURRENT_USER), ("machine", reg.HKEY_LOCAL_MACHINE)):
        approved = _values(reg, root, APPROVED_KEY)
        for name, command in _values(reg, root, RUN_KEY).items():
            data = approved.get(name)
            enabled = not data or not (data[0] & 1)  # нечётный первый байт — выключено в диспетчере задач
            entries.append({"name": name, "command": str(command), "enabled": enabled, "scope": scope})
    return entries


def find_startup(entries, query):
    """Запись по названию из речи: сначала точное совпадение, потом по вхождению."""
    q = re.sub(r"\s+", "", (query or "").lower())
    if not q:
        return None
    for e in entries:
        if re.sub(r"\s+", "", e["name"].lower()) == q:
            return e
    for e in entries:
        flat = re.sub(r"\s+", "", e["name"].lower())
        if q in flat or (len(flat) >= 3 and flat in q):
            return e
    return None


def set_startup_enabled(reg, name, enabled):
    """Включить/выключить запись так же, как это делает диспетчер задач (StartupApproved). Только для пользователя."""
    data = bytes([2 if enabled else 3]) + b"\x00" * 11
    with reg.CreateKey(reg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
        reg.SetValueEx(key, name, 0, reg.REG_BINARY, data)
    return True


# ---------------------------------------------------------------- напоминания об отдыхе
def system_idle_seconds():
    """Сколько секунд пользователь не трогал клавиатуру и мышь."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)


def foreground_fullscreen():
    """Окно на весь экран (игра, фильм): тогда отдыхать не зовём."""
    try:
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.right - r.left >= u.GetSystemMetrics(0) and r.bottom - r.top >= u.GetSystemMetrics(1)
                and (r.left <= 0 and r.top <= 0))
    except Exception:
        return False


class BreakWatcher:
    """Следит, сколько человек сидит за компьютером без настоящего перерыва, и зовёт отдохнуть.

    Перерыв — rest_minutes без клавиатуры и мыши. Напоминание — через after_minutes непрерывной работы,
    потом не чаще раза в repeat_minutes. Часы и «простой» подменяются, поэтому логика проверяется тестами.
    """

    def __init__(self, notify, enabled=lambda: True, idle=system_idle_seconds, busy=foreground_fullscreen,
                 clock=time.time, after_minutes=120, rest_minutes=5, repeat_minutes=30, interval=30):
        self.notify, self.enabled, self.idle, self.busy, self.clock = notify, enabled, idle, busy, clock
        self.after, self.rest, self.repeat = after_minutes * 60, rest_minutes * 60, repeat_minutes * 60
        self.interval = interval
        self.active_since = None
        self.last_nag = None
        self._stop = threading.Event()

    def tick(self):
        """Один шаг проверки. True — если позвали отдохнуть."""
        now = self.clock()
        if self.idle() >= self.rest:
            self.active_since, self.last_nag = None, None  # настоящий перерыв: считаем заново
            return False
        if self.active_since is None:
            self.active_since = now
            return False
        worked = now - self.active_since
        if worked < self.after or not self.enabled() or self.busy():
            return False
        if self.last_nag is not None and now - self.last_nag < self.repeat:
            return False
        self.last_nag = now
        self.notify(worked)
        return True

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("Напоминание об отдыхе")

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="kar-breaks").start()

    def stop(self):
        self._stop.set()
