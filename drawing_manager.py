from __future__ import annotations

from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QDate, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices

from settings_store import load_app_settings
from google_drive_service import GoogleDriveService, extract_drive_id
from attachment_utils import make_attachment_ref, split_attachment_ref, attachment_name, open_attachment
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QFormLayout, QDialogButtonBox, QDateEdit, QTextEdit, QFileDialog, QFrame,
    QAbstractItemView, QListWidget, QListWidgetItem, QTabWidget
)

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None


DRAWING_TYPES = {
    "SHOPDRAWING": {
        "tab": "Shopdrawing",
        "title": "SHOPDRAWING",
        "hint": "Theo dõi bản vẽ shopdrawing, revision, ngày nhận, trạng thái duyệt và file đính kèm.",
    },
    "ISSUED_DESIGN": {
        "tab": "BV phát hành TKTC",
        "title": "BẢN VẼ PHÁT HÀNH THIẾT KẾ THI CÔNG",
        "hint": "Quản lý bản vẽ thiết kế thi công được phát hành chính thức và file phát hành tương ứng.",
    },
    "UPDATED": {
        "tab": "BV cập nhật",
        "title": "BẢN VẼ CẬP NHẬT",
        "hint": "Theo dõi các revision/bản vẽ cập nhật, bản thay thế và ngày nhận phiên bản mới.",
    },
    "AS_BUILT": {
        "tab": "BV hoàn công",
        "title": "BẢN VẼ HOÀN CÔNG",
        "hint": "Quản lý bản vẽ hoàn công theo khu vực/hệ thống, revision, ngày nhận, trạng thái kiểm tra và file hoàn công đính kèm.",
    },
}

DRAWING_STATUSES = [
    "Mới nhận", "Đang kiểm tra", "Chờ phản hồi", "Chấp thuận",
    "Chấp thuận có điều kiện", "Cần sửa", "Thay thế", "Hủy"
]


class DrawingStatCard(QFrame):
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


