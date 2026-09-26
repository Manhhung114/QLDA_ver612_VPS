from __future__ import annotations

import unittest
from unittest.mock import patch

from qlda.infrastructure.ai import live_domain_context as ctx


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, *, documents=False, all_domains=False):
        self.documents = documents
        self.all_domains = all_domains
        self.calls = []

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).lower()
        self.calls.append((text, params))
        if text.startswith("select count(*) as n from"):
            return _Result([{"n": 1}])

        if " from documents " in f" {text} ":
            return _Result([
                {
                    "id": 21, "project_id": 101, "doc_type": "BBHOP", "code": "BBH-002",
                    "subject": "Họp tiến độ MEP tuần 39", "discipline": "MEP", "contractor": "SIGMA",
                    "issuer": "Ban điều hành", "assignee": "SIGMA, TVGS", "issue_date": "2026-09-25",
                    "due_date": "", "closed_date": "", "status": "", "priority": "", "related_wbs": "",
                    "description": "Thống nhất sản lượng và các khu vực cần tăng nhân lực.",
                    "response": "SIGMA bổ sung nhân lực tháp S4 và báo cáo lại ngày 27/09.",
                    "note": "Biên bản mới nhất", "cost_impact": 0, "time_impact_days": 0,
                    "created_at": "2026-09-25 16:00:00", "updated_at": "2026-09-25 16:00:00",
                },
                {
                    "id": 20, "project_id": 101, "doc_type": "BBHOP", "code": "BBH-001",
                    "subject": "Họp tiến độ tuần 38", "issue_date": "2026-09-18",
                    "description": "Biên bản cũ", "response": "", "note": "",
                },
            ])
        if "from document_attachments" in text:
            return _Result([{
                "document_id": 21, "file_name": "BBH_tuan_39.pdf", "mime_type": "application/pdf",
                "storage_backend": "local_vps", "created_at": "2026-09-25 16:05:00",
            }])
        if " from drawings " in f" {text} ":
            return _Result([{
                "id": 31, "project_id": 101, "drawing_type": "SHOPDRAWING", "drawing_no": "SD-MEP-001",
                "title": "Shopdrawing cấp thoát nước S4", "discipline": "MEP", "revision": "R2",
                "issuer": "SIGMA", "receiver": "BĐH", "received_date": "2026-09-24", "issue_date": "2026-09-23",
                "due_date": "2026-09-27", "priority": "Cao", "description": "Bản vẽ thi công tầng 10-20",
                "status": "Chờ duyệt", "related_wbs": "S4-MEP", "reference_no": "", "note": "",
            }])
        if "from drawing_attachments" in text:
            return _Result([])
        if " from cost_budgets " in f" {text} ":
            return _Result([{
                "id": 41, "project_id": 101, "task_ref": "S4-MEP", "boq_item": "Ống PPR DN50", "quantity": 120,
                "unit": "m", "unit_price": 150000, "budget_total": 18000000, "contract_type": "Trọn gói",
                "contractor": "SIGMA", "note": "",
            }])
        if " from payment_tracking " in f" {text} ":
            return _Result([{
                "id": 51, "project_id": 101, "payment_code": "IPC-03", "task_ref": "S4-MEP", "installment": "3",
                "certified_cumulative": 900000000, "paid_amount": 700000000, "advance_amount": 100000000,
                "advance_recovery": 20000000, "planned_disbursement_pct": 0, "payment_status": "Đã duyệt",
                "payment_date": "2026-09-20", "note": "Thanh toán kỳ 3",
            }])
        if " from payment_claims " in f" {text} ":
            return _Result([{
                "claim_id": "ipc3", "project_id": 101, "claim_no": "3", "claim_code": "IPC-03",
                "filename": "IPC03.xlsx", "contractor": "SIGMA", "contract_no": "E3-MEP-01", "package_name": "MEP",
                "from_date": "2026-08-01", "to_date": "2026-08-31", "contract_value": 5000000000,
                "requested_amount": 800000000, "approved_amount": 750000000, "disbursed_amount": 700000000,
                "certified_cumulative": 900000000, "previous_approved": 150000000, "retention_cumulative": 0,
                "advance_amount": 100000000, "advance_recovery": 20000000, "current_deductions": 0,
                "payment_status": "Đã duyệt", "disbursement_date": "2026-09-20", "latest_revision": 1, "note": "",
            }])
        if " from cost_variations " in f" {text} ":
            return _Result([{
                "id": 61, "project_id": 101, "vo_code": "VO-05", "task_ref": "S4-MEP",
                "description": "Bổ sung tuyến ống", "proposed_amount": 100000000, "approved_amount": 80000000,
                "funding_source": "Dự phòng", "status": "Đã duyệt", "vo_date": "2026-09-19", "note": "",
            }])
        if " from variation_orders " in f" {text} ":
            return _Result([{
                "vo_id": "vo5", "project_id": 101, "vo_no": 5, "vo_code": "VO-05", "filename": "VO05.xlsx",
                "revision_label": "R1", "vo_date": "2026-09-19", "project_name": "E3", "package_name": "MEP",
                "subtotal_before_vat": 80000000, "vat_amount": 8000000, "total_after_vat": 88000000,
                "increase_amount": 100000000, "decrease_amount": -20000000, "proposed_amount": 100000000,
                "approved_amount": 80000000, "funding_source": "Dự phòng", "status": "Đã duyệt", "note": "",
            }])
        if " from material_master " in f" {text} ":
            return _Result([{
                "id": 71, "project_id": 101, "material_code": "PPR50", "material_name": "Ống PPR DN50",
                "spec_brand": "PN20", "legal_ref": "CO/CQ", "supply_type": "Nhà thầu", "task_ref": "S4-MEP", "note": "",
            }])
        if " from procurement_schedule " in f" {text} ":
            return _Result([{
                "id": 72, "project_id": 101, "material_code": "PPR50", "task_ref": "S4-MEP", "supplier": "NCC A",
                "sample_approval_date": "2026-09-01", "order_date": "2026-09-05", "planned_delivery_date": "2026-09-15",
                "actual_delivery_date": "2026-09-16", "status": "Đã giao", "note": "",
            }])
        if " from inventory_inspection " in f" {text} ":
            return _Result([{
                "id": 73, "project_id": 101, "slip_code": "NK-01", "transaction_date": "2026-09-16",
                "material_code": "PPR50", "quantity_in": 120, "quantity_out": 0, "task_ref": "S4-MEP",
                "inspection_code": "NTVL-01", "material_status": "Đạt", "note": "",
            }])
        if " from owner_material_plans " in f" {text} ":
            return _Result([{
                "id": 74, "workspace_project_id": 101, "material_code": "FCU01", "material_name": "FCU",
                "unit": "bộ", "boq_qty": 20, "vo_qty": 2, "planned_qty": 22, "location": "S4",
                "contractor_code": "NT-01", "contractor_name": "SIGMA", "note": "CĐT cấp",
            }])
        if " from owner_material_ledger " in f" {text} ":
            return _Result([{
                "id": 75, "txn_code": "VT-01", "workspace_project_id": 101, "txn_type": "CĐT_BÀN_GIAO",
                "txn_date": "2026-09-17", "material_code": "FCU01", "material_name": "FCU", "unit": "bộ",
                "quantity": 10, "warehouse_from": "", "warehouse_to": "KHO-S4", "contractor_code": "NT-01",
                "contractor_name": "SIGMA", "location": "S4", "document_no": "BBBG-01", "task_ref": "S4-MEP", "note": "",
            }])
        if " from project_work_tasks " in f" {text} ":
            return _Result([{
                "id": 81, "task_code": "CV-001", "workspace_project_id": 101, "title": "Bổ sung nhân lực S4",
                "description": "Tăng 5 công nhân", "assignee_email": "", "assignee_name": "Chỉ huy SIGMA",
                "priority": "Khẩn", "status": "ĐANG XỬ LÝ", "progress_percent": 40, "source_module": "BBHOP",
                "source_type": "document", "source_id": "21", "source_code": "BBH-002", "source_title": "Họp tuần 39",
                "start_at": "2026-09-25", "due_at": "2026-09-27", "completion_requested_at": "", "completed_at": "",
                "closed_at": "", "created_at": "2026-09-25", "updated_at": "2026-09-26",
            }])
        if " from project_contract_records " in f" {text} ":
            return _Result([{
                "id": 91, "workspace_project_id": 101, "record_type": "Hợp đồng", "record_no": "E3-MEP-01",
                "title": "Hợp đồng MEP SIGMA", "signed_date": "2025-01-01", "amount": 5000000000, "currency": "VND",
                "effective_date": "2025-01-01", "expiry_date": "2027-01-01", "note": "", "current_file_id": "f1",
                "current_file_name": "Hop_dong_SIGMA.pdf", "current_mime_type": "application/pdf", "current_file_size": 1000,
                "created_by_name": "Admin", "created_at": "2025-01-01", "updated_by_name": "Admin", "updated_at": "2026-09-01",
            }])
        if " from approval_workflows " in f" {text} ":
            return _Result([{
                "id": 101, "project_id": 101, "record_kind": "document", "subtype": "RFI", "record_id": 5,
                "record_code": "RFI-005", "overall_status": "Đang duyệt - Ban điều hành", "current_stage": "SITE_MANAGEMENT",
                "submitted_by": "contractor@example.com", "submitted_at": "2026-09-25", "final_approved_at": "",
                "updated_at": "2026-09-25", "revision_no": 0, "return_stage": "",
            }])
        if " from approval_steps " in f" {text} ":
            return _Result([{
                "workflow_id": 101, "stage_code": "SITE_MANAGEMENT", "stage_order": 1, "stage_label": "Ban điều hành",
                "approver_name": "BĐH", "status": "Đang chờ duyệt", "comment": "", "acted_by": "", "acted_at": "",
            }])
        if " from project_cost_settings " in f" {text} ":
            return _Result([{
                "workspace_project_id": 101, "currency": "VND", "baseline_work_cost": 5000000000,
                "contingency_reserve": 100000000, "management_reserve": 50000000, "estimate_tolerance_pct": 5,
                "control_threshold_pct": 10, "baseline_date": "2026-01-01", "note": "", "updated_at": "2026-09-20",
            }])
        if " from project_cost_control_snapshots " in f" {text} ":
            return _Result([{
                "workspace_project_id": 101, "status_date": "2026-09-25", "actual_cost": 1800000000,
                "note": "Kỳ tháng 9", "updated_at": "2026-09-25",
            }])
        return _Result([])


