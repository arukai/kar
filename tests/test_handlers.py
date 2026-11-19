"""Поведение обработчиков (сеть и Windows подменены)."""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

import i18n


@pytest.fixture(autouse=True)
def _lang():
    i18n.set_lang("ru")


def fake_urlopen(req, timeout=0):
    url = req.full_url if hasattr(req, "full_url") else req
    if "geocoding" in url:
        body = {"results": [{"name": "Алматы", "latitude": 43.25, "longitude": 76.9}]}
    else:
        body = {"current": {"temperature_2m": 18.2, "weather_code": 0, "wind_speed_10m": 3.1},
                "daily": {"temperature_2m_min": [10, 7.4], "temperature_2m_max": [19, 15.6], "weather_code": [0, 61]}}
    m = MagicMock()
    m.__enter__.return_value.read.return_value = json.dumps(body).encode()
    return m


def test_weather_now_in_both_languages(actions):
    with patch("urllib.request.urlopen", fake_urlopen):
        assert "+18°C" in actions.handle("какая погода в алматы").text
        i18n.set_lang("kk")
        assert "ашық" in actions.handle("Алматыдағы ауа райын көрсет").text


def test_weather_tomorrow_uses_tomorrows_forecast(actions):
    with patch("urllib.request.urlopen", fake_urlopen):
        text = actions.handle("погода завтра в алматы").text
        assert "завтра" in text and "+7…+16" in text and "дождь" in text
        i18n.set_lang("kk")
        assert "ертең" in actions.handle("ертең Алматы ауа райы").text


def test_weather_without_network_says_so(actions):
    with patch("urllib.request.urlopen", side_effect=OSError("offline")):
        assert actions.handle("какая погода").text == i18n.t("weather_fail")


def test_timer_fires_and_notifies(actions):
    said = []
    actions.notify = said.append
    assert "1 сек" in actions.handle("таймер на 1 секунду").text
    time.sleep(1.5)
    assert said and "1 сек" in said[0]


def test_timer_cancel_and_ask(actions):
    actions.handle("таймер на 10 минут")
    assert actions.handle("отмени таймер").text == i18n.t("timer_cancel")
    assert actions.handle("отмени таймер").text == i18n.t("timer_none")
    assert actions.handle("таймер").text == i18n.t("timer_ask")


def test_kazakh_timer(actions):
    i18n.set_lang("kk")
    assert "1 сек" in actions.handle("1 секундқа таймер қой").text
    assert actions.handle("таймерді өшір").text == i18n.t("timer_cancel")


@pytest.mark.parametrize("yes", ["да", "иә"])
def test_shutdown_needs_confirmation(actions, yes):
    with patch("actions.subprocess.Popen") as popen:
        assert actions.handle("выключи компьютер").text == i18n.t("power_ask_off")
        assert not popen.called                       # без подтверждения ничего не выключается
        actions.handle(yes)
        popen.assert_called_once()
        assert popen.call_args[0][0] == ["shutdown", "/s", "/t", "10"]


def test_shutdown_confirmation_expires_after_20_seconds(actions):
    with patch("actions.subprocess.Popen") as popen:
        actions.handle("выключи компьютер")
        actions.pending_until = time.time() - 1       # «прошло больше 20 секунд»
        actions.handle("да")
        assert not popen.called


def test_other_phrase_cancels_pending_shutdown(actions):
    with patch("actions.subprocess.Popen") as popen:
        actions.handle("выключи компьютер")
        actions.handle("который час")
        actions.handle("да")
        assert not popen.called


@pytest.mark.parametrize("phrase", ["отмени выключение", "өшіруді болдырма"])
def test_abort_shutdown(actions, phrase):
    with patch("actions.subprocess.run") as run:
        assert actions.handle(phrase).text == i18n.t("shutdown_abort")
        assert run.call_args[0][0] == ["shutdown", "/a"]


def test_generic_query_asks_instead_of_searching_it(actions):
    assert actions.handle("ищи песню").text == i18n.t("ask_query")


def test_kazakh_numbers_are_parsed():
    import actions as A
    assert A.parse_number("громкость елу") == 50
    assert A.parse_number("яркость жетпіс") == 70
    assert A.parse_number("громкость тоқсан бес") == 95


def test_date_and_time_localized(actions):
    i18n.set_lang("kk")
    assert actions.handle("бүгін қай күн").text.startswith("Бүгін ")
