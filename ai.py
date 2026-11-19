"""Ответы на вопросы через Claude (с веб-поиском для свежих фактов). Русский и казахский."""
import datetime
import importlib
import logging
import os

import i18n
from cloud_ai import kind_of, parse_route, router_system

log = logging.getLogger("kar")

EXTRA = {"ru": "Для свежих фактов (новости, курсы, расписания) используй веб-поиск. Командами ПК (открыть сайт "
               "или приложение, громкость, яркость) занимается отдельный модуль; если просят то, чего ты сделать "
               "не можешь, скажи, какую команду произнести, например «КАР, открой ютуб».",
         "kk": "Жаңа деректер (жаңалық, бағам, кесте) үшін веб-іздеуді қолдан. Компьютер командаларын (сайт немесе "
               "бағдарлама ашу, дыбыс, жарықтық) бөлек модуль орындайды; егер сен істей алмайтын нәрсе сұраса, "
               "қандай команда айту керегін айт, мысалы: «КАР, ютубты аш»."}


class AI:
    def __init__(self, cfg):
        self.model = cfg.get("model", "claude-opus-5-5")
        self.history = []
        self.client = None
        self.anthropic = None
        shared = cfg.get("ai_key") if kind_of(cfg.get("ai_key")) == "anthropic" else None  # единое поле «ключ ИИ»
        key = shared or cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if key:
            try:
                self.anthropic = importlib.import_module("anthropic")
            except ImportError:
                log.warning("Пакет anthropic не установлен")
            else:
                self.client = self.anthropic.Anthropic(api_key=key, timeout=60, max_retries=2)

    @property
    def available(self):
        return self.client is not None

    @staticmethod
    def _system(notes=""):
        lang = i18n.lang()
        return (i18n.PROMPTS[lang] + "\n" + EXTRA[lang]
                + (("\n\nЧто известно о пользователе:\n" + notes) if notes else ""))

    def _call(self, system, messages, tools):
        """Запрос с серверным фолбэком при отказе и продолжением при pause_turn. Если сервер не принял
        бета-параметры или инструмент поиска — один раз повторяем обычным запросом."""
        msgs = list(messages)
        for _ in range(4):
            try:
                resp = self.client.beta.messages.create(
                    model=self.model, max_tokens=2000, system=system, messages=msgs,
                    **({"tools": tools} if tools else {}),
                    output_config={"effort": "low"},
                    betas=["server-side-fallback-2026-07-01"],
                    extra_body={"fallbacks": "default"},
                )
            except self.anthropic.BadRequestError as e:
                log.info("Расширенный запрос не принят (%s), повторяю обычным", e)
                resp = self.client.messages.create(model=self.model, max_tokens=2000, system=system, messages=msgs)
            if resp.stop_reason == "pause_turn":
                msgs.append({"role": "assistant", "content": resp.content})
                continue
            if resp.stop_reason == "refusal":
                return i18n.t("ai_refusal")
            text = " ".join(b.text for b in resp.content if b.type == "text").strip()
            return text or i18n.t("ai_empty")
        return i18n.t("ai_long")

    def ask(self, question, notes=""):
        if not self.available:
            return i18n.t("ai_no_key")
        now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        self.history.append({"role": "user", "content": f"[{now}] {question}"})
        self.history = self.history[-12:]
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)
        try:
            answer = self._call(self._system(notes), self.history,
                                [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}])
        except Exception as e:
            self.history.pop()
            return self._explain(e)
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def _explain(self, e):
        a = self.anthropic
        if isinstance(e, a.AuthenticationError):
            return i18n.t("ai_auth")
        if isinstance(e, a.RateLimitError):
            return i18n.t("ai_rate")
        if isinstance(e, a.APIConnectionError):
            return i18n.t("ai_conn")
        if isinstance(e, a.APIStatusError):
            log.error("Ошибка API %s: %s", e.status_code, e.message)
        else:
            log.exception("Claude")
        return i18n.t("ai_status")

    def route(self, utterance, notes=""):
        """Фраза → ('cmd', команда) или ('answer', ответ); ошибки возвращаются ответом-объяснением."""
        if not self.available:
            return "answer", i18n.t("ai_no_key")
        try:
            text = self._call(router_system(notes), [{"role": "user", "content": utterance}], [])
        except Exception as e:
            return "answer", self._explain(e)
        return parse_route(text)

    def summarize_report(self, report):
        if not self.available:
            return None
        try:
            return self._call(self._system(),
                              [{"role": "user", "content": i18n.REPORT_ASK[i18n.lang()] + report[:30000]}], [])
        except Exception:
            log.exception("summarize_report")
            return None
