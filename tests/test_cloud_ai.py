"""Облачный «мозг»: определение сервиса по ключу, выбор модели, запросы к настоящему (локальному) HTTP-серверу."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import cloud_ai
import i18n
from cloud_ai import Cloud, CloudError


# ------------------------------------------------------------------ ключ → сервис
@pytest.mark.parametrize("key,kind", [
    ("AIzaSyA-example", "google"), ("AQ.Ab8RN6", "google"), ("sk-or-v1-abc", "openrouter"),
    ("sk-ant-api03-xyz", "anthropic"), ("nvapi-abcdef", "nvidia"), ("sk-" + "a1" * 16, "deepseek"),
    ("sk-proj-abc123", "openai"), ("", None), ("hello", None),
])
def test_key_prefix_detection(key, kind):
    assert cloud_ai.kind_of(key) == kind


def test_describe_key():
    assert cloud_ai.describe_key("nvapi-x") == "NVIDIA"
    assert cloud_ai.describe_key("junk") is None
    assert cloud_ai.describe_key("", "http://localhost:1234") == "OpenAI-compatible"


@pytest.mark.parametrize("given,expected", [
    ("localhost:1234", "http://localhost:1234/v1"),
    ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
    ("https://example.com/v1/chat/completions", "https://example.com/v1"),
    ("", ""),
])
def test_normalize_base(given, expected):
    assert cloud_ai.normalize_base(given) == expected


# ------------------------------------------------------------------ выбор модели
def test_pick_google_prefers_newest_plain_flash():
    ids = ["models/gemini-2.0-flash", "models/gemini-2.5-flash", "models/gemini-2.5-flash-lite",
           "models/gemini-2.5-pro", "models/text-embedding-004", "models/gemini-2.5-flash-preview-tts"]
    assert cloud_ai.pick_model("google", ids) == "gemini-2.5-flash"


def test_pick_openrouter_takes_free_chat_model():
    ids = ["openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-8b:free"]
    assert cloud_ai.pick_model("openrouter", ids) == "meta-llama/llama-3.3-70b-instruct:free"


def test_pick_nvidia_skips_embedding_models():
    ids = ["nvidia/nv-embedqa-e5-v5", "nvidia/llama-3.2-nv-rerankqa", "meta/llama-3.1-8b-instruct",
           "openai/gpt-oss-20b"]
    assert cloud_ai.pick_model("nvidia", ids) == "openai/gpt-oss-20b"


def test_pick_deepseek_and_openai():
    assert cloud_ai.pick_model("deepseek", ["deepseek-reasoner", "deepseek-chat"]) == "deepseek-chat"
    assert cloud_ai.pick_model("openai", ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "whisper-1",
                                          "text-embedding-3-small"]) == "gpt-4.1-mini"


def test_pick_returns_none_when_nothing_suitable():
    assert cloud_ai.pick_model("nvidia", ["nvidia/nv-embedqa-e5-v5"]) is None
    assert cloud_ai.pick_model("google", []) is None


# ------------------------------------------------------------------ разбор ответа «мозга»
@pytest.mark.parametrize("text,expected", [
    ("CMD: открой ютуб", ("cmd", "открой ютуб")),
    ("  cmd:  громче\nпояснение", ("cmd", "громче")),
    ("`CMD: найди борщ`", ("cmd", "найди борщ")),
    ("Астана — столица Казахстана.", ("answer", "Астана — столица Казахстана.")),
    ("CMD:", ("answer", "CMD:")),
])
def test_parse_route(text, expected):
    assert cloud_ai.parse_route(text) == expected


def test_router_prompt_contains_commands_notes_and_language():
    i18n.set_lang("kk")
    prompt = cloud_ai.router_system("- мен 10 сыныптамын")
    assert "CMD:" in prompt and "открой" in prompt and "казахском" in prompt and "10 сыныптамын" in prompt


def test_clean_removes_markdown_emoji_and_thinking():
    assert cloud_ai.clean("<think>hmm</think>**Привет** 😀 мир") == "Привет мир"


# ------------------------------------------------------------------ настоящий HTTP-сервер
class FakeApi(BaseHTTPRequestHandler):
    log = []
    models = ["text-embedding-3", "good-chat-instruct"]
    chat_status = 200
    chat_text = "Привет!"

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeApi.log.append(("GET", self.path, self.headers.get("Authorization")))
        self._send(200, {"data": [{"id": m} for m in FakeApi.models]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeApi.log.append(("POST", self.path, self.headers.get("Authorization"), body))
        if FakeApi.chat_status != 200:
            self._send(FakeApi.chat_status, {"error": {"message": "model not found" if FakeApi.chat_status == 404 else "x"}})
        else:
            self._send(200, {"choices": [{"message": {"content": FakeApi.chat_text}}]})


@pytest.fixture
def server():
    FakeApi.log, FakeApi.models, FakeApi.chat_status, FakeApi.chat_text = [], ["emb-model", "good-chat-instruct"], 200, "Привет!"
    srv = HTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_cloud_picks_model_and_sends_authorized_chat(server):
    i18n.set_lang("ru")
    c = Cloud("secret-key", base_url=server)
    assert c.ask("привет") == "Привет!"
    assert c.model == "good-chat-instruct"            # embedding-модель пропущена
    get, post = FakeApi.log[0], FakeApi.log[1]
    assert get[1] == "/v1/models" and get[2] == "Bearer secret-key"
    assert post[1] == "/v1/chat/completions" and post[3]["model"] == "good-chat-instruct"
    assert post[3]["messages"][0]["role"] == "system" and post[3]["messages"][-1]["content"] == "привет"


def test_cloud_remembers_conversation(server):
    c = Cloud("k", base_url=server)
    c.ask("первый вопрос")
    c.ask("второй")
    sent = FakeApi.log[-1][3]["messages"]
    assert [m["content"] for m in sent[1:]] == ["первый вопрос", "Привет!", "второй"]


def test_route_returns_command_or_answer(server):
    c = Cloud("k", base_url=server)
    FakeApi.chat_text = "CMD: открой ютуб"
    assert c.route("ютубты аш") == ("cmd", "открой ютуб")
    FakeApi.chat_text = "Астана."
    assert c.route("столица?") == ("answer", "Астана.")
    assert "CMD:" in FakeApi.log[-1][3]["messages"][0]["content"]     # в запросе — список команд


@pytest.mark.parametrize("status,key", [(401, "ai_auth"), (403, "ai_auth"), (429, "ai_rate"), (500, "ai_status")])
def test_http_errors_become_spoken_messages(server, status, key):
    FakeApi.chat_status = status
    with pytest.raises(CloudError) as e:
        Cloud("k", base_url=server).ask("привет")
    assert e.value.key == key and key in i18n.TEXT["ru"] and key in i18n.TEXT["kk"]


def test_no_connection_is_reported():
    with pytest.raises(CloudError) as e:
        Cloud("k", base_url="http://127.0.0.1:1", timeout=1).ask("привет")
    assert e.value.key == "ai_conn"


def test_removed_model_is_replaced_once(server):
    c = Cloud("k", model="old-removed-model", base_url=server)
    FakeApi.chat_status = 404                          # «model not found» для старой модели
    with pytest.raises(CloudError) as e:
        c.ask("привет")                               # после одной замены тоже 404 → ошибка модели
    assert e.value.key == "ai_model"
    assert sum(1 for r in FakeApi.log if r[0] == "GET") == 1   # список моделей запрошен заново один раз


def test_test_button_does_a_real_roundtrip(server):
    ok, text = Cloud("k", base_url=server).test()
    assert ok and "good-chat-instruct" in text
    FakeApi.chat_status = 401
    ok, text = Cloud("k", base_url=server).test()
    assert not ok and text == i18n.t("ai_auth")


def test_make_brain_from_config():
    assert cloud_ai.make_brain({}) is None
    assert cloud_ai.make_brain({"ai_key": "nvapi-x"}).kind == "nvidia"
    assert cloud_ai.make_brain({"ai_key": "sk-ant-x"}) is None          # Claude обслуживает ai.py
    assert cloud_ai.make_brain({"ai_base_url": "localhost:1234"}).available   # свой сервер без ключа
