import sys
import os
import time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QHBoxLayout, QVBoxLayout, QFileDialog, QMessageBox,
    QFrame, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint
from PyQt5.QtGui import QFont, QColor, QCursor, QPixmap, QPainter, QPen, QIcon
from PIL import ImageGrab


# ─────────────────────────────────────────────
# 坐标拾取线程：隐藏主窗口后等待用户单击
# ─────────────────────────────────────────────
class PickThread(QThread):
    picked = pyqtSignal(int, int)   # 发出 (x, y)

    def run(self):
        """轮询等待鼠标左键按下，返回坐标"""
        import ctypes
        # 等待上一次按下释放，避免立即触发
        time.sleep(0.3)
        while True:
            # GetAsyncKeyState：左键 = 0x01
            state = ctypes.windll.user32.GetAsyncKeyState(0x01)
            if state & 0x8000:
                pos = QCursor.pos()
                self.picked.emit(pos.x(), pos.y())
                break
            time.sleep(0.01)


# ─────────────────────────────────────────────
# 半透明十字准心覆盖层（拾取坐标时显示）
# ─────────────────────────────────────────────
class CrosshairOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self._pos = QPoint(0, 0)
        self.setMouseTracking(True)

    def update_pos(self, x, y):
        self._pos = QPoint(x, y)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x, y = self._pos.x(), self._pos.y()
        w, h = self.width(), self.height()

        pen = QPen(QColor(255, 60, 60, 200), 1, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(0, y, w, y)
        painter.drawLine(x, 0, x, h)

        # 中心圆
        painter.setPen(QPen(QColor(255, 60, 60, 230), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(x - 10, y - 10, 20, 20)


# ─────────────────────────────────────────────
# 主界面
# ─────────────────────────────────────────────
class ScreenshotTool(QWidget):
    def __init__(self):
        super().__init__()
        self.pick_thread = None
        self.overlay = None
        self._tracking_timer = None
        self.init_ui()

    # ── UI 搭建 ────────────────────────────────
    def init_ui(self):
        self.setWindowTitle("截图工具")
        self.setFixedSize(440, 400)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 外层阴影卡片
        card = QFrame(self)
        card.setGeometry(10, 10, 400, 360)
        card.setStyleSheet("""
            QFrame {
                background: #1a1a2e;
                border-radius: 16px;
                border: 1px solid #2a2a4a;
            }
        """)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 6)
        card.setGraphicsEffect(shadow)

        root = QVBoxLayout(card)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(16)

        # ── 标题栏（带拖动） ──
        title_bar = QHBoxLayout()
        lbl_title = QLabel("📷  区域截图工具")
        lbl_title.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        lbl_title.setStyleSheet("color: #e0e0ff;")
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet("""
            QPushButton {
                background: #ff4466;
                color: white;
                border: none;
                border-radius: 14px;
                font-size: 12px;
            }
            QPushButton:hover { background: #ff2244; }
        """)
        btn_close.clicked.connect(self.close)
        title_bar.addWidget(lbl_title)
        title_bar.addStretch()
        title_bar.addWidget(btn_close)
        root.addLayout(title_bar)

        # ── 分隔线 ──
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background: #2a2a4a; max-height: 1px;")
        root.addWidget(line)

        # ── 左上角坐标 ──
        root.addWidget(self._section_label("① 左上角坐标"))

        coord_row = QHBoxLayout()
        coord_row.setSpacing(8)
        self.x_input = self._input("X", "0", 80)
        self.y_input = self._input("Y", "0", 80)
        self.btn_pick = QPushButton("🖱  单击拾取")
        self.btn_pick.setFixedHeight(36)
        self.btn_pick.setStyleSheet(self._btn_style("#16213e", "#4fc3f7"))
        self.btn_pick.clicked.connect(self.start_pick)
        coord_row.addWidget(QLabel("X:", styleSheet="color:#8888aa;"))
        coord_row.addWidget(self.x_input)
        coord_row.addWidget(QLabel("Y:", styleSheet="color:#8888aa;"))
        coord_row.addWidget(self.y_input)
        coord_row.addStretch()
        coord_row.addWidget(self.btn_pick)
        root.addLayout(coord_row)

        # ── 截图尺寸 ──
        root.addWidget(self._section_label("② 截图尺寸（像素）"))

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.w_input = self._input("宽", "800", 90)
        self.h_input = self._input("高", "600", 90)
        size_row.addWidget(QLabel("宽:", styleSheet="color:#8888aa;"))
        size_row.addWidget(self.w_input)
        size_row.addWidget(QLabel("px    高:", styleSheet="color:#8888aa;"))
        size_row.addWidget(self.h_input)
        size_row.addWidget(QLabel("px", styleSheet="color:#8888aa;"))
        size_row.addStretch()
        root.addLayout(size_row)

        # ── 保存路径 ──
        root.addWidget(self._section_label("③ 保存路径"))

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        self.path_input = QLineEdit(desktop)
        self.path_input.setFixedHeight(34)
        self.path_input.setStyleSheet(self._input_style())
        btn_browse = QPushButton("浏览…")
        btn_browse.setFixedSize(64, 34)
        btn_browse.setStyleSheet(self._btn_style("#16213e", "#9c8fff"))
        btn_browse.clicked.connect(self.browse_path)
        path_row.addWidget(self.path_input)
        path_row.addWidget(btn_browse)
        root.addLayout(path_row)

        root.addStretch()

        # ── 截图按钮 ──
        self.btn_shot = QPushButton("单 击 截 图")
        self.btn_shot.setFixedHeight(36)
        self.btn_shot.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        self.btn_shot.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #4fc3f7, stop:1 #9c8fff);
                color: #0a0a1a;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #81d4fa, stop:1 #b39ddb);
            }
            QPushButton:pressed { padding-top: 2px; }
        """)
        self.btn_shot.clicked.connect(self.take_screenshot)
        root.addWidget(self.btn_shot)

        # 拖动支持
        self._drag_pos = None

    # ── 辅助控件工厂 ──────────────────────────
    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Microsoft YaHei", 9))
        lbl.setStyleSheet("color: #6688cc; margin-top: 2px;")
        return lbl

    def _input(self, placeholder, default, width):
        w = QLineEdit(default)
        w.setPlaceholderText(placeholder)
        w.setFixedSize(width, 34)
        w.setAlignment(Qt.AlignCenter)
        w.setStyleSheet(self._input_style())
        return w

    def _input_style(self):
        return """
            QLineEdit {
                background: #16213e;
                color: #e0e0ff;
                border: 1px solid #2a2a5a;
                border-radius: 8px;
                padding: 0 8px;
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #4fc3f7; }
        """

    def _btn_style(self, bg, accent):
        return f"""
            QPushButton {{
                background: {bg};
                color: {accent};
                border: 1px solid {accent};
                border-radius: 8px;
                font-size: 12px;
                padding: 0 10px;
            }}
            QPushButton:hover {{ background: {accent}33; }}
        """

    # ── 窗口拖动 ──────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(e.globalPos() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # ── 浏览路径 ──────────────────────────────
    def browse_path(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存文件夹",
                                                  self.path_input.text())
        if folder:
            self.path_input.setText(folder)

    # ── 坐标拾取 ──────────────────────────────
    def start_pick(self):
        self.btn_pick.setText("请单击屏幕…")
        self.btn_pick.setEnabled(False)
        self.hide()

        # 显示十字准心覆盖层
        self.overlay = CrosshairOverlay()
        self.overlay.show()

        # 追踪鼠标位置更新准心
        self._tracking_timer = QTimer()
        self._tracking_timer.timeout.connect(self._update_crosshair)
        self._tracking_timer.start(16)  # ~60fps

        # 启动拾取线程
        self.pick_thread = PickThread()
        self.pick_thread.picked.connect(self.on_picked)
        self.pick_thread.start()

    def _update_crosshair(self):
        pos = QCursor.pos()
        if self.overlay:
            self.overlay.update_pos(pos.x(), pos.y())

    def on_picked(self, x, y):
        if self._tracking_timer:
            self._tracking_timer.stop()
        if self.overlay:
            self.overlay.close()
            self.overlay = None

        self.x_input.setText(str(x))
        self.y_input.setText(str(y))

        self.show()
        self.raise_()
        self.activateWindow()
        self.btn_pick.setText("🖱  单击拾取")
        self.btn_pick.setEnabled(True)

    # ── 截图 ─────────────────────────────────
    def take_screenshot(self):
        try:
            x = int(self.x_input.text())
            y = int(self.y_input.text())
            w = int(self.w_input.text())
            h = int(self.h_input.text())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "坐标和尺寸必须为整数！")
            return

        if w <= 0 or h <= 0:
            QMessageBox.warning(self, "输入错误", "宽高必须大于 0！")
            return

        folder = self.path_input.text()
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "路径错误", "保存路径不存在，请重新选择。")
            return

        # 生成带时间戳的文件名
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(folder, f"screenshot_{ts}.png")

        # 截图区域：(left, top, right, bottom)
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(filename)

        QMessageBox.information(
            self, "截图成功",
            f"已保存至：\n{filename}\n\n区域：({x}, {y})  {w} × {h} px"
        )


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ScreenshotTool()
    # 居中显示
    screen = app.primaryScreen().geometry()
    win.move(
        (screen.width() - win.width()) // 2,
        (screen.height() - win.height()) // 2
    )
    win.show()
    sys.exit(app.exec_())