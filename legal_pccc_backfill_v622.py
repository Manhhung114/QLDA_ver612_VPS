from __future__ import annotations

import re
import threading
from typing import Any, Callable
from urllib.parse import quote_plus


PATCH_MARKER = "V6.22 LEGAL PCCC OFFICIAL BACKFILL V1"
_SEED_LOCK = threading.RLock()
_SEEDED_REPOS: set[str] = set()

# Các truy vấn PCCC/CNCH được ghép vào luồng QLXD hiện hữu. Lớp này không thay
# thế nguồn chính thức: nó tăng độ bao phủ tìm kiếm; các văn bản nền tảng bên
# dưới được seed bằng URL Công báo/VBPL/VSQI chính thức để không bị mất do xếp
# hạng tìm kiếm hoặc anti-bot của nguồn tra cứu.
PCCC_SYNC_QUERIES = (
    "Luật phòng cháy chữa cháy cứu nạn cứu hộ",
    "Nghị định phòng cháy chữa cháy cứu nạn cứu hộ",
    "xử phạt vi phạm hành chính phòng cháy chữa cháy cứu nạn cứu hộ",
    "Thông tư Bộ Công an phòng cháy chữa cháy cứu nạn cứu hộ",
    "Thông tư Bộ Xây dựng phòng cháy chữa cháy công trình",
    "thẩm định thiết kế phòng cháy chữa cháy công trình",
    "nghiệm thu phòng cháy chữa cháy công trình",
    "kiểm tra an toàn phòng cháy chữa cháy công trình",
    "phương tiện phòng cháy chữa cháy cứu nạn cứu hộ",
    "bảo quản bảo dưỡng phương tiện phòng cháy chữa cháy",
    "QCVN 06 an toàn cháy nhà công trình",
    "QCVN 03 phương tiện phòng cháy chữa cháy",
    "QCVN 10 trang bị bố trí phương tiện phòng cháy chữa cháy nhà công trình",
    "TCVN phòng cháy chữa cháy nhà công trình",
    "TCVN hệ thống báo cháy tự động",
    "TCVN hệ thống chữa cháy tự động bằng nước bọt",
    "TCVN cấp nước chữa cháy trong ngoài nhà",
    "TCVN hút khói tăng áp cầu thang phòng cháy chữa cháy",
)

