"""Заметки о пользователе («запомни, что я учусь в 10 классе»): простой файл notes.md, который можно править
руками. Заметки передаются нейросети, чтобы ответы были по делу. Идея — из capri-desktop (см. NOTICE.md)."""
import os
import re
import threading

MAX_NOTES = 100
MAX_LEN = 200


class Notes:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def items(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [m.group(1).strip() for line in f if (m := re.match(r"^\s*[-*]\s+(.+)$", line))]
        except OSError:
            return []

    def _write(self, items):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# KAR: что я знаю о пользователе\n\n" + "".join(f"- {i}\n" for i in items))

    def add(self, fact):
        """Записать факт. False, если такой уже есть или факт пустой."""
        fact = re.sub(r"\s+", " ", (fact or "")).strip(" .,;")[:MAX_LEN]
        if not fact:
            return False
        with self._lock:
            items = self.items()
            if fact.lower() in (i.lower() for i in items):
                return False
            self._write((items + [fact])[-MAX_NOTES:])
        return True

    def clear(self):
        with self._lock:
            try:
                os.remove(self.path)
            except OSError:
                pass

    def prompt(self):
        """Текст для нейросети или пустая строка."""
        return "\n".join("- " + i for i in self.items())
