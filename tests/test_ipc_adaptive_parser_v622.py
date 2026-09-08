from __future__ import annotations

import io
import unittest

from openpyxl import Workbook

import ipc_claim_v622 as ipc
from ipc_claim_fast_v622 import install_ipc_claim_fast_path
from ipc_claim_summary_fix_v622 import install_ipc_claim_summary_fix
from ipc_adaptive_parser_v622 import install_ipc_adaptive_parser
from ipc_claim_number_fix_v622 import install_ipc_claim_number_fix


install_ipc_claim_fast_path()
install_ipc_claim_summary_fix()
install_ipc_adaptive_parser()
install_ipc_claim_number_fix()


def ipc10_form_bytes() -> bytes:
    wb = Workbook()

    khai = wb.active
    khai.title = "KHAI BÁO"
    khai["A4"] = "DỰ ÁN:"
    khai["B4"] = "DỰ ÁN IPC10"
    khai["A5"] = "ĐỊA ĐIỂM:"
    khai["B5"] = "TP.HCM"
    khai["A6"] = "GÓI THẦU:"
    khai["B6"] = "MEP"
    khai["A7"] = "NHÀ THẦU THI CÔNG TRỰC TIẾP:"
    khai["B7"] = "CÔNG TY CỔ PHẦN KỸ THUẬT SIGMA"
    khai["A12"] = "THANH TOÁN LẦN:"
    khai["B12"] = 10
    khai["A13"] = "TỪ NGÀY"
    khai["B13"] = 0
    khai["D13"] = "ĐẾN NGÀY"
    khai["E13"] = 0
    khai["A15"] = "HỢP ĐỒNG SỐ:"
    khai["B15"] = "293/2024/HĐ/SCG-SIGMA và CÁC PHỤ LỤC HỢP ĐỒNG"

    # Legacy-looking payment sheet is deliberately broken. Adaptive role scoring
    # must choose the valid semantic summary below instead of this sheet.
    broken_payment = wb.create_sheet("Thanh toán")
    broken_payment["A11"] = "Giá trị hợp đồng + PLHĐ [1]"
    broken_payment["D11"] = "#REF!"
    broken_payment["A17"] = "Lũy kế giá trị nghiệm thu đến nay"
    broken_payment["D17"] = "#REF!"
    broken_payment["G33"] = "Giá trị thanh toán kỳ này"
    broken_payment["K33"] = "#REF!"

    payment = wb.create_sheet("03- ĐNTT")
    payment["A3"] = "BẢNG TỔNG HỢP ĐỀ NGHỊ THANH TOÁN"
    payment["A9"] = "Giá trị Hợp đồng + PLHĐ [1] = [1.1]+[1.2]"
    payment["E9"] = 511_007_181_260
    payment["A12"] = "Giá trị tạm ứng HĐ + PLHĐ [2]"
    payment["E12"] = 94_444_444_444.44444
    payment["A18"] = "Giá trị nghiệm thu kỳ này [5]"
    payment["E18"] = 29_373_263_888
    payment["A21"] = "Lũy kế giá trị nghiệm thu đến hết kỳ này [6]"
    payment["E21"] = 411_915_024_735
    payment["B22"] = "- Giá trị nghiệm thu vật tư [6a]=[4a]+[5a]"
    payment["E22"] = 338_803_303_217
    payment["B23"] = "- Giá trị nghiệm thu lắp đặt [6b]=[4b]+[5b]"
    payment["E23"] = 73_111_721_518
    payment["A24"] = "Lũy kế giá trị khấu trừ, giữ lại đến hết kỳ này [7]"
    payment["E24"] = 155_618_318_624.12445
    payment["H24"] = "Giá trị trừ kỳ này [7.1]"
    payment["K24"] = 326_732_638.63
    payment["B25"] = "- Lũy kế giá trị khấu trừ tạm ứng HĐ, PLHĐ [7a]"
    payment["E25"] = 94_444_444_444.44444
    payment["B26"] = "- Lũy kế giá trị HĐ, PLHĐ bảo lưu đến hết kỳ này [7b]"
    payment["E26"] = 56_900_123_933.05
    payment["A32"] = "Lũy kế giá trị ĐNTT đến hết kỳ trước [8]"
    payment["E32"] = 230_630_816_135
    payment["A42"] = "Giá trị Đề nghị thanh toán kỳ này [9]= [6] - [7]-[8]"
    payment["E42"] = 25_665_889_975.87555
    payment["B44"] = "Giá trị ĐNTT bằng chữ:"
    payment["D44"] = "Hai mươi lăm tỷ sáu trăm sáu mươi lăm triệu đồng"

    # Broken duplicate GTHT appears first and has the old/invalid external-link cache.
    bad = wb.create_sheet("04-THGTHT (ĐGR) (2)")
    bad["A3"] = "BẢNG TỔNG HỢP GIÁ TRỊ KHỐI LƯỢNG HOÀN THÀNH"
    bad["B5"] = "CÔNG TRÌNH: …................................"
    bad["A8"] = "Stt"
    bad["B8"] = "Hạng mục công việc"
    bad["D8"] = "ĐVT"
    bad["E8"] = "Đơn giá vật tư (Chưa VAT)"
    bad["G8"] = "Hợp đồng"
    for row in range(11, 30):
        bad.cell(row, 5).value = "#REF!"
        bad.cell(row, 8).value = "#REF!"

    good = wb.create_sheet("04-THGTHT (ĐGR)")
    good["A3"] = "BẢNG TỔNG HỢP GIÁ TRỊ KHỐI LƯỢNG HOÀN THÀNH"
    good["A8"] = "Stt"
    good["B8"] = "Hạng mục công việc"
    good["C8"] = "Quy cách công việc/sản phẩm"
    good["D8"] = "ĐVT"
    good["E8"] = "Đơn giá vật tư (Chưa VAT)"
    good["F8"] = "Đơn giá lắp đặt (Chưa VAT)"
    good["G8"] = "Hợp đồng"
    good["I8"] = "Khối lượng nghiệm thu vật tư"
    good["L8"] = "Khối lượng nghiệm thu lắp đặt"
    good["O8"] = "Thành tiền nghiệm thu vật tư"
    good["R8"] = "Thành tiền nghiệm thu lắp đặt"
    good["X8"] = "Ghi chú"
    good["G9"] = "Khối lượng"
    good["H9"] = "Thành tiền"
    good["I9"] = "LK kỳ trước"
    good["J9"] = "Kỳ này"
    good["K9"] = "LK đến hết kỳ này"
    good["L9"] = "LK kỳ trước"
    good["M9"] = "Kỳ này"
    good["N9"] = "LK đến hết kỳ này"
    good["O9"] = "LK kỳ trước"
    good["P9"] = "Kỳ này"
    good["Q9"] = "LK đến hết kỳ này"
    good["R9"] = "LK kỳ trước"
    good["S9"] = "Kỳ này"
    good["T9"] = "LK đến hết kỳ này"
    good["Y10"] = "Code"
    good["Z10"] = "Hệ thống"
    good["AA10"] = "Sheet theo BOQ"

    good["A16"] = 1
    good["B16"] = "Tủ điện TĐ.S4-QH2"
    good["C16"] = "Form mới IPC10"
    good["D16"] = "Set"
    good["E16"] = 52_573_400
    good["F16"] = 1_375_000
    good["G16"] = 1
    good["H16"] = 53_948_400
    good["I16"] = 1
    good["J16"] = 0
    good["K16"] = 1
    good["L16"] = 0
    good["M16"] = 0
    good["N16"] = 0
    good["O16"] = 52_573_400
    good["P16"] = 0
    good["Q16"] = 52_573_400
    good["R16"] = 0
    good["S16"] = 0
    good["T16"] = 0
    good["X16"] = ""
    good["Y16"] = "1088"
    good["Z16"] = "E"
    good["AA16"] = "S2"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class AdaptiveIPCParserTests(unittest.TestCase):
    def test_ipc10_changed_form_is_recognized_by_content(self):
        result = ipc.parse_ipc_workbook(ipc10_form_bytes(), "(SME213) IPC#10 (20260422) - Form mới.xlsx")
        self.assertEqual(result["claim_no"], "10")
        self.assertEqual(result["claim_code"], "IPC-10")
        self.assertAlmostEqual(result["summary"]["contract_value"], 511_007_181_260, places=2)
        self.assertAlmostEqual(result["summary"]["requested_amount"], 25_665_889_975.87555, places=2)
        self.assertAlmostEqual(result["summary"]["cumulative_completed"], 411_915_024_735, places=2)
        self.assertEqual(result["parser_profile"]["sheet_roles"]["payment"], "03- ĐNTT")
        self.assertEqual(result["parser_profile"]["sheet_roles"]["gtht"], "04-THGTHT (ĐGR)")
        self.assertTrue(result["parser_profile"]["validation"]["claim_formula"]["ok"])
        self.assertGreaterEqual(result["parser_profile"]["confidence"], 0.90)
        self.assertEqual(result["detail_line_count"], 1)
        item = result["detail_items"][0]
        self.assertEqual(item["boq_item"], "Tủ điện TĐ.S4-QH2")
        self.assertEqual(item["unit"], "Set")
        self.assertEqual(item["cost_code"], "1088")
        self.assertEqual(item["system"], "E")
        self.assertAlmostEqual(item["material_cumulative_qty"], 1.0, places=6)
        self.assertAlmostEqual(item["current_value"], 0.0, places=2)
        self.assertEqual(item["installation_measure"], "quantity")


if __name__ == "__main__":
    unittest.main()
