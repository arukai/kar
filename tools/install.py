"""Установка KAR: библиотеки из requirements.txt и модели распознавания речи (қазақша + русский).

    python tools/install.py

Вызывается из setup.bat. Все сообщения здесь, а не в .bat: cmd плохо читает русский текст в батниках."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_model  # noqa: E402

BASE = download_model.BASE


def main():
    print(f"[KAR] Python {sys.version.split()[0]}")
    if sys.version_info >= (3, 15):
        print("[KAR] Внимание: с Python 3.15 и новее голос Edge не работает, будет голос Windows.")
        print("        Поставь Python 3.13: https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe")
    print("[KAR] Устанавливаю библиотеки / Кітапханаларды орнатып жатырмын...", flush=True)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", os.path.join(BASE, "requirements.txt")])
    if r.returncode:
        print()
        print("[KAR] Библиотеки не установились. Проверь интернет и что Python поставлен")
        print("        с галочкой «Add python.exe to PATH», затем запусти setup.bat ещё раз.")
        return 1
    print()
    if download_model.main():
        return 1
    print()
    print("[KAR] Готово / Дайын. run.bat іске қосыңыз.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
