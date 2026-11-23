# Universal Excel Recognition – giai đoạn 1 (an toàn dữ liệu)

## Mục đích

Bổ sung nhận diện cấu trúc chung cho các file Excel nghiệp vụ QLDA:
BOQ (bảng khối lượng), IPC (hồ sơ thanh toán), VO (phát sinh)
và Schedule (tiến độ). Mã nguồn mới đặt trong
qlda.application.excel.template_recognition.

Mục đích giai đoạn 1 là **đọc và gợi ý ánh xạ mẫu** chứ không phải tự
hợp thức hoá mọi mẫu mới. Các tổng tiền và dữ liệu tài chính vẫn phải
được bộ nhập hiện hữu kiểm chứng.

## Đã kết nối

- WORKBOOK_SCAN: kiểm kê dữ liệu, đếm Sheet và trả về
  sheets[].template_recognition (role, header_row, columns, candidates,
  fingerprint, review_required).
- BOQ, IPC, VO, SCHEDULE_EXCEL: trước khi thực hiện nhập,
  đọc giới hạn tiêu đề để ghi thêm kết quả template_recognition vào
  result_summary của Excel Job. **Không tự thay đổi đường ghi DB**.
- SCHEDULE_EXCEL: parser tiến độ sử dụng ánh xạ chung (vị trí Sheet,
  cột và tiêu đề có thể thay đổi). Vẫn giữ bộ nhận diện cũ làm dự phòng,
  giữ nguyên luật ngày, bỏ dòng lỗi, đếm số dòng và transaction.
- Streamlit nhập tiến độ: màn xem trước có bảng trường và cột được nhận diện.

## Quy tắc an toàn

1. Chỉ đọc .xlsx/.xlsm, mở bằng openpyxl read-only, data_only=True,
   keep_links=False. Không thực thi macro, VBA, công thức.
2. Giới hạn dò tối đa 80 dòng đầu × 40 cột đầu của mỗi Sheet để
   không nhân đôi việc nạp toàn bộ workbook lớn vào RAM.
3. Không tự đoán ô dữ liệu, không tự sửa ngày, khối lượng, đơn giá,
   VAT, thanh toán kỳ này/lũy kế hoặc ghi sổ.
4. status=recognized nghĩa là **đã phát hiện tiêu đề tương đối rõ**,
   *không có nghĩa hồ sơ đã được xác minh và có thể ghi DB tự động*.
5. review_required=true, unsupported hoặc cấu trúc không thuộc alias:
   cần người dùng kiểm tra/xác nhận mẫu. Nếu parser nghiệp vụ không đọc
   được, tiếp tục trả lỗi như trước, tuyệt đối không ghi dữ liệu giả.
6. Dấu vân tay (fingerprint) chỉ dựa vào loại hồ sơ và trường được
   nhận diện, không phải bằng chứng các tệp có nội dung giống nhau.
7. Giữ dữ liệu cũ, không xoá thay thế hay thay đổi schema chỉ để
   phục vụ bộ dò mẫu.

## Giới hạn chưa giải quyết trong giai đoạn 1

- Không có AI tự học/memorize template trên PostgreSQL; chưa có màn
  người quản trị phê duyệt và lưu mẫu cho các phân hệ tài chính.
- Các mẫu IPC/VO/BOQ mới có thể được **nhận diện cấu trúc** nhưng
  bộ nhập nghiệp vụ hiện tại vẫn cần hiểu đủ các trường tài chính.
  Không được quảng cáo là tự nhập chính xác tất cả mẫu tuỳ ý.
- Chưa có chương trình quét đệ quy mọi thư mục trên VPS/Google Drive;
  nguồn file vẫn phải theo quyền và luồng upload/lưu trữ hiện hữu.
- .xls/.xlsb không được xử lý bởi bộ đọc mới. Cần bộ chuyển đổi có
  kiểm chứng nếu muốn hỗ trợ định dạng cũ.
- Không trích xuất giá trị công thức chưa có cached result;
  tài liệu nguồn phải được tính toán/lưu lại bằng Excel trước.

## Kiểm thử/nghiệm thu

Chạy trong môi trường dự án:

PYTHONPATH=src python -m unittest tests.test_universal_excel_recognition -v

Và các hồi quy:

PYTHONPATH=src python -m unittest tests.test_schedule_background tests.test_boq_multisheet -v

Sau đó chạy CI Architecture Quality Gate, Critical Domain Regression,
V7 Native Regression và V7 Docker Check trước khi merge.

Để nâng cấp giai đoạn 2 cần bổ sung mẫu Excel thực tế đã ẩn dữ liệu nhạy cảm,
mapping được admin duyệt theo workspace, cơ chế phê duyệt tài chính,
đối chiếu số dòng/tổng tiền khi đổi mẫu và thử nghiệm trên bản sao cơ sở dữ liệu.
