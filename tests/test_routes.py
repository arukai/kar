"""Какой обработчик получит фразу. Включает фразы, которые раньше ломались (по логу пользователя)."""
import re

import pytest

import kazakh


def route_of(actions, phrase):
    import actions as A
    import automation
    cmd = kazakh.canon(phrase) or phrase
    c = A.norm(cmd)
    special = (A.REMEMBER_RE, A.FORGET_RE, A.REPLY_RE, A.FORGET_REPLY_RE, A.ROUTINE_A, A.ROUTINE_B)
    if not any(re.match(p, c) for p in special) and automation.split_chain(c):
        return "chain"
    for pattern, fn in actions.routes:
        if re.search(pattern, c):
            return getattr(fn, "__name__", "lambda")
    return None


CASES = [
    # --- раньше ломалось
    ("включи spotify песню change of tanks", "spotify"),
    ("в этом spotify ищи lil peep", "spotify"),
    ("ищи песню", "search"),                 # внутри — просьба уточнить запрос, а не поиск слова «песню»
    ("напиши в поиске spotify deftones change", "type_cmd"),
    # --- новое
    ("какая погода", "weather"), ("погода в астане", "weather"), ("погода завтра в алматы", "weather"),
    ("поставь таймер на 5 минут", "timer"), ("таймер 30 секунд", "timer"), ("отмени таймер", "timer"),
    ("отмени выключение", "abort_shutdown"),
    # --- прежний функционал
    ("открой ютуб", "open"), ("найди рецепт борща", "search"), ("закрой блокнот", "close"),
    # --- казахские
    ("ютубты аш", "open"), ("5 минутқа таймер қой", "timer"), ("Алматыдағы ауа райын көрсет", "weather"),
    ("ертең ауа райы қандай", "weather"), ("Spotify-дан Deftones ізде", "spotify"),
    ("сәлем деп жаз", "type_cmd"), ("өшіруді болдырма", "abort_shutdown"), ("борщ рецептін ізде", "search"),
]


@pytest.mark.parametrize("phrase,expected", CASES)
def test_route(actions, phrase, expected):
    got = route_of(actions, phrase)
    if expected is None:
        pytest.skip("не проверяется")
    assert got is not None and expected in got, f"{phrase!r} → {got}"