# Metadata tối thiểu đã được đối chiếu với nguồn chính thức. Không lưu toàn văn
# tiêu chuẩn có bản quyền; chỉ lưu số hiệu, trích yếu, trạng thái và link mở nguồn.
OFFICIAL_CORE_DOCS: tuple[dict[str, Any], ...] = (
    # --- Phân cấp công trình: regression case người dùng yêu cầu ---
    {
        "category": "Thông tư",
        "number": "06/2021/TT-BXD",
        "title": "Thông tư số 06/2021/TT-BXD quy định về phân cấp công trình xây dựng và hướng dẫn áp dụng trong quản lý hoạt động đầu tư xây dựng",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2021-06-30",
        "effective_date": "2021-08-15",
        "expiry_date": "",
        "status": "Còn hiệu lực (đã được sửa đổi, bổ sung)",
        "field": "QLDA xây dựng / Phân cấp công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-06-2021-tt-bxd-33988.htm",
        "is_draft": 0,
        "note": "Văn bản gốc; đã được sửa đổi, bổ sung trong năm 2025. Xem 06/VBHN-BXD để tra cứu bản hợp nhất.",
    },
    {
        "category": "Thông tư",
        "number": "02/2025/TT-BXD",
        "title": "Thông tư số 02/2025/TT-BXD sửa đổi, bổ sung một số điều của Thông tư số 06/2021/TT-BXD",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2025-03-31",
        "effective_date": "2025-05-20",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "QLDA xây dựng / Phân cấp công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-02-2025-tt-bxd-44746.htm",
        "is_draft": 0,
        "note": "Sửa đổi, bổ sung Thông tư 06/2021/TT-BXD.",
    },
    {
        "category": "Thông tư",
        "number": "09/2025/TT-BXD",
        "title": "Thông tư số 09/2025/TT-BXD sửa đổi, bổ sung một số Thông tư thuộc lĩnh vực quản lý nhà nước của Bộ Xây dựng liên quan phân cấp cho chính quyền địa phương",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2025-06-13",
        "effective_date": "2025-07-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "QLDA xây dựng / Phân cấp công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-09-2025-tt-bxd-45399.htm",
        "is_draft": 0,
        "note": "Có nội dung sửa đổi các quy định thuộc phạm vi quản lý của Bộ Xây dựng; tra cứu cùng 06/VBHN-BXD.",
    },
    {
        "category": "Văn bản hợp nhất",
        "number": "06/VBHN-BXD",
        "title": "Văn bản hợp nhất số 06/VBHN-BXD hợp nhất Thông tư quy định về phân cấp công trình xây dựng và hướng dẫn áp dụng trong quản lý hoạt động đầu tư xây dựng",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2025-07-03",
        "effective_date": "",
        "expiry_date": "",
        "status": "Văn bản hợp nhất hiện hành",
        "field": "QLDA xây dựng / Phân cấp công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/van-ban-hop-nhat-so-06-vbhn-bxd-45496.htm",
        "is_draft": 0,
        "note": "Bản hợp nhất phục vụ tra cứu quy định phân cấp công trình sau các sửa đổi năm 2025.",
    },

    # --- Khung pháp lý PCCC/CNCH hiện hành ---
    {
        "category": "Luật",
        "number": "55/2024/QH15",
        "title": "Luật Phòng cháy, chữa cháy và cứu nạn, cứu hộ",
        "issuer": "Quốc hội",
        "issue_date": "2024-11-29",
        "effective_date": "2025-07-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/luat-so-55-2024-qh15-43594.htm",
        "is_draft": 0,
        "note": "Khung pháp lý PCCC/CNCH hiện hành từ 01/07/2025.",
    },
    {
        "category": "Nghị định",
        "number": "105/2025/NĐ-CP",
        "title": "Nghị định số 105/2025/NĐ-CP quy định chi tiết một số điều và biện pháp thi hành Luật Phòng cháy, chữa cháy và cứu nạn, cứu hộ",
        "issuer": "Chính phủ",
        "issue_date": "2025-05-15",
        "effective_date": "2025-07-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH / Thẩm định - kiểm tra",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-105-2025-nd-cp-44912.htm",
        "is_draft": 0,
        "note": "Nghị định hướng dẫn Luật 55/2024/QH15.",
    },
    {
        "category": "Nghị định",
        "number": "106/2025/NĐ-CP",
        "title": "Nghị định số 106/2025/NĐ-CP quy định xử phạt vi phạm hành chính trong lĩnh vực phòng cháy, chữa cháy và cứu nạn, cứu hộ",
        "issuer": "Chính phủ",
        "issue_date": "2025-05-15",
        "effective_date": "2025-07-01",
        "expiry_date": "",
        "status": "Còn hiệu lực (đã được sửa đổi, bổ sung)",
        "field": "PCCC / CNCH / Xử phạt",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-106-2025-nd-cp-44893.htm",
        "is_draft": 0,
        "note": "Đã được sửa đổi, bổ sung bởi Nghị định 69/2026/NĐ-CP.",
    },
    {
        "category": "Nghị định",
        "number": "69/2026/NĐ-CP",
        "title": "Nghị định số 69/2026/NĐ-CP sửa đổi, bổ sung Nghị định 106/2025/NĐ-CP về xử phạt vi phạm hành chính trong lĩnh vực PCCC và CNCH",
        "issuer": "Chính phủ",
        "issue_date": "2026-03-06",
        "effective_date": "2026-04-20",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH / Xử phạt",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-69-2026-nd-cp-469073.htm",
        "is_draft": 0,
        "note": "Sửa đổi Nghị định 106/2025/NĐ-CP.",
    },
    {
        "category": "Thông tư",
        "number": "36/2025/TT-BCA",
        "title": "Thông tư số 36/2025/TT-BCA quy định chi tiết một số điều của Luật Phòng cháy, chữa cháy và cứu nạn, cứu hộ và Nghị định 105/2025/NĐ-CP",
        "issuer": "Bộ Công an",
        "issue_date": "2025-05-15",
        "effective_date": "2025-07-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH / Thẩm định - kiểm tra - phương tiện",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-36-2025-tt-bca-44981.htm",
        "is_draft": 0,
        "note": "Quy định chi tiết Luật 55/2024/QH15 và Nghị định 105/2025/NĐ-CP.",
    },
    {
        "category": "Thông tư",
        "number": "63/2025/TT-BXD",
        "title": "Thông tư số 63/2025/TT-BXD hướng dẫn Điều 23 Nghị định 105/2025/NĐ-CP thuộc phạm vi quản lý của Bộ Xây dựng",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2025-12-30",
        "effective_date": "2026-02-16",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH / Công trình xây dựng",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-63-2025-tt-bxd-468658.htm",
        "is_draft": 0,
        "note": "Hướng dẫn nội dung PCCC/CNCH thuộc phạm vi quản lý Bộ Xây dựng.",
    },
    {
        "category": "Thông tư",
        "number": "103/2025/TT-BCA",
        "title": "Thông tư số 103/2025/TT-BCA ban hành QCVN 10:2025/BCA về trang bị, bố trí phương tiện PCCC, CNCH cho nhà và công trình",
        "issuer": "Bộ Công an",
        "issue_date": "2025-11-04",
        "effective_date": "2025-12-30",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH / Phương tiện nhà và công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-103-2025-tt-bca-46574.htm",
        "is_draft": 0,
        "note": "Văn bản ban hành QCVN 10:2025/BCA.",
    },
    {
        "category": "Thông tư",
        "number": "56/2023/TT-BCA",
        "title": "Thông tư số 56/2023/TT-BCA ban hành QCVN 03:2023/BCA về phương tiện phòng cháy và chữa cháy",
        "issuer": "Bộ Công an",
        "issue_date": "2023-10-30",
        "effective_date": "2024-04-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / Phương tiện",
        "source_name": "CSDL Quốc gia về VBPL",
        "source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=170069",
        "is_draft": 0,
        "note": "Ban hành QCVN 03:2023/BCA; thay thế quy chuẩn ban hành kèm Thông tư 123/2021/TT-BCA.",
    },
    {
        "category": "Thông tư",
        "number": "06/2022/TT-BXD",
        "title": "Thông tư số 06/2022/TT-BXD ban hành QCVN 06:2022/BXD Quy chuẩn kỹ thuật quốc gia về An toàn cháy cho nhà và công trình",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2022-11-30",
        "effective_date": "2023-01-16",
        "expiry_date": "",
        "status": "Còn hiệu lực (đã được sửa đổi, bổ sung)",
        "field": "PCCC / An toàn cháy nhà và công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-06-2022-tt-bxd-38322.htm",
        "is_draft": 0,
        "note": "Ban hành QCVN 06:2022/BXD; áp dụng cùng Sửa đổi 1:2023 ban hành bởi 09/2023/TT-BXD.",
    },
    {
        "category": "Thông tư",
        "number": "09/2023/TT-BXD",
        "title": "Thông tư số 09/2023/TT-BXD ban hành Sửa đổi 1:2023 QCVN 06:2022/BXD về An toàn cháy cho nhà và công trình",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2023-10-16",
        "effective_date": "2023-12-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / An toàn cháy nhà và công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-09-2023-tt-bxd-40285.htm",
        "is_draft": 0,
        "note": "Sửa đổi 1:2023 của QCVN 06:2022/BXD.",
    },

    # --- Quy chuẩn kỹ thuật PCCC quan trọng ---
    {
        "category": "QCVN",
        "number": "QCVN 06:2022/BXD",
        "title": "Quy chuẩn kỹ thuật quốc gia về An toàn cháy cho nhà và công trình",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2022-11-30",
        "effective_date": "2023-01-16",
        "expiry_date": "",
        "status": "Còn hiệu lực (áp dụng Sửa đổi 1:2023)",
        "field": "PCCC / An toàn cháy nhà và công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-06-2022-tt-bxd-38322.htm#qcvn-06-2022-bxd",
        "is_draft": 0,
        "note": "Ban hành kèm 06/2022/TT-BXD; tra cứu cùng 09/2023/TT-BXD.",
    },
    {
        "category": "QCVN",
        "number": "Sửa đổi 1:2023 QCVN 06:2022/BXD",
        "title": "Sửa đổi 1:2023 QCVN 06:2022/BXD Quy chuẩn kỹ thuật quốc gia về An toàn cháy cho nhà và công trình",
        "issuer": "Bộ Xây dựng",
        "issue_date": "2023-10-16",
        "effective_date": "2023-12-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / An toàn cháy nhà và công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-09-2023-tt-bxd-40285.htm#sua-doi-1-2023-qcvn-06",
        "is_draft": 0,
        "note": "Ban hành kèm 09/2023/TT-BXD.",
    },
    {
        "category": "QCVN",
        "number": "QCVN 03:2023/BCA",
        "title": "Quy chuẩn kỹ thuật quốc gia về Phương tiện phòng cháy và chữa cháy",
        "issuer": "Bộ Công an",
        "issue_date": "2023-10-30",
        "effective_date": "2024-04-01",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / Phương tiện",
        "source_name": "CSDL Quốc gia về VBPL",
        "source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=170069#qcvn-03-2023-bca",
        "is_draft": 0,
        "note": "Ban hành kèm 56/2023/TT-BCA.",
    },
    {
        "category": "QCVN",
        "number": "QCVN 10:2025/BCA",
        "title": "Quy chuẩn kỹ thuật quốc gia về trang bị, bố trí phương tiện phòng cháy, chữa cháy, cứu nạn, cứu hộ cho nhà và công trình",
        "issuer": "Bộ Công an",
        "issue_date": "2025-11-04",
        "effective_date": "2025-12-30",
        "expiry_date": "",
        "status": "Còn hiệu lực",
        "field": "PCCC / CNCH / Phương tiện nhà và công trình",
        "source_name": "Công báo điện tử Chính phủ",
        "source_url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-103-2025-tt-bca-46574.htm#qcvn-10-2025-bca",
        "is_draft": 0,
        "note": "Ban hành kèm 103/2025/TT-BCA.",
    },

    # --- TCVN PCCC nền tảng; chỉ metadata + URL VSQI ---
    {
        "category": "TCVN",
        "number": "TCVN 3890:2023",
        "title": "Phòng cháy chữa cháy – Phương tiện phòng cháy và chữa cháy cho nhà và công trình – Trang bị, bố trí",
        "issuer": "Bộ Khoa học và Công nghệ",
        "issue_date": "2023-01-01",
        "effective_date": "",
        "expiry_date": "",
        "status": "A - Còn hiệu lực",
        "field": "PCCC / ICS 13.220",
        "source_name": "VSQI - CSDL Tiêu chuẩn quốc gia",
        "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+3890%3A2023",
        "is_draft": 0,
        "note": "Chỉ lưu metadata/link VSQI; không sao chép nội dung tiêu chuẩn có bản quyền.",
    },
    {
        "category": "TCVN",
        "number": "TCVN 7336:2021",
        "title": "Phòng cháy và chữa cháy – Hệ thống chữa cháy tự động bằng nước, bọt – Yêu cầu thiết kế và lắp đặt",
        "issuer": "Bộ Khoa học và Công nghệ",
        "issue_date": "2021-01-01",
        "effective_date": "",
        "expiry_date": "",
        "status": "A - Còn hiệu lực",
        "field": "PCCC / ICS 13.220",
        "source_name": "VSQI - CSDL Tiêu chuẩn quốc gia",
        "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+7336%3A2021",
        "is_draft": 0,
        "note": "Chỉ lưu metadata/link VSQI; không sao chép nội dung tiêu chuẩn có bản quyền.",
    },
    {
        "category": "TCVN",
        "number": "TCVN 2622:1995",
        "title": "Phòng cháy, chống cháy cho nhà và công trình - Yêu cầu thiết kế",
        "issuer": "Bộ Khoa học và Công nghệ",
        "issue_date": "1995-01-01",
        "effective_date": "",
        "expiry_date": "",
        "status": "A - Còn hiệu lực",
        "field": "PCCC / ICS 13.220",
        "source_name": "VSQI - CSDL Tiêu chuẩn quốc gia",
        "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+2622%3A1995",
        "is_draft": 0,
        "note": "Theo trạng thái công bố trên VSQI; chỉ lưu metadata/link.",
    },
    {
        "category": "TCVN",
        "number": "TCVN 7568-14:2025",
        "title": "Hệ thống báo cháy – Phần 14: Thiết kế, lắp đặt các hệ thống báo cháy cho nhà và công trình",
        "issuer": "Bộ Khoa học và Công nghệ",
        "issue_date": "2025-01-01",
        "effective_date": "",
        "expiry_date": "",
        "status": "A - Còn hiệu lực",
        "field": "PCCC / ICS 13.220",
        "source_name": "VSQI - CSDL Tiêu chuẩn quốc gia",
        "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+7568-14%3A2025",
        "is_draft": 0,
        "note": "Thay thế TCVN 7568-14:2015 và TCVN 5738:2021; chỉ lưu metadata/link VSQI.",
    },
    {
        "category": "TCVN",
        "number": "TCVN 5738:2021",
        "title": "Phòng cháy chữa cháy - Hệ thống báo cháy tự động - Yêu cầu kỹ thuật",
        "issuer": "Bộ Khoa học và Công nghệ",
        "issue_date": "2021-01-01",
        "effective_date": "",
        "expiry_date": "2025-05-12",
        "status": "W - Hết hiệu lực",
        "field": "PCCC / ICS 13.220 / Lịch sử",
        "source_name": "VSQI - CSDL Tiêu chuẩn quốc gia",
        "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+5738%3A2021",
        "is_draft": 0,
        "note": "Đã được thay thế bởi TCVN 7568-14:2025; giữ để tra cứu hồ sơ/dự án cũ.",
    },
)


