from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QProcess, QTimer
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog,
    QAbstractItemView, QFrame, QProgressBar, QDialog, QFormLayout, QDialogButtonBox
)

from legal_documents import LegalRepository, load_google_search_config, google_browser_url
from settings_store import get_specified_search_domains

try:
    from openpyxl import Workbook
except Exception:
    Workbook = None


class MetricCard(QFrame):
    def __init__(self, title: str):
        super().__init__(); self.setObjectName("legalCard")
        lay = QVBoxLayout(self)
        self.title = QLabel(title); self.title.setObjectName("legalCardTitle")
        self.value = QLabel("0"); self.value.setObjectName("legalCardValue")
        lay.addWidget(self.title); lay.addWidget(self.value)


class OnlineSearchResultsDialog(QDialog):
    def __init__(self, query: str, docs: list[dict], parent=None):
        super().__init__(parent)
        self.docs = docs or []
        self.setWindowTitle(f"Kết quả tìm kiếm online - {query}")
        self.resize(1250, 650)
        lay = QVBoxLayout(self)
        title = QLabel(f"Tìm thấy {len(self.docs)} kết quả cho: {query}")
        title.setStyleSheet("font-weight:700; font-size:15px;")
        note = QLabel("Nguồn chính thức được ưu tiên. Kết quả từ web tham khảo cần mở nguồn gốc để kiểm tra hiệu lực/nội dung trước khi áp dụng.")
        note.setWordWrap(True)
        lay.addWidget(title); lay.addWidget(note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Loại", "Số hiệu", "Tên / trích yếu", "Cơ quan", "Nguồn", "URL"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        hdr=self.table.horizontalHeader(); hdr.setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(0,100); self.table.setColumnWidth(1,180); self.table.setColumnWidth(2,470); self.table.setColumnWidth(3,180); self.table.setColumnWidth(4,260)
        self.table.setColumnHidden(5, True)
        self.table.setRowCount(len(self.docs))
        for r,d in enumerate(self.docs):
            vals=[d.get("category",""),d.get("number",""),d.get("title",""),d.get("issuer",""),d.get("source_name",""),d.get("source_url","")]
            for c,v in enumerate(vals): self.table.setItem(r,c,QTableWidgetItem(str(v or "")))
            if "chính thức" in str(d.get("status","")).lower() or "vsqi" in str(d.get("source_name","")).lower():
                self.table.item(r,4).setBackground(QColor("#dcfce7"))
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        lay.addWidget(self.table, 1)
        row=QHBoxLayout(); row.addStretch()
        bopen=QPushButton("Mở nguồn"); bopen.clicked.connect(self.open_selected)
        bclose=QPushButton("Đóng"); bclose.clicked.connect(self.accept)
        row.addWidget(bopen); row.addWidget(bclose); lay.addLayout(row)

    def open_selected(self):
        r=self.table.currentRow()
        if r < 0: return
        item=self.table.item(r,5); url=item.text().strip() if item else ""
        if url: QDesktopServices.openUrl(QUrl(url))






class LegalDocumentsPage(QWidget):
    """Legal dashboard with online sync isolated in a child Python process.

    QThread does not isolate Python's GIL, so BeautifulSoup parsing may still make
    the Qt GUI stutter. QProcess fully isolates network/HTML parsing and keeps the
    desktop responsive even during large online updates.
    """
    def __init__(self, db_path: str | Path, parent=None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self.repo = LegalRepository(self.db_path)
        self.proc: QProcess | None = None
        self.proc_stdout = ""; self.proc_stderr = ""; self.rows = []
        self._active_mode = ""; self._active_query = ""
        self._sync_buttons = []
        self._search_timer = QTimer(self); self._search_timer.setSingleShot(True); self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self.build_ui(); self.reload_filters(); self.refresh()

    def build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(12,10,12,10); root.setSpacing(9)
        title=QLabel("VĂN BẢN QLDA XÂY DỰNG"); title.setObjectName("pageTitle")
        sub=QLabel("Luật • Nghị định • Thông tư • QCVN • TCVN • Quyết định • Dự thảo — nguồn chính thức + đồng bộ Thư Viện Pháp Luật (tham khảo)")
        sub.setObjectName("subtitle")
        self.sync_label=QLabel(""); self.sync_label.setObjectName("sourceLabel")
        root.addWidget(title); root.addWidget(sub); root.addWidget(self.sync_label)

        buttons=QHBoxLayout()
        for text,mode in [("🔄 Cập nhật tất cả","all"),("⚖ VBPL / Cổng Chính phủ","vbpl"),("📐 TCVN XD - VSQI","vsqi"),("📝 Dự thảo BXD","moc_drafts"),("📚 Cập nhật TVPL","tvpl")]:
            b=QPushButton(text); b.clicked.connect(lambda _=False,m=mode:self.start_sync(m)); buttons.addWidget(b); self._sync_buttons.append(b)
        self.btn_cancel=QPushButton("⛔ Dừng cập nhật"); self.btn_cancel.clicked.connect(self.cancel_sync); self.btn_cancel.setEnabled(False); buttons.addWidget(self.btn_cancel)
        buttons.addStretch(); root.addLayout(buttons)

        self.progress=QProgressBar(); self.progress.setRange(0,0); self.progress.setTextVisible(False); self.progress.hide(); root.addWidget(self.progress)

        lookup=QHBoxLayout(); self.standard_search=QLineEdit(); self.standard_search.setPlaceholderText("Nhập bất kỳ nội dung cần tìm trên web: Thông tư 06/2021/TT-BXD, phân cấp công trình, TCVN 5575, QCVN 06...")
        self.standard_search.returnPressed.connect(self.lookup_online)
        btn_lookup=QPushButton("🔎 Tìm Google / toàn web"); btn_lookup.clicked.connect(self.lookup_online)
        self.btn_sites=QPushButton("🏛 Tìm trang chỉ định"); self.btn_sites.clicked.connect(self.lookup_specified_sites)
        self.btn_google_browser=QPushButton("↗ Mở Google toàn web"); self.btn_google_browser.clicked.connect(self.open_google_browser)
        lookup.addWidget(QLabel("Tìm online:")); lookup.addWidget(self.standard_search,1); lookup.addWidget(btn_lookup); lookup.addWidget(self.btn_sites); lookup.addWidget(self.btn_google_browser); self.btn_lookup=btn_lookup; root.addLayout(lookup)
        self.google_status=QLabel(""); self.google_status.setObjectName("sourceLabel"); root.addWidget(self.google_status); self.refresh_google_status()
        self.sites_hint=QLabel(""); self.refresh_sites_hint(); sites_hint=self.sites_hint
        sites_hint.setObjectName("subtitle"); root.addWidget(sites_hint)

        cards=QHBoxLayout(); self.card_total=MetricCard("Tổng văn bản"); self.card_active=MetricCard("Còn hiệu lực"); self.card_std=MetricCard("TCVN / QCVN"); self.card_draft=MetricCard("Dự thảo")
        for c in [self.card_total,self.card_active,self.card_std,self.card_draft]: cards.addWidget(c)
        root.addLayout(cards)

        filters=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText("Tìm số hiệu / tên / lĩnh vực...")
        self.search.textChanged.connect(lambda *_: self._search_timer.start())
        self.category=QComboBox(); self.category.currentTextChanged.connect(self.refresh)
        self.status=QComboBox(); self.status.currentTextChanged.connect(self.refresh)
        self.source=QComboBox(); self.source.currentTextChanged.connect(self.refresh)
        btn_open=QPushButton("Mở nguồn"); btn_open.clicked.connect(self.open_selected)
        btn_export=QPushButton("Xuất Excel"); btn_export.clicked.connect(self.export_excel)
        filters.addWidget(self.search,2); filters.addWidget(self.category); filters.addWidget(self.status); filters.addWidget(self.source); filters.addWidget(btn_open); filters.addWidget(btn_export); root.addLayout(filters)

        self.table=QTableWidget(0,12); self.table.setHorizontalHeaderLabels(["Loại","Số hiệu","Tên / trích yếu","Cơ quan","Ban hành","Hiệu lực","Hết hiệu lực/Hạn góp ý","Trạng thái","Lĩnh vực","Nguồn","URL","Cập nhật online"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False)
        hdr=self.table.horizontalHeader(); hdr.setSectionResizeMode(QHeaderView.Interactive); hdr.setStretchLastSection(False)
        self.table.setColumnWidth(0,90); self.table.setColumnWidth(1,150); self.table.setColumnWidth(2,380); self.table.setColumnWidth(3,180); self.table.setColumnWidth(7,190); self.table.setColumnWidth(8,220); self.table.setColumnWidth(9,260); self.table.setColumnWidth(11,150)
        self.table.setColumnHidden(10,True); self.table.cellDoubleClicked.connect(lambda *_:self.open_selected()); root.addWidget(self.table,1)
        note=QLabel("Lưu ý: TVPL là nguồn tham khảo. App chỉ lưu metadata/link và ưu tiên đối chiếu số hiệu với nguồn chính thức. Cập nhật online chạy ở process riêng."); note.setObjectName("ttHint"); root.addWidget(note)

    def reload_filters(self):
        current=(self.category.currentText(),self.status.currentText(),self.source.currentText())
        specs=[(self.category,["Tất cả"]+self.repo.categories(),current[0]),(self.status,["Tất cả"]+self.repo.statuses(),current[1]),(self.source,["Tất cả"]+self.repo.sources(),current[2])]
        for combo,vals,old in specs:
            combo.blockSignals(True); combo.clear(); combo.addItems(vals)
            if old in vals: combo.setCurrentText(old)
            combo.blockSignals(False)

    def refresh(self):
        self.rows=self.repo.list_documents(self.search.text().strip(),self.category.currentText() or "Tất cả",self.status.currentText() or "Tất cả",self.source.currentText() or "Tất cả",True)
        self.table.setUpdatesEnabled(False); self.table.blockSignals(True); self.table.setSortingEnabled(False)
        try:
            self.table.clearContents(); self.table.setRowCount(len(self.rows))
            for r,x in enumerate(self.rows):
                vals=[x["category"],x["number"],x["title"],x["issuer"],x["issue_date"],x["effective_date"],x["expiry_date"],x["status"],x["field"],x["source_name"],x["source_url"],x["online_updated_at"]]
                for c,v in enumerate(vals): self.table.setItem(r,c,QTableWidgetItem(str(v or "")))
                st=(x["status"] or "").lower()
                if "hết hiệu lực" in st: self.table.item(r,7).setBackground(QColor("#fee2e2"))
                elif "còn hiệu lực" in st: self.table.item(r,7).setBackground(QColor("#dcfce7"))
                elif x["is_draft"]: self.table.item(r,7).setBackground(QColor("#fef3c7"))
        finally:
            self.table.blockSignals(False); self.table.setUpdatesEnabled(True); self.table.viewport().update()
        active=sum(1 for x in self.rows if "còn hiệu lực" in (x["status"] or "").lower() and "hết hiệu lực" not in (x["status"] or "").lower())
        standards=sum(1 for x in self.rows if x["category"] in ("TCVN","QCVN","Dự thảo TCVN","Dự thảo QCVN")); drafts=sum(1 for x in self.rows if x["is_draft"])
        self.card_total.value.setText(str(len(self.rows))); self.card_active.value.setText(str(active)); self.card_std.value.setText(str(standards)); self.card_draft.value.setText(str(drafts))
        last=self.repo.last_sync(); self.sync_label.setText(f"Cập nhật online gần nhất: {last['sync_time']} • {last['source_name']} • {last['status']}" if last else "Chưa cập nhật online")

    def _set_busy(self,busy:bool):
        for b in self._sync_buttons: b.setEnabled(not busy)
        self.btn_lookup.setEnabled(not busy); self.btn_sites.setEnabled(not busy); self.btn_google_browser.setEnabled(not busy); self.btn_cancel.setEnabled(busy)
        self.progress.setVisible(busy)
        if busy: self.sync_label.setText("Đang cập nhật online ở tiến trình nền… Có thể tiếp tục dùng các sheet khác.")

    def start_sync(self,mode:str,query:str=""):
        if self.proc is not None and self.proc.state()!=QProcess.NotRunning:
            QMessageBox.information(self,"Đang cập nhật","Một tác vụ cập nhật online đang chạy."); return
        self.proc_stdout=""; self.proc_stderr=""; self._active_mode=mode; self._active_query=query; self.proc=QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read_stdout); self.proc.readyReadStandardError.connect(self._read_stderr); self.proc.finished.connect(self._process_finished)
        script=Path(__file__).resolve().parent/'legal_update_cli.py'
        args=[str(script),'--db',str(self.db_path),'--source',mode]
        if query: args += ['--query',query]
        self._set_busy(True); self.proc.start(sys.executable,args)
        if not self.proc.waitForStarted(2500):
            self._set_busy(False); QMessageBox.critical(self,"Không chạy được cập nhật","Không khởi động được process cập nhật online.")

    def _read_stdout(self):
        if self.proc: self.proc_stdout += bytes(self.proc.readAllStandardOutput()).decode('utf-8','replace')
    def _read_stderr(self):
        if self.proc: self.proc_stderr += bytes(self.proc.readAllStandardError()).decode('utf-8','replace')

    def cancel_sync(self):
        if self.proc and self.proc.state()!=QProcess.NotRunning:
            self.proc.terminate(); QTimer.singleShot(1500, lambda: self.proc.kill() if self.proc and self.proc.state()!=QProcess.NotRunning else None)
            self.sync_label.setText("Đang dừng cập nhật…")

    def _process_finished(self,exit_code:int,_status):
        self._read_stdout(); self._read_stderr(); self._set_busy(False)
        payload=None
        for line in reversed([x.strip() for x in self.proc_stdout.splitlines() if x.strip()]):
            try:
                obj=json.loads(line)
                if isinstance(obj,dict) and ('ok' in obj): payload=obj; break
            except Exception: pass
        self.reload_filters(); self.refresh()
        if payload and payload.get('ok'):
            results=payload.get('results',[]); added=sum(x.get('added',0) for x in results); updated=sum(x.get('updated',0) for x in results); errors=[x for x in results if x.get('error')]
            crosschecked=sum(x.get("crosschecked",0) for x in results)
            msg=f"Đã cập nhật online. Thêm {added}, cập nhật {updated}."
            if crosschecked: msg += f"\nĐã đối chiếu {crosschecked} bản TVPL với nguồn chính thức có cùng số hiệu trong kho."
            if errors: msg += "\n\n" + "\n".join(f"{x.get('source','Nguồn')}: {x.get('error')}" for x in errors)
            if self._active_mode in {"search_web", "search_sites"}:
                docs=payload.get("documents",[]) or []
                if docs:
                    OnlineSearchResultsDialog(payload.get("query") or self._active_query, docs, self).exec()
                else:
                    cfg=load_google_search_config()
                    note="Không tìm thấy kết quả phù hợp."
                    if not cfg.get("configured"):
                        note += "\n\nGoogle API chưa được cấu hình. Vào sheet ⚙ Cài đặt để nhập API/CX hoặc dùng 'Mở Google toàn web'."
                    QMessageBox.information(self,"Tìm kiếm online",note)
            else:
                QMessageBox.information(self,"Cập nhật văn bản",msg)
        else:
            err=(payload or {}).get('error') if payload else ''
            detail=err or self.proc_stderr.strip() or self.proc_stdout.strip() or f"Process kết thúc mã {exit_code}"
            QMessageBox.warning(self,"Cập nhật chưa hoàn tất",detail[:3000])
        self.proc=None

    def refresh_google_status(self):
        cfg=load_google_search_config()
        if cfg.get("configured"):
            self.google_status.setText("Google Search API: ĐÃ CẤU HÌNH • Quản lý API/CX tại sheet ⚙ Cài đặt.")
        else:
            self.google_status.setText("Google Search API: CHƯA CẤU HÌNH • Vào sheet ⚙ Cài đặt để nhập API/CX; app vẫn dùng engine fallback rộng.")

    def refresh_sites_hint(self):
        domains = get_specified_search_domains()
        if hasattr(self, "sites_hint"):
            self.sites_hint.setText("Trang chỉ định (sửa tại ⚙ Cài đặt): " + " • ".join(domains))

    def reload_settings_status(self):
        self.refresh_google_status()
        self.refresh_sites_hint()

    def open_google_browser(self):
        q=self.standard_search.text().strip()
        if not q:
            QMessageBox.information(self,"Google Search","Nhập nội dung cần tìm trước.")
            return
        QDesktopServices.openUrl(QUrl(google_browser_url(q)))

    def lookup_online(self):
        q=self.standard_search.text().strip()
        if not q:
            QMessageBox.information(self,"Tìm kiếm online","Nhập số hiệu hoặc nội dung cần tìm, ví dụ: Thông tư 06/2021/TT-BXD phân cấp công trình xây dựng.")
            return
        self.start_sync('search_web',q)

    def lookup_specified_sites(self):
        q=self.standard_search.text().strip()
        if not q:
            QMessageBox.information(self,"Tìm trang chỉ định","Nhập số hiệu hoặc nội dung cần tìm trước.")
            return
        self.start_sync('search_sites',q)

    def open_selected(self):
        r=self.table.currentRow()
        if r<0:return
        it=self.table.item(r,10); url=it.text().strip() if it else ''
        if url: QDesktopServices.openUrl(QUrl(url))

    def export_excel(self):
        if Workbook is None: QMessageBox.warning(self,"Thiếu openpyxl","Cài openpyxl để xuất Excel."); return
        path,_=QFileDialog.getSaveFileName(self,"Xuất danh mục văn bản","Van_ban_QLDA_XD.xlsx","Excel (*.xlsx)")
        if not path:return
        wb=Workbook(); ws=wb.active; ws.title="VanBanQLDAXD"; ws.append([self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount())])
        for x in self.rows: ws.append([x["category"],x["number"],x["title"],x["issuer"],x["issue_date"],x["effective_date"],x["expiry_date"],x["status"],x["field"],x["source_name"],x["source_url"],x["online_updated_at"]])
        ws.freeze_panes="A2"; wb.save(path); QMessageBox.information(self,"Hoàn tất",f"Đã xuất:\n{path}")
