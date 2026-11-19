"""KAR — голосовой помощник для Windows (қазақша / русский).
Запуск: run.bat (или pythonw main.py)."""
import difflib
import json
import logging
import os
import queue
import re
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
HISTORY_PATH = os.path.join(BASE, "history.json")

# журнал — до всех библиотек: если чего-то не хватает, KAR падает без окна, и причину видно только тут
logging.basicConfig(filename=os.path.join(BASE, "kar.log"), level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kar")
log.info("Python %s", sys.version.split()[0])


def fatal(text):
    """KAR запускается без консоли, поэтому о проблеме сообщаем окном Windows."""
    log.error(text)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "KAR", 0x10)
    except Exception:
        pass
    sys.exit(1)


try:
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    import automation
    import i18n
    import winsys
    from actions import Actions, Reply
    from ai import AI
    from cloud_ai import Cloud, CloudError, kind_of, make_brain
    from i18n import line
    from local_ai import SEARCH, LocalAI
    from voice import Listener, Speaker, default_input_name, google_stt, input_devices
    from ui import MainWindow, app_icon
except ImportError as e:
    log.exception("Не хватает библиотек")
    fatal(f"Қажетті кітапханалар орнатылмаған / Не установлены нужные библиотеки ({e.name or e}).\n\n"
          f"setup.bat іске қосыңыз / Запустите setup.bat, затем run.bat.\n\nPython {sys.version.split()[0]}")

DEFAULTS = {
    "language": "kk",              # kk — қазақша, ru — русский: язык интерфейса, речи и распознавания
    "enable_ai": False,            # отвечать на вопросы через Claude (нужен ключ)
    "local_ai": True,              # отвечать на вопросы через локальную Ollama
    "local_model": "qwen3:8b",
    "barge_in_enabled": False,     # перебивать голосом, пока KAR говорит (нужны наушники: колонки KAR слышит сама)
    "echo_tail_seconds": 0.7,      # сколько после речи микрофон ещё молчит (эхо из колонок и комнаты)
    "mic_device": None,
    "mic_gain": 1.0,               # громкость микрофона: 1 — как есть, 2 — вдвое громче
    "mic_sensitivity": 100,        # 100 — слышит всё; меньше — тихий шум и фон отбрасываются
    "use_wake_word": True,         # активация словом «КАР»; выключено — только кнопка с микрофоном
    "wake_words": ["кар", "қар", "кәр", "каар", "car"],
    "use_google_stt": True,        # уточнять распознанную фразу через Google (онлайн)
    "search_when_unsure": True,    # если вопрос непонятен — открыть поиск Google
    "type_press_enter": True,      # «напиши …» после текста нажимает Enter
    "spotify_autoplay": True,      # «включи песню …» в Spotify сама нажимает Play у первого результата
    "health_breaks": True,         # напоминать отдохнуть после долгой работы за компьютером
    "break_after_minutes": 120,    # через сколько минут без перерыва напомнить
    "game_mode_close": [],         # программы (например "chrome.exe"), которые закрывает «игровой режим»
    "browser": "",                 # в каком браузере открывать сайты: «хром», «опера»… или путь к exe
    "city": "Алматы",              # город для погоды, если он не назван
    "replies": {},                 # свои ответы: {"фраза": "что ответит KAR"}
    "routines": {},                # рутины: {"рабочий режим": "открой телеграм и ютуб"}
    "use_hotkeys": True,           # глобальные горячие клавиши (работают из любого окна)
    "hotkeys": winsys.DEFAULT_HOTKEYS,
    "ai_key": "",                  # ключ любого сервиса: Gemini, NVIDIA, OpenRouter, DeepSeek, OpenAI или Claude
    "ai_base_url": "",             # свой OpenAI-совместимый сервер (LM Studio, Ollama /v1 …); ключ тогда можно не указывать
    "ai_model": "",                # пусто — модель выберется сама
    "ai_route": True,              # ИИ переводит непонятные фразы в команды (особенно полезно для казахского)
    "anthropic_api_key": "",
    "model": "claude-opus-5-5",
    "tts_engine": "edge",          # edge — онлайн (есть казахский голос), sapi — офлайн голос Windows
    "voice_kk": "kk-KZ-AigulNeural",   # или kk-KZ-DauletNeural (мужской)
    "voice_ru": "ru-RU-SvetlanaNeural",
    "voice_speed": 160,            # слов в минуту
    "voice_volume": 100,           # громкость голоса 0..100 (общая громкость Windows не меняется)
    "listen_window_seconds": 8,
    "followup_seconds": 6,
    "greet_on_start": True,
    "close_to_tray": True,
    "history_size": 500,
    "vosk_models": {"kk": "models/vosk-model-small-kz-0.15", "ru": "models/vosk-model-small-ru-0.22"},
    "sites": {
        "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
        "гугл": "https://www.google.com", "google": "https://www.google.com",
        "яндекс": "https://ya.ru", "вк": "https://vk.com", "вконтакте": "https://vk.com",
        "почту": "https://mail.google.com", "почта": "https://mail.google.com", "gmail": "https://mail.google.com",
        "гитхаб": "https://github.com", "твич": "https://www.twitch.tv",
        "википедию": "https://www.wikipedia.org", "википедия": "https://www.wikipedia.org",
        "переводчик": "https://translate.google.com", "аудармашы": "https://translate.google.com",
        "карты": "https://2gis.kz", "2гис": "https://2gis.kz", "картаны": "https://2gis.kz",
        "егов": "https://egov.kz", "egov": "https://egov.kz", "каспи": "https://kaspi.kz",
        "kaspi": "https://kaspi.kz", "олх": "https://www.olx.kz", "chatgpt": "https://chatgpt.com",
        "клод": "https://claude.ai", "claude": "https://claude.ai",
    },
    "apps": {
        "блокнот": "notepad", "калькулятор": "calc", "проводник": "explorer",
        "диспетчер задач": "taskmgr", "параметры": "ms-settings:", "настройки": "ms-settings:",
        "баптаулар": "ms-settings:", "панель управления": "control", "командную строку": "cmd",
        "командная строка": "cmd", "терминал": "wt", "пейнт": "mspaint", "paint": "mspaint",
        "ворд": "winword", "word": "winword", "эксель": "excel", "excel": "excel",
        "браузер": "https://www.google.com", "настройки звука": "ms-settings:sound",
        "настройки экрана": "ms-settings:display", "корзину": "shell:RecycleBinFolder",
        "загрузки": "shell:Downloads",
    },
    "app_aliases": {
        "хром": "chrome", "гугл хром": "google chrome", "стим": "steam", "дискорд": "discord",
        "телеграм": "telegram", "телеграмм": "telegram", "спотифай": "spotify",
        "опера": "opera", "яндекс браузер": "yandex", "вс код": "visual studio code",
        "визуал студио код": "visual studio code", "обс": "obs", "ватсап": "whatsapp", "зум": "zoom",
        "фотошоп": "photoshop", "майнкрафт": "minecraft",
    },
}