def _merge_unique(existing, extra) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in tuple(existing or ()) + tuple(extra or ()):
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def _doc_key(doc: dict[str, Any]) -> str:
    number = re.sub(r"\s+", "", str(doc.get("number", "") or "")).upper()
    if number:
        return "n:" + number
    url = str(doc.get("source_url", "") or "").split("#", 1)[0].rstrip("/").casefold()
    if url:
        return "u:" + url
    return "t:" + re.sub(r"\s+", " ", str(doc.get("title", "") or "")).strip().casefold()[:240]


def _dedupe(primary, secondary, limit: int = 2000) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in list(primary or []) + list(secondary or []):
        try:
            doc = dict(raw)
        except Exception:
            continue
        if int(doc.get("is_draft", 0) or 0) != 0:
            continue
        text = " ".join(str(doc.get(k, "") or "") for k in ("title", "status", "category")).casefold()
        if "dự thảo" in text:
            continue
        key = _doc_key(doc)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(doc)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _repo_seed_key(repo: Any) -> str:
    return f"{type(repo).__module__}.{type(repo).__qualname__}:{getattr(repo, 'path', '')}"


def seed_official_legal_documents(repo: Any) -> dict[str, int]:
    """Seed official/legal anchors once per repository/process.

    This runs when the Legal sheet is opened, so critical documents such as
    06/2021/TT-BXD are visible even when an external search engine misses them.
    Repeated Streamlit reruns do not repeat database writes.
    """
    key = _repo_seed_key(repo)
    with _SEED_LOCK:
        if key in _SEEDED_REPOS:
            return {"found": 0, "added": 0, "updated": 0}
        stats = repo.upsert_many([dict(x) for x in OFFICIAL_CORE_DOCS], "Nguồn chính thức")
        _SEEDED_REPOS.add(key)
        return stats


