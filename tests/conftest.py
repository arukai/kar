"""Тесты запускаются на любой ОС: Windows-библиотеки подменяются заглушками."""
import ctypes
import os
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
for name in ("winreg", "winsound"):
    sys.modules.setdefault(name, MagicMock())

import pytest  # noqa: E402


@pytest.fixture
def actions(tmp_path):
    import actions as A
    A.AppIndex.build = lambda self: None   # без сканирования программ
    cfg = {"sites": {"ютуб": "https://www.youtube.com"}, "apps": {}, "app_aliases": {}, "replies": {},
           "city": "Алматы"}
    return A.Actions(cfg, str(tmp_path), None)   # заметки и напоминания пишутся во временную папку