class LiveDomainContextTests(unittest.TestCase):
    def test_latest_meeting_minutes_exposes_content_conclusion_and_file(self):
        connection = _Connection(documents=True)
        existing = {"documents", "document_attachments"}
        with patch.object(ctx, "_table_exists", side_effect=lambda _c, table: table in existing):
            text = ctx.build_live_domain_context(
                connection,
                [101],
                "Tổng hợp biên bản họp gần đây nhất",
            )

        self.assertIn("[DOC:21]", text)
        self.assertIn("Họp tiến độ MEP tuần 39", text)
        self.assertIn("Thống nhất sản lượng", text)
        self.assertIn("SIGMA bổ sung nhân lực tháp S4", text)
        self.assertIn("BBH_tuan_39.pdf", text)
        self.assertLess(text.index("[DOC:21]"), text.index("[DOC:20]"))
        scoped_calls = [params for sql, params in connection.calls if "project_id=any" in sql]
        self.assertTrue(scoped_calls)
        self.assertTrue(all(list(params[0]) == [101] for params in scoped_calls if params))

    def test_all_operational_domains_expose_rows_not_only_counts(self):
        connection = _Connection(all_domains=True)
        existing = {
            "drawings", "drawing_attachments", "cost_budgets", "payment_tracking", "payment_claims",
            "cost_variations", "variation_orders", "material_master", "procurement_schedule", "inventory_inspection",
            "project_work_tasks", "project_contract_records", "approval_workflows", "approval_steps",
            "owner_material_plans", "owner_material_ledger", "project_cost_settings", "project_cost_control_snapshots",
        }
        question = (
            "Tổng hợp bản vẽ BOQ chi phí thanh toán IPC VO vật tư mua sắm nhập kho kiểm định, "
            "vật tư chủ đầu tư cấp, nhiệm vụ, hợp đồng phụ lục và phê duyệt"
        )
        with patch.object(ctx, "_table_exists", side_effect=lambda _c, table: table in existing):
            text = ctx.build_live_domain_context(connection, [101], question, max_chars=30000)

        for marker in (
            "[DRAWING:31]", "[BOQ:41]", "[PAYMENT:51]", "[IPC:ipc3]", "[VO:61]", "[VO-EXCEL:vo5]",
            "[MATERIAL:71]", "[PROCUREMENT:72]", "[INVENTORY:73]", "[OWNER-MAT-PLAN:74]",
            "[OWNER-MAT-TXN:75]", "[WORK-TASK:81]", "[CONTRACT:91]", "[APPROVAL:101]",
        ):
            self.assertIn(marker, text)
        self.assertIn("[COST-SETTING]", text)
        self.assertIn("[COST-SNAPSHOT]", text)
        self.assertIn("workspace_project_id=any", "\n".join(sql for sql, _ in connection.calls))


if __name__ == "__main__":
    unittest.main()