# ---------------------------------------------------------------- config.json
def _config_blank():
    """config.json нет или он пустой — его можно спокойно создать заново."""
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            return not f.read().strip()
    except FileNotFoundError:
        return True


def _edit_config(change):
    """Прочитать config.json, применить change(data) и записать. Файл с ошибкой не перезаписываем —
    иначе пропадут правки пользователя. Возвращает текст ошибки или None."""
    try:
        if _config_blank():
            data = {}
        else:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:  # Блокнот иногда сохраняет с BOM
                data = json.load(f)
    except Exception:
        log.exception("config.json с ошибкой, не записываю")
        return "config.json ішінде қате бар. Түзетіңіз немесе файлды жойыңыз / Ошибка в config.json."
    change(data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return None


def save_setting(key, value):
    """Записать одну настройку в config.json, не трогая остальные."""
    _edit_config(lambda data: data.__setitem__(key, value))


def save_entry(section, name, value):
    """Свой сайт, программа или ответ: section "sites"/"apps"/"replies". value=None — убрать; встроенные
    записи убираются пометкой null, иначе вернулись бы из настроек по умолчанию."""
    def change(data):
        entries = data.setdefault(section, {})
        if value is None and name not in DEFAULTS.get(section, {}):
            entries.pop(name, None)
        else:
            entries[name] = value
    return _edit_config(change)


CONFIG_ERROR = ""  # что сказать вслух, если config.json не прочитался


def load_config():
    global CONFIG_ERROR
    CONFIG_ERROR = ""
    cfg = json.loads(json.dumps(DEFAULTS))
    if not _config_blank():
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    for name, value in v.items():  # null — убранная встроенная запись
                        if value is None:
                            cfg[k].pop(name, None)
                        else:
                            cfg[k][name] = value
                else:
                    cfg[k] = v
            missing = [k for k in DEFAULTS if k not in user]
            if missing:  # файл неполный (старая версия) — дописываем недостающее
                _edit_config(lambda data: [data.setdefault(k, DEFAULTS[k]) for k in missing])
                log.info("В config.json добавлены настройки: %s", ", ".join(missing))
        except json.JSONDecodeError as e:
            log.error("config.json с ошибкой (строка %d, символ %d: %s)", e.lineno, e.colno, e.msg)
            CONFIG_ERROR = "config.json"
        except Exception:
            log.exception("config.json не прочитался, использую настройки по умолчанию")
            CONFIG_ERROR = "config.json"
    else:  # файла нет или он пустой — создаём со стандартными настройками
        log.info("config.json пустой или удалён, создаю заново")
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, ensure_ascii=False, indent=2)
    return cfg


