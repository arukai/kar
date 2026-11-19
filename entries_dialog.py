"""Окно «Менің сайттарым / Мои сайты»: сайты, программы и свои ответы без правки config.json руками."""
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from i18n import t

STYLE = """
QDialog { background: #ffffff; color: #0f172a; }
QLabel { color: #0f172a; }
QTabWidget::pane { border: 1px solid #cbd5e1; }
QTabBar::tab { background: #e2e8f0; color: #0f172a; padding: 6px 14px; border: 1px solid #cbd5e1; }
QTabBar::tab:selected { background: #2563eb; color: #ffffff; }
QTreeWidget, QLineEdit { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 3px;
                         selection-background-color: #2563eb; selection-color: #ffffff; }
QHeaderView::section { background: #e2e8f0; color: #0f172a; border: 0; padding: 4px; font-weight: bold; }
QPushButton { background: #2563eb; color: #ffffff; border: 0; padding: 6px 16px; border-radius: 3px; }
QPushButton:hover { background: #1d4ed8; }
"""
URL_RE = re.compile(r"^(https?://)?[\w\-]+(\.[\w\-]+)*\.[a-zа-яәғқңөұүһі]{2,}(:\d+)?(/\S*)?$", re.IGNORECASE)


def _clipboard_url():
    text = (QGuiApplication.clipboard().text() or "").strip()
    return text if URL_RE.match(text) else ""


def _buttons(dialog, on_ok):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(t("ui_cancel"))
    box.accepted.connect(on_ok)
    box.rejected.connect(dialog.reject)
    return box


class SiteForm(QDialog):
    """Название + адрес сайта. Адрес подставляется из буфера обмена, если там ссылка."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("ed_new_site"))
        self.setStyleSheet(STYLE)
        self.name = QLineEdit()
        self.name.setPlaceholderText(t("ed_example_name"))
        self.url = QLineEdit(_clipboard_url())
        self.url.setPlaceholderText(t("ed_example_url"))
        form = QFormLayout(self)
        form.addRow(QLabel(t("ed_site_help")))
        form.addRow(t("ed_name") + ":", self.name)
        form.addRow(t("ed_url") + ":", self.url)
        form.addRow(_buttons(self, self._check))
        self.resize(460, 0)

    def _check(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "KAR", t("ed_need_name"))
        elif not URL_RE.match(self.url.text().strip()):
            QMessageBox.warning(self, "KAR", t("ed_bad_url"))
        else:
            self.accept()

    def values(self):
        url = self.url.text().strip()
        return self.name.text().strip(), url if url.startswith("http") else "https://" + url


class ReplyForm(QDialog):
    """Своя фраза и ответ KAR на неё."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("ed_new_reply"))
        self.setStyleSheet(STYLE)
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText(t("ed_example_phrase"))
        self.answer = QLineEdit()
        self.answer.setPlaceholderText(t("ed_example_answer"))
        form = QFormLayout(self)
        form.addRow(QLabel(t("ed_reply_help")))
        form.addRow(t("ed_phrase") + ":", self.phrase)
        form.addRow(t("ed_answer") + ":", self.answer)
        form.addRow(_buttons(self, self._check))
        self.resize(460, 0)

    def _check(self):
        if not self.phrase.text().strip() or not self.answer.text().strip():
            QMessageBox.warning(self, "KAR", t("ed_need_both"))
        else:
            self.accept()

    def values(self):
        return self.phrase.text().strip(), self.answer.text().strip()


class EntriesDialog(QDialog):
    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle(t("ed_title"))
        self.setStyleSheet(STYLE)
        self.tabs = QTabWidget()
        self.trees = {}
        for section, title, first, column in (("sites", t("ed_sites"), t("ed_name"), t("ed_url")),
                                              ("apps", t("ed_apps"), t("ed_name"), t("ed_target")),
                                              ("replies", t("ed_replies"), t("ed_phrase"), t("ed_answer"))):
            tree = QTreeWidget()
            tree.setHeaderLabels([first, column])
            tree.setRootIsDecorated(False)
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.trees[section] = tree
            self.tabs.addTab(tree, title)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.tabs.currentChanged.connect(self._update_hint)
        self._update_hint()
        add, remove, close = QPushButton(t("ed_add")), QPushButton(t("ed_remove")), QPushButton(t("ui_close"))
        add.clicked.connect(self.add)
        remove.clicked.connect(self.remove)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.hint)
        lay.addWidget(self.tabs)
        lay.addLayout(row)
        self.resize(640, 520)
        self.refresh()

    def _book(self, section):
        return {"sites": self.actions.sites, "apps": self.actions.apps, "replies": self.actions.replies}[section]

    def refresh(self):
        for section, tree in self.trees.items():
            book = self._book(section)
            tree.clear()
            for name in sorted(book):
                tree.addTopLevelItem(QTreeWidgetItem([name, str(book[name])]))

    def _section(self):
        return ("sites", "apps", "replies")[self.tabs.currentIndex()]

    def _update_hint(self, *_):
        self.hint.setText(t("ed_hint_replies") if self._section() == "replies" else t("ed_hint"))

    def _save(self, section, name, target):
        err = self.actions.remember(section, name, target)
        if err:
            QMessageBox.warning(self, "KAR", t("ed_save_fail", e=err))
            return False
        self.refresh()
        return True

    def add(self):
        section = self._section()
        if section == "replies":
            form = ReplyForm(self)
            if form.exec():
                self._save("replies", *form.values())
        elif section == "sites":
            form = SiteForm(self)
            if form.exec():
                self._save("sites", *form.values())
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, t("ed_pick_app"), os.path.join(os.path.expanduser("~"), "Desktop"),
                "*.exe *.lnk *.url *.bat *.cmd;;*")
            if not path:
                return
            guess = os.path.splitext(os.path.basename(path))[0].lower()
            name, ok = QInputDialog.getText(self, t("ed_name"), t("ed_ask_name"), text=guess)
            if ok and name.strip():
                self._save("apps", name.strip(), os.path.normpath(path))

    def remove(self):
        item = self.trees[self._section()].currentItem()
        if not item:
            QMessageBox.information(self, "KAR", t("ed_choose_row"))
            return
        name = item.text(0)
        box = QMessageBox(QMessageBox.Icon.Question, "KAR", t("ed_forget", n=name), parent=self)
        yes = box.addButton(t("ed_forget_btn"), QMessageBox.ButtonRole.YesRole)
        box.addButton(t("ui_cancel"), QMessageBox.ButtonRole.NoRole)
        box.exec()
        if box.clickedButton() is yes:
            self._save(self._section(), name, None)
