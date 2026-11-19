"""Ставит офлайн-модели распознавания речи Vosk в папку models/: казахскую и русскую.

    python tools/download_model.py

Вызывается из setup.bat / setup.bat арқылы шақырылады.
Русская модель лежит в самом архиве (bundled/), казахскую (~42 МБ) скачиваем с сайта разработчиков Vosk.
Повторный запуск ничего не делает, если модели уже на месте.
Если казахская модель не скачалась, KAR всё равно запустится (распознавание пойдёт через Google), а модель
можно положить вручную: https://alphacephei.com/vosk/models → vosk-model-small-kz-0.15 → папка models/."""
import os
import shutil
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(BASE, "models")
# (название, нужна ли обязательно, есть ли копия в bundled/)
WANTED = (("vosk-model-small-kz-0.15", False), ("vosk-model-small-ru-0.22", False))


def ready(name):
    return os.path.isfile(os.path.join(MODELS, name, "am", "final.mdl"))


def download(url, path):
    with urllib.request.urlopen(url, timeout=60) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 2**20:.0f} / {total / 2**20:.0f} МБ / MB", end="", flush=True)
    print()
    if total and done < total:
        raise IOError("загрузка оборвалась / download interrupted")


def unpack(name, archive):
    shutil.rmtree(os.path.join(MODELS, name), ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(MODELS)


def install(name):
    if ready(name):
        print(f"[KAR] {name}: уже установлена / орнатылған.")
        return True
    bundled = os.path.join(BASE, "bundled", name + ".zip")
    if os.path.isfile(bundled):
        print(f"[KAR] {name}: распаковываю / ашып жатырмын...")
        try:
            unpack(name, bundled)
        except (OSError, zipfile.BadZipFile) as e:
            print(f"  не получилось ({e}), попробую скачать / жүктеп көрейін")
    if not ready(name):
        tmp = os.path.join(MODELS, name + ".zip")
        url = f"https://alphacephei.com/vosk/models/{name}.zip"
        print(f"[KAR] {name}: скачиваю / жүктеп жатырмын (~45 МБ)...")
        for attempt in range(3):
            try:
                download(url, tmp)
                unpack(name, tmp)
                os.remove(tmp)
                break
            except Exception as e:
                print(f"  не получилось ({e}), попытка {attempt + 2} из 3..." if attempt < 2 else f"  ошибка: {e}")
        else:
            print(f"[KAR] Не удалось скачать {name}. Скачайте вручную / Қолмен жүктеңіз: {url}")
            print(f"      и распакуйте в папку / және мына папкаға шығарыңыз: {MODELS}")
    return ready(name)


def main():
    os.makedirs(MODELS, exist_ok=True)
    results = {name: install(name) for name, _ in WANTED}
    if not any(results.values()):
        print("[KAR] Ни одна модель не установлена / Ешбір модель орнатылмады.")
        return 1
    for name, ok in results.items():
        if not ok:
            print(f"[KAR] Внимание: {name} не установлена. Этот язык будет работать хуже (через Google).")
    print("[KAR] Модели готовы / Модельдер дайын.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
