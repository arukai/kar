"""Облачный «мозг» KAR: любой OpenAI-совместимый сервис (Gemini, NVIDIA, OpenRouter, DeepSeek, OpenAI, свой
сервер) по одному ключу. Провайдер определяется по началу ключа, модель выбирается из списка /models сама.

Второе, что умеет мозг, — «понимать команды»: непонятную фразу (особенно казахскую, где у слов много окончаний)
он переводит в каноническую команду, которую уже умеет выполнять actions.py. Так не нужно описывать регулярками
каждую форму слова.

Идея ключ → провайдер и «мозг как переводчик команд» взята из открытого проекта capri-desktop (см. NOTICE.md).
"""
import datetime
import json
import logging
import re
import urllib.error
import urllib.request

import i18n

log = logging.getLogger("kar")

PROVIDERS = {
    "google": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
    "nvidia": ("NVIDIA", "https://integrate.api.nvidia.com/v1"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com"),
    "openai": ("OpenAI", "https://api.openai.com/v1"),
    "anthropic": ("Claude", "https://api.anthropic.com"),
    "custom": ("OpenAI-compatible", ""),
}
# где взять бесплатный ключ
KEY_HELP = {"nvidia": "https://build.nvidia.com", "google": "https://aistudio.google.com/apikey",
            "openrouter": "https://openrouter.ai/keys"}


def kind_of(key):
    """Какому сервису принадлежит ключ — по его началу."""
    key = (key or "").strip()
    if not key:
        return None
    if key.startswith(("AIza", "AQ.")):
        return "google"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("nvapi-"):
        return "nvidia"
    if re.fullmatch(r"sk-[0-9a-f]{32}", key):
        return "deepseek"
    return "openai" if key.startswith("sk-") else None


def describe_key(key, base_url=""):
    """Название сервиса для окна настроек или None, если ключ не распознан."""
    kind = "custom" if (base_url or "").strip() else kind_of(key)
    return PROVIDERS[kind][0] if kind else None


class CloudError(Exception):
    """Ошибка, которую можно сказать пользователю: .key — ключ фразы i18n."""

    def __init__(self, key, detail=""):
        super().__init__(detail or key)
        self.key = key


def normalize_base(address):
    """Адрес сервера как ввели → .../v1 (без /chat/completions); http:// добавляется, если схемы нет."""
    address = (address or "").strip()
    if not address:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", address, re.I):
        address = "http://" + address
    address = re.sub(r"(/v1)?(/chat/completions)?/?$", "", address, flags=re.I)
    return address + "/v1"


# модели, которые не умеют просто разговаривать
_NOT_CHAT = re.compile(r"embed|rerank|guard|safety|reward|moderation|tts|whisper|transcrib|image|vision|-vl|vl-|"
                       r"audio|speech|dall|clip|parse|ocr|imagen|veo|robotics|aqa|learnlm", re.I)


_CHATTY = re.compile(r"instruct|chat|-it\b|gpt|llama|gemma|mistral|qwen|nemotron|deepseek|claude|gemini|phi|command|"
                     r"glm|granite|smol", re.I)


def _version(model_id):
    nums = re.findall(r"\d+(?:\.\d+)?", model_id)
    return tuple(float(n) for n in nums[:3])


def pick_model(kind, ids):
    """Подходящая для разговора модель из списка или None. Названия моделей часто меняются, поэтому
    ищем по шаблонам, а не по точным именам."""
    ids = [re.sub(r"^models/", "", i) for i in ids]
    chat = [i for i in ids if not _NOT_CHAT.search(i)]
    if not chat:
        return None
    if kind == "google":
        flash = [i for i in chat if re.search(r"gemini-[\d.]+-flash", i)
                 and not re.search(r"lite|thinking|preview|exp|live|8b", i)]
        pool = flash or [i for i in chat if "flash" in i] or chat
        return max(pool, key=_version)
    if kind == "openrouter":
        free = [i for i in chat if i.endswith(":free")]
        for pattern in (r"llama|gemma|qwen|mistral|deepseek|gpt-oss", r"."):
            for i in free:
                if re.search(pattern, i):
                    return i
        return chat[0] if not free else free[0]
    if kind == "nvidia":
        for pattern in (r"gpt-oss-20b", r"llama-3\.[123].*instruct", r"nemotron.*(nano|bolt)", r"qwen.*instruct",
                        r"mistral.*instruct", r"instruct|chat|-it\b|gpt-oss|llama|gemma|mistral|qwen|nemotron"):
            for i in chat:
                if re.search(pattern, i, re.I):
                    return i
        return chat[0]
    if kind == "deepseek":
        return "deepseek-chat" if "deepseek-chat" in chat else next((i for i in chat if i.startswith("deepseek-")), chat[0])
    if kind == "openai":
        minis = [i for i in chat if re.fullmatch(r"gpt-[\d.o]+-mini", i)]
        return max(minis, key=_version) if minis else next((i for i in chat if i.startswith("gpt-")), chat[0])
    return next((i for i in chat if _CHATTY.search(i)), chat[0])  # свой сервер: первая «разговорная» модель


# ------------------------------------------------------------------ понимание команд
COMMAND_HELP = """открой <сайт|программа|папка|диск D> · закрой <программа|окно|вкладка> · перейди на <сайт>
найди <запрос> · найди видео <запрос> · найди картинки <запрос> · найди информацию о <тема> · найди файл <имя>
включи видео <название> · включи песню <название> · ищи в spotify <запрос>
пауза · продолжи · следующий трек · предыдущий трек · что играет
громче · тише · громкость <0-100> · выключи звук · включи звук
сделай <программа> тише · сделай <программа> громче · выключи звук в <программа> · громкость <программа> <0-100>
ярче · темнее · яркость <0-100>
таймер на <время> · отмени таймер · напомни <когда> <что> · какие у меня напоминания
погода в <город> · погода завтра в <город> · который час · какое сегодня число
напиши <текст> · напиши в поиске <текст> · нажми <клавиши> · прокрути вниз · прокрути вверх
новую вкладку · закрой вкладку · сверни окно · скриншот
проверь компьютер · проверь интернет · проверь микрофон · заблокируй компьютер · выключи компьютер · перезагрузи компьютер
запомни этот сайт как <имя> · запомни, что <факт обо мне> · что ты обо мне знаешь
включи диктовку · выключи диктовку
открой ворд и напиши <текст> · открой спотифай и найди песню <название> · открой спотифай и включи песню <название>
сколько заряда · сколько места на диске · сколько оперативной памяти · какой у меня ip
включи тёмную тему · включи светлую тему · включи вайфай · выключи блютуз · извлеки флешку
прижми окно влево · разверни окно · верни потерянные окна · скриншот области · запиши экран
включи игровой режим · освободи место · что запускается с windows · обнови программы"""


def router_system(notes=""):
    lang = "казахском" if i18n.lang() == "kk" else "русском"
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    base = ("Ты — слой понимания голосовых команд ассистента KAR на компьютере с Windows. Пользователь говорит "
            "по-казахски или по-русски; речь распознана автоматически и может быть с ошибками.\n"
            "1) Если фраза — просьба что-то сделать на компьютере, ответь РОВНО одной строкой вида "
            "`CMD: <команда>`. Команда — по-русски, строго в одной из форм ниже (названия сайтов, программ, "
            "запросы и текст оставляй как сказал пользователь, на его языке).\n"
            "2) Если это вопрос или разговор, просто ответь по существу: 1–3 предложения на " + lang +
            " языке, без списков, Markdown, эмодзи и ссылок (ответ зачитают вслух). Слово CMD в таком ответе "
            "не используй.\n"
            "3) Сомневаешься, команда это или вопрос, — отвечай как на разговор. Никогда не выдумывай команды, "
            "которых нет в списке.\n\nКоманды:\n" + COMMAND_HELP + "\n\nСейчас: " + now)
    return base + (("\n\nЧто известно о пользователе:\n" + notes) if notes else "")


def parse_route(text):
    """Ответ модели → ('cmd', команда) или ('answer', текст)."""
    text = (text or "").strip()
    m = re.match(r"^\W*CMD\s*[:：]\s*(.+)$", text, re.I | re.S)
    if m:
        cmd = m.group(1).strip().splitlines()[0].strip(" `\"'«»")
        if cmd:
            return "cmd", cmd[:200]
    return "answer", text


_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")
_THINK = re.compile(r"<think>.*?</think>|<think>.*", re.S)


def clean(text):
    text = _THINK.sub("", text or "")
    text = re.sub(r"[*#_`>|]|\[[^\]]*\]\([^)]*\)", "", text)
    return re.sub(r"\s+", " ", _EMOJI.sub("", text)).strip()


# ------------------------------------------------------------------ сам клиент
class Cloud:
    """Один облачный сервис. opener подменяется в тестах."""

    def __init__(self, key="", model="", base_url="", opener=None, timeout=45):
        self.key = (key or "").strip()
        self.kind = "custom" if (base_url or "").strip() else kind_of(self.key)
        if self.kind == "anthropic":
            raise ValueError("Ключ Claude обслуживает ai.py")
        self.base = normalize_base(base_url) if self.kind == "custom" else (PROVIDERS[self.kind][1] if self.kind else "")
        self.model = model
        self.history = []
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout
        self._ids = None

    @property
    def available(self):
        return bool(self.kind) and (bool(self.key) or self.kind == "custom")

    @property
    def name(self):
        return PROVIDERS[self.kind][0] if self.kind else ""

    def _request(self, path, payload=None):
        headers = {"Content-Type": "application/json", "User-Agent": "KAR/1.0"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        if self.kind == "openrouter":
            headers["X-Title"] = "KAR"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with self._open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            log.info("%s: HTTP %s %s", self.name, e.code, detail)
            if e.code in (401, 403):
                raise CloudError("ai_auth", detail)
            if e.code == 429:
                raise CloudError("ai_rate", detail)
            if e.code in (400, 404) and re.search(r"model", detail, re.I):
                raise CloudError("ai_model", detail)
            raise CloudError("ai_status", detail)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CloudError("ai_conn", str(e))
        except ValueError as e:
            raise CloudError("ai_status", str(e))

    def models(self):
        if self._ids is None:
            data = self._request("/models").get("data", [])
            self._ids = [m.get("id", "") for m in data if m.get("id")]
        return self._ids

    def ensure_model(self):
        if not self.model:
            self.model = pick_model(self.kind, self.models())
            if not self.model:
                raise CloudError("ai_model", "в списке нет подходящей модели")
            log.info("%s: выбрана модель %s", self.name, self.model)
        return self.model

    def _chat(self, system, messages, max_tokens=300, temperature=0.5):
        for attempt in (0, 1):
            self.ensure_model()
            try:
                reply = self._request("/chat/completions", {
                    "model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                    "messages": [{"role": "system", "content": system}] + messages})
                break
            except CloudError as e:
                if e.key == "ai_model" and attempt == 0 and not getattr(self, "_model_fixed", False):
                    self._ids, self.model, self._model_fixed = None, "", True  # модель убрали — выберем другую
                    continue
                raise
        try:
            return clean(reply["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise CloudError("ai_empty")

    def ask(self, question, notes=""):
        """Ответ на вопрос (с памятью о последних репликах)."""
        self.history = (self.history + [{"role": "user", "content": question}])[-8:]
        lang = i18n.lang()
        system = i18n.PROMPTS[lang] + ("\n\nЧто известно о пользователе:\n" + notes if notes else "")
        try:
            answer = self._chat(system, self.history)
        except CloudError:
            self.history.pop()
            raise
        if not answer:
            self.history.pop()
            raise CloudError("ai_empty")
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def route(self, utterance, notes=""):
        """Фраза → ('cmd', команда) или ('answer', ответ). Один запрос и на понимание, и на ответ."""
        text = self._chat(router_system(notes), [{"role": "user", "content": utterance}], max_tokens=200,
                          temperature=0.2)
        if not text:
            raise CloudError("ai_empty")
        kind, value = parse_route(text)
        if kind == "answer":  # обычный разговор попадает в память, чтобы можно было переспросить
            self.history = (self.history + [{"role": "user", "content": utterance},
                                            {"role": "assistant", "content": value}])[-8:]
        return kind, value

    def summarize_report(self, report):
        try:
            return self._chat(i18n.PROMPTS[i18n.lang()], [{"role": "user", "content":
                              i18n.REPORT_ASK[i18n.lang()] + report[:12000]}], max_tokens=400)
        except CloudError:
            return None

    def test(self):
        """Проверка ключа для окна настроек: (получилось ли, текст). Не тратит запросы на разговор."""
        try:
            self.ensure_model()
            self._chat("Answer with one word.", [{"role": "user", "content": "ping"}], max_tokens=8)
            return True, f"{self.name} · {self.model}"
        except CloudError as e:
            return False, i18n.t(e.key)


def make_brain(cfg, anthropic_cls=None):
    """Мозг из настроек: облачный сервис, если в ai_key есть ключ; иначе None."""
    key, base = (cfg.get("ai_key") or "").strip(), (cfg.get("ai_base_url") or "").strip()
    if not key and not base:
        return None
    if not base and kind_of(key) == "anthropic":
        return None  # ключ Claude берёт ai.AI (см. main.py)
    cloud = Cloud(key, cfg.get("ai_model", ""), base)
    return cloud if cloud.available else None
