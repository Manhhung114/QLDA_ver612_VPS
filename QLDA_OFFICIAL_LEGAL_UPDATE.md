# Nguồn cập nhật pháp luật – QLDA, Xây dựng và MEPF/PCCC

**Mốc bổ sung:** 15/11/2026. Phần mở rộng độc lập không can thiệp bản QLDA production đang chạy trên VPS.

## Mục tiêu

- Ưu tiên `vbpl.vn`, `congbao.chinhphu.vn`, `vanban.chinhphu.vn`, `bxd.gov.vn`/`moc.gov.vn`, `tieuchuan.vsqi.gov.vn`.
- Tra cứu chủ động theo họ tiêu chuẩn và số hiệu chính xác cho **Xây dựng/QLDA** và **MEPF/PCCC**.
- Không coi kết quả của Thư Viện Pháp Luật hay repository GitHub là nguồn ban hành chính thống.
- Không tự gắn nhãn *còn hiệu lực* cho tài liệu vừa tìm thấy; ghi `Cần kiểm tra hiệu lực tại nguồn`.
- Giữ số hiệu theo năm, không loại bỏ âm thầm phiên bản cũ, không thu thập toàn văn tiêu chuẩn có bản quyền.

## Các file

- `src/qlda/application/legal_sources.py` – kiểm tra nguồn, phân nhóm, chống trùng, bộ quét số hiệu/nhóm và xuất JSON.
- `data/legal/official_catalog.json` – danh mục nền đã kiểm tra đường dẫn chính thống, gồm cả hai nhóm.
- `.github/workflows/refresh-official-legal.yml` – **chạy thủ công** khi cần cập nhật (Actions → Run workflow); không chạy ngầm trên VPS.
- `tests/test_legal_official_catalog.py` – regression chống URL giả, tiêu chuẩn dự thảo, trùng số hiệu và kiểm tra cập nhật hai nhóm.

## Chạy trên máy quản trị

```bash
PYTHONPATH=src python -m pytest tests/test_legal_official_catalog.py -q
PYTHONPATH=src python -m qlda.application.legal_sources \
  --seed data/legal/official_catalog.json --online \
  --output data/legal/official_catalog.json
```

Khi nguồn mạng hoặc chống bot không trả dữ liệu, không được xóa danh mục đã lưu.

## Đồng bộ ứng dụng XD & MEP AI 3D

Ứng dụng đọc catalog qua URL công khai GitHub `Manhhung114/QLDA_ver612_VPS/main/data/legal/official_catalog.json` khi người dùng nhấn **Cập nhật nguồn QLDA**. Giá trị tham chiếu offline trong app có cùng cấu trúc. Đây chỉ là metadata nguồn, không phải ý kiến pháp lý hoặc phê duyệt kỹ thuật.

## Xác thực và phân biệt hiệu lực

1. Đường dẫn nguồn chính thức chỉ chứng minh *nơi tra cứu*.
2. Phải mở văn bản gốc, đối chiếu ngày hiệu lực, các văn bản sửa đổi, thay thế và điều khoản chuyển tiếp tại thời điểm thiết kế/nghiệm thu.
3. TCVN không mặc nhiên bắt buộc chỉ vì có trong danh mục; kiểm tra quy chuẩn và hợp đồng viện dẫn.
4. Đối với PCCC phải kiểm tra đồng thời quy định chuyên ngành, quy chuẩn kỹ thuật và phiên bản sửa đổi hiện hành.