CONFIG_ERROR_TEXT = {"ru": "В config.json ошибка, я его не прочитал и работаю со стандартными настройками. "
                           "Проверьте кавычки, запятые и двойные слеши в путях.",
                     "kk": "config.json ішінде қате бар, оны оқи алмадым және стандартты баптаумен жұмыс істеп "
                           "тұрмын. Тырнақша, үтір және жол ішіндегі қос слэштерді тексеріңіз."}

# «стоп» без имени: русские и казахские слова
STOP_RE = re.compile(r"\b(стоп|хватит|молчи|замолчи|помолчи|достаточно|подожди|остановись|перестань|заткнись|"
                     r"умолкни|тоқта|тоқтат|токта|болды|жеткілікті|үндеме|ундеме)\b")


DICTATION_STOP = re.compile(r"\b(стоп|хватит|выключи|закончи|заверши|останови)\s+диктовк|диктовк\w*\s+(стоп|хватит)|"
                            r"диктовк\w*\s+(тоқтат|токтат|өшір|ошир)")


# ---------------------------------------------------------------- движок
class KarEngine(QObject):
    state = Signal(str)        # idle / listening / processing / speaking / error / off
    chat = Signal(str, str)    # роль ("user" / "assistant"), текст
    partial = Signal(str)      # распознанный текст «на лету»
    arm_changed = Signal(str)  # "", "search", "open": что произойдёт с ближайшей фразой
    hide_window = Signal()
    quit_app = Signal()
    language_changed = Signal(str)
    notify = Signal(str, str)  # заголовок, текст: всплывающее сообщение из трея
    hotkey = Signal(str)       # имя сработавшей горячей клавиши

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.wake_re = None
        self.rebuild_wake()
        self.main_py = os.path.join(BASE, "main.py")
        self._make_brains()
        self.local = LocalAI(cfg) if cfg.get("local_ai") else None
        if self.local:
            self.local.on_model_change = lambda name: self._set("local_model", name)
        self.actions = Actions(cfg, BASE, self.cloud or self.claude, save=save_entry)
        self.actions.local = self.local
        self.actions.notify = lambda text: self.jobs.put(("say", text))  # таймер говорит вслух
        self.actions.save_setting = save_setting
        self.actions.start_breaks()
        self.config_mtime = self._config_mtime()
        self.speaker = Speaker(cfg)
        self.jobs = queue.Queue()
        self.awake_until = 0.0
        self.last_partial_wake = 0.0
        self.speaking = False
        self.interrupted = False
        self.busy = False
        self.mic_off = False
        self.arm = ""
        self.speech_words = set()
        self.speech_end = 0.0
        self.cur_state = "idle"
        self.listener = None
        self.history = self._load_history()
        self._history_lock = threading.Lock()
        self.dictation = False
        self.hotkeys = None

    # ----- «мозг»: облачный сервис по ключу, Claude или локальная модель -----
    def _make_brains(self):
        cfg = self.cfg
        self.cloud = make_brain(cfg)                      # Gemini / NVIDIA / OpenRouter / DeepSeek / OpenAI / свой сервер
        self.claude = AI(cfg)
        if self.claude.available and not (cfg.get("enable_ai") or kind_of(cfg.get("ai_key")) == "anthropic"):
            self.claude.client = None                     # старый ключ без галочки enable_ai не используем
        actions = getattr(self, "actions", None)
        if actions:
            actions.ai = self.cloud or self.claude

    def brain(self):
        """Лучший доступный «мозг» или None (тогда — локальная модель / поиск)."""
        if self.cloud:
            return self.cloud
        return self.claude if self.claude.available else None

    def test_ai(self, key, base_url=""):
        """Проверка ключа из окна настроек (вызывать не из главного потока): (получилось ли, текст)."""
        if not base_url and kind_of(key) == "anthropic":
            claude = AI({"ai_key": key})
            reply = claude.ask("ping")
            ok = reply not in (i18n.t("ai_auth"), i18n.t("ai_conn"), i18n.t("ai_status"), i18n.t("ai_rate"))
            return ok, "Claude" if ok else reply
        try:
            cloud = Cloud(key, "", base_url)
        except ValueError:
            return False, i18n.t("ui_ai_unknown")
        if not cloud.available:
            return False, i18n.t("ui_ai_unknown")
        return cloud.test()

    # ----- автозапуск, горячие клавиши -----
    def autostart_enabled(self):
        try:
            return winsys.autostart_enabled(self.main_py)
        except Exception:
            return False

    def set_autostart(self, on):
        return winsys.set_autostart(on, self.main_py)

    def start_hotkeys(self):
        if not self.cfg.get("use_hotkeys", True):
            return
        try:
            self.hotkeys = winsys.GlobalHotkeys(self.cfg.get("hotkeys") or winsys.DEFAULT_HOTKEYS, self.hotkey.emit)
            self.hotkeys.start()
        except Exception:
            log.exception("Горячие клавиши")
            return
        if self.hotkeys.failed:
            names = [self.cfg["hotkeys"].get(n, n) for n in self.hotkeys.failed]
            log.info("Не зарегистрированы горячие клавиши: %s", names)
            self.jobs.put(("say", line("hotkey_busy", t=", ".join(names))))

    # ----- запуск -----
    def start(self):
        threading.Thread(target=self._worker, daemon=True).start()
        if self.local:
            threading.Thread(target=self.local.start_server, daemon=True).start()
        self._start_listener()
        self.start_hotkeys()
        if CONFIG_ERROR:
            self.jobs.put(("say", CONFIG_ERROR_TEXT[i18n.lang()]))
        if self.cfg.get("greet_on_start"):
            self.jobs.put(("say", line("greet")))
        threading.Thread(target=self._prewarm_voice, daemon=True).start()

    def _model_path(self):
        """Папка модели Vosk для текущего языка; если её нет — любая другая из скачанных."""
        models = self.cfg["vosk_models"]
        lang = i18n.lang()
        want = os.path.join(BASE, models.get(lang, ""))
        if os.path.isdir(want):
            return want, True
        for other, rel in models.items():
            path = os.path.join(BASE, rel)
            if os.path.isdir(path):
                log.warning("Модели для языка %s нет, использую %s", lang, other)
                return path, False
        return None, False

    def _start_listener(self):
        if self.listener:
            self.listener.stop()
            self.listener = None
        path, exact = self._model_path()
        if not path:
            self.jobs.put(("say", line("no_model")))
            self.set_state("error")
            return
        if not exact:
            self.jobs.put(("say", line("no_model")))
        self.listener = Listener(path, self.on_final, self.on_partial, self.on_listener_error,
                                 device_name=self.cfg.get("mic_device") or None,
                                 gain=float(self.cfg.get("mic_gain", 1.0)),
                                 sensitivity=int(self.cfg.get("mic_sensitivity", 100)))
        if self.mic_off:
            self.listener.muted.set()
        self.listener.start()

    def _prewarm_voice(self):
        """Через полминуты озвучиваем в фоне постоянные фразы — потом они звучат сразу."""
        time.sleep(30)
        table = i18n.TEXT[i18n.lang()]
        static = []
        for key in ("greet", "listen", "done", "cancel", "unknown", "timer_cancel", "mute", "unmute"):
            value = table.get(key)
            static += list(value) if isinstance(value, (list, tuple)) else [value]
        static += list(self.cfg.get("replies", {}).values())
        self.speaker.prewarm([t for t in static if t and "{" not in t])

    # ----- настройки -----
    def _set(self, key, value):
        self.cfg[key] = value
        save_setting(key, value)

    def rebuild_wake(self):
        words = sorted(self.cfg["wake_words"], key=len, reverse=True)
        self.wake_re = re.compile(r"(^|\s)(" + "|".join(map(re.escape, words)) + r")(?=\s|$|[,.!?])",
                                  re.IGNORECASE)

    def update_settings(self, changes):
        """Применить настройки из окна «Баптаулар»: запомнить и сразу начать использовать."""
        for key, value in changes.items():
            if key == "language":
                continue
            if self.cfg.get(key) != value:
                self._set(key, value)
        self.speaker.configure(self.cfg)
        if {"ai_key", "ai_base_url", "ai_model"} & set(changes):
            self._make_brains()
        if self.listener:
            self.listener.set_device(self.cfg.get("mic_device") or None)
            self.listener.gain = float(self.cfg.get("mic_gain", 1.0))
            self.listener.sensitivity = int(self.cfg.get("mic_sensitivity", 100))
        if "language" in changes and changes["language"] != i18n.lang():
            self.set_language(changes["language"])

    def set_language(self, lang):
        i18n.set_lang(lang)
        self._set("language", i18n.lang())
        self.speaker.configure(self.cfg)
        self._start_listener()
        self.language_changed.emit(i18n.lang())
        log.info("Язык: %s", i18n.lang())

    @property
    def stt_lang(self):
        return "kk-KZ" if i18n.lang() == "kk" else "ru-RU"

    def set_mic_tuning(self, gain=None, sensitivity=None):
        """Громкость ввода и чувствительность — на лету, без записи (пишется кнопкой «Сохранить»)."""
        if gain is not None and self.listener:
            self.listener.gain = float(gain)
        if sensitivity is not None and self.listener:
            self.listener.sensitivity = int(sensitivity)

    def current_mic(self):
        if self.listener and self.listener.active_name:
            return self.listener.active_name
        return self.cfg.get("mic_device") or default_input_name()

    @staticmethod
    def _config_mtime():
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return None

    def reload_config_if_changed(self):
        """Правки config.json (сайты, программы, ответы) подхватываются без перезапуска."""
        mtime = self._config_mtime()
        if mtime == self.config_mtime:
            return
        fresh = load_config()
        self.config_mtime = mtime
        if CONFIG_ERROR:
            self.say(CONFIG_ERROR_TEXT[i18n.lang()])
            return
        for key in ("sites", "apps", "app_aliases", "search_when_unsure", "browser", "replies", "city"):
            self.cfg[key] = fresh[key]
        self.actions.set_lists(self.cfg)
        log.info("config.json перечитан")

    # ----- состояния -----
    def set_state(self, name):
        if name != self.cur_state:
            self.cur_state = name
            self.state.emit(name)

    def rest_state(self):
        return "off" if self.mic_off else "idle"

    def tick(self):
        """Раз в 200 мс из главного потока: окно прослушивания закончилось — возвращаемся к ожиданию."""
        if self.actions.reminders.has_due():
            for r in self.actions.reminders.due():
                self.notify.emit("KAR", r["text"])
                self.jobs.put(("say", line("reminder_due", text=r["text"])))
        if self.dictation:
            return  # во время диктовки микрофон остаётся «слушающим»
        if (self.cur_state == "listening" and time.time() > self.awake_until
                and not self.speaking and not self.busy):
            self.set_state(self.rest_state())
            self.partial.emit("")
            if self.arm:
                self.set_arm("")

    def open_window(self, seconds=None):
        self.awake_until = time.time() + float(seconds or self.cfg["listen_window_seconds"])
        self.set_state("listening")

    def set_arm(self, kind):
        self.arm = kind
        self.arm_changed.emit(kind)

    # ----- история диалога -----
    def _load_history(self):
        try:
            with open(HISTORY_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def log_chat(self, role, text):
        self.chat.emit(role, text)
        with self._history_lock:
            self.history.append({"t": time.strftime("%d.%m.%Y %H:%M"), "role": role, "text": text})
            del self.history[:-int(self.cfg.get("history_size", 500))]
            try:
                with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                    json.dump(self.history, f, ensure_ascii=False)
            except OSError:
                pass

    def clear_history(self):
        with self._history_lock:
            self.history.clear()
            try:
                os.remove(HISTORY_PATH)
            except OSError:
                pass

    # ----- события микрофона (поток слушателя) -----
    def has_wake(self, text):
        return bool(self.cfg.get("use_wake_word", True)) and bool(self.wake_re.search(text.lower()))

    def strip_wake(self, text):
        t = text.lower()
        last = None
        for last in self.wake_re.finditer(t):
            pass
        if last and t[last.end():].strip(" ,.!?"):
            t = t[last.end():]  # берём то, что сказано после имени (до него могло быть эхо)
        t = self.wake_re.sub(" ", t)
        return re.sub(r"^[\s,.!?]+|[\s,.!?]+$", "", t)

    def is_echo(self, text):
        """Микрофон слышит голос самой KAR: все слова фразы есть в том, что она говорит."""
        words = text.lower().replace("ё", "е").split()
        if not words or not self.speech_words:
            return False
        # распознавание искажает слова эха («погода» → «погоду»), поэтому похожие слова тоже считаем
        known = sum(1 for w in words if w in self.speech_words or any(
            len(w) >= 4 and difflib.SequenceMatcher(None, w, k).ratio() >= 0.75 for k in self.speech_words))
        return known >= max(1, round(len(words) * 0.7))

    def wants_interrupt(self, text):
        t = text.lower()
        return (self.has_wake(t) or STOP_RE.search(t)) and not self.is_echo(t)

    def interrupt(self):
        if not self.speaking or self.interrupted:
            return
        self.interrupted = True
        self.speaker.stop()
        self.open_window()
        log.info("KAR перебили")

    def on_partial(self, text):
        now = time.time()
        if self.speaking:
            if self.cfg.get("barge_in_enabled") and self.wants_interrupt(text):
                self.interrupt()
            return
        if self.cur_state == "listening":
            self.partial.emit(text)
        if now - self.last_partial_wake > 3 and now > self.awake_until and self.has_wake(text):
            self.last_partial_wake = now
            self.open_window()

    def on_final(self, text, audio):
        now = time.time()
        if self.actions.autoscroll_active() and STOP_RE.search(text.lower()) and not self.is_echo(text):
            self.actions.stop_background()  # во время автопрокрутки «стоп» слышим и без имени
            return
        if self.speaking and not self.interrupted:
            if not (self.cfg.get("barge_in_enabled") and self.wants_interrupt(text)):
                return  # эхо собственной речи
            self.interrupt()
        if now - self.speech_end < 2.5 and self.is_echo(text):
            return  # хвост собственной речи
        if self.dictation:  # всё сказанное печатается; «стоп диктовка» заканчивает
            if DICTATION_STOP.search(text.lower()):
                self.jobs.put(("text", "выключи диктовку"))
            else:
                self.jobs.put(("dictate", text, audio))
            return
        woke = self.has_wake(text)
        if not woke and now > self.awake_until:
            return
        cmd = self.strip_wake(text) if woke else text
        if STOP_RE.match(cmd.strip()) and len(cmd.split()) <= 3:
            self.awake_until = now + self.cfg["listen_window_seconds"]  # «КАР, стоп» — замолчать и слушать дальше
            self.actions.stop_background()
            return
        if len(cmd) < 2:
            self.open_window()
            self.partial.emit("")
            return
        self.awake_until = 0
        self.partial.emit(cmd)
        self.jobs.put(("voice", cmd, audio, woke))

    def on_listener_error(self, err, kind="mic"):
        self.set_state("error")
        key = "model_broken" if kind == "model" else winsys.diagnose_mic(err)  # понятная причина, а не «не слышно»
        self.jobs.put(("say", line(key)))

    def mic_check_text(self):
        if winsys.mic_privacy_blocked():
            return line("mic_blocked")
        if not self.listener or not self.listener.active_name:
            return line("no_mic")
        return line("mic_ok", t=self.current_mic())

    def preview_voice(self, overrides):
        """Окно настроек: озвучить пример с ещё не сохранёнными настройками голоса."""
        if self.speaking:
            return
        if getattr(self, "_preview", None):
            self._preview.stop()
        cfg = {**self.cfg, **overrides}
        sample = i18n.TEXT[cfg.get("language", i18n.lang())]["preview_text"]
        self._preview = Speaker(cfg)
        threading.Thread(target=self._preview.say, args=(sample,), daemon=True).start()

    # ----- кнопки окна -----
    def toggle_listen(self):
        """Кнопка с микрофоном: слушать ближайшую фразу (или включить микрофон, если он выключен)."""
        if self.mic_off:
            self.set_mic_off(False)
        if self.speaking:
            self.interrupt()
            return
        self.open_window()
        self.partial.emit("")

    def arm_next(self, kind):
        """«Ғаламтордан іздеу» / «Бағдарлама ашу»: ближайшая сказанная фраза станет поиском или открытием."""
        self.set_arm(kind)
        if self.mic_off:
            self.set_mic_off(False)
        self.open_window(max(10, self.cfg["listen_window_seconds"]))

    def set_mic_off(self, off):
        self.mic_off = bool(off)
        if self.listener:
            if off:
                self.listener.muted.set()
            else:
                self.listener.muted.clear()
        self.awake_until = 0
        if not self.speaking and not self.busy:
            self.set_state(self.rest_state())
        self.partial.emit("")

    def stop_or_disconnect(self):
        """«Тоқтату / Ажырату»: сначала остановить речь и фоновые действия; если нечего — выключить микрофон."""
        active = (self.speaking or self.arm or self.actions.pending or self.actions.autoscroll_active()
                  or self.cur_state == "listening")
        if active:
            if self.speaking:
                self.interrupted = True
                self.speaker.stop()
            self.actions.stop_background()
            self.actions.pending = None
            self.set_arm("")
            self.awake_until = 0
            self.set_state(self.rest_state())
            self.partial.emit("")
        else:
            self.set_mic_off(not self.mic_off)

    def text_command(self, text):
        self.jobs.put(("text", text))

    def mic_command(self, action):
        if action == "mic_info":
            return Reply(line("mic_info", t=self.current_mic()))
        devices = [n for n, _ in input_devices()]
        if len(devices) < 2:
            return Reply(line("mic_only_one", t=self.current_mic()))
        cur = self.cfg.get("mic_device") or self.current_mic()
        idx = next((i for i, n in enumerate(devices) if n == cur or cur.startswith(n) or n.startswith(cur)), -1)
        nxt = devices[(idx + 1) % len(devices)]
        self._set("mic_device", nxt)
        if self.listener:
            self.listener.set_device(nxt)
        return Reply(line("mic_set", t=nxt))

    # ----- рабочий поток -----
    def _worker(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while True:
            job = self.jobs.get()
            self.busy = True
            try:
                self._process(job)
            except Exception:
                log.exception("Ошибка обработки %s", job[0])
                self.set_state("error")
                self.say(line("crash"))
                self.set_state(self.rest_state())
            finally:
                self.busy = False

    def _process(self, job):
        kind = job[0]
        if kind == "say":
            if not self.say(job[1]):
                self.set_state(self.rest_state())
            return
        if kind == "dictate":
            self._dictate(job[1], job[2])
            return
        if kind == "voice":
            _, cmd, audio, woke = job
            self.set_state("processing")
            if self.cfg.get("use_google_stt"):
                better = google_stt(audio, self.stt_lang)
                if better and len(better) >= 2:
                    cmd = better.lower()
        else:
            cmd = job[1].strip()
            self.set_state("processing")
        if self.has_wake(cmd):  # имя могло остаться в тексте («кар, нажми …»)
            stripped = self.strip_wake(cmd)
            if len(stripped) < 2:
                self.open_window()  # просто позвали по имени — слушаем
                return
            cmd = stripped
        spoken = cmd
        if self.arm:  # кнопка «іздеу» / «ашу»: фраза — это запрос или название программы
            cmd = {"search": "найди ", "open": "открой "}[self.arm] + cmd
            self.set_arm("")
        log.info("Команда: %s", cmd)
        self.log_chat("user", spoken)
        self.reload_config_if_changed()

        reply = self.actions.handle(cmd)
        if reply is None:
            reply = self._answer(cmd)
        reply = self._finalize(reply)
        if self.say(reply.text):
            return  # перебили — уже слушаем новую команду
        if reply.action == "quit":
            self.quit_app.emit()
        elif reply.action == "hide":
            self.hide_window.emit()
            self.set_state(self.rest_state())
        elif reply.action == "ask":
            self.open_window(float(self.cfg["listen_window_seconds"]))
        else:
            self.open_window(float(self.cfg["followup_seconds"]))

    def _finalize(self, reply):
        """Действия, которые выполняет сам движок: микрофон, диктовка."""
        if reply.action in ("mic_info", "mic_next"):
            return self.mic_command(reply.action)
        if reply.action == "mic_check":
            return Reply(self.mic_check_text())
        if reply.action == "dictation_on":
            automation.restore_external()
            if automation.foreground_is_own():
                return Reply(line("dictation_no_window"))
            self.dictation = True
            self.set_state("listening")
        elif reply.action == "dictation_off":
            self.dictation = False
            self.set_state(self.rest_state())
        return reply

    def _dictate(self, text, audio):
        """Диктовка: распознанная фраза печатается в окно, с которым работает пользователь."""
        if self.cfg.get("use_google_stt"):
            better = google_stt(audio, self.stt_lang)
            if better and len(better) >= 2:
                text = better.lower()
        automation.restore_external()
        if automation.foreground_is_own():
            self.dictation = False
            self.set_state(self.rest_state())
            self.say(line("dictation_no_window"))
            return
        automation.input_text(text + " ")
        self.partial.emit(text)

    def _answer(self, cmd):
        """Фраза — не команда. Если есть ключ ИИ — он понимает её (команда или ответ). Иначе локальная модель;
        если и её нет — поиск в Google (только для вопросов)."""
        brain = self.brain()
        if brain is not None:
            notes = self.actions.notes.prompt()
            try:
                if self.cfg.get("ai_route", True):
                    kind, value = brain.route(cmd, notes)
                else:
                    kind, value = "answer", brain.ask(cmd, notes)
            except CloudError as e:
                return Reply(line(e.key), "answer")
            if kind == "cmd":
                log.info("ИИ понял команду: %r → %r", cmd, value)
                reply = self.actions.handle(value)
                return reply or Reply(line("unknown"), "answer")
            return Reply(value, "answer")
        answer = self.local.ask(cmd) if self.local else None
        if isinstance(answer, str) and answer:
            return Reply(answer, "answer")
        if answer is SEARCH:  # модель сама попросила поиск (погода, новости, курсы)
            return self.actions.fallback(cmd, force_search=True)
        return self.actions.fallback(cmd)  # Ollama недоступна: поиск открываем только для вопросов

    def say(self, text):
        """Озвучивает текст. Возвращает True, если KAR перебили."""
        log.info("KAR: %s", text)
        self.log_chat("assistant", text)
        self.set_state("speaking")
        barge_in = self.cfg.get("barge_in_enabled") and self.listener
        if self.listener and not barge_in:
            self.listener.muted.set()
        self.speech_words = set(re.findall(r"\w+", text.lower().replace("ё", "е")))
        self.interrupted = False
        self.speaking = True
        try:
            self.speaker.say(text)
        finally:
            self.speaking = False
            self.speech_end = time.time()
            if barge_in and not self.interrupted:
                self.listener.reset_req.set()  # выбрасываем эхо её голоса
            if self.listener and not barge_in:
                time.sleep(float(self.cfg.get("echo_tail_seconds", 0.7)))  # затихает эхо колонок
                if not self.mic_off:
                    self.listener.muted.clear()
        return self.interrupted


# ---------------------------------------------------------------- запуск
def build_tray(app, win, engine):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(app_icon(), app)
    menu = QMenu()
    entries = {}

    def add(key, slot):
        act = QAction(menu)
        act.triggered.connect(slot)
        menu.addAction(act)
        entries[key] = act
    add("tray_show", win.bring_up)
    add("tray_listen", lambda: (win.bring_up(), engine.toggle_listen()))
    add("tray_stop", engine.stop_or_disconnect)
    menu.addSeparator()
    add("tray_quit", engine.quit_app.emit)

    def retitle():
        for key, act in entries.items():
            act.setText(i18n.t(key))
        tray.setToolTip(i18n.t("tray_tip"))
    retitle()
    engine.language_changed.connect(lambda _: retitle())
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: win.bring_up()
                           if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                         QSystemTrayIcon.ActivationReason.DoubleClick) else None)
    tray.show()
    return tray


def main():
    cfg = load_config()
    i18n.set_lang(cfg.get("language", "kk"))
    app = QApplication(sys.argv)
    app.setApplicationName("KAR")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    engine = KarEngine(cfg)
    win = MainWindow(engine, cfg)
    tray = build_tray(app, win, engine)
    win.tray = tray
    engine.quit_app.connect(app.quit)
    engine.hide_window.connect(win.hide)
    if tray:
        engine.notify.connect(lambda title, text: tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 8000))

    def on_hotkey(name):
        if name == "talk":
            engine.toggle_listen()
        elif name == "mute":
            engine.set_mic_off(not engine.mic_off)
        elif name == "stop":
            engine.stop_or_disconnect()
        elif name == "hide":
            win.hide() if win.isVisible() else win.bring_up()
        elif name == "type":
            win.bring_up()
            win.input.setFocus()
    engine.hotkey.connect(on_hotkey)

    tick = QTimer(app)  # окно прослушивания + запоминание «чужого» окна для печати и клавиш
    tick.timeout.connect(engine.tick)
    tick.timeout.connect(automation.remember_foreground)
    tick.start(200)

    engine.start()
    win.show()
    code = app.exec()
    if engine.listener:
        engine.listener.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
