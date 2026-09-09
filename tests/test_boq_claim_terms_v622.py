from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

import ai_claim_context_v622 as claim_ai
import ai_service
import boq_multisheet_v622 as boq
import ipc_adaptive_parser_v622 as adaptive
import ipc_claim_v622 as ipc
from ai_claim_context_v622 import install_ai_claim_context
from ai_live_context_v622 import install_ai_live_context
from boq_claim_terms_v622 import PATCH_MARKER, install_boq_claim_terms
from boq_cost_components_v622 import install_boq_cost_components
from cloud_db import CloudDatabase
from ipc_adaptive_parser_v622 import install_ipc_adaptive_parser
from ipc_claim_fast_v622 import install_ipc_claim_fast_path
from ipc_claim_summary_fix_v622 import install_ipc_claim_summary_fix


class BOQClaimTermsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_ai_live_context()
        install_ai_claim_context()
        install_boq_cost_components()
        install_ipc_claim_fast_path()
        install_ipc_claim_summary_fix()
        install_ipc_adaptive_parser()
        install_boq_claim_terms()

    @staticmethod
    def _boq_bytes() -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "E.1 THÁP S2"
        ws.append(["STT", "Nội dung công việc", "ĐVT", "Khối lượng", "Xuất xứ", "Đơn giá", None, "Thành tiền"])
        ws.append([None, None, None, None, None, "Vật tư", "Nhân công", None])
        ws.append([1, "Tủ điện", "Set", 3, "Việt Nam", 52_573_400, 1_375_000, 161_845_200])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def test_boq_uses_khoi_luong_and_two_row_unit_prices(self):
        result = boq.parse_boq_workbook(self._boq_bytes(), "BOQ.xlsx")
        item = result["detail_items"][0]
        self.assertEqual(item["quantity"], 3.0)
        self.assertEqual(item["material_unit_price"], 52_573_400.0)
        self.assertEqual(item["labor_unit_price"], 1_375_000.0)
        self.assertEqual(item["material_cost"], 157_720_200.0)
        self.assertEqual(item["labor_cost"], 4_125_000.0)
        warnings = "\n".join(result.get("warnings") or [])
        self.assertNotIn("Số lượng ×", warnings)
        self.assertIn("Khối lượng ×", warnings)

    def test_claim_header_accepts_don_gia_nhan_cong(self):
        headers = ["noi dung cong viec", "don gia vat tu", "don gia nhan cong", "thanh tien"]
        self.assertEqual(adaptive._find_header_col(headers, ("don gia lap dat",)), 2)
        self.assertEqual(adaptive._find_header_col(headers, ("hang muc cong viec",)), 0)

    def test_ai_claim_exposes_khoi_luong_material_and_labor_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "claim_terms.db"
            db = CloudDatabase(db_path)
            pid = db.add_project("TERM01", "Claim terminology")
            with db.connect() as c:
                ipc._ensure_tables(c)
                c.execute(
                    """INSERT INTO payment_claims(
                           claim_id,project_id,claim_no,claim_code,filename,payment_status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    ("claim-01", pid, "01", "IPC-01", "IPC01.xlsx", "Đã duyệt", "2026-09-09", "2026-09-09"),
                )
                c.execute(
                    """INSERT INTO payment_claim_items(
                           claim_id,project_id,row_no,boq_item,contract_qty,unit,
                           material_unit_price,labor_unit_price,
                           material_current_qty,material_cumulative_qty,
                           installation_current_pct,installation_cumulative_pct,
                           material_current_value,installation_current_value,current_value,cumulative_value
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "claim-01", pid, 16, "Tủ điện TĐ.S4-QH2", 3, "Set",
                        52_573_400, 1_375_000,
                        2, 3, 1, 2,
                        105_146_800, 1_375_000, 106_521_800, 161_845_200,
                    ),
                )

            builder = ai_service.ProjectContextBuilder(db_path)
            context = claim_ai._claim_appendix(
                builder, pid, "đơn giá vật tư nhân công và khối lượng claim 1"
            )
            self.assertIn(PATCH_MARKER, PATCH_MARKER)
            self.assertIn("KHỐI LƯỢNG", context)
            self.assertIn("[CLAIM-ITEM-V2:IPC-01:16]", context)
            self.assertIn("Khối_lượng_HĐ=3", context)
            self.assertIn("Đơn_giá_vật_tư=52,573,400 VND", context)
            self.assertIn("Đơn_giá_nhân_công=1,375,000 VND", context)
            self.assertIn("Khối_lượng_lắp_đặt_kỳ=1", context)
            self.assertIn("Chi_phí_vật_tư_kỳ=105,146,800 VND", context)
            self.assertIn("Chi_phí_nhân_công_kỳ=1,375,000 VND", context)
            self.assertNotIn("LĐ kỳ=1%", context)


if __name__ == "__main__":
    unittest.main()
