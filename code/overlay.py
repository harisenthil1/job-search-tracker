from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QWidget,
)

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent
DATABASE_DIR = PROJECT_ROOT / "storage" / "database"
SETTINGS_FILE = DATABASE_DIR / "overlay_settings.json"
OVERLAY_PID_FILE = DATABASE_DIR / "overlay.pid"
BASE_URL = "http://127.0.0.1:8765"
LOCAL_TICK_MS = 200
RECONNECT_MS = 750


class StatusDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "offline"  # offline | idle | green | red
        self._blink_on = True
        self.setFixedSize(18, 18)

    def set_mode(self, mode: str, blink_on: bool = True):
        if mode != self._mode or blink_on != self._blink_on:
            self._mode = mode
            self._blink_on = blink_on
            self.update()

    def paintEvent(self, event):
        colors = {
            "offline": QColor("#4b5563"),
            "idle": QColor("#64748b"),
            "green": QColor("#22c55e"),
            "red": QColor("#ef4444") if self._blink_on else QColor("#31181b"),
        }
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colors.get(self._mode, colors["offline"]))
        painter.drawEllipse(2, 2, 14, 14)


class DragFrame(QFrame):
    drag_moved = Signal(QPoint)
    drag_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_origin = None
        self._window_origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = self.window().pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.drag_moved.emit(self._window_origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self._window_origin = None
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)

        self.setWindowTitle("Search")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(372)
        self.setMaximumWidth(516)

        self.net = QNetworkAccessManager(self)
        self.last_state = None
        self.state_received_ms = 0
        self.post_in_flight = False
        self.shutdown_in_flight = False
        self.red_phase = True
        self.ws = QWebSocket()
        self.ws.textMessageReceived.connect(self._ws_message)
        self.ws.connected.connect(self._ws_connected)
        self.ws.disconnected.connect(self._ws_disconnected)

        self._build_ui()
        self._build_tray()
        self._restore_position()

        self.local_timer = QTimer(self)
        self.local_timer.timeout.connect(self._local_tick)
        self.local_timer.start(LOCAL_TICK_MS)

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._toggle_blink)
        self.blink_timer.start(450)

        QTimer.singleShot(0, self._connect_ws)

    def _build_ui(self):
        self.shell = DragFrame(self)
        self.shell.setObjectName("shell")
        self.shell.drag_moved.connect(self.move)
        self.shell.drag_finished.connect(self._save_position)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.shell)

        row = QHBoxLayout(self.shell)
        row.setContentsMargins(10, 5, 6, 5)
        row.setSpacing(7)

        self.dot = StatusDot()
        row.addWidget(self.dot, 0, Qt.AlignVCenter)

        text_wrap = QWidget()
        text_wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_layout = QHBoxLayout(text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)

        self.metro = QLabel("Search offline")
        self.metro.setObjectName("metro")
        self.metro.setTextInteractionFlags(Qt.NoTextInteraction)
        self.metro.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.metro.setMinimumWidth(150)
        text_layout.addWidget(self.metro)
        row.addWidget(text_wrap, 1)

        self.easy = QPushButton("Easy")
        self.hard = QPushButton("Hard")
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("close")

        for button in (self.easy, self.hard):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(68, 35)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFixedSize(26, 26)

        self.easy.clicked.connect(lambda: self.record_application("easy"))
        self.hard.clicked.connect(lambda: self.record_application("hard"))
        self.close_btn.clicked.connect(self.shutdown_search)

        row.addWidget(self.easy)
        row.addWidget(self.hard)
        row.addWidget(self.close_btn)

        self.setStyleSheet(
            """
            #shell {
                background: #111827;
                border: 1px solid #334155;
                border-radius: 10px;
            }
            QLabel#metro {
                color: #f8fafc;
                font-size: 16px;
                font-weight: 600;
                padding: 0 2px;
            }
            QPushButton {
                background: #1f2937;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover:enabled { background: #334155; }
            QPushButton:pressed:enabled { background: #475569; }
            QPushButton:disabled {
                color: #64748b;
                background: #172033;
                border-color: #253247;
            }
            QPushButton#close {
                background: transparent;
                border: none;
                color: #94a3b8;
                font-size: 18px;
                font-weight: 400;
            }
            QPushButton#close:hover { color: #f8fafc; background: #1f2937; }
            """
        )

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Search overlay")
        menu = QMenu()
        show_action = QAction("Show overlay", self)
        show_action.triggered.connect(self._show_overlay)
        quit_action = QAction("Quit Search", self)
        quit_action.triggered.connect(self.shutdown_search)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_overlay()

    def _show_overlay(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _restore_position(self):
        pos = None
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "x" in data and "y" in data:
                pos = QPoint(int(data["x"]), int(data["y"]))
        except Exception:
            pass

        if pos is not None:
            self.move(pos)
            return

        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.adjustSize()
            x = area.x() + max(8, (area.width() - self.width()) // 2)
            y = area.y() + 10
            self.move(x, y)

    def _save_position(self):
        try:
            SETTINGS_FILE.write_text(
                json.dumps({"x": self.x(), "y": self.y()}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_position()
        event.accept()

    def shutdown_search(self):
        if self.shutdown_in_flight:
            return
        self.shutdown_in_flight = True
        self._save_position()

        # Ask the local backend to terminate itself after a short grace period.
        # We close the overlay immediately; the server exits about 5 seconds later.
        req = QNetworkRequest(QUrl(f"{BASE_URL}/api/shutdown"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        reply = self.net.post(req, b"{}")

        # Quit as soon as the server acknowledges the shutdown request.  Keep a
        # fallback so the overlay can still close if the backend is unreachable.
        reply.finished.connect(QApplication.instance().quit)
        QTimer.singleShot(1500, QApplication.instance().quit)

    def _connect_ws(self):
        if self.ws.state() in (QAbstractSocket.ConnectingState, QAbstractSocket.ConnectedState):
            return
        self.ws.open(QUrl("ws://127.0.0.1:8765/ws"))

    def _ws_connected(self):
        # The server immediately sends a full authoritative state snapshot.
        pass

    def _ws_disconnected(self):
        self._set_offline()
        QTimer.singleShot(RECONNECT_MS, self._connect_ws)

    def _ws_message(self, message: str):
        try:
            payload = json.loads(message)
            if payload.get("type") != "state":
                return
            state = payload.get("state") or {}
            self._accept_state(state)
        except Exception:
            return

    def _accept_state(self, state: dict):
        self.last_state = state
        self.state_received_ms = time.monotonic() * 1000.0
        self._render_state(state)

    def _live_delta(self) -> float:
        if not (self.last_state or {}).get("running") or not self.state_received_ms:
            return 0.0
        return max(0.0, (time.monotonic() * 1000.0 - self.state_received_ms) / 1000.0)

    def _live_over(self, state: dict) -> bool:
        if not state.get("running"):
            return False
        active = state.get("active") or {}
        bucket = active.get("bucket")
        bs = (state.get("bucket_status") or {}).get(bucket, {})
        try:
            used = float(bs.get("used_seconds", 0.0)) + self._live_delta()
            allowed = float(bs.get("allowed_seconds", 0.0))
            return used >= allowed if allowed > 0 else used > 0
        except Exception:
            return bool(bs.get("over", False))

    def _local_tick(self):
        state = self.last_state or {}
        if not state.get("running"):
            return
        # Only the threshold can change while nothing else happens. Compute it
        # locally so the status dot turns red instantly without HTTP polling.
        self._render_status_only(state)

    def _render_status_only(self, state: dict):
        if not state.get("running"):
            return
        self.dot.set_mode("red" if self._live_over(state) else "green", self.red_phase)

    def _render_state(self, state: dict):
        running = bool(state.get("running"))
        active = state.get("active") or {}

        if not running or not active:
            self.metro.setText("No active search")
            self.metro.setToolTip("Start a bucket in Search")
            self.easy.setEnabled(False)
            self.hard.setEnabled(False)
            self.dot.set_mode("idle")
            return

        name = active.get("metro_name") or "Current metro"
        bucket = active.get("bucket")
        phase = active.get("phase") or ""
        self.metro.setText(name)
        self.metro.setToolTip(f"Bucket {bucket} · {phase.title()}")
        self.easy.setEnabled(not self.post_in_flight)
        self.hard.setEnabled(not self.post_in_flight)

        self.dot.set_mode("red" if self._live_over(state) else "green", self.red_phase)

    def _set_offline(self):
        self.last_state = None
        self.metro.setText("Search offline")
        self.metro.setToolTip("Start Search local server")
        self.easy.setEnabled(False)
        self.hard.setEnabled(False)
        self.dot.set_mode("offline")

    def _toggle_blink(self):
        self.red_phase = not self.red_phase
        state = self.last_state or {}
        if not state.get("running"):
            return
        if self._live_over(state):
            self.dot.set_mode("red", self.red_phase)

    def record_application(self, apply_type: str):
        if self.post_in_flight or not (self.last_state or {}).get("running"):
            return
        self.post_in_flight = True
        self.easy.setEnabled(False)
        self.hard.setEnabled(False)

        req = QNetworkRequest(QUrl(f"{BASE_URL}/api/applications"))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        body = json.dumps({"apply_type": apply_type}).encode("utf-8")
        reply = self.net.post(req, body)
        reply.finished.connect(lambda r=reply: self._post_finished(r))

    def _post_finished(self, reply: QNetworkReply):
        self.post_in_flight = False
        try:
            if reply.error() == QNetworkReply.NoError:
                payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
                self._accept_state(payload)
            else:
                self._render_state(self.last_state or {})
        except Exception:
            self._render_state(self.last_state or {})
        finally:
            reply.deleteLater()


def main():
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    OVERLAY_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    app = QApplication(sys.argv)
    app.setApplicationName("Search")
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay()
    overlay.show()
    overlay.raise_()

    try:
        return app.exec()
    finally:
        try:
            if OVERLAY_PID_FILE.exists() and OVERLAY_PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                OVERLAY_PID_FILE.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