def _collect_pccc_vsqi(ld: Any, *, max_pages: int = 20, enrich_limit: int = 40) -> list[dict[str, Any]]:
    """Scan the complete VSQI fire-protection ICS family (13.220) by pages."""
    session = ld._session()
    found: list[dict[str, Any]] = []
    seen_numbers: set[str] = set()
    empty_streak = 0
    for page in range(1, max(1, int(max_pages)) + 1):
        url = f"{ld.VSQI_SEARCH}?ic%5B%5D={quote_plus('13.220')}&page={page}"
        try:
            docs = ld._collect_vsqi_page(session, url, "PCCC / ICS 13.220")
        except Exception:
            break
        if not docs:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue
        empty_streak = 0
        new_count = 0
        for doc in docs:
            number = re.sub(r"\s+", "", str(doc.get("number", "") or "")).casefold()
            if not number or number in seen_numbers:
                continue
            seen_numbers.add(number)
            found.append(dict(doc))
            new_count += 1
        if new_count == 0 and page > 2:
            break

    # Enrich a bounded subset; the remaining rows still keep official VSQI URLs.
    for idx in range(min(max(0, int(enrich_limit)), len(found))):
        try:
            found[idx] = ld._enrich_vsqi_status(session, found[idx])
        except Exception:
            pass
    return found


