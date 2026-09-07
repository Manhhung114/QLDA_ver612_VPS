from __future__ import annotations

import os
from pathlib import Path
from datetime import date, datetime

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QFormLayout, QDialogButtonBox, QDateEdit, QTextEdit, QFileDialog, QFrame,
    QAbstractItemView, QListWidget, QListWidgetItem, QDoubleSpinBox, QSpinBox,
    QTabWidget
)
from PySide6.QtCore import QUrl

from settings_store import load_app_settings
from google_drive_service import GoogleDriveService, extract_drive_id
from attachment_utils import make_attachment_ref, split_attachment_ref, attachment_name, open_attachment

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None

DATE_FMT = "%Y-%m-%d"

DOC_CONFIG = {
    "NCR": {
        "title": "NCR - Non-Conformance Report", "code_label": "Mã NCR *", "subject_label": "Nội dung không phù hợp *",
        "issuer_label": "Người phát hành", "assignee_label": "Người/Đơn vị xử lý", "response_label": "Biện pháp khắc phục / Kết quả",
        "statuses": ["Mở", "Đang khắc phục", "Chờ kiểm tra", "Đóng", "Hủy"], "done_statuses": ["Đóng", "Hủy"],
    },
    "RFA": {
        "title": "RFA - Request for Approval", "code_label": "Mã RFA *", "subject_label": "Nội dung trình duyệt *",
        "issuer_label": "Người trình", "assignee_label": "Người/Đơn vị duyệt", "response_label": "Ý kiến / Kết quả phê duyệt",
        "statuses": ["Soạn thảo", "Đã gửi", "Chờ duyệt", "Đã duyệt", "Từ chối", "Đóng"], "done_statuses": ["Đã duyệt", "Từ chối", "Đóng"],
    },
    "RFI": {
        "title": "RFI - Request for Information", "code_label": "Mã RFI *", "subject_label": "Câu hỏi / Nội dung cần làm rõ *",
        "issuer_label": "Người gửi", "assignee_label": "Người/Đơn vị phản hồi", "response_label": "Nội dung phản hồi",
        "statuses": ["Đã gửi", "Chờ phản hồi", "Đã phản hồi", "Đóng", "Hủy"], "done_statuses": ["Đã phản hồi", "Đóng", "Hủy"],
    },
    "BBHT": {
        "title": "Biên bản hiện trường", "code_label": "Mã biên bản *", "subject_label": "Nội dung / Sự việc hiện trường *",
        "issuer_label": "Người/Đơn vị lập biên bản", "assignee_label": "Người/Đơn vị xử lý", "response_label": "Kết quả xử lý / Phản hồi",
        "statuses": ["Mới lập", "Đã phát hành", "Đang xử lý", "Đã xử lý", "Đóng", "Hủy"], "done_statuses": ["Đã xử lý", "Đóng", "Hủy"],
    },
    "NTCV": {
        "title": "Hồ sơ nghiệm thu công việc", "code_label": "Mã hồ sơ NTCV *", "subject_label": "Hạng mục / Công việc nghiệm thu *",
        "issuer_label": "Người/Đơn vị trình nghiệm thu", "assignee_label": "Người/Đơn vị nghiệm thu", "response_label": "Kết quả / Ý kiến nghiệm thu",
        "issue_date_label": "Ngày trình nghiệm thu", "due_date_label": "Ngày dự kiến nghiệm thu", "closed_date_label": "Ngày nghiệm thu / đóng",
        "statuses": ["Chuẩn bị hồ sơ", "Đã trình nghiệm thu", "Chờ nghiệm thu", "Yêu cầu sửa", "Đạt", "Không đạt", "Đóng"], "done_statuses": ["Đạt", "Không đạt", "Đóng"],
    },
    "NTVL": {
        "title": "Hồ sơ nghiệm thu vật liệu đầu vào", "code_label": "Mã hồ sơ NTVL *", "subject_label": "Vật liệu / Thiết bị nghiệm thu đầu vào *",
        "issuer_label": "Nhà thầu/Người trình", "assignee_label": "Người/Đơn vị kiểm tra", "response_label": "Kết quả nghiệm thu / Ý kiến",
        "issue_date_label": "Ngày trình / nhận hồ sơ", "due_date_label": "Ngày dự kiến nghiệm thu", "closed_date_label": "Ngày nghiệm thu / đóng",
        "statuses": ["Chuẩn bị hồ sơ", "Đã trình", "Chờ kiểm tra", "Yêu cầu bổ sung", "Chấp thuận", "Chấp thuận có điều kiện", "Không chấp thuận", "Đóng"], "done_statuses": ["Chấp thuận", "Chấp thuận có điều kiện", "Không chấp thuận", "Đóng"],
    },
    "KDVT": {
        "title": "Hồ sơ kiểm định vật tư", "code_label": "Mã hồ sơ kiểm định *", "subject_label": "Vật tư / Thiết bị kiểm định *",
        "issuer_label": "Người/Đơn vị gửi kiểm định", "assignee_label": "Đơn vị kiểm định/Người phụ trách", "response_label": "Kết quả kiểm định / Chứng chỉ",
        "issue_date_label": "Ngày gửi kiểm định", "due_date_label": "Hạn trả kết quả", "closed_date_label": "Ngày có kết quả / đóng",
        "statuses": ["Chuẩn bị hồ sơ", "Đã gửi kiểm định", "Đang kiểm định", "Chờ kết quả", "Đạt", "Không đạt", "Đóng"], "done_statuses": ["Đạt", "Không đạt", "Đóng"],
    },
}

