from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton, QLineEdit,
    QTextEdit, QTextBrowser, QTabWidget, QComboBox, QCheckBox, QFileDialog,
    QMessageBox, QFrame
)

from ai_service import AIServiceError, AISettings, OpenAIProjectAssistant, GeminiSettings, GeminiProjectAssistant, ProjectContextBuilder
from settings_store import get_ai_runtime_settings


class AIWorker(QThread):
    result = Signal(str)
    error = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(str(self.fn()))
        except Exception as exc:
            self.error.emit(str(exc))


class AIAssistantPage(QWidget):
    """Desktop AI sheet. API calls run in worker threads so Qt remains responsive."""

    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self.project_id = None
        self.history: list[dict] = []
        self.local_file_path = ""
        self._workers: list[AIWorker] = []
        self.context_builder = ProjectContextBuilder(self.db_path)
        self.build_ui()
        self.refresh_attachments()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        title = QLabel("🤖 TRỢ LÝ AI QLDA")
        title.setObjectName("aiPageTitle")
        subtitle = QLabel("Chat với dự án • Rủi ro tiến độ • Dự thảo báo cáo • Đọc hồ sơ • Tra cứu văn bản")
        subtitle.setObjectName("aiSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        cfg = QFrame(); cfg.setObjectName("aiConfig")
        cfg_layout = QHBoxLayout(cfg)
        self.settings_status = QLabel(""); self.settings_status.setWordWrap(True)
        self.ai_health = QLabel("Chưa kiểm tra"); self.ai_health.setStyleSheet("color:#64748b;font-weight:600;")
        self.test_btn = QPushButton("Kiểm tra AI"); self.test_btn.clicked.connect(self.test_ai)
        cfg_layout.addWidget(self.settings_status, 1); cfg_layout.addWidget(self.ai_health); cfg_layout.addWidget(self.test_btn)
        root.addWidget(cfg)
        note = QLabel("Cấu hình OpenAI API key, model và Web Search đã chuyển sang sheet ⚙ Cài đặt. AI chỉ đưa ra đề xuất; người dùng vẫn là người phê duyệt/kết luận.")
        note.setObjectName("aiHint"); note.setWordWrap(True); root.addWidget(note)
        self.refresh_settings_status()

        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self._build_chat_tab()
        self._build_analysis_tab()
        self._build_file_tab()
        self._build_legal_tab()

    def _build_chat_tab(self):
        page = QWidget(); lay = QVBoxLayout(page)
        self.chat_view = QTextBrowser(); self.chat_view.setOpenExternalLinks(True)
        self.chat_input = QTextEdit(); self.chat_input.setPlaceholderText("Ví dụ: 20 công việc rủi ro nhất? RFI nào đang quá hạn? Biên bản hiện trường nào cần xử lý?"); self.chat_input.setFixedHeight(85)
        row = QHBoxLayout()
        ask = QPushButton("Gửi câu hỏi"); ask.clicked.connect(self.ask_chat)
        clear = QPushButton("Xóa hội thoại"); clear.clicked.connect(self.clear_chat)
        row.addWidget(ask); row.addWidget(clear); row.addStretch()
        lay.addWidget(self.chat_view, 1); lay.addWidget(self.chat_input); lay.addLayout(row)
        self.tabs.addTab(page, "💬 Chat với dự án")

    def _build_analysis_tab(self):
        page = QWidget(); lay = QVBoxLayout(page)
        row = QHBoxLayout()
        b1 = QPushButton("⚠️ Phân tích rủi ro tiến độ"); b1.clicked.connect(self.analyze_risk)
        b2 = QPushButton("📝 Báo cáo tuần"); b2.clicked.connect(lambda: self.draft_report("tuần"))
        b3 = QPushButton("📝 Báo cáo tháng"); b3.clicked.connect(lambda: self.draft_report("tháng"))
        for b in (b1,b2,b3): row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        self.analysis_view = QTextBrowser(); self.analysis_view.setOpenExternalLinks(True); lay.addWidget(self.analysis_view, 1)
        self.tabs.addTab(page, "📈 Phân tích & báo cáo")

    def _build_file_tab(self):
        page = QWidget(); lay = QVBoxLayout(page)
        form = QFormLayout()
        self.attachment_combo = QComboBox(); self.attachment_combo.setMinimumWidth(500)
        refresh = QPushButton("Làm mới danh sách"); refresh.clicked.connect(self.refresh_attachments)
        ar = QHBoxLayout(); ar.addWidget(self.attachment_combo, 1); ar.addWidget(refresh)
        form.addRow("File hồ sơ đã lưu:", ar)
        self.file_label = QLabel("Chưa chọn file ngoài")
        browse = QPushButton("Chọn file ngoài..."); browse.clicked.connect(self.browse_file)
        fr = QHBoxLayout(); fr.addWidget(self.file_label, 1); fr.addWidget(browse)
        form.addRow("Hoặc file trên máy:", fr)
        self.file_instruction = QTextEdit(); self.file_instruction.setFixedHeight(70); self.file_instruction.setPlaceholderText("Ví dụ: kiểm tra các điểm thiếu trong hồ sơ, tóm tắt CO/CQ, trích thông số kỹ thuật...")
        form.addRow("Yêu cầu AI:", self.file_instruction)
        lay.addLayout(form)
        row = QHBoxLayout()
        use_saved = QPushButton("Tóm tắt file đã lưu"); use_saved.clicked.connect(self.summarize_saved_attachment)
        use_local = QPushButton("Tóm tắt file ngoài"); use_local.clicked.connect(self.summarize_local_file)
        row.addWidget(use_saved); row.addWidget(use_local); row.addStretch(); lay.addLayout(row)
        self.file_view = QTextBrowser(); self.file_view.setOpenExternalLinks(True); lay.addWidget(self.file_view, 1)
        self.tabs.addTab(page, "📎 Đọc hồ sơ đính kèm")

    def _build_legal_tab(self):
        page = QWidget(); lay = QVBoxLayout(page)
        self.legal_question = QTextEdit(); self.legal_question.setFixedHeight(90); self.legal_question.setPlaceholderText("Ví dụ: Tra cứu các văn bản trong kho liên quan quản lý chất lượng và nghiệm thu vật liệu đầu vào")
        ask = QPushButton("Tra cứu bằng AI"); ask.clicked.connect(self.ask_legal)
        lay.addWidget(self.legal_question); lay.addWidget(ask)
        self.legal_view = QTextBrowser(); self.legal_view.setOpenExternalLinks(True); lay.addWidget(self.legal_view, 1)
        self.tabs.addTab(page, "⚖️ AI tra cứu văn bản")

    def set_project(self, project_id):
        self.project_id = project_id
        self.history.clear()
        self.chat_view.clear()
        self.refresh_attachments()

    def refresh_settings_status(self):
        cfg = get_ai_runtime_settings()
        if not hasattr(self, "settings_status"):
            return
        provider = "Gemini" if cfg.get("provider") == "gemini" else "OpenAI"
        if cfg.get("api_key"):
            self.settings_status.setText(f"AI: {provider} • Model: {cfg.get('model','')} • Web Search: {'Bật' if cfg.get('use_web') else 'Tắt'} • chỉnh tại ⚙ Cài đặt")
        else:
            key_name = "GEMINI_API_KEY" if cfg.get("provider") == "gemini" else "OPENAI_API_KEY"
            self.settings_status.setText(f"AI: CHƯA CÓ {key_name} • vào sheet ⚙ Cài đặt để cấu hình.")

    def _settings(self):
        cfg = get_ai_runtime_settings()
        if cfg.get("provider") == "gemini":
            return GeminiSettings(api_key=cfg.get("api_key", ""), model=cfg.get("model", "gemini-2.5-flash"), use_web=bool(cfg.get("use_web", False)))
        return AISettings(api_key=cfg.get("api_key", ""), model=cfg.get("model", "gpt-5-mini"), use_web=bool(cfg.get("use_web", False)))

    def _assistant(self):
        settings = self._settings()
        if isinstance(settings, GeminiSettings):
            return GeminiProjectAssistant(self.db_path, settings)
        return OpenAIProjectAssistant(self.db_path, settings)

    def _require_project(self) -> bool:
        if not self.project_id:
            QMessageBox.information(self, "Chưa chọn dự án", "Hãy chọn dự án ở sheet Quản lý tiến độ trước.")
            return False
        return True

    def _run(self, fn, target: QTextBrowser | None = None, prefix="Đang xử lý AI..."):
        if target:
            target.setMarkdown(f"*{prefix}*")
        worker = AIWorker(fn, self)
        self._workers.append(worker)
        worker.result.connect(lambda text, w=worker: self._worker_ok(w, text, target))
        worker.error.connect(lambda err, w=worker: self._worker_error(w, err, target))
        worker.start()

    def _worker_ok(self, worker, text, target):
        if target:
            target.setMarkdown(text)
        try: self._workers.remove(worker)
        except ValueError: pass
        worker.deleteLater()

    def _friendly_error_title(self, err: str) -> str:
        e = (err or "").lower()
        if "quota/credit" in e or "insufficient" in e: return "AI hết quota/credit"
        if "api key không hợp lệ" in e or "xác thực" in e: return "API key không hợp lệ"
        if "giới hạn tốc độ" in e or "rate limit" in e: return "Rate limit API"
        if "không kết nối" in e or "timeout" in e: return "Lỗi kết nối AI"
        if "model" in e and ("không" in e or "quyền" in e): return "Lỗi model/quyền"
        return "Lỗi AI"

    def _worker_error(self, worker, err, target):
        if target:
            target.setMarkdown(f"**{self._friendly_error_title(err)}**\n\n{err}")
        QMessageBox.warning(self, self._friendly_error_title(err), err)
        try: self._workers.remove(worker)
        except ValueError: pass
        worker.deleteLater()

    def _test_ai_ok(self, text, worker):
        self.ai_health.setText("● Hoạt động")
        self.ai_health.setStyleSheet("color:#15803d;font-weight:700;")
        QMessageBox.information(self, "Kiểm tra AI", text)
        self._cleanup_worker(worker)

    def _test_ai_error(self, err, worker):
        low=(err or "").lower()
        if "quota/credit" in low:
            label="● Hết quota"; color="#b45309"
        elif "giới hạn tốc độ" in low:
            label="● Rate limit"; color="#b45309"
        elif "api key không hợp lệ" in low:
            label="● Key lỗi"; color="#b91c1c"
        elif "không kết nối" in low or "timeout" in low:
            label="● Lỗi mạng"; color="#b91c1c"
        else:
            label="● Có lỗi"; color="#b91c1c"
        self.ai_health.setText(label)
        self.ai_health.setStyleSheet(f"color:{color};font-weight:700;")
        QMessageBox.warning(self, self._friendly_error_title(err), err)
        self._cleanup_worker(worker)

    def test_ai(self):
        self.ai_health.setText("● Đang kiểm tra...")
        self.ai_health.setStyleSheet("color:#2563eb;font-weight:700;")
        self.test_btn.setEnabled(False)
        worker = AIWorker(lambda: self._assistant().test_connection(), self)
        self._workers.append(worker)
        worker.result.connect(lambda text, w=worker: self._test_ai_ok(text, w))
        worker.error.connect(lambda err, w=worker: self._test_ai_error(err, w))
        worker.finished.connect(lambda: self.test_btn.setEnabled(True))
        worker.start()

    def _render_history(self):
        md = []
        for m in self.history:
            title = "**Bạn:**" if m["role"] == "user" else "**AI:**"
            md.append(f"{title}\n\n{m['content']}\n\n---")
        self.chat_view.setMarkdown("\n\n".join(md))

    def ask_chat(self):
        if not self._require_project(): return
        q = self.chat_input.toPlainText().strip()
        if not q: return
        previous = list(self.history)
        self.history.append({"role":"user", "content":q}); self.chat_input.clear(); self._render_history()
        worker = AIWorker(lambda: self._assistant().ask_project(self.project_id, q, previous, date.today(), False), self)
        self._workers.append(worker)
        def ok(text, w=worker):
            self.history.append({"role":"assistant", "content":text}); self._render_history(); self._cleanup_worker(w)
        worker.result.connect(ok)
        worker.error.connect(lambda err, w=worker: (QMessageBox.warning(self,"Lỗi AI",err), self._cleanup_worker(w)))
        worker.start()

    def _cleanup_worker(self, w):
        try: self._workers.remove(w)
        except ValueError: pass
        w.deleteLater()

    def clear_chat(self):
        self.history.clear(); self.chat_view.clear()

    def analyze_risk(self):
        if not self._require_project(): return
        self._run(lambda: self._assistant().analyze_schedule_risk(self.project_id, date.today()), self.analysis_view, "Đang phân tích tiến độ...")

    def draft_report(self, period):
        if not self._require_project(): return
        self._run(lambda: self._assistant().draft_report(self.project_id, period, date.today()), self.analysis_view, f"Đang soạn báo cáo {period}...")

    def refresh_attachments(self):
        self.attachment_combo.clear()
        if not self.project_id:
            self.attachment_combo.addItem("Chưa chọn dự án", None); return
        try:
            items = self.context_builder.attachment_catalog(int(self.project_id))
        except Exception as exc:
            self.attachment_combo.addItem(f"Không đọc được file: {exc}", None); return
        if not items:
            self.attachment_combo.addItem("Chưa có file hồ sơ đính kèm", None); return
        for a in items:
            label = f"{a.get('doc_type','')} {a.get('code','')} — {a.get('file_name') or Path(a.get('file_path') or '').name} — {a.get('subject','')}"
            self.attachment_combo.addItem(label, int(a["id"]))

    def browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file để AI đọc", "", "Tài liệu (*.pdf *.docx *.xlsx *.xls *.txt *.csv *.png *.jpg *.jpeg);;Tất cả (*.*)")
        if path:
            self.local_file_path = path; self.file_label.setText(path)

    def summarize_saved_attachment(self):
        if not self._require_project(): return
        aid = self.attachment_combo.currentData()
        if not aid:
            QMessageBox.information(self,"Chưa có file","Hãy chọn một file hồ sơ đã lưu."); return
        def work():
            name, mime, data = self.context_builder.load_attachment(int(aid))
            return self._assistant().summarize_file(self.project_id, name, data, self.file_instruction.toPlainText().strip(), date.today())
        self._run(work, self.file_view, "Đang đọc file hồ sơ...")

    def summarize_local_file(self):
        if not self._require_project(): return
        if not self.local_file_path:
            QMessageBox.information(self,"Chưa chọn file","Hãy chọn file trên máy trước."); return
        path = Path(self.local_file_path)
        self._run(lambda: self._assistant().summarize_file(self.project_id, path.name, path.read_bytes(), self.file_instruction.toPlainText().strip(), date.today()), self.file_view, "Đang đọc file...")

    def ask_legal(self):
        if not self._require_project(): return
        q = self.legal_question.toPlainText().strip()
        if not q: return
        self._run(lambda: self._assistant().legal_qa(self.project_id, q, date.today(), self._settings().use_web), self.legal_view, "Đang tra cứu văn bản...")
