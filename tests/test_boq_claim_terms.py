from __future__ import annotations

import io
import unittest

from openpyxl import Workbook

import qlda.runtime_core.boq_multisheet as boq
import qlda.runtime_core.ipc_adaptive_parser as adaptive
from qlda.runtime_core.boq_claim_terms import PATCH_MARKER, install_boq_claim_terms
from qlda.runtime_core.boq_cost_components import install_boq_cost_components
from qlda.runtime_core.ipc_adaptive_parser import install_ipc_adaptive_parser
from qlda.runtime_core.ipc_claim_fast import install_ipc_claim_fast_path
from qlda.runtime_core.ipc_claim_summary_fix import install_ipc_claim_summary_fix


class BOQClaimTermsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        self.assertIn("CLAIM TERMS", PATCH_MARKER)

    def test_claim_header_accepts_don_gia_nhan_cong(self):
        headers = ["noi dung cong viec", "don gia vat tu", "don gia nhan cong", "thanh tien"]
        self.assertEqual(adaptive._find_header_col(headers, ("don gia lap dat",)), 2)
        self.assertEqual(adaptive._find_header_col(headers, ("hang muc cong viec",)), 0)


if __name__ == "__main__":
    unittest.main()
