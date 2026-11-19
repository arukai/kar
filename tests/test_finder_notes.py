"""Поиск файлов и заметки о пользователе."""
import os

import pytest

import finder
from notes import Notes


@pytest.fixture
def tree(tmp_path):
    for rel in ["Documents/отчет по физике.docx", "Documents/old/отчет.docx", "Documents/отчет.pdf",
                "Downloads/report_final.pdf", "node_modules/pkg/отчет.docx", "Desktop/Договор аренды.pdf",
                "Pictures/қазақ тілі.jpg", "Documents/версия 1.5.txt"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return tmp_path


@pytest.fixture
def ff(tree):
    shown, opened = [], []
    f = finder.FileFinder(roots=lambda: [str(tree / d) for d in ("Desktop", "Documents", "Downloads", "Pictures")],
                          wider=lambda: [str(tree)], reveal=shown.append, opener=opened.append)
    f.shown, f.opened, f.root = shown, opened, tree
    return f


def names(ff, query):
    return [os.path.relpath(p, ff.root).replace("\\", "/") for p in ff.find(query)]


@pytest.mark.parametrize("desc,wanted", [
    ("мой файл отчёт", "отчет"), ("найди документ договор аренды", "договор аренды"),
    ("«отчёт по физике»", "отчет по физике"), ("отчёт.docx", "отчет.docx"), ("қазақ тілі", "қазақ тілі"),
])
def test_wanted_name(desc, wanted):
    assert finder.wanted_name(desc).replace("найди ", "") == wanted


def test_exact_and_partial_matches_ranked(ff):
    assert names(ff, "отчёт")[0] in ("Documents/old/отчет.docx", "Documents/отчет.pdf")   # точное имя — первым
    assert "Documents/отчет по физике.docx" in names(ff, "отчёт")


def test_extension_filters(ff):
    assert names(ff, "отчёт.docx") == ["Documents/old/отчет.docx", "Documents/отчет по физике.docx"] or \
        set(names(ff, "отчёт.docx")) == {"Documents/old/отчет.docx", "Documents/отчет по физике.docx"}
    assert names(ff, "отчёт.pdf") == ["Documents/отчет.pdf"]


def test_junk_folders_are_skipped(ff):
    assert not any("node_modules" in n for n in names(ff, "отчёт"))


def test_kazakh_letters_and_version_numbers(ff):
    assert names(ff, "қазақ тілі") == ["Pictures/қазақ тілі.jpg"]
    assert names(ff, "версия 1.5") == ["Documents/версия 1.5.txt"]   # «.5» — не расширение, поиск не ломается


def test_nothing_found(ff):
    assert names(ff, "несуществующий") == []


def test_wider_search_runs_when_usual_places_are_empty(ff, tree):
    ff.roots = lambda: [str(tree / "Pictures")]            # там отчёта нет
    assert any("отчет" in n for n in names(ff, "отчёт"))   # нашёлся при поиске по всей «диску»


def test_show_and_open_use_first_result(ff):
    assert ff.show() is None and ff.open() is None         # ещё ничего не искали
    ff.find("договор аренды")
    assert ff.show() is True and ff.shown[0].endswith("Договор аренды.pdf")
    assert ff.open() is True and ff.opened[0].endswith("Договор аренды.pdf")
    os.remove(ff.last[0])
    assert ff.show() is False                               # файл исчез


def test_search_can_be_cancelled(tree):
    assert finder.search("отчет", [str(tree)], cancel=lambda: True) == []


# ------------------------------------------------------------------ заметки
def test_notes_roundtrip_and_dedupe(tmp_path):
    n = Notes(str(tmp_path / "notes.md"))
    assert n.add("  мен 10 сыныпта оқимын. ") and not n.add("Мен 10 сыныпта оқимын")
    assert n.add("люблю чай") and n.items() == ["мен 10 сыныпта оқимын", "люблю чай"]
    assert "- люблю чай" in n.prompt()
    n.clear()
    assert n.items() == [] and n.prompt() == ""


def test_notes_file_is_hand_editable(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# заметки\n\n- меня зовут Аружан\n* учусь в 10 классе\nпросто текст\n", encoding="utf-8")
    assert Notes(str(path)).items() == ["меня зовут Аружан", "учусь в 10 классе"]


def test_notes_empty_fact_rejected(tmp_path):
    assert not Notes(str(tmp_path / "n.md")).add("  .,; ")