PRIORITIES = ["Thấp", "Trung bình", "Cao", "Khẩn"]


class DocStatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("docCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        t = QLabel(title)
        t.setObjectName("docCardTitle")
        self.value = QLabel("0")
        self.value.setObjectName("docCardValue")
        lay.addWidget(t)
        lay.addWidget(self.value)


class DocumentDialog(QDialog):
    def __init__(self, doc_type: str, parent=None, record=None, attachments=None):
        super().__init__(parent)
        self.doc_type = doc_type
        self.cfg = DOC_CONFIG[doc_type]
        self.setWindowTitle(self.cfg["title"])
        self.resize(720, 720)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.code = QLineEdit()
        self.subject = QLineEdit()
        self.discipline = QLineEdit()
        self.contractor = QLineEdit()
        self.issuer = QLineEdit()
        self.assignee = QLineEdit()
        self.issue_date = QDateEdit(calendarPopup=True)
        self.issue_date.setDisplayFormat("dd/MM/yyyy")
        self.issue_date.setDate(QDate.currentDate())
        self.due_date = QDateEdit(calendarPopup=True)
        self.due_date.setDisplayFormat("dd/MM/yyyy")
        self.due_date.setDate(QDate.currentDate().addDays(7))
        self.closed_date = QDateEdit(calendarPopup=True)
        self.closed_date.setDisplayFormat("dd/MM/yyyy")
        self.closed_date.setSpecialValueText("Chưa đóng")
        self.closed_date.setMinimumDate(QDate(2000, 1, 1))
        self.closed_date.setDate(self.closed_date.minimumDate())
        self.status = QComboBox()
        self.status.addItems(self.cfg["statuses"])
        self.priority = QComboBox()
        self.priority.addItems(PRIORITIES)
        self.wbs = QLineEdit()
        self.wbs.setPlaceholderText("Ví dụ: 2.3.1 hoặc UID task liên quan")
        self.description = QTextEdit()
        self.description.setFixedHeight(95)
        self.response = QTextEdit()
        self.response.setFixedHeight(95)

        form.addRow(self.cfg["code_label"], self.code)
        form.addRow(self.cfg["subject_label"], self.subject)
        form.addRow("Bộ môn / Hệ", self.discipline)
        form.addRow("Nhà thầu / Đơn vị", self.contractor)
        form.addRow(self.cfg["issuer_label"], self.issuer)
        form.addRow(self.cfg["assignee_label"], self.assignee)
        form.addRow(self.cfg.get("issue_date_label", "Ngày phát hành"), self.issue_date)
        form.addRow(self.cfg.get("due_date_label", "Hạn xử lý"), self.due_date)
        form.addRow(self.cfg.get("closed_date_label", "Ngày đóng"), self.closed_date)
        form.addRow("Trạng thái", self.status)
        form.addRow("Mức độ", self.priority)
        form.addRow("WBS / Task liên quan", self.wbs)

        self.cost_impact = QDoubleSpinBox()
        self.cost_impact.setRange(-1_000_000_000_000, 1_000_000_000_000)
        self.cost_impact.setDecimals(0)
        self.cost_impact.setSingleStep(1_000_000)
        self.cost_impact.setSuffix(" đ")
        self.time_impact = QSpinBox()
        self.time_impact.setRange(-3650, 3650)
        self.time_impact.setSuffix(" ngày")
        if doc_type == "VO":
            form.addRow("Giá trị phát sinh", self.cost_impact)
            form.addRow("Ảnh hưởng tiến độ", self.time_impact)

        form.addRow("Mô tả / Ghi chú", self.description)
        form.addRow(self.cfg["response_label"], self.response)
        root.addLayout(form)

        file_box = QVBoxLayout()
        file_title = QLabel("Tệp đính kèm")
        file_title.setStyleSheet("font-weight:700;")
        file_box.addWidget(file_title)
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(105)
        file_box.addWidget(self.file_list)
        file_btns = QHBoxLayout()
        b_add = QPushButton("+ Thêm file")
        b_remove = QPushButton("Bỏ file")
        b_open = QPushButton("Mở file")
        b_add.clicked.connect(self.add_files)
        b_remove.clicked.connect(self.remove_file)
        b_open.clicked.connect(self.open_file)
        file_btns.addWidget(b_add)
        file_btns.addWidget(b_remove)
        file_btns.addWidget(b_open)
        file_btns.addStretch()
        file_box.addLayout(file_btns)
        root.addLayout(file_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for path in attachments or []:
            self._append_file(path)

        if record is not None:
            self.code.setText(record["code"] or "")
            self.subject.setText(record["subject"] or "")
            self.discipline.setText(record["discipline"] or "")
            self.contractor.setText(record["contractor"] or "")
            self.issuer.setText(record["issuer"] or "")
            self.assignee.setText(record["assignee"] or "")
            self._set_date(self.issue_date, record["issue_date"])
            self._set_date(self.due_date, record["due_date"])
            if record["closed_date"]:
                self._set_date(self.closed_date, record["closed_date"])
            if record["status"]:
                i = self.status.findText(record["status"])
                if i >= 0:
                    self.status.setCurrentIndex(i)
            if record["priority"]:
                i = self.priority.findText(record["priority"])
                if i >= 0:
                    self.priority.setCurrentIndex(i)
            self.wbs.setText(record["related_wbs"] or "")
            self.description.setPlainText(record["description"] or "")
            self.response.setPlainText(record["response"] or "")
            self.cost_impact.setValue(float(record["cost_impact"] or 0))
            self.time_impact.setValue(int(record["time_impact_days"] or 0))

    @staticmethod
    def _set_date(widget: QDateEdit, value: str):
        if value:
            qd = QDate.fromString(value, "yyyy-MM-dd")
            if qd.isValid():
                widget.setDate(qd)

    def _append_file(self, path: str):
        p = str(path)
        item = QListWidgetItem(attachment_name(p))
        item.setData(Qt.UserRole, p)
        item.setToolTip(split_attachment_ref(p)[0])
        self.file_list.addItem(item)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn tệp đính kèm", "", "Tất cả tệp (*.*)")
        existing = {self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())}
        for path in paths:
            if path not in existing:
                self._append_file(path)

    def remove_file(self):
        row = self.file_list.currentRow()
        if row >= 0:
            self.file_list.takeItem(row)

    def open_file(self):
        item = self.file_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if not open_attachment(path):
            QMessageBox.warning(self, "Không thấy file", f"Không mở được:\n{split_attachment_ref(path)[0]}")

    def validate_accept(self):
        if not self.code.text().strip() or not self.subject.text().strip():
            QMessageBox.warning(self, "Thiếu dữ liệu", "Vui lòng nhập mã hồ sơ và nội dung.")
            return
        if self.due_date.date() < self.issue_date.date():
            QMessageBox.warning(self, "Sai ngày", "Hạn xử lý không được trước ngày phát hành.")
            return
        self.accept()

    def data(self):
        closed = ""
        if self.closed_date.date() != self.closed_date.minimumDate():
            closed = self.closed_date.date().toString("yyyy-MM-dd")
        return {
            "code": self.code.text().strip(),
            "subject": self.subject.text().strip(),
            "discipline": self.discipline.text().strip(),
            "contractor": self.contractor.text().strip(),
            "issuer": self.issuer.text().strip(),
            "assignee": self.assignee.text().strip(),
            "issue_date": self.issue_date.date().toString("yyyy-MM-dd"),
            "due_date": self.due_date.date().toString("yyyy-MM-dd"),
            "closed_date": closed,
            "status": self.status.currentText(),
            "priority": self.priority.currentText(),
            "related_wbs": self.wbs.text().strip(),
            "description": self.description.toPlainText().strip(),
            "response": self.response.toPlainText().strip(),
            "cost_impact": float(self.cost_impact.value()) if self.doc_type == "VO" else 0,
            "time_impact_days": int(self.time_impact.value()) if self.doc_type == "VO" else 0,
        }

    def attachments(self):
        return [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]


class DocumentTypeTab(QWidget):
    def __init__(self, db, doc_type: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.doc_type = doc_type
        self.cfg = DOC_CONFIG[doc_type]
        self.project_id = None
        self.access_role = "admin"
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 8, 6, 6)
        root.setSpacing(8)

        cards = QGridLayout()
        self.card_total = DocStatCard("Tổng hồ sơ")
        self.card_open = DocStatCard("Đang mở")
        self.card_overdue = DocStatCard("Quá hạn")
        self.card_closed = DocStatCard("Đã đóng/duyệt")
        self.card_files = DocStatCard("Tệp đính kèm")
        for i, card in enumerate([self.card_total, self.card_open, self.card_overdue, self.card_closed, self.card_files]):
            cards.addWidget(card, 0, i)
        root.addLayout(cards)

        toolbar = QHBoxLayout()
        self.btn_add = QPushButton(f"+ Thêm {self.doc_type}")
        self.btn_edit = QPushButton("Sửa")
        self.btn_delete = QPushButton("Xóa")
        self.btn_delete.setObjectName("dangerBtn")
        self.btn_files = QPushButton("Tệp đính kèm")
        self.btn_open_file = QPushButton("Mở file")
        self.btn_export = QPushButton("Xuất Excel")
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Tìm mã {self.doc_type} / nội dung / WBS / nhà thầu...")
        self.status_filter = QComboBox()
        self.status_filter.addItem("Tất cả")
        self.status_filter.addItems(self.cfg["statuses"])
        self.deadline_filter = QComboBox()
        self.deadline_filter.addItems(["Tất cả hạn", "Quá hạn", "Sắp đến hạn 7 ngày", "Còn hạn", "Đã đóng"])
        self.btn_add.clicked.connect(self.add_record)
        self.btn_edit.clicked.connect(self.edit_record)
        self.btn_delete.clicked.connect(self.delete_record)
        self.btn_files.clicked.connect(self.manage_files)
        self.btn_open_file.clicked.connect(self.open_first_file)
        self.btn_export.clicked.connect(self.export_excel)
        self.search.textChanged.connect(self.refresh)
        self.status_filter.currentTextChanged.connect(self.refresh)
        self.deadline_filter.currentTextChanged.connect(self.refresh)
        for w in [self.btn_add, self.btn_edit, self.btn_delete, self.btn_files, self.btn_open_file, self.btn_export]:
            toolbar.addWidget(w)
        toolbar.addSpacing(8)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.status_filter)
        toolbar.addWidget(self.deadline_filter)
        root.addLayout(toolbar)

        headers = [
            "ID", "Mã", "Nội dung", "Bộ môn/Hệ", "Nhà thầu/Đơn vị", "Người phát hành",
            "Người xử lý", self.cfg.get("issue_date_label", "Ngày phát hành"), self.cfg.get("due_date_label", "Hạn xử lý"), self.cfg.get("closed_date_label", "Ngày đóng"), "Trạng thái",
            "Mức độ", "WBS/Task", "Tình trạng hạn", "File"
        ]
        if self.doc_type == "VO":
            headers += ["Giá trị phát sinh (đ)", "Ảnh hưởng (ngày)"]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setColumnHidden(0, True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _index: self.edit_record())
        root.addWidget(self.table, 1)

    def set_access_role(self, role: str):
        self.access_role = role or "unknown"
        can_update = self.access_role in {"update", "admin"}
        is_admin = self.access_role == "admin"
        self.btn_add.setEnabled(can_update)
        self.btn_edit.setEnabled(can_update)
        self.btn_files.setEnabled(can_update)
        self.btn_delete.setEnabled(is_admin)

    def _require_update(self) -> bool:
        if self.access_role not in {"update", "admin"}:
            QMessageBox.warning(self, "Chỉ đọc", "Tài khoản hiện tại chỉ có quyền đọc. Không thể cập nhật dữ liệu.")
            return False
        return True

    def _require_admin(self) -> bool:
        if self.access_role != "admin":
            QMessageBox.warning(self, "Cần quyền Admin", "Chức năng xóa yêu cầu quyền Admin.")
            return False
        return True

    def deadline_state(self, row):
        done_statuses = set(self.cfg.get("done_statuses", ["Đóng", "Hủy"]))
        if row["closed_date"] or row["status"] in done_statuses:
            return "Đã đóng"
        due_s = row["due_date"] or ""
        if not due_s:
            return "Chưa có hạn"
        try:
            due = datetime.strptime(due_s, DATE_FMT).date()
        except ValueError:
            return "Chưa xác định"
        today = date.today()
        delta = (due - today).days
        if delta < 0:
            return f"Quá hạn {abs(delta)} ngày"
        if delta <= 7:
            return f"Còn {delta} ngày"
        return "Còn hạn"

    def set_project(self, project_id):
        self.project_id = project_id
        self.refresh()

    def selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.text()) if item and item.text() else None

    def _records(self):
        if not self.project_id:
            return []
        sql = "SELECT d.*, (SELECT COUNT(*) FROM document_attachments a WHERE a.document_id=d.id) AS file_count FROM documents d WHERE d.project_id=? AND d.doc_type=?"
        params = [self.project_id, self.doc_type]
        kw = self.search.text().strip() if hasattr(self, "search") else ""
        if kw:
            k = f"%{kw}%"
            sql += " AND (d.code LIKE ? OR d.subject LIKE ? OR d.related_wbs LIKE ? OR d.contractor LIKE ? OR d.assignee LIKE ?)"
            params += [k, k, k, k, k]
        status = self.status_filter.currentText() if hasattr(self, "status_filter") else "Tất cả"
        if status != "Tất cả":
            sql += " AND d.status=?"
            params.append(status)
        sql += " ORDER BY d.issue_date DESC, d.id DESC"
        rows = self.db.conn.execute(sql, params).fetchall()
        mode = self.deadline_filter.currentText() if hasattr(self, "deadline_filter") else "Tất cả hạn"
        if mode == "Tất cả hạn":
            return rows
        filtered = []
        for row in rows:
            ds = self.deadline_state(row)
            if mode == "Quá hạn" and ds.startswith("Quá hạn"):
                filtered.append(row)
            elif mode == "Sắp đến hạn 7 ngày" and ds.startswith("Còn ") and ds != "Còn hạn":
                filtered.append(row)
            elif mode == "Còn hạn" and (ds == "Còn hạn" or ds.startswith("Còn ")):
                filtered.append(row)
            elif mode == "Đã đóng" and ds == "Đã đóng":
                filtered.append(row)
        return filtered

    def refresh(self):
        rows = self._records()
        self.table.setRowCount(len(rows))
        overdue = 0
        closed = 0
        files = 0
        for r, rec in enumerate(rows):
            state = self.deadline_state(rec)
            if state.startswith("Quá hạn"):
                overdue += 1
            if state == "Đã đóng":
                closed += 1
            files += int(rec["file_count"] or 0)
            vals = [
                rec["id"], rec["code"], rec["subject"], rec["discipline"], rec["contractor"],
                rec["issuer"], rec["assignee"], rec["issue_date"], rec["due_date"], rec["closed_date"],
                rec["status"], rec["priority"], rec["related_wbs"], state, rec["file_count"] or 0
            ]
            if self.doc_type == "VO":
                vals += [f"{float(rec['cost_impact'] or 0):,.0f}", rec["time_impact_days"] or 0]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v if v is not None else ""))
                if c in (7, 8, 9, 10, 11, 13, 14) or (self.doc_type == "VO" and c >= 15):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)
            if state.startswith("Quá hạn"):
                self.table.item(r, 13).setBackground(QColor("#fee2e2"))
            elif state == "Đã đóng":
                self.table.item(r, 13).setBackground(QColor("#dcfce7"))
            elif state.startswith("Còn "):
                self.table.item(r, 13).setBackground(QColor("#fef3c7"))
            if rec["priority"] == "Khẩn":
                self.table.item(r, 11).setBackground(QColor("#fecaca"))
            elif rec["priority"] == "Cao":
                self.table.item(r, 11).setBackground(QColor("#ffedd5"))
        total = len(rows)
        open_count = total - closed
        self.card_total.value.setText(str(total))
        self.card_open.value.setText(str(open_count))
        self.card_overdue.value.setText(str(overdue))
        self.card_closed.value.setText(str(closed))
        self.card_files.value.setText(str(files))

    def _get_record(self, doc_id):
        return self.db.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def _attachments(self, doc_id):
        rows = self.db.conn.execute(
            "SELECT file_path,file_name FROM document_attachments WHERE document_id=? ORDER BY id", (doc_id,)
        ).fetchall()
        return [make_attachment_ref(r["file_path"], r["file_name"] or "") for r in rows]

    def _save_attachments(self, doc_id, paths):
        cfg = load_app_settings()
        auto_drive = bool(cfg.get("drive_enabled") and cfg.get("drive_auto_upload"))
        drive = None; parent_id = ""
        if auto_drive:
            root_id = extract_drive_id(str(cfg.get("drive_root_folder_id") or cfg.get("drive_root_folder_url") or ""))
            if not root_id:
                raise RuntimeError("Google Drive đã bật nhưng chưa có thư mục gốc. Vào Cài đặt → Google Drive.")
            meta = self.db.conn.execute("""
                SELECT d.code,d.doc_type,p.code AS project_code
                FROM documents d JOIN projects p ON p.id=d.project_id WHERE d.id=?
            """, (doc_id,)).fetchone()
            drive = GoogleDriveService.desktop(interactive=False)
            folder = drive.ensure_storage_path(root_id, meta["project_code"], f"Ho_so_{meta['doc_type']}", meta["code"])
            parent_id = folder["id"]
        stored = []
        for ref in paths:
            if not ref: continue
            target, name = split_attachment_ref(ref)
            if target.startswith("http://") or target.startswith("https://"):
                stored.append((target, name))
            elif auto_drive:
                uploaded = drive.upload_path(target, parent_id)
                stored.append((uploaded["webViewLink"], uploaded.get("name") or name))
            else:
                stored.append((target, name or Path(target).name))
        with self.db.conn:
            self.db.conn.execute("DELETE FROM document_attachments WHERE document_id=?", (doc_id,))
            for path, name in stored:
                self.db.conn.execute(
                    "INSERT INTO document_attachments(document_id,file_path,file_name,created_at) VALUES(?,?,?,?)",
                    (doc_id, str(path), str(name), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )

    def add_record(self):
        if not self._require_update():
            return
        if not self.project_id:
            QMessageBox.information(self, "Chưa có dự án", "Hãy chọn hoặc tạo dự án trước.")
            return
        dlg = DocumentDialog(self.doc_type, self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.data()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cols = [
            "project_id", "doc_type", "code", "subject", "discipline", "contractor", "issuer", "assignee",
            "issue_date", "due_date", "closed_date", "status", "priority", "related_wbs", "description",
            "response", "cost_impact", "time_impact_days", "created_at", "updated_at"
        ]
        values = [self.project_id, self.doc_type] + [data[k] for k in [
            "code", "subject", "discipline", "contractor", "issuer", "assignee", "issue_date", "due_date",
            "closed_date", "status", "priority", "related_wbs", "description", "response", "cost_impact", "time_impact_days"
        ]] + [now, now]
        try:
            cur = self.db.conn.execute(
                f"INSERT INTO documents({','.join(cols)}) VALUES({','.join(['?'] * len(cols))})", values
            )
            self.db.conn.commit()
            self._save_attachments(cur.lastrowid, dlg.attachments())
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Không lưu được", str(exc))

    def edit_record(self):
        if not self._require_update():
            return
        doc_id = self.selected_id()
        if not doc_id:
            return
        rec = self._get_record(doc_id)
        dlg = DocumentDialog(self.doc_type, self, rec, self._attachments(doc_id))
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.data()
        cols = [
            "code", "subject", "discipline", "contractor", "issuer", "assignee", "issue_date", "due_date",
            "closed_date", "status", "priority", "related_wbs", "description", "response", "cost_impact", "time_impact_days"
        ]
        vals = [data[c] for c in cols]
        try:
            self.db.conn.execute(
                f"UPDATE documents SET {','.join(f'{c}=?' for c in cols)}, updated_at=? WHERE id=?",
                vals + [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), doc_id]
            )
            self.db.conn.commit()
            self._save_attachments(doc_id, dlg.attachments())
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Không lưu được", str(exc))

    def delete_record(self):
        if not self._require_admin():
            return
        doc_id = self.selected_id()
        if not doc_id:
            return
        if QMessageBox.question(self, "Xác nhận", f"Xóa hồ sơ {self.doc_type} đang chọn?") == QMessageBox.Yes:
            self.db.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self.db.conn.commit()
            self.refresh()

    def manage_files(self):
        if not self._require_update():
            return
        doc_id = self.selected_id()
        if not doc_id:
            return
        rec = self._get_record(doc_id)
        dlg = DocumentDialog(self.doc_type, self, rec, self._attachments(doc_id))
        # This uses the same dialog so attachments can be managed together with record details.
        if dlg.exec() == QDialog.Accepted:
            data = dlg.data()
            cols = [
                "code", "subject", "discipline", "contractor", "issuer", "assignee", "issue_date", "due_date",
                "closed_date", "status", "priority", "related_wbs", "description", "response", "cost_impact", "time_impact_days"
            ]
            self.db.conn.execute(
                f"UPDATE documents SET {','.join(f'{c}=?' for c in cols)}, updated_at=? WHERE id=?",
                [data[c] for c in cols] + [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), doc_id]
            )
            self.db.conn.commit()
            self._save_attachments(doc_id, dlg.attachments())
            self.refresh()

    def export_excel(self):
        if Workbook is None:
            QMessageBox.warning(self, "Thiếu thư viện", "Cài openpyxl: pip install openpyxl")
            return
        if not self.project_id:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"Xuất {self.doc_type}", f"{self.doc_type}_Ho_so.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        rows = self._records()
        wb = Workbook()
        ws = wb.active
        ws.title = self.doc_type
        headers = [
            "Mã", "Nội dung", "Bộ môn/Hệ", "Nhà thầu/Đơn vị", "Người phát hành",
            "Người xử lý", self.cfg.get("issue_date_label", "Ngày phát hành"), self.cfg.get("due_date_label", "Hạn xử lý"), self.cfg.get("closed_date_label", "Ngày đóng"), "Trạng thái",
            "Mức độ", "WBS/Task", "Tình trạng hạn", "Số file", "Mô tả/Ghi chú", "Phản hồi/Kết quả"
        ]
        if self.doc_type == "VO":
            headers += ["Giá trị phát sinh (đ)", "Ảnh hưởng tiến độ (ngày)"]
        ws.append(headers)
        for rec in rows:
            vals = [
                rec["code"], rec["subject"], rec["discipline"], rec["contractor"], rec["issuer"], rec["assignee"],
                rec["issue_date"], rec["due_date"], rec["closed_date"], rec["status"], rec["priority"],
                rec["related_wbs"], self.deadline_state(rec), rec["file_count"] or 0, rec["description"], rec["response"]
            ]
            if self.doc_type == "VO":
                vals += [rec["cost_impact"] or 0, rec["time_impact_days"] or 0]
            ws.append(vals)
        ws.freeze_panes = "A2"
        for col in ws.columns:
            width = min(60, max(12, max(len(str(cell.value or "")) for cell in col) + 2))
            ws.column_dimensions[col[0].column_letter].width = width
        wb.save(path)
        QMessageBox.information(self, "Hoàn tất", f"Đã xuất {len(rows)} hồ sơ {self.doc_type}:\n{path}")

    def open_first_file(self):
        doc_id = self.selected_id()
        if not doc_id:
            return
        paths = self._attachments(doc_id)
        if not paths:
            QMessageBox.information(self, "Chưa có file", "Hồ sơ này chưa có tệp đính kèm.")
            return
        path = paths[0]
        if not open_attachment(path):
            QMessageBox.warning(self, "Không thấy file", f"Không mở được:\n{split_attachment_ref(path)[0]}")


