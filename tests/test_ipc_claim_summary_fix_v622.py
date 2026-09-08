from __future__ import annotations

import io
import unittest

from openpyxl import Workbook

import ipc_claim_v622 as ipc
from ipc_claim_summary_fix_v622 import install_ipc_claim_summary_fix


install_ipc_claim_summary_fix()


def _base_workbook(claim_no="3"):
    wb = Workbook()
    khai = wb.active
    khai.title = "KHAI BÁO"
    khai["B4"] = "DỰ ÁN TEST"
    khai["B6"] = "GÓI MEP"
    khai["B7"] = "CÔNG TY CỔ PHẦN KỸ THUẬT SIGMA"
    khai["B12"] = claim_no
    khai["B14"] = "VNĐ"
    khai["B15"] = "293/2024/HĐ/SCG-SIGMA và các Phụ lục hợp đồng"
    return wb


def _bytes(wb):
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class IPCClaimPaymentSummaryFixTests(unittest.TestCase):
    def test_sigma_ipc3_reads_final_payment_not_utility_deduction(self):
        wb = _base_workbook("3")
        ws = wb.create_sheet("Thanh toán")
        ws["A10"] = "Giá trị hợp đồng + PLHĐ [1]=[1.1]+[1.2]+….."
        ws["D10"] = 510_000_000_000
        ws["A12"] = "Giá trị tạm ứng HĐ + PLHĐ [2] = [2.1]+[2.2]+…"
        ws["D12"] = 94_444_444_444.4
        ws["A15"] = "Lũy kế giá trị nghiệm thu đến nay [4]=[4a+4b-4c]"
        ws["D15"] = 75_053_143_710
        ws["A16"] = "- Lũy kế giá trị nghiệm thu vật tư [4a]"
        ws["D16"] = 68_278_931_190
        ws["A17"] = "- Lũy kế giá trị nghiệm thu lắp đặt [4b]"
        ws["F17"] = 18_749_466_576
        ws["A18"] = "- Lũy kế khấu trừ giá trị vật tư lắp đặt [4c]"
        ws["F18"] = 11_975_254_056
        ws["A20"] = "Lũy kế giá trị đã duyệt các kỳ trước [6]"
        ws["D20"] = 16_657_174_457.68
        ws["G25"] = "[17]=[17a+17b+17c+17d]"
        ws["K25"] = 426_906_333.34
        ws["A26"] = "Lũy kế bảo lưu đến nay [12]"
        ws["D26"] = 15_950_865_942
        ws["A29"] = "Lũy kế thu hồi tạm ứng đến nay [13]"
        ws["D29"] = 17_659_563_225.882355
        ws["A30"] = "Lũy kế thu hồi tạm ứng vật tư [14]"
        ws["D30"] = 0
        ws["G30"] = "- Trừ khác kỳ này: Chi phí tiện ích 1% [17d]"
        ws["K30"] = 426_906_333.34
        ws["G31"] = "Giá trị thanh toán kỳ này [18]=[16 - 17]"
        ws["K31"] = 24_358_633_750.66
        ws["A32"] = "Giá trị thanh toán kỳ này (chưa trừ) [16] = [4-15]"
        ws["D32"] = 24_785_540_084
        ws["A33"] = "Giá trị thanh toán kỳ này, Bằng chữ:"
        ws["C33"] = "Hai mươi bốn tỷ ba trăm năm mươi tám triệu sáu trăm ba mươi ba nghìn bảy trăm năm mươi mốt đồng./."

        result = ipc.parse_ipc_workbook(_bytes(wb), "(SME213) IPC#3 IN Final.xlsx")
        s = result["summary"]
        self.assertEqual(result["claim_code"], "IPC-03")
        self.assertAlmostEqual(s["contract_value"], 510_000_000_000, places=2)
        self.assertAlmostEqual(s["cumulative_acceptance"], 75_053_143_710, places=2)
        self.assertAlmostEqual(s["current_gross"], 24_785_540_084, places=2)
        self.assertAlmostEqual(s["current_deductions"], 426_906_333.34, places=2)
        self.assertAlmostEqual(s["requested_amount"], 24_358_633_750.66, places=2)
        self.assertNotAlmostEqual(s["requested_amount"], 426_906_333.34, places=2)

    def test_legacy_template_falls_back_to_old_cells(self):
        wb = _base_workbook("01")
        ws = wb.create_sheet("Thanh toán")
        ws["D11"] = 510_000_000_000
        ws["D14"] = 2_430_038_232
        ws["D19"] = 0
        ws["K24"] = 24_300_382.32
        ws["D25"] = 607_509_558
        ws["D28"] = 571_773_701.18
        ws["D30"] = 1_179_283_259.18
        ws["D31"] = 1_250_754_973
        ws["K30"] = 1_226_454_590.68
        ws["C32"] = "Một tỷ hai trăm triệu đồng"

        result = ipc.parse_ipc_workbook(_bytes(wb), "IPC#1.xlsx")
        s = result["summary"]
        self.assertAlmostEqual(s["contract_value"], 510_000_000_000, places=2)
        self.assertAlmostEqual(s["requested_amount"], 1_226_454_590.68, places=2)


if __name__ == "__main__":
    unittest.main()
