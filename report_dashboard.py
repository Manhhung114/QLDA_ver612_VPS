from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy
)

from document_manager import DOC_CONFIG
from drawing_manager import DRAWING_TYPES


class ReportMetricCard(QFrame):
    def __init__(self, title: str, suffix: str = "%", parent=None):
        super().__init__(parent)
        self.suffix = suffix
        self.setObjectName("reportMetricCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.title = QLabel(title)
        self.title.setObjectName("reportMetricTitle")
        self.value = QLabel("0" + suffix)
        self.value.setObjectName("reportMetricValue")
        self.note = QLabel("")
        self.note.setObjectName("reportMetricNote")
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.note)

    def set_value(self, value, note: str = ""):
        if isinstance(value, float):
            txt = f"{value:.1f}"
        else:
            txt = str(value)
        self.value.setText(txt + self.suffix)
        self.note.setText(note)


class ChartPanel(QFrame):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("reportChartPanel")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(14, 12, 14, 12)
        self.root.setSpacing(6)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("reportChartTitle")
        self.root.addWidget(title_lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("reportChartSubtitle")
            sub.setWordWrap(True)
            self.root.addWidget(sub)

    def add_chart(self, chart: QWidget):
        self.root.addWidget(chart, 1)


class ProgressBarsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.planned = 0.0
        self.actual = 0.0
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_values(self, planned: float, actual: float):
        self.planned = max(0.0, min(100.0, float(planned)))
        self.actual = max(0.0, min(100.0, float(actual)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left = 92
        right = 34
        track_w = max(60, w - left - right)
        rows = [("KH trung bình", self.planned, QColor("#2563eb")),
                ("TT trung bình", self.actual, QColor("#16a34a"))]
        y0 = 52
        for i, (label, value, color) in enumerate(rows):
            y = y0 + i * 72
            p.setPen(QColor("#334155"))
            p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
            p.drawText(8, y + 18, label)
            track = QRectF(left, y, track_w, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e8edf5"))
            p.drawRoundedRect(track, 7, 7)
            fill = QRectF(left, y, track_w * value / 100.0, 22)
            p.setBrush(color)
            p.drawRoundedRect(fill, 7, 7)
            p.setPen(QColor("#0f172a"))
            p.setFont(QFont("Segoe UI", 10, QFont.Bold))
            p.drawText(left + 8, y + 17, f"{value:.1f}%")
        delta = self.actual - self.planned
        p.setPen(QColor("#b91c1c") if delta < -1 else QColor("#15803d") if delta > 1 else QColor("#0369a1"))
        p.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        message = f"Chênh lệch TT − KH: {delta:+.1f}%"
        p.drawText(8, h - 18, message)


class DonutWidget(QWidget):
    COLORS = [QColor("#16a34a"), QColor("#2563eb"), QColor("#dc2626"), QColor("#94a3b8"), QColor("#f59e0b"), QColor("#7c3aed")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = OrderedDict()
        self.setMinimumHeight(245)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, data: dict[str, int]):
        self.data = OrderedDict((str(k), max(0, int(v))) for k, v in data.items())
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(self.data.values())
        w, h = self.width(), self.height()
        size = min(170, max(110, min(w * 0.42, h - 40)))
        cx, cy = 24 + size / 2, h / 2
        rect = QRectF(cx - size / 2, cy - size / 2, size, size)
        if total <= 0:
            p.setPen(QColor("#64748b"))
            p.drawText(self.rect(), Qt.AlignCenter, "Chưa có dữ liệu")
            return
        start = 90 * 16
        for i, (_, value) in enumerate(self.data.items()):
            span = -int(round(360 * 16 * value / total))
            p.setPen(Qt.NoPen)
            p.setBrush(self.COLORS[i % len(self.COLORS)])
            p.drawPie(rect, start, span)
            start += span
        inner = QRectF(rect.x() + size * 0.28, rect.y() + size * 0.28, size * 0.44, size * 0.44)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(inner)
        p.setPen(QColor("#0f172a"))
        p.setFont(QFont("Segoe UI", 11, QFont.Bold))
        p.drawText(inner, Qt.AlignCenter, f"{total}\nCông việc")

        lx = rect.right() + 28
        ly = max(20, (h - len(self.data) * 30) / 2)
        p.setFont(QFont("Segoe UI", 9))
        for i, (label, value) in enumerate(self.data.items()):
            color = self.COLORS[i % len(self.COLORS)]
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(lx, ly + i * 30 + 5, 12, 12), 3, 3)
            pct = value * 100 / total if total else 0
            p.setPen(QColor("#334155"))
            p.drawText(int(lx + 20), int(ly + i * 30 + 16), f"{label}: {value} ({pct:.1f}%)")


class HorizontalPercentWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[tuple[str, float, str]] = []
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_rows(self, rows: list[tuple[str, float, str]]):
        self.rows = [(str(a), max(0.0, min(100.0, float(b))), str(c)) for a, b, c in rows]
        self.setMinimumHeight(max(220, 40 + len(self.rows) * 37))
        self.updateGeometry()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rows:
            p.setPen(QColor("#64748b"))
            p.drawText(self.rect(), Qt.AlignCenter, "Chưa có dữ liệu")
            return
        w = self.width()
        label_w = min(170, max(115, int(w * 0.32)))
        x0 = label_w + 10
        track_w = max(80, w - x0 - 76)
        y = 20
        for label, pct, detail in self.rows:
            p.setPen(QColor("#334155"))
            p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
            p.drawText(QRectF(4, y - 2, label_w - 8, 24), Qt.AlignVCenter | Qt.AlignRight, label)
            track = QRectF(x0, y, track_w, 17)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e8edf5"))
            p.drawRoundedRect(track, 6, 6)
            p.setBrush(QColor("#0f62fe"))
            p.drawRoundedRect(QRectF(x0, y, track_w * pct / 100.0, 17), 6, 6)
            p.setPen(QColor("#0f172a"))
            p.setFont(QFont("Segoe UI", 8, QFont.Bold))
            p.drawText(int(x0 + track_w + 8), y + 14, f"{pct:.1f}%")
            if detail:
                p.setPen(QColor("#64748b"))
                p.setFont(QFont("Segoe UI", 7))
                p.drawText(int(x0), y + 31, detail)
            y += 37


class ReportDashboardPage(QWidget):
    """Dashboard báo cáo trực quan dùng chung DB của app desktop."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(18, 14, 18, 22)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("BÁO CÁO TRỰC QUAN DỰ ÁN")
        title.setObjectName("reportPageTitle")
        self.project_label = QLabel("Chưa chọn dự án")
        self.project_label.setObjectName("reportProjectLabel")
        title_box.addWidget(title)
        title_box.addWidget(self.project_label)
        header.addLayout(title_box)
        header.addStretch()
        btn = QPushButton("⟳ Làm mới báo cáo")
        btn.clicked.connect(self.refresh)
        header.addWidget(btn)
        lay.addLayout(header)

        cards = QGridLayout()
        self.card_planned = ReportMetricCard("KH trung bình")
        self.card_actual = ReportMetricCard("TT trung bình")
        self.card_delay = ReportMetricCard("Công việc chậm")
        self.card_done = ReportMetricCard("Công việc hoàn thành")
        self.card_docs = ReportMetricCard("Hồ sơ đã xử lý")
        self.card_drawings = ReportMetricCard("Bản vẽ đã chấp thuận")
        for i, card in enumerate([self.card_planned, self.card_actual, self.card_delay, self.card_done, self.card_docs, self.card_drawings]):
            cards.addWidget(card, i // 3, i % 3)
        lay.addLayout(cards)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        p1 = ChartPanel("KH % và TT %", "So sánh phần trăm kế hoạch và thực tế trung bình của các công việc đang hiển thị trong dự án.")
        self.progress_chart = ProgressBarsWidget()
        p1.add_chart(self.progress_chart)
        grid.addWidget(p1, 0, 0)

        p2 = ChartPanel("Cơ cấu trạng thái tiến độ", "Tỷ trọng công việc theo nhóm trạng thái chính.")
        self.task_donut = DonutWidget()
        p2.add_chart(self.task_donut)
        grid.addWidget(p2, 0, 1)

        p3 = ChartPanel("Tỷ lệ xử lý hồ sơ", "% hồ sơ đã đạt trạng thái kết thúc theo từng loại NCR/RFA/RFI/biên bản hiện trường/nghiệm thu/kiểm định.")
        self.doc_chart = HorizontalPercentWidget()
        p3.add_chart(self.doc_chart)
        grid.addWidget(p3, 1, 0)

        p4 = ChartPanel("Tỷ lệ chấp thuận bản vẽ", "% bản vẽ Chấp thuận hoặc Chấp thuận có điều kiện theo từng nhóm bản vẽ.")
        self.drawing_chart = HorizontalPercentWidget()
        p4.add_chart(self.drawing_chart)
        grid.addWidget(p4, 1, 1)

        lay.addLayout(grid)
        scroll.setWidget(body)
        root.addWidget(scroll)

    def set_project(self, project_id):
        self.project_id = project_id
        self.refresh()

    def _scalar(self, sql: str, params=()):
        row = self.db.conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        try:
            return row[0] or 0
        except Exception:
            return 0

    def refresh(self):
        pid = self.project_id
        if not pid:
            self.project_label.setText("Chưa chọn dự án")
            for card in [self.card_planned, self.card_actual, self.card_delay, self.card_done, self.card_docs, self.card_drawings]:
                card.set_value(0)
            self.progress_chart.set_values(0, 0)
            self.task_donut.set_data({})
            self.doc_chart.set_rows([])
            self.drawing_chart.set_rows([])
            return

        project = self.db.project(pid)
        if project:
            self.project_label.setText(f"{project['code']} - {project['name']}")

        task_rows = self.db.conn.execute(
            "SELECT planned_progress, actual_progress, status FROM tasks WHERE project_id=?", (pid,)
        ).fetchall()
        total_tasks = len(task_rows)
        planned_avg = sum(float(r["planned_progress"] or 0) for r in task_rows) / total_tasks if total_tasks else 0
        actual_avg = sum(float(r["actual_progress"] or 0) for r in task_rows) / total_tasks if total_tasks else 0
        delayed = sum(1 for r in task_rows if (r["status"] or "") == "Chậm tiến độ")
        completed = sum(1 for r in task_rows if (r["status"] or "") == "Hoàn thành")
        delay_pct = delayed * 100 / total_tasks if total_tasks else 0
        done_pct = completed * 100 / total_tasks if total_tasks else 0

        self.card_planned.set_value(planned_avg, f"{total_tasks} công việc")
        self.card_actual.set_value(actual_avg, f"Lệch {actual_avg-planned_avg:+.1f}% so với KH")
        self.card_delay.set_value(delay_pct, f"{delayed}/{total_tasks} công việc")
        self.card_done.set_value(done_pct, f"{completed}/{total_tasks} công việc")
        self.progress_chart.set_values(planned_avg, actual_avg)

        task_groups = OrderedDict([
            ("Hoàn thành", completed),
            ("Đúng/Nhanh", sum(1 for r in task_rows if (r["status"] or "") in ("Đúng tiến độ", "Nhanh tiến độ"))),
            ("Chậm", delayed),
            ("Chưa bắt đầu/Khác", sum(1 for r in task_rows if (r["status"] or "") not in ("Hoàn thành", "Đúng tiến độ", "Nhanh tiến độ", "Chậm tiến độ"))),
        ])
        self.task_donut.set_data(task_groups)

        # Hồ sơ
        doc_rows = []
        doc_total_all = 0
        doc_done_all = 0
        doc_labels = {"NCR": "NCR", "RFA": "RFA", "RFI": "RFI", "BBHT": "Biên bản hiện trường", "NTCV": "NT công việc", "NTVL": "NT VL đầu vào", "KDVT": "Kiểm định VT"}
        for doc_type, cfg in DOC_CONFIG.items():
            rows = self.db.conn.execute(
                "SELECT status FROM documents WHERE project_id=? AND doc_type=?", (pid, doc_type)
            ).fetchall()
            total = len(rows)
            done_set = set(cfg.get("done_statuses", []))
            done = sum(1 for r in rows if (r["status"] or "") in done_set)
            pct = done * 100 / total if total else 0
            doc_rows.append((doc_labels.get(doc_type, doc_type), pct, f"{done}/{total} hồ sơ"))
            doc_total_all += total
            doc_done_all += done
        doc_pct_all = doc_done_all * 100 / doc_total_all if doc_total_all else 0
        self.card_docs.set_value(doc_pct_all, f"{doc_done_all}/{doc_total_all} hồ sơ")
        self.doc_chart.set_rows(doc_rows)

        # Bản vẽ
        approved_statuses = {"Chấp thuận", "Chấp thuận có điều kiện"}
        drawing_rows = []
        drawing_total_all = 0
        drawing_approved_all = 0
        for drawing_type, cfg in DRAWING_TYPES.items():
            rows = self.db.conn.execute(
                "SELECT status FROM drawings WHERE project_id=? AND drawing_type=?", (pid, drawing_type)
            ).fetchall()
            total = len(rows)
            approved = sum(1 for r in rows if (r["status"] or "") in approved_statuses)
            pct = approved * 100 / total if total else 0
            drawing_rows.append((cfg["tab"], pct, f"{approved}/{total} bản vẽ"))
            drawing_total_all += total
            drawing_approved_all += approved
        drawing_pct_all = drawing_approved_all * 100 / drawing_total_all if drawing_total_all else 0
        self.card_drawings.set_value(drawing_pct_all, f"{drawing_approved_all}/{drawing_total_all} bản vẽ")
        self.drawing_chart.set_rows(drawing_rows)