class DocumentManagerPage(QWidget):
    projectChanged = Signal(object)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_project_id = None
        self.access_role = "admin"
        self._ensure_tables()
        self._build_ui()
        self.reload_projects()

    def _ensure_tables(self):
        self.db.conn.execute("PRAGMA foreign_keys=ON")
        self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                doc_type TEXT NOT NULL,
                code TEXT NOT NULL,
                subject TEXT NOT NULL,
                discipline TEXT DEFAULT '',
                contractor TEXT DEFAULT '',
                issuer TEXT DEFAULT '',
                assignee TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                due_date TEXT DEFAULT '',
                closed_date TEXT DEFAULT '',
                status TEXT DEFAULT '',
                priority TEXT DEFAULT 'Trung bình',
                related_wbs TEXT DEFAULT '',
                description TEXT DEFAULT '',
                response TEXT DEFAULT '',
                cost_impact REAL DEFAULT 0,
                time_impact_days INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, doc_type, code)
            )
        """)
        self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS document_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)
        self.db.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_project_type ON documents(project_id, doc_type)")
        self.db.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_due ON documents(project_id, due_date)")
        self.db.conn.commit()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("QUẢN LÝ HỒ SƠ DỰ ÁN")
        title.setObjectName("pageTitle")
        subtitle = QLabel("NCR • RFA • RFI • Biên bản hiện trường • Nghiệm thu công việc • Nghiệm thu VL đầu vào • Kiểm định vật tư")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        header.addWidget(QLabel("Dự án:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(390)
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        header.addWidget(self.project_combo)
        root.addLayout(header)

        hint = QLabel("Mỗi loại hồ sơ là một sheet riêng. Hệ thống tự đánh dấu quá hạn theo Hạn xử lý và lưu nhiều tệp đính kèm cho từng hồ sơ.")
        hint.setObjectName("docHint")
        root.addWidget(hint)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.type_tabs = {}
        for doc_type in ("NCR", "RFA", "RFI", "BBHT", "NTCV", "NTVL", "KDVT"):
            tab = DocumentTypeTab(self.db, doc_type, self)
            self.type_tabs[doc_type] = tab
            tab_label = {"BBHT": "Biên bản hiện trường", "NTCV": "NT công việc", "NTVL": "NT VL đầu vào", "KDVT": "Kiểm định vật tư"}.get(doc_type, doc_type)
            self.tabs.addTab(tab, tab_label)
        root.addWidget(self.tabs, 1)

    def set_access_role(self, role: str):
        self.access_role = role or "unknown"
        for tab in self.type_tabs.values():
            tab.set_access_role(self.access_role)

    def reload_projects(self, select_id=None):
        old = select_id or self.current_project_id
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        projects = self.db.projects()
        for p in projects:
            self.project_combo.addItem(f"{p['code']} - {p['name']}", p["id"])
        self.project_combo.blockSignals(False)
        if projects:
            idx = 0
            if old:
                for i in range(self.project_combo.count()):
                    if self.project_combo.itemData(i) == old:
                        idx = i
                        break
            self.project_combo.setCurrentIndex(idx)
            self._project_changed(idx, emit=False)
        else:
            self.current_project_id = None
            for tab in self.type_tabs.values():
                tab.set_project(None)

    def _project_changed(self, index, emit=True):
        pid = self.project_combo.itemData(index) if index >= 0 else None
        self.current_project_id = pid
        for tab in self.type_tabs.values():
            tab.set_project(pid)
        if emit:
            self.projectChanged.emit(pid)

    def set_project(self, project_id):
        if project_id is None:
            return
        for i in range(self.project_combo.count()):
            if self.project_combo.itemData(i) == project_id:
                self.project_combo.blockSignals(True)
                self.project_combo.setCurrentIndex(i)
                self.project_combo.blockSignals(False)
                self._project_changed(i, emit=False)
                return