class DrawingDialog(QDialog):
    def __init__(self, drawing_type: str, parent=None, record=None, attachments=None):
        super().__init__(parent)
        self.drawing_type = drawing_type
        cfg = DRAWING_TYPES[drawing_type]
        self.setWindowTitle(cfg["title"])
        self.resize(760, 700)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.number = QLineEdit()
        self.title = QLineEdit()
        self.discipline = QLineEdit()
        self.revision = QLineEdit()
        self.revision.setPlaceholderText("Ví dụ: Rev.00 / Rev.A / C01")
        self.issuer = QLineEdit()
        self.receiver = QLineEdit()
        self.received_date = QDateEdit(calendarPopup=True)
        self.received_date.setDisplayFormat("dd/MM/yyyy")
        self.received_date.setDate(QDate.currentDate())
        self.issue_date = QDateEdit(calendarPopup=True)
        self.issue_date.setDisplayFormat("dd/MM/yyyy")
        self.issue_date.setSpecialValueText("Chưa xác định")
        self.issue_date.setMinimumDate(QDate(2000, 1, 1))
        self.issue_date.setDate(self.issue_date.minimumDate())
        self.status = QComboBox()
        self.status.addItems(DRAWING_STATUSES)
        self.related_wbs = QLineEdit()
        self.related_wbs.setPlaceholderText("WBS / Task / Khu vực liên quan")
        self.reference_no = QLineEdit()
        self.reference_no.setPlaceholderText("Mã bản vẽ cũ / bản vẽ bị thay thế / hồ sơ tham chiếu")
        self.note = QTextEdit()
        self.note.setFixedHeight(100)

        form.addRow("Mã bản vẽ *", self.number)
        form.addRow("Tên bản vẽ *", self.title)
        form.addRow("Bộ môn / Hệ", self.discipline)
        form.addRow("Revision", self.revision)
        form.addRow("Đơn vị phát hành", self.issuer)
        form.addRow("Người nhận", self.receiver)
        form.addRow("Ngày nhận *", self.received_date)
        form.addRow("Ngày phát hành", self.issue_date)
        form.addRow("Trạng thái", self.status)
        form.addRow("WBS / Task", self.related_wbs)
        form.addRow("Tham chiếu / Thay thế", self.reference_no)
        form.addRow("Ghi chú", self.note)
        root.addLayout(form)

        file_title = QLabel("File đính kèm")
        file_title.setStyleSheet("font-weight:700;")
        root.addWidget(file_title)
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(120)
        root.addWidget(self.file_list)
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
        root.addLayout(file_btns)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for path in attachments or []:
            self._append_file(path)

        if record is not None:
            self.number.setText(record["drawing_no"] or "")
            self.title.setText(record["title"] or "")
            self.discipline.setText(record["discipline"] or "")
            self.revision.setText(record["revision"] or "")
            self.issuer.setText(record["issuer"] or "")
            self.receiver.setText(record["receiver"] or "")
            self._set_date(self.received_date, record["received_date"])
            if record["issue_date"]:
                self._set_date(self.issue_date, record["issue_date"])
            if record["status"]:
                idx = self.status.findText(record["status"])
                if idx >= 0:
                    self.status.setCurrentIndex(idx)
            self.related_wbs.setText(record["related_wbs"] or "")
            self.reference_no.setText(record["reference_no"] or "")
            self.note.setPlainText(record["note"] or "")

    @staticmethod
    def _set_date(widget: QDateEdit, value: str):
        if not value:
            return
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
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn file bản vẽ", "",
            "Bản vẽ / Tài liệu (*.pdf *.dwg *.dxf *.ifc *.rvt *.xlsx *.xls *.docx *.doc *.zip *.rar);;Tất cả file (*.*)"
        )
        existing = {self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())}
        for path in paths:
            if path not in existing:
                self._append_file(path)
                existing.add(path)

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
        if not self.number.text().strip() or not self.title.text().strip():
            QMessageBox.warning(self, "Thiếu dữ liệu", "Vui lòng nhập Mã bản vẽ và Tên bản vẽ.")
            return
        self.accept()

    def data(self):
        issue_date = ""
        if self.issue_date.date() != self.issue_date.minimumDate():
            issue_date = self.issue_date.date().toString("yyyy-MM-dd")
        return {
            "drawing_no": self.number.text().strip(),
            "title": self.title.text().strip(),
            "discipline": self.discipline.text().strip(),
            "revision": self.revision.text().strip(),
            "issuer": self.issuer.text().strip(),
            "receiver": self.receiver.text().strip(),
            "received_date": self.received_date.date().toString("yyyy-MM-dd"),
            "issue_date": issue_date,
            "status": self.status.currentText(),
            "related_wbs": self.related_wbs.text().strip(),
            "reference_no": self.reference_no.text().strip(),
            "note": self.note.toPlainText().strip(),
        }

    def attachments(self):
        return [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]


class AttachmentManagerDialog(QDialog):
    """Quản lý file nhanh mà không cần sửa thông tin bản vẽ."""
    def __init__(self, parent=None, drawing_no="", attachments=None):
        super().__init__(parent)
        self.setWindowTitle(f"Cập nhật file đính kèm - {drawing_no}")
        self.resize(680, 430)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Thêm file revision mới, bỏ file không còn dùng hoặc mở file hiện có."))
        self.file_list = QListWidget()
        root.addWidget(self.file_list, 1)
        for path in attachments or []:
            self._append_file(path)

        btns = QHBoxLayout()
        b_add = QPushButton("+ Thêm file / revision")
        b_remove = QPushButton("Bỏ file")
        b_open = QPushButton("Mở file")
        b_add.clicked.connect(self.add_files)
        b_remove.clicked.connect(self.remove_file)
        b_open.clicked.connect(self.open_file)
        btns.addWidget(b_add)
        btns.addWidget(b_remove)
        btns.addWidget(b_open)
        btns.addStretch()
        root.addLayout(btns)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _append_file(self, path: str):
        p = str(path)
        item = QListWidgetItem(attachment_name(p))
        item.setData(Qt.UserRole, p)
        item.setToolTip(split_attachment_ref(p)[0])
        self.file_list.addItem(item)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn file bản vẽ / revision mới", "",
            "Bản vẽ / Tài liệu (*.pdf *.dwg *.dxf *.ifc *.rvt *.xlsx *.xls *.docx *.doc *.zip *.rar);;Tất cả file (*.*)"
        )
        existing = {self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())}
        for path in paths:
            if path not in existing:
                self._append_file(path)
                existing.add(path)

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

    def attachments(self):
        return [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]


