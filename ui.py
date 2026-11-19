"""Окна KAR — простой интерфейс по макету: главный экран, настройки, справка, история, таймер и звук."""
import math
import time

import threading

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSlider,
                               QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

import datetime

import i18n
from cloud_ai import describe_key
from i18n import t

NAVY, BLUE, GREEN, PURPLE, RED = "#111b33", "#2563eb", "#059669", "#7c3aed", "#dc2626"
STATE_COLORS = {"idle": "#64748b", "listening": GREEN, "processing": BLUE, "speaking": RED, "error": RED,
                "off": "#94a3b8"}

STYLE = f"""
QWidget {{ font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 13px; color: #0f172a; }}
QDialog, QMainWindow, #root {{ background: #ffffff; }}
#header {{ background: {NAVY}; color: #ffffff; font-weight: bold; font-size: 14px; padding: 10px 16px; }}
#nav {{ background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 4px; }}
#nav QPushButton {{ background: transparent; border: 0; padding: 8px 12px; font-weight: bold; color: {NAVY};
                    font-size: 12px; }}
#nav QPushButton:hover {{ color: {BLUE}; }}
#navsep {{ color: #94a3b8; }}
#panel {{ background: #ffffff; border: 1px solid #cbd5e1; }}
#inner {{ background: #ffffff; }}
QScrollArea {{ background: #ffffff; }}
#paneltitle {{ font-weight: bold; font-size: 12px; }}
#stream {{ color: #475569; font-style: italic; font-size: 12px; }}
#hint {{ color: {BLUE}; font-weight: bold; font-size: 12px; }}
QLineEdit, QComboBox, QSpinBox {{ background: #ffffff; border: 1px solid #cbd5e1; padding: 5px 8px;
                                  border-radius: 3px; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {BLUE}; }}
QSlider::groove:horizontal {{ height: 4px; background: #cbd5e1; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {BLUE}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {BLUE}; border-radius: 2px; }}
QProgressBar {{ background: #e2e8f0; border: 0; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: #38bdf8; border-radius: 3px; }}
#settinglabel {{ font-weight: bold; font-size: 11px; color: {NAVY}; margin-top: 6px; }}
#note {{ color: #64748b; font-size: 11px; }}
#cancel {{ background: #94a3b8; color: #ffffff; border: 0; padding: 9px 18px; font-weight: bold; }}
#save {{ background: {BLUE}; color: #ffffff; border: 0; padding: 9px 18px; font-weight: bold; }}
#cancel:hover {{ background: #64748b; }}
#save:hover {{ background: #1d4ed8; }}
"""