def _safe_search(search_fn: Callable[..., list[dict]], query: str, limit: int) -> list[dict[str, Any]]:
    try:
        return [dict(x) for x in (search_fn(query, limit=max(1, int(limit))) or [])]
    except Exception:
        return []


def fetch_pccc_documents(limit: int = 1200) -> list[dict[str, Any]]:
    """Build a broad PCCC/CNCH metadata set from official anchors + online sources."""
    import legal_documents as ld

    online: list[dict[str, Any]] = []
    # VSQI ICS 13.220 has many pages; scan the family independently of the
    # generic QLXD two-page-per-ICS sync.
    online.extend(_collect_pccc_vsqi(ld))

    # TVPL acts only as a discovery/reference source. Official anchors always
    # win dedupe order, so a reference result cannot overwrite the official URL.
    for query in PCCC_SYNC_QUERIES:
        online.extend(_safe_search(ld.search_thuvienphapluat, query, 24))

    return _dedupe(OFFICIAL_CORE_DOCS, online, limit=max(1, int(limit)))


def install_legal_pccc_backfill() -> None:
    """Install PCCC/CNCH discovery and include it in the normal QLXD sync."""
    import legal_documents as ld

    if getattr(ld, "_v622_legal_pccc_installed", False):
        return

    ld.CONSTRUCTION_KEYWORDS = list(_merge_unique(
        getattr(ld, "CONSTRUCTION_KEYWORDS", ()),
        (
            "phòng cháy chữa cháy", "pccc", "cứu nạn cứu hộ", "cnch",
            "an toàn cháy", "ngăn cháy", "chống cháy lan", "thoát nạn",
            "hút khói", "tăng áp", "báo cháy", "chữa cháy tự động",
            "sprinkler", "họng nước chữa cháy", "cấp nước chữa cháy",
        ),
    ))
    ld.TVPL_SYNC_QUERIES = _merge_unique(getattr(ld, "TVPL_SYNC_QUERIES", ()), PCCC_SYNC_QUERIES)

    original_sync_source = ld.sync_source

    def sync_source_with_pccc(repo, source: str):
        if source != "pccc":
            return original_sync_source(repo, source)
        label = "PCCC / CNCH - Công báo, VBPL, VSQI"
        try:
            docs = fetch_pccc_documents()
            stats = repo.upsert_many(docs, label)
            repo.log_sync(label, "OK", stats, "Official anchors + VSQI ICS 13.220 + PCCC reference discovery")
            return {"source": label, **stats, "error": ""}
        except Exception as exc:
            repo.log_sync(label, "ERROR", {}, str(exc))
            return {"source": label, "found": 0, "added": 0, "updated": 0, "error": str(exc)}

    ld.sync_source = sync_source_with_pccc

    # Preserve all existing QLXD sources and append the dedicated PCCC family.
    def sync_all_with_pccc(repo):
        return [ld.sync_source(repo, source) for source in ("vbpl", "vsqi", "tvpl", "pccc")]

    ld.sync_all = sync_all_with_pccc
    ld._v622_legal_pccc_installed = True
    ld._v622_legal_pccc_marker = PATCH_MARKER


def clear_pccc_seed_for_tests() -> None:
    with _SEED_LOCK:
        _SEEDED_REPOS.clear()