class DrawingTypeTab(QWidget):
    def __init__(self, db, drawing_type: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.drawing_type = drawing_type
        self.cfg = DRAWING_TYPES[drawing_type]
        self.project_id = None
        self.access_role = "admin"
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 8, 6, 6)
        root.setSpacing(8)

        cards = QGridLayout()
        self.card_total = DrawingStatCard("Tổng bản vẽ")
        self.card_new = DrawingStatCard("Mới nhận")
        self.card_review = DrawingStatCard("Đang xử lý")
        self.card_approved = DrawingStatCard("Đã chấp thuận")
        self.card_files = DrawingStatCard("File đính kèm")
        for i, card in enumerate([self.card_total, self.card_new, self.card_review, self.card_approved, self.card_files]):
            cards.addWidget(card, 0, i)
        root.addLayout(cards)

        toolbar = QHBoxLayout()
        self.btn_add = QPushButton("+ Thêm bản vẽ")
        self.btn_edit = QPushButton("Sửa")
        self.btn_delete = QPushButton("Xóa")
        self.btn_delete.setObjectName("dangerBtn")
        self.btn_files = QPushButton("📎 Cập nhật file")
        self.btn_open = QPushButton("Mở file")
        self.btn_export = QPushButton("Xuất Excel")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm mã / tên bản vẽ / bộ môn / revision / WBS...")
        self.status_filter = QComboBox()
        self.status_filter.addItem("Tất cả")
        self.status_filter.addItems(DRAWING_STATUSES)

        self.btn_add.clicked.connect(self.add_record)
        self.btn_edit.clicked.connect(self.edit_record)
        self.btn_delete.clicked.connect(self.delete_record)
        self.btn_files.clicked.connect(self.update_files)
        self.btn_open.clicked.connect(self.open_first_file)
        self.btn_export.clicked.connect(self.export_excel)
        self.search.textChanged.connect(self.refresh)
        self.status_filter.currentTextChanged.connect(self.refresh)

        for w in [self.btn_add, self.btn_edit, self.btn_delete, self.btn_files, self.btn_open, self.btn_export]:
            toolbar.addWidget(w)
        toolbar.addSpacing(8)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.status_filter)
        root.addLayout(toolbar)

        hint = QLabel(self.cfg["hint"] + "  Cột 'Cập nhật file' ghi thời điểm file đính kèm được thay đổi gần nhất.")
        hint.setObjectName("drawingHint")
        root.addWidget(hint)

        headers = [
            "ID", "Mã bản vẽ", "Tên bản vẽ", "Bộ môn/Hệ", "Revision",
            "Đơn vị phát hành", "Người nhận", "Ngày nhận", "Ngày phát hành",
            "Trạng thái", "WBS/Task", "Tham chiếu/Thay thế", "File",
            "Cập nhật file", "Ghi chú"
        ]
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
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_record())
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

    def set_project(self, project_id):
        self.project_id = project_id
        self.refresh()

    def _records(self):
        if not self.project_id:
            return []
        sql = """
            SELECT d.*,
                   (SELECT COUNT(*) FROM drawing_attachments a WHERE a.drawing_id=d.id) AS file_count
            FROM drawings d
            WHERE d.project_id=? AND d.drawing_type=?
        """
        params = [self.project_id, self.drawing_type]
        keyword = self.search.text().strip() if hasattr(self, "search") else ""
        status = self.status_filter.currentText() if hasattr(self, "status_filter") else "Tất cả"
        if keyword:
            sql += " AND (d.drawing_no LIKE ? OR d.title LIKE ? OR d.discipline LIKE ? OR d.revision LIKE ? OR d.related_wbs LIKE ? OR d.reference_no LIKE ?)"
            k = f"%{keyword}%"
            params += [k, k, k, k, k, k]
        if status != "Tất cả":
            sql += " AND d.status=?"
            params.append(status)
        sql += " ORDER BY d.received_date DESC, d.id DESC"
        return self.db.conn.execute(sql, params).fetchall()

    def refresh(self):
        rows = self._records()
        self.table.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            vals = [
                rec["id"], rec["drawing_no"], rec["title"], rec["discipline"], rec["revision"],
                rec["issuer"], rec["receiver"], rec["received_date"], rec["issue_date"], rec["status"],
                rec["related_wbs"], rec["reference_no"], rec["file_count"] or 0,
                rec["file_updated_at"] or "", rec["note"]
            ]
            for c, value in enumerate(vals):
                item = QTableWidgetItem(str(value or ""))
                if c in (4, 7, 8, 9, 12, 13):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)

            # Màu trạng thái để nhìn nhanh.
            status = rec["status"] or ""
            if status in ("Chấp thuận", "Chấp thuận có điều kiện"):
                self.table.item(r, 9).setBackground(QColor("#dcfce7"))
            elif status in ("Cần sửa", "Hủy", "Thay thế"):
                self.table.item(r, 9).setBackground(QColor("#fee2e2"))
            elif status in ("Đang kiểm tra", "Chờ phản hồi"):
                self.table.item(r, 9).setBackground(QColor("#fef3c7"))
            elif status == "Mới nhận":
                self.table.item(r, 9).setBackground(QColor("#dbeafe"))

            if int(rec["file_count"] or 0) > 0:
                self.table.item(r, 12).setBackground(QColor("#ecfdf5"))

        self._update_cards(rows)

    def _update_cards(self, rows):
        total = len(rows)
        new_count = sum(1 for r in rows if (r["status"] or "") == "Mới nhận")
        processing = sum(1 for r in rows if (r["status"] or "") in ("Đang kiểm tra", "Chờ phản hồi", "Cần sửa"))
        approved = sum(1 for r in rows if (r["status"] or "") in ("Chấp thuận", "Chấp thuận có điều kiện"))
        files = sum(int(r["file_count"] or 0) for r in rows)
        self.card_total.value.setText(str(total))
        self.card_new.value.setText(str(new_count))
        self.card_review.value.setText(str(processing))
        self.card_approved.value.setText(str(approved))
        self.card_files.value.setText(str(files))

    def selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        try:
            return int(self.table.item(row, 0).text())
        except Exception:
            return None

    def _get_record(self, drawing_id):
        return self.db.conn.execute("SELECT * FROM drawings WHERE id=?", (drawing_id,)).fetchone()

    def _attachments(self, drawing_id):
        rows = self.db.conn.execute(
            "SELECT file_path,file_name FROM drawing_attachments WHERE drawing_id=? ORDER BY id", (drawing_id,)
        ).fetchall()
        return [make_attachment_ref(r["file_path"], r["file_name"] or "") for r in rows]

    def _save_attachments(self, drawing_id, paths, force_touch=False):
        old_rows = self.db.conn.execute(
            "SELECT file_path FROM drawing_attachments WHERE drawing_id=? ORDER BY id", (drawing_id,)
        ).fetchall()
        old = [r["file_path"] for r in old_rows]
        cfg = load_app_settings()
        auto_drive = bool(cfg.get("drive_enabled") and cfg.get("drive_auto_upload"))
        drive = None; parent_id = ""
        if auto_drive:
            root_id = extract_drive_id(str(cfg.get("drive_root_folder_id") or cfg.get("drive_root_folder_url") or ""))
            if not root_id:
                raise RuntimeError("Google Drive đã bật nhưng chưa có thư mục gốc. Vào Cài đặt → Google Drive.")
            meta = self.db.conn.execute("""
                SELECT d.drawing_no,d.drawing_type,p.code AS project_code
                FROM drawings d JOIN projects p ON p.id=d.project_id WHERE d.id=?
            """, (drawing_id,)).fetchone()
            drive = GoogleDriveService.desktop(interactive=False)
            folder = drive.ensure_storage_path(root_id, meta["project_code"], f"Ban_ve_{meta['drawing_type']}", meta["drawing_no"])
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
        self.db.conn.execute("DELETE FROM drawing_attachments WHERE drawing_id=?", (drawing_id,))
        for path, name in stored:
            self.db.conn.execute(
                "INSERT INTO drawing_attachments(drawing_id,file_path,file_name,created_at) VALUES(?,?,?,?)",
                (drawing_id, str(path), str(name), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
        changed = force_touch or [x[0] for x in stored] != old
        if changed:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.conn.execute("UPDATE drawings SET file_updated_at=?,updated_at=? WHERE id=?", (stamp, stamp, drawing_id))
        self.db.conn.commit()

    def add_record(self):
        if not self._require_update():
            return
        if not self.project_id:
            QMessageBox.information(self, "Chưa có dự án", "Hãy tạo/chọn dự án ở sheet Quản lý tiến độ trước.")
            return
        dlg = DrawingDialog(self.drawing_type, self)
        if dlg.exec() != QDialog.Accepted:
            return
        d = dlg.data()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cols = [
            "project_id", "drawing_type", "drawing_no", "title", "discipline", "revision", "issuer", "receiver",
            "received_date", "issue_date", "status", "related_wbs", "reference_no", "note", "created_at", "updated_at"
        ]
        vals = [self.project_id, self.drawing_type] + [d[k] for k in [
            "drawing_no", "title", "discipline", "revision", "issuer", "receiver", "received_date", "issue_date",
            "status", "related_wbs", "reference_no", "note"
        ]] + [now, now]
        try:
            cur = self.db.conn.execute(
                f"INSERT INTO drawings({','.join(cols)}) VALUES({','.join(['?']*len(cols))})", vals
            )
            drawing_id = cur.lastrowid
            self.db.conn.commit()
            self._save_attachments(drawing_id, dlg.attachments())
            self.refresh()
        except Exception as exc:
            self.db.conn.rollback()
            QMessageBox.critical(self, "Không lưu được", f"Không thể thêm bản vẽ.\n\n{exc}")

    def edit_record(self):
        if not self._require_update():
            return
        drawing_id = self.selected_id()
        if not drawing_id:
            return
        rec = self._get_record(drawing_id)
        dlg = DrawingDialog(self.drawing_type, self, rec, self._attachments(drawing_id))
        if dlg.exec() != QDialog.Accepted:
            return
        d = dlg.data()
        cols = [
            "drawing_no", "title", "discipline", "revision", "issuer", "receiver", "received_date", "issue_date",
            "status", "related_wbs", "reference_no", "note"
        ]
        try:
            self.db.conn.execute(
                f"UPDATE drawings SET {','.join(f'{c}=?' for c in cols)}, updated_at=? WHERE id=?",
                [d[c] for c in cols] + [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), drawing_id]
            )
            self.db.conn.commit()
            self._save_attachments(drawing_id, dlg.attachments())
            self.refresh()
        except Exception as exc:
            self.db.conn.rollback()
            QMessageBox.critical(self, "Không lưu được", str(exc))

    def delete_record(self):
        if not self._require_admin():
            return
        drawing_id = self.selected_id()
        if not drawing_id:
            return
        rec = self._get_record(drawing_id)
        if QMessageBox.question(self, "Xác nhận", f"Xóa bản vẽ {rec['drawing_no']} đang chọn?") == QMessageBox.Yes:
            self.db.conn.execute("DELETE FROM drawings WHERE id=?", (drawing_id,))
            self.db.conn.commit()
            self.refresh()

    def update_files(self):
        if not self._require_update():
            return
        drawing_id = self.selected_id()
        if not drawing_id:
            QMessageBox.information(self, "Chưa chọn bản vẽ", "Hãy chọn một dòng bản vẽ trước.")
            return
        rec = self._get_record(drawing_id)
        dlg = AttachmentManagerDialog(self, rec["drawing_no"], self._attachments(drawing_id))
        if dlg.exec() == QDialog.Accepted:
            self._save_attachments(drawing_id, dlg.attachments(), force_touch=True)
            self.refresh()

    def open_first_file(self):
        drawing_id = self.selected_id()
        if not drawing_id:
            return
        paths = self._attachments(drawing_id)
        if not paths:
            QMessageBox.information(self, "Chưa có file", "Bản vẽ này chưa có file đính kèm.")
            return
        path = paths[-1]  # ưu tiên file/revision được thêm sau cùng
        if not Path(path).exists():
            QMessageBox.warning(self, "Không thấy file", f"Không tìm thấy:\n{path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def export_excel(self):
        if Workbook is None:
            QMessageBox.warning(self, "Thiếu thư viện", "Cài openpyxl: pip install openpyxl")
            return
        if not self.project_id:
            return
        default_name = f"{self.cfg['tab'].replace(' ', '_')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Xuất danh mục bản vẽ", default_name, "Excel (*.xlsx)")
        if not path:
            return
        rows = self._records()
        wb = Workbook()
        ws = wb.active
        ws.title = self.cfg["tab"][:31]
        headers = [
            "Mã bản vẽ", "Tên bản vẽ", "Bộ môn/Hệ", "Revision", "Đơn vị phát hành", "Người nhận",
            "Ngày nhận", "Ngày phát hành", "Trạng thái", "WBS/Task", "Tham chiếu/Thay thế",
            "Số file", "Cập nhật file gần nhất", "Ghi chú"
        ]
        ws.append(headers)
        for rec in rows:
            ws.append([
                rec["drawing_no"], rec["title"], rec["discipline"], rec["revision"], rec["issuer"], rec["receiver"],
                rec["received_date"], rec["issue_date"], rec["status"], rec["related_wbs"], rec["reference_no"],
                rec["file_count"] or 0, rec["file_updated_at"] or "", rec["note"]
            ])
        ws.freeze_panes = "A2"
        for col in ws.columns:
            width = min(60, max(12, max(len(str(cell.value or "")) for cell in col) + 2))
            ws.column_dimensions[col[0].column_letter].width = width
        wb.save(path)
        QMessageBox.information(self, "Hoàn tất", f"Đã xuất {len(rows)} bản vẽ:\n{path}")


class DrawingManagerPage(QWidget):
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
            CREATE TABLE IF NOT EXISTS drawings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                drawing_type TEXT NOT NULL,
                drawing_no TEXT NOT NULL,
                title TEXT NOT NULL,
                discipline TEXT DEFAULT '',
                revision TEXT DEFAULT '',
                issuer TEXT DEFAULT '',
                receiver TEXT DEFAULT '',
                received_date TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                status TEXT DEFAULT 'Mới nhận',
                related_wbs TEXT DEFAULT '',
                reference_no TEXT DEFAULT '',
                note TEXT DEFAULT '',
                file_updated_at TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, drawing_type, drawing_no, revision)
            )
        """)
        self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS drawing_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drawing_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                FOREIGN KEY(drawing_id) REFERENCES drawings(id) ON DELETE CASCADE
            )
        """)
        self.db.conn.execute("CREATE INDEX IF NOT EXISTS idx_drawings_project_type ON drawings(project_id, drawing_type)")
        self.db.conn.execute("CREATE INDEX IF NOT EXISTS idx_drawings_received ON drawings(project_id, received_date)")
        self.db.conn.commit()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("QUẢN LÝ BẢN VẼ DỰ ÁN")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Shopdrawing • Bản vẽ phát hành thiết kế thi công • Bản vẽ cập nhật • Bản vẽ hoàn công • Revision • File đính kèm")
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

        hint = QLabel(
            "Mỗi nhóm bản vẽ là một sheet riêng. Ngày nhận được lưu cho từng revision; nút 'Cập nhật file' cho phép thêm/thay file và tự ghi thời điểm cập nhật gần nhất."
        )
        hint.setObjectName("drawingHint")
        root.addWidget(hint)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.type_tabs = {}
        for drawing_type in ("SHOPDRAWING", "ISSUED_DESIGN", "UPDATED", "AS_BUILT"):
            tab = DrawingTypeTab(self.db, drawing_type, self)
            self.type_tabs[drawing_type] = tab
            self.tabs.addTab(tab, DRAWING_TYPES[drawing_type]["tab"])
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