def app_icon():
    """Значок KAR: тёмно-синий круг с белым микрофоном."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(BLUE))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    _draw_mic(p, QPointF(32, 32), 1.0, QColor("white"))
    p.end()
    return QIcon(pm)


def _draw_mic(p, c, k, color):
    """Белый микрофон: капсула, дуга, ножка. c — центр, k — масштаб (1.0 = 64 px значок)."""
    pen = QPen(color, 3 * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawRoundedRect(QRectF(c.x() - 6 * k, c.y() - 15 * k, 12 * k, 20 * k), 6 * k, 6 * k)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(c.x() - 11 * k, c.y() - 9 * k, 22 * k, 20 * k), 200 * 16, 140 * 16)
    p.drawLine(QPointF(c.x(), c.y() + 11 * k), QPointF(c.x(), c.y() + 17 * k))
    p.drawLine(QPointF(c.x() - 6 * k, c.y() + 17 * k), QPointF(c.x() + 6 * k, c.y() + 17 * k))


class MicButton(QAbstractButton):
    """Круглая синяя кнопка с микрофоном; цвет и «пульс» зависят от состояния."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(92, 92)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.state = "idle"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(BLUE if self.state in ("idle", "processing") else STATE_COLORS[self.state])
        if self.state in ("listening", "speaking"):  # пульсирующее кольцо
            phase = (math.sin(time.time() * 5) + 1) / 2
            ring = QColor(color)
            ring.setAlpha(int(40 + 70 * phase))
            p.setBrush(ring)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(0, 0, 92, 92))
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(8, 8, 76, 76))
        _draw_mic(p, QPointF(46, 46), 1.15, QColor("white"))
        if self.state == "off":  # перечёркнутый микрофон
            p.setPen(QPen(QColor("white"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(26, 26), QPointF(66, 66))


class WaveWidget(QWidget):
    """Тёмная панель с живой волной: амплитуда следует за громкостью микрофона."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.state = "idle"
        self.setMinimumHeight(120)
        self.caption = t("ui_wave")
        self.level = 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        p.fillPath(path, QColor("#0f172a"))
        p.setPen(QColor("#94a3b8"))
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        p.drawText(QRectF(0, 8, r.width(), 16), Qt.AlignmentFlag.AlignHCenter, self.caption)
        listener = self.engine.listener
        target = min(1.0, listener.level) if listener and self.state == "listening" else 0.0
        if self.state == "speaking":
            target = 0.55 + 0.25 * math.sin(time.time() * 7)
        self.level += (target - self.level) * 0.35  # плавное изменение
        amp = (0.07 + 0.85 * self.level) if self.state != "off" else 0.0
        mid, h, w = r.height() / 2 + 8, r.height() / 2 - 26, r.width() - 40
        pen = QPen(QColor("#38bdf8" if self.state != "off" else "#475569"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        phase = time.time() * 4.5
        prev = None
        for i in range(0, 121):
            x = i / 120
            env = math.sin(math.pi * x) ** 1.6  # края волны затухают
            y = mid - math.sin(x * 14 + phase) * h * amp * env
            pt = QPointF(20 + x * w, y)
            if prev:
                p.drawLine(prev, pt)
            prev = pt


class Bubble(QFrame):
    """Сообщение в чате: синий пузырь у пользователя, зелёный у помощника."""

    def __init__(self, role, text):
        super().__init__()
        self.role = role
        user = role == "user"
        self.setObjectName("bubble_user" if user else "bubble_bot")
        bg, border, title = ("#dbeafe", "#93c5fd", "#1d4ed8") if user else ("#dcfce7", "#86efac", "#047857")
        self.setStyleSheet(f"#{self.objectName()} {{ background: {bg}; border: 1px solid {border}; "
                           f"border-radius: 6px; }} #{self.objectName()} QLabel {{ background: transparent; }}")
        self.title = QLabel()
        self.title.setStyleSheet(f"color: {title}; font-weight: bold; font-size: 11px;")
        body = QLabel("«" + text + "»")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(4)
        lay.addWidget(self.title)
        lay.addWidget(body)
        self.retitle()

    def retitle(self):
        self.title.setText(t("ui_user") if self.role == "user" else t("ui_assistant"))


class ChatView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("inner")
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(14, 10, 14, 10)
        self.lay.setSpacing(12)
        self.lay.addStretch()
        self.setWidget(inner)
        self.bubbles = []

    def add(self, role, text):
        bubble = Bubble(role, text)
        bubble.setMaximumWidth(int(self.viewport().width() * 0.74))
        row = QHBoxLayout()
        if role == "user":
            row.addStretch()
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch()
        self.lay.insertLayout(self.lay.count() - 1, row)
        self.bubbles.append(bubble)
        QTimer.singleShot(30, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def retranslate(self):
        for b in self.bubbles:
            b.retitle()

    def resizeEvent(self, e):
        for b in self.bubbles:
            b.setMaximumWidth(int(self.viewport().width() * 0.74))
        super().resizeEvent(e)


def color_button(text, color, tip=""):
    btn = QPushButton(text)
    btn.setMinimumHeight(54)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tip)
    btn.setStyleSheet(f"QPushButton {{ background: {color}; color: white; border: 3px solid {color}; "
                      f"border-radius: 3px; font-weight: bold; font-size: 13px; }}"
                      f"QPushButton:hover {{ border: 3px solid rgba(15, 23, 42, 0.35); }}"
                      f"QPushButton[armed=\"true\"] {{ border: 3px solid {NAVY}; }}")
    return btn


class MainWindow(QWidget):
    """Главный экран: шапка, навигация и статус, чат + волна + микрофон, четыре цветные кнопки."""

    def __init__(self, engine, cfg):
        super().__init__()
        self.engine, self.cfg, self.tray = engine, cfg, None
        self.setObjectName("root")
        self.setStyleSheet(STYLE)
        self.resize(960, 640)
        self.setMinimumSize(820, 560)

        self.header = QLabel()
        self.header.setObjectName("header")
        # навигация
        nav = QFrame()
        nav.setObjectName("nav")
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(8, 2, 14, 2)
        self.nav_buttons = []
        for i, (key, slot) in enumerate((("ui_home", self.bring_up), ("ui_settings", self.open_settings),
                                         ("ui_help", self.open_help), ("ui_history", self.open_history))):
            if i:
                sep = QLabel("|")
                sep.setObjectName("navsep")
                nav_row.addWidget(sep)
            btn = QPushButton()
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            nav_row.addWidget(btn)
            self.nav_buttons.append((key, btn))
        nav_row.addStretch()
        self.status = QLabel()
        nav_row.addWidget(self.status)

        # левая панель: чат
        left = QFrame()
        left.setObjectName("panel")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 8, 0, 8)
        self.chat_title = QLabel()
        self.chat_title.setObjectName("paneltitle")
        self.chat_title.setContentsMargins(14, 0, 0, 0)
        self.chat = ChatView()
        self.stream = QLabel()
        self.stream.setObjectName("stream")
        self.stream.setContentsMargins(14, 0, 14, 0)
        self.stream.setWordWrap(True)
        self.input = QLineEdit()
        self.input.setContentsMargins(0, 0, 0, 0)
        self.input.returnPressed.connect(self.send_text)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(14, 0, 14, 0)
        input_row.addWidget(self.input)
        left_lay.addWidget(self.chat_title)
        left_lay.addWidget(self.chat, 1)
        left_lay.addWidget(self.stream)
        left_lay.addLayout(input_row)

        # правая панель: волна + микрофон
        right = QFrame()
        right.setObjectName("panel")
        right.setFixedWidth(280)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(14, 14, 14, 14)
        self.wave = WaveWidget(engine)
        self.mic = MicButton()
        self.mic.clicked.connect(engine.toggle_listen)
        self.mic_hint = QLabel()
        self.mic_hint.setObjectName("hint")
        self.mic_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_lay.addWidget(self.wave, 2)
        right_lay.addStretch(1)
        right_lay.addWidget(self.mic, 0, Qt.AlignmentFlag.AlignHCenter)
        right_lay.addWidget(self.mic_hint)
        right_lay.addStretch(1)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        middle.addWidget(left, 1)
        middle.addWidget(right)

        # четыре цветные кнопки
        self.btn_search = color_button("", BLUE)
        self.btn_open = color_button("", GREEN)
        self.btn_tools = color_button("", PURPLE)
        self.btn_stop = color_button("", RED)
        self.btn_search.clicked.connect(lambda: engine.arm_next("search"))
        self.btn_open.clicked.connect(lambda: engine.arm_next("open"))
        self.btn_tools.clicked.connect(self.open_tools)
        self.btn_stop.clicked.connect(engine.stop_or_disconnect)
        buttons = QHBoxLayout()
        buttons.setSpacing(14)
        for b in (self.btn_search, self.btn_open, self.btn_tools, self.btn_stop):
            buttons.addWidget(b)

        body = QVBoxLayout()
        body.setContentsMargins(18, 14, 18, 18)
        body.setSpacing(14)
        body.addWidget(nav)
        body.addLayout(middle, 1)
        body.addLayout(buttons)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header)
        root.addLayout(body, 1)

        engine.state.connect(self.on_state)
        engine.chat.connect(self.chat.add)
        engine.partial.connect(self.on_partial)
        engine.arm_changed.connect(self.on_arm)
        engine.language_changed.connect(lambda _: self.retranslate())
        self.anim = QTimer(self)
        self.anim.timeout.connect(self._animate)
        self.anim.start(40)
        self.state = "idle"
        self.retranslate()
        self.on_state("idle")

    # ----- тексты и состояние -----
    def retranslate(self):
        self.setWindowTitle("KAR")
        self.header.setText(t("ui_title"))
        for key, btn in self.nav_buttons:
            btn.setText(t(key))
        self.chat_title.setText(t("ui_chat"))
        self.chat.retranslate()
        self.btn_search.setText(t("ui_btn_search"))
        self.btn_open.setText(t("ui_btn_open"))
        self.btn_tools.setText(t("ui_btn_tools"))
        self.btn_stop.setText(t("ui_btn_stop"))
        self.wave.caption = t("ui_wave")
        self.input.setPlaceholderText(t("ui_type_hint"))
        self.on_partial("")
        self.on_state(self.state)

    def on_state(self, state):
        self.state = state
        text = t("st_" + state)
        self.status.setText(t("ui_state", s=text))
        self.status.setStyleSheet(f"color: {STATE_COLORS[state]}; font-weight: bold; font-size: 12px;")
        self.mic.state = state
        self.wave.state = state
        self.mic_hint.setText(t("ui_mic_hint_off") if state == "off" else
                              t("ui_mic_hint_on") if state == "listening" else t("ui_mic_hint"))

    def on_partial(self, text):
        if self.engine.arm == "search":
            self.stream.setText(t("arm_search") + ("  «" + text + "»" if text else ""))
        elif self.engine.arm == "open":
            self.stream.setText(t("arm_open") + ("  «" + text + "»" if text else ""))
        elif text:
            self.stream.setText(t("ui_stream", t=text))
        else:
            self.stream.setText(t("ui_stream", t="…"))

    def on_arm(self, kind):
        for name, btn in (("search", self.btn_search), ("open", self.btn_open)):
            btn.setProperty("armed", "true" if kind == name else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.on_partial("")

    def _animate(self):
        if self.isVisible():
            self.wave.update()
            if self.state in ("listening", "speaking"):
                self.mic.update()

    # ----- действия -----
    def send_text(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.engine.text_command(text)

    def bring_up(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def open_settings(self):
        SettingsDialog(self.engine, self.cfg, self).exec()

    def open_help(self):
        HelpDialog(self).exec()

    def open_history(self):
        HistoryDialog(self.engine, self).exec()

    def open_tools(self):
        ToolsDialog(self.engine, self).exec()

    def closeEvent(self, e):
        if self.tray and self.cfg.get("close_to_tray", True):
            e.ignore()
            self.hide()
        else:
            self.engine.quit_app.emit()
            e.accept()


# ---------------------------------------------------------------------------- диалоги
def _header(text):
    label = QLabel(text)
    label.setObjectName("header")
    return label


def _setting_label(text):
    label = QLabel(text)
    label.setObjectName("settinglabel")
    return label


def _slider(lo, hi, value, fmt, on_change=None):
    """Ползунок со значением справа. Возвращает (slider, строка-layout)."""
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    label = QLabel(fmt(value))
    label.setMinimumWidth(70)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    label.setStyleSheet("color: #64748b; font-size: 11px;")

    def changed(v):
        label.setText(fmt(v))
        if on_change:
            on_change(v)
    s.valueChanged.connect(changed)
    row = QHBoxLayout()
    row.addWidget(s, 1)
    row.addWidget(label)
    return s, row


class SettingsDialog(QDialog):
    """«Баптаулар терезесі»: язык, микрофон, скорость речи, синтезатор — как на макете."""
    ai_tested = Signal(bool, str)

    def __init__(self, engine, cfg, parent=None):
        super().__init__(parent)
        self.engine, self.cfg = engine, cfg
        self.setStyleSheet(STYLE)
        self.setWindowTitle(t("ui_set_title"))
        self.setMinimumWidth(480)
        body = QVBoxLayout()
        body.setContentsMargins(22, 8, 22, 18)
        body.setSpacing(2)

        body.addWidget(_setting_label(t("ui_set_lang")))
        self.lang = QComboBox()
        self.lang.addItem("Қазақша (kk-KZ)", "kk")
        self.lang.addItem("Русский (ru-RU)", "ru")
        self.lang.setCurrentIndex(0 if i18n.lang() == "kk" else 1)
        body.addWidget(self.lang)

        body.addWidget(_setting_label(t("ui_set_mic")))
        self.mic = QComboBox()
        try:
            from voice import default_input_name, input_devices
            self.mic.addItem(t("ui_mic_default", n=default_input_name() or "—"), None)
            for name, _ in input_devices():
                self.mic.addItem(name, name)
        except Exception:
            self.mic.addItem(t("ui_mic_default", n="—"), None)
        current = cfg.get("mic_device")
        idx = self.mic.findData(current) if current else 0
        self.mic.setCurrentIndex(max(0, idx))
        body.addWidget(self.mic)

        body.addWidget(_setting_label(t("ui_set_gain")))
        self.gain, row = _slider(10, 400, int(float(cfg.get("mic_gain", 1.0)) * 100), lambda v: f"×{v / 100:.1f}",
                                 lambda v: engine.set_mic_tuning(gain=v / 100))
        body.addLayout(row)
        body.addWidget(_setting_label(t("ui_set_sens")))
        self.sens, row = _slider(0, 100, int(cfg.get("mic_sensitivity", 100)), lambda v: f"{v}%",
                                 lambda v: engine.set_mic_tuning(sensitivity=v))
        body.addLayout(row)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        body.addWidget(self.meter)

        body.addWidget(_setting_label(t("ui_set_speed")))
        self.speed, row = _slider(80, 260, int(cfg.get("voice_speed", 160)), lambda v: f"{v} wpm")
        body.addLayout(row)
        body.addWidget(_setting_label(t("ui_set_volume")))
        self.volume, row = _slider(0, 100, int(cfg.get("voice_volume", 100)), lambda v: f"{v}%")
        body.addLayout(row)

        body.addWidget(_setting_label(t("ui_set_voice")))
        voice_row = QHBoxLayout()
        self.voice = QComboBox()
        self.preview = QPushButton(t("ui_preview"))
        self.preview.setStyleSheet(f"QPushButton {{ background: #e2e8f0; border: 0; padding: 7px 14px; color: {NAVY}; }}"
                                   "QPushButton:hover { background: #cbd5e1; }")
        self.preview.clicked.connect(self.play_preview)
        voice_row.addWidget(self.voice, 1)
        voice_row.addWidget(self.preview)
        body.addLayout(voice_row)
        self.lang.currentIndexChanged.connect(self.fill_voices)
        self.fill_voices()

        body.addWidget(_setting_label(t("ui_set_engine")))
        radios = QHBoxLayout()
        self.r_edge = QRadioButton(t("ui_eng_edge"))
        self.r_sapi = QRadioButton(t("ui_eng_sapi"))
        (self.r_edge if cfg.get("tts_engine", "edge") == "edge" else self.r_sapi).setChecked(True)
        radios.addWidget(self.r_edge)
        radios.addSpacing(30)
        radios.addWidget(self.r_sapi)
        radios.addStretch()
        body.addLayout(radios)
        note = QLabel(t("ui_set_note"))
        note.setObjectName("note")
        note.setWordWrap(True)
        body.addWidget(note)

        body.addWidget(_setting_label(t("ui_set_aikey")))
        key_row = QHBoxLayout()
        self.ai_key = QLineEdit(cfg.get("ai_key", ""))
        self.ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_test = QPushButton(t("ui_ai_test"))
        self.ai_test.setStyleSheet(f"QPushButton {{ background: #e2e8f0; border: 0; padding: 7px 14px; color: {NAVY}; }}"
                                   "QPushButton:hover { background: #cbd5e1; }")
        self.ai_test.clicked.connect(self.test_ai)
        key_row.addWidget(self.ai_key, 1)
        key_row.addWidget(self.ai_test)
        body.addLayout(key_row)
        self.ai_status = QLabel()
        self.ai_status.setObjectName("note")
        self.ai_status.setWordWrap(True)
        body.addWidget(self.ai_status)
        help_label = QLabel(t("ui_ai_help"))
        help_label.setObjectName("note")
        help_label.setWordWrap(True)
        body.addWidget(help_label)
        self.ai_key.textChanged.connect(self.show_ai_kind)
        self.ai_tested.connect(self.show_ai_result)
        self.show_ai_kind()
        self.route = QCheckBox(t("ui_set_route"))
        self.route.setChecked(bool(cfg.get("ai_route", True)))
        body.addWidget(self.route)

        body.addWidget(_setting_label(t("ui_set_city")))
        self.city = QLineEdit(cfg.get("city", ""))
        body.addWidget(self.city)
        body.addSpacing(6)
        self.google = QCheckBox(t("ui_set_google"))
        self.google.setChecked(bool(cfg.get("use_google_stt", True)))
        self.wake = QCheckBox(t("ui_set_wake"))
        self.wake.setChecked(bool(cfg.get("use_wake_word", True)))
        body.addWidget(self.google)
        body.addWidget(self.wake)
        self.autostart = QCheckBox(t("ui_set_autostart"))
        self.autostart.setChecked(engine.autostart_enabled())
        self._autostart_was = self.autostart.isChecked()
        body.addWidget(self.autostart)
        hot = QLabel(t("ui_hotkeys"))
        hot.setObjectName("note")
        hot.setWordWrap(True)
        body.addWidget(hot)
        entries = QPushButton(t("ui_set_entries"))
        entries.setStyleSheet(f"QPushButton {{ background: #e2e8f0; border: 0; padding: 8px; color: {NAVY}; }}"
                              "QPushButton:hover { background: #cbd5e1; }")
        entries.clicked.connect(self.open_entries)
        body.addSpacing(6)
        body.addWidget(entries)

        cancel, save = QPushButton(t("ui_cancel")), QPushButton(t("ui_save"))
        cancel.setObjectName("cancel")
        save.setObjectName("save")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        buttons = QHBoxLayout()
        buttons.setSpacing(18)
        buttons.addWidget(cancel, 1)
        buttons.addWidget(save, 2)
        body.addSpacing(10)
        body.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("inner")
        inner.setLayout(body)
        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(_header(t("ui_set_title")))
        root.addWidget(scroll)
        self.resize(500, min(760, int(QApplication.primaryScreen().availableGeometry().height() * 0.9)))

        self.timer = QTimer(self)  # индикатор громкости микрофона
        self.timer.timeout.connect(lambda: self.meter.setValue(
            int(self.engine.listener.level * 100) if self.engine.listener else 0))
        self.timer.start(80)

    def show_ai_kind(self):
        key = self.ai_key.text().strip()
        if not key:
            self.ai_status.setText(t("ui_ai_none"))
            self.ai_status.setStyleSheet("color: #64748b; font-size: 11px;")
            return
        name = describe_key(key, self.cfg.get("ai_base_url", ""))
        self.ai_status.setText(t("ui_ai_detected", n=name) if name else t("ui_ai_unknown"))
        self.ai_status.setStyleSheet(f"color: {GREEN if name else RED}; font-size: 11px;")

    def test_ai(self):
        key = self.ai_key.text().strip()
        self.ai_status.setText(t("ui_ai_testing"))
        self.ai_test.setEnabled(False)

        def work():
            try:
                ok, text = self.engine.test_ai(key, self.cfg.get("ai_base_url", ""))
            except Exception as e:  # сеть, формат ответа — любая неожиданность не должна «повесить» кнопку
                ok, text = False, str(e)
            self.ai_tested.emit(ok, text)
        threading.Thread(target=work, daemon=True).start()

    def show_ai_result(self, ok, text):
        self.ai_test.setEnabled(True)
        self.ai_status.setText(("✓ " if ok else "✗ ") + text)
        self.ai_status.setStyleSheet(f"color: {GREEN if ok else RED}; font-size: 11px;")

    def fill_voices(self):
        """Голоса выбранного в окне языка; текущий — из настроек."""
        from voice import VOICE_CHOICES
        lang = self.lang.currentData()
        self.voice.clear()
        for label, name in VOICE_CHOICES[lang]:
            self.voice.addItem(label, name)
        idx = self.voice.findData(self.cfg.get("voice_" + lang))
        self.voice.setCurrentIndex(max(0, idx))

    def voice_settings(self):
        lang = self.lang.currentData()
        return {"language": lang, "voice_" + lang: self.voice.currentData(),
                "voice_speed": self.speed.value(), "voice_volume": self.volume.value(),
                "tts_engine": "edge" if self.r_edge.isChecked() else "sapi"}

    def play_preview(self):
        self.engine.preview_voice(self.voice_settings())

    def open_entries(self):
        from entries_dialog import EntriesDialog
        EntriesDialog(self.engine.actions, self).exec()

    def save(self):
        self.engine.update_settings({
            "language": self.lang.currentData(),
            "mic_device": self.mic.currentData(),
            "mic_gain": round(self.gain.value() / 100, 2),
            "mic_sensitivity": self.sens.value(),
            "voice_speed": self.speed.value(),
            "voice_volume": self.volume.value(),
            "tts_engine": "edge" if self.r_edge.isChecked() else "sapi",
            "voice_" + self.lang.currentData(): self.voice.currentData(),
            "city": self.city.text().strip() or self.cfg.get("city", ""),
            "use_google_stt": self.google.isChecked(),
            "use_wake_word": self.wake.isChecked(),
            "ai_key": self.ai_key.text().strip(),
            "ai_route": self.route.isChecked(),
        })
        if self.autostart.isChecked() != self._autostart_was:
            if not self.engine.set_autostart(self.autostart.isChecked()):
                QMessageBox.warning(self, "KAR", t("ed_save_fail", e="Windows registry"))
        self.accept()

    def reject(self):  # «Бас тарту»: вернуть микрофон к сохранённым значениям
        self.engine.set_mic_tuning(gain=float(self.cfg.get("mic_gain", 1.0)),
                                   sensitivity=int(self.cfg.get("mic_sensitivity", 100)))
        super().reject()


HELP = {
    "ru": """<h3>Как пользоваться</h3>
<p>Скажите <b>«КАР»</b> и команду, либо нажмите на значок микрофона, либо введите команду в строке под чатом.
Кнопка «Поиск в интернете» или «Открыть программу» превращает следующую сказанную фразу в запрос или название.</p>
<table cellpadding="4">
<tr><td><b>Программы и сайты</b></td><td>«открой ютуб», «открой калькулятор», «открой папку загрузки», «открой диск D»</td></tr>
<tr><td><b>Поиск</b></td><td>«найди рецепт борща», «найди видео …», «найди картинки …», «найди информацию о …»</td></tr>
<tr><td><b>Музыка и видео</b></td><td>«пауза», «продолжи», «следующий трек», «что играет», «перемотай на 2 минуты», «включи spotify песню …»</td></tr>
<tr><td><b>Звук и яркость</b></td><td>«громче», «тише», «громкость 40», «выключи звук», «яркость 70»</td></tr>
<tr><td><b>Таймер и погода</b></td><td>«таймер на 5 минут», «отмени таймер», «какая погода», «погода в Астане»</td></tr>
<tr><td><b>Текст и клавиши</b></td><td>«напиши привет», «напиши в поиске …», «нажми контрол с», «прокрути вниз»</td></tr>
<tr><td><b>Окна и вкладки</b></td><td>«новая вкладка», «закрой вкладку», «сверни окно», «закрой блокнот»</td></tr>
<tr><td><b>Компьютер</b></td><td>«проверь компьютер», «сделай скриншот», «заблокируй компьютер», «выключи компьютер» (потом «да»)</td></tr>
<tr><td><b>Запоминание</b></td><td>«запомни этот сайт как музыка», «когда я скажу … отвечай …», «который час», «какое число»</td></tr>
<tr><td><b>Напоминания</b></td><td>«напомни завтра в девять позвонить маме», «через десять минут напомни выключить плиту», «какие у меня напоминания»</td></tr>
<tr><td><b>Рутины</b></td><td>«когда я скажу рабочий режим, открой телеграм и ютуб» — потом достаточно сказать «рабочий режим»</td></tr>
<tr><td><b>Файлы</b></td><td>«найди файл отчёт», затем «покажи его» (выделю в Проводнике) или «открой его»</td></tr>
<tr><td><b>Громкость программы</b></td><td>«сделай спотифай тише», «выключи звук в хроме», «громкость дискорд 30»</td></tr>
<tr><td><b>Обо мне и диктовка</b></td><td>«запомни, что я учусь в 10 классе», «что ты обо мне знаешь», «включи диктовку» / «стоп диктовка»</td></tr>
<tr><td><b>Проверки</b></td><td>«проверь интернет», «проверь микрофон»</td></tr>
</table>
<p>Ключ ИИ (Gemini, NVIDIA, OpenRouter и др.) в настройках позволяет говорить свободно: KAR сама поймёт, команда это или вопрос.</p>
<p>Остановить речь: «стоп» или кнопка «Остановить». Остальные вопросы KAR передаёт нейросети (Ollama или Claude) либо открывает поиск.</p>""",
    "kk": """<h3>Қалай қолдану керек</h3>
<p><b>«КАР»</b> деп команда айтыңыз, немесе микрофон белгісін басыңыз, немесе чат астындағы жолға команданы жазыңыз.
«Ғаламтордан іздеу» немесе «Бағдарлама ашу» батырмасы келесі айтылған фразаны сұраныс не атау етеді.</p>
<table cellpadding="4">
<tr><td><b>Бағдарлама мен сайт</b></td><td>«ютубты аш», «калькуляторды аш», «жүктеулер папкасын аш», «D дискісін аш»</td></tr>
<tr><td><b>Іздеу</b></td><td>«борщ рецептін ізде», «ютубтан … ізде», «… суреттерін ізде», «… туралы ақпарат тап»</td></tr>
<tr><td><b>Музыка мен бейне</b></td><td>«музыканы тоқтат», «жалғастыр», «келесі ән», «қандай ән ойнап тұр», «… әнін қос»</td></tr>
<tr><td><b>Дыбыс пен жарықтық</b></td><td>«дыбысты арттыр», «дыбысты азайт», «дыбыс елу», «дыбысты өшір», «жарықтық 70»</td></tr>
<tr><td><b>Таймер мен ауа райы</b></td><td>«5 минутқа таймер қой», «таймерді өшір», «ауа райы қандай», «Астана ауа райы»</td></tr>
<tr><td><b>Мәтін мен перне</b></td><td>«сәлем деп жаз», «іздеу жолына … жаз», «Enter бас», «төмен жылжыт»</td></tr>
<tr><td><b>Терезе мен қойынды</b></td><td>«жаңа қойынды аш», «қойындыны жап», «терезені жап», «блокнотты жап»</td></tr>
<tr><td><b>Компьютер</b></td><td>«компьютерді тексер», «скриншот жаса», «компьютерді блокта», «компьютерді өшір» (кейін «иә»)</td></tr>
<tr><td><b>Есте сақтау</b></td><td>«осы сайтты музыка деп есте сақта», «сағат неше», «бүгін қай күн»</td></tr>
<tr><td><b>Еске салу</b></td><td>«ертең сағат тоғызда анама қоңырау шалуды еске сал», «10 минуттан кейін шәйнекті өшіруді еске сал»</td></tr>
<tr><td><b>Рутина</b></td><td>«жұмыс деп айтсам, ютубты аш телеграмды аш істе» — кейін тек «жұмыс» деп айтсаңыз жеткілікті</td></tr>
<tr><td><b>Файл</b></td><td>«есеп файлын тап», кейін «оны көрсет» (Проводникте белгілейді) немесе «оны аш»</td></tr>
<tr><td><b>Бағдарлама дыбысы</b></td><td>«Spotify дыбысын азайт», «хромның дыбысын өшір», «Discord дыбыс 30»</td></tr>
<tr><td><b>Мен туралы, диктовка</b></td><td>«мен 10 сыныпта оқимын деп есте сақта», «мен туралы не білесің», «диктовканы қос» / «диктовканы тоқтат»</td></tr>
<tr><td><b>Тексеру</b></td><td>«интернетті тексер», «микрофонды тексер»</td></tr>
</table>
<p>Баптауларға ЖИ кілтін (Gemini, NVIDIA, OpenRouter т.б.) енгізсеңіз, еркін сөйлей аласыз: KAR команда ма, сұрақ па — өзі түсінеді.</p>
<p>Сөйлеуді тоқтату: «тоқта» немесе «Тоқтату» батырмасы. Қалған сұрақтарды KAR нейрожеліге (Ollama не Claude) береді немесе іздеуді ашады.
Орыс тіліндегі командалар да жұмыс істейді.</p>""",
}


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.setWindowTitle(t("ui_help_title"))
        view = QTextBrowser()
        view.setHtml(HELP[i18n.lang()])
        view.setFrameShape(QFrame.Shape.NoFrame)
        close = QPushButton(t("ui_close"))
        close.setObjectName("save")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_header(t("ui_help_title")))
        inner = QVBoxLayout()
        inner.setContentsMargins(18, 10, 18, 16)
        inner.addWidget(view)
        inner.addWidget(close)
        lay.addLayout(inner)
        self.resize(720, 560)


class HistoryDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setStyleSheet(STYLE)
        self.setWindowTitle(t("ui_history_title"))
        self.view = QTextBrowser()
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        clear, close = QPushButton(t("ui_clear")), QPushButton(t("ui_close"))
        clear.setObjectName("cancel")
        close.setObjectName("save")
        clear.clicked.connect(self.clear)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(clear)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_header(t("ui_history_title")))
        inner = QVBoxLayout()
        inner.setContentsMargins(18, 10, 18, 16)
        inner.addWidget(self.view)
        inner.addLayout(row)
        lay.addLayout(inner)
        self.resize(640, 520)
        self.fill()

    def fill(self):
        if not self.engine.history:
            self.view.setHtml(f"<p style='color:#64748b'>{t('ui_history_empty')}</p>")
            return
        rows = []
        for e in self.engine.history:
            user = e.get("role") == "user"
            who = t("ui_user") if user else t("ui_assistant")
            color = "#1d4ed8" if user else "#047857"
            text = (e.get("text") or "").replace("<", "&lt;")
            rows.append(f"<p><span style='color:#94a3b8'>{e.get('t', '')}</span> "
                        f"<b style='color:{color}'>{who}</b><br>{text}</p>")
        self.view.setHtml("".join(rows))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def clear(self):
        self.engine.clear_history()
        self.fill()


class ToolsDialog(QDialog):
    """«Таймер / Дыбыс»: запустить таймер и поправить громкость системы."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setStyleSheet(STYLE)
        self.setWindowTitle(t("ui_tools_title"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_header(t("ui_tools_title")))
        body = QVBoxLayout()
        body.setContentsMargins(22, 10, 22, 18)

        body.addWidget(_setting_label(t("ui_timer_min")))
        row = QHBoxLayout()
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 600)
        self.minutes.setValue(5)
        self.minutes.setMinimumWidth(90)
        start, stop = QPushButton(t("ui_timer_start")), QPushButton(t("ui_timer_cancel"))
        start.setObjectName("save")
        stop.setObjectName("cancel")
        start.clicked.connect(lambda: engine.text_command(f"таймер на {self.minutes.value()} минут"))
        stop.clicked.connect(lambda: engine.text_command("отмени таймер"))
        row.addWidget(self.minutes)
        row.addWidget(start)
        row.addWidget(stop)
        body.addLayout(row)

        body.addWidget(_setting_label(t("ui_reminders")))
        self.reminder_list = QListWidget()
        self.reminder_list.setMaximumHeight(110)
        body.addWidget(self.reminder_list)
        remove = QPushButton(t("ui_reminder_remove"))
        remove.setObjectName("cancel")
        remove.clicked.connect(self.remove_reminder)
        body.addWidget(remove)
        self.fill_reminders()

        body.addWidget(_setting_label(t("ui_vol")))
        volume = 50
        self.volume_ok = True
        try:
            from actions import get_volume
            volume = int(get_volume())
        except Exception:
            self.volume_ok = False
        self.vol, vrow = _slider(0, 100, volume, lambda v: f"{v}%", self.set_volume)
        self.vol.setEnabled(self.volume_ok)
        body.addLayout(vrow)
        close = QPushButton(t("ui_close"))
        close.setObjectName("cancel")
        close.clicked.connect(self.accept)
        body.addSpacing(12)
        body.addWidget(close)
        lay.addLayout(body)
        self.setMinimumWidth(420)

    def fill_reminders(self):
        self.reminder_list.clear()
        items = self.engine.actions.reminders.upcoming()
        for r in items:
            when = datetime.datetime.fromisoformat(r["when"])
            from reminders import format_when
            row = f"{format_when(when)} — {r['text']}"
            self.reminder_list.addItem(row)
            self.reminder_list.item(self.reminder_list.count() - 1).setData(Qt.ItemDataRole.UserRole, r["id"])
        if not items:
            self.reminder_list.addItem(t("ui_reminders_empty"))

    def remove_reminder(self):
        item = self.reminder_list.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole) is not None:
            self.engine.actions.reminders.remove(item.data(Qt.ItemDataRole.UserRole))
            self.fill_reminders()

    def set_volume(self, v):
        try:
            from actions import set_volume
            set_volume(v)
        except Exception:
            pass
