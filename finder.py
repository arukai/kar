"""Поиск файлов и папок по названию: «найди файл отчёт», «где мой договор.docx». Сначала ищет там, где файлы
обычно лежат (Рабочий стол, Документы, Загрузки…), и только если ничего нет — по всем дискам. Можно прервать.
Результат выделяется в Проводнике. Идея — из capri-desktop (NOTICE.md), реализация своя."""
import collections
import os
import re
import subprocess
import threading
import time

SKIP_DIRS = re.compile(r"^(windows|winsxs|node_modules|bower_components|\.git|\.svn|\.hg|\.cache|\.nuget|\.npm|"
                       r"\.pnpm-store|__pycache__|\.venv|venv|\$recycle\.bin|system volume information|recovery|"
                       r"config\.msi|programdata|inetcache|code cache|gpucache|crashdumps|d3dscache)$", re.I)
NOISE = re.compile(r"\b(файл\w*|папк\w*|документ\w*|мой|моя|мои|мое|мою|на компьютере|компьютере|пожалуйста|"
                   r"где|найди|покажи|көрсет|файлы|құжат\w*)\b")
MAX_VISITED = 400_000


def plain(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (text or "").lower().replace("ё", "е"))).strip()


def wanted_name(description):
    """«мой файл отчёт по физике» → «отчет по физике»; расширение сохраняется: «отчёт.docx» → «отчет.docx»."""
    description = description or ""
    ext = ""
    m = re.search(r"\.([a-z][a-z0-9]{1,4})\b", description, re.I)  # «.docx»; «1.5» расширением не считаем
    if m:
        ext, description = "." + m.group(1).lower(), description[:m.start()] + description[m.end():]
    quoted = re.search(r"[\"'«“](.+?)[\"'»”]", description)
    if quoted:
        return plain(quoted.group(1)) + ext
    p = plain(description)
    return (re.sub(r"\s+", " ", NOISE.sub(" ", p)).strip() or p) + ext


def score(name, wanted):
    """Насколько имя подходит: 1000 точное, 950 без пробелов, иначе по словам; 0 — не подходит."""
    ext_m = re.search(r"\.[a-z][a-z0-9]{1,4}$", wanted)
    stem, file_ext = os.path.splitext(name)
    if ext_m:
        if file_ext.lower() != ext_m.group(0):
            return 0
        wanted = wanted[:ext_m.start()]
        if not wanted:
            return 500  # просто «.pdf»: подходят все PDF
    actual = plain(stem)
    if not wanted or not actual:
        return 0
    if wanted == actual:
        return 1000
    if actual.replace(" ", "") == wanted.replace(" ", ""):
        return 950
    total = 0
    terms = wanted.split()
    for term in terms:
        if f" {term} " in f" {actual} ":
            total += 100
        elif len(term) >= 3 and term in actual:
            total += 50
        else:
            return 0
    return 100 + total // len(terms)


def search(wanted, roots, cancel=lambda: False, seconds=20.0, limit=8):
    """Обход в ширину: близкие к корню файлы находятся раньше. → [(оценка, путь)] лучшие первыми."""
    found, visited = [], 0
    deadline = time.time() + seconds
    queue = collections.deque(r for r in roots if os.path.isdir(r))
    seen = set()
    while queue and visited < MAX_VISITED and time.time() < deadline and not cancel():
        folder = queue.popleft()
        try:
            entries = list(os.scandir(folder))
        except (OSError, PermissionError):
            continue
        for e in entries:
            visited += 1
            sc = score(e.name, wanted)
            if sc and e.path not in seen:
                seen.add(e.path)
                found.append((sc, e.path))
            try:
                if e.is_dir(follow_symlinks=False) and not SKIP_DIRS.match(e.name):
                    queue.append(e.path)
            except OSError:
                pass
    found.sort(key=lambda x: (-x[0], len(x[1])))
    return found[:limit]


def default_roots():
    home = os.path.expanduser("~")
    names = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
    roots = [os.path.join(home, n) for n in names]
    one = os.path.join(home, "OneDrive")
    roots += [os.path.join(one, n) for n in names] + [os.path.join(one, "Desktop"), os.path.join(one, "Документы")]
    roots += [os.path.join(home, n) for n in ("Рабочий стол", "Документы", "Загрузки")]
    return [r for r in dict.fromkeys(roots) if os.path.isdir(r)]


def all_drives():
    try:
        import psutil
        return [p.mountpoint for p in psutil.disk_partitions(all=False) if "cdrom" not in p.opts.lower()]
    except Exception:
        return [f"{c}:\\" for c in "CDEFGH" if os.path.isdir(f"{c}:\\")]


def reveal(path):
    """Открыть Проводник с выделенным файлом."""
    subprocess.Popen(["explorer", "/select," + os.path.normpath(path)])


class FileFinder:
    def __init__(self, roots=default_roots, wider=all_drives, reveal=reveal, opener=None):
        self.roots, self.wider, self.reveal = roots, wider, reveal
        self.opener = opener or (lambda p: os.startfile(p))
        self.last = []
        self.found_at = 0.0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def find(self, description):
        """Найти по названию. Сначала привычные папки, потом все диски. → [путь, …]"""
        self._stop.clear()
        want = wanted_name(description)
        if not want:
            return []
        hits = search(want, self.roots(), self._stop.is_set, seconds=8)
        if not hits and not self._stop.is_set():
            hits = search(want, self.wider(), self._stop.is_set, seconds=25)
        self.last = [p for _, p in hits]
        self.found_at = time.time()
        return self.last

    @property
    def has_result(self):
        return bool(self.last) and time.time() - self.found_at < 600  # 10 минут «помним» результат

    def show(self):
        if not self.has_result:
            return None
        path = self.last[0]
        if not os.path.exists(path):
            return False
        self.reveal(path)
        return True

    def open(self):
        if not self.has_result:
            return None
        path = self.last[0]
        if not os.path.exists(path):
            return False
        self.opener(path)
        return True
