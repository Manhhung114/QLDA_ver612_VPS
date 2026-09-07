# QLDA Xây dựng V6.22 PostgreSQL Cloud

Bản hiện tại chạy trực tiếp trên **Streamlit Community Cloud**, giữ nguyên nghiệp vụ V6.21/WebOpt và sử dụng backend PostgreSQL khi `DATABASE_URL` được cấu hình.

## Deploy chính
- Repository: `Manhhung114/QLDA_ver612`
- Branch: `main`
- Main file: `streamlit_app.py`
- Python: **3.12**

`streamlit_app.py` giải nén, finalize và compile source ứng dụng trực tiếp trong bộ nhớ. Community Cloud không cần bước build container hoặc thư mục `dist/` ghi lúc chạy.

Streamlit Community Cloud tự dùng:
- `requirements.txt` cho Python dependencies;
- `packages.txt` cho `default-jre-headless` phục vụ MPXJ/JPype;
- `.streamlit/config.toml` cho cấu hình giao diện/server.

## Tính năng/tối ưu vẫn giữ
- lazy navigation + `st.fragment`, chỉ tải module/sheet đang dùng;
- lazy import Plotly và Văn bản; Gantt tắt mặc định;
- PostgreSQL Cloud, đồng thời giữ lớp tương thích SQLite khi cần;
- HTTP connection pool + cache ngắn hạn Google Apps Script/Drive;
- phân trang bảng lớn và chỉ tạo Excel khi người dùng yêu cầu;
- persistent login qua Refresh/F5;
- AI Gemini/OpenAI streaming và typewriter stream;
- workflow Nhà thầu → Ban điều hành → TVGS → Ban QLDA;
- tương thích dữ liệu/workflow legacy.

## Secrets
Khi tạo app trên Streamlit Community Cloud, vào **Advanced settings → Secrets** và nhập các khóa hệ thống đang dùng, đặc biệt:
- `DATABASE_URL`;
- `QLDA_DRIVE_WEBAPP_URL`;
- `QLDA_DRIVE_API_TOKEN`;
- `GEMINI_API_KEY` hoặc `OPENAI_API_KEY`.

Xem hướng dẫn chi tiết tại `DEPLOY_STREAMLIT_COMMUNITY_CLOUD.md`.

> Lưu ý: filesystem cục bộ của Streamlit Community Cloud không phải nơi lưu dữ liệu nghiệp vụ lâu dài. Dữ liệu cần bền vững nên lưu trong PostgreSQL và Google Drive theo cấu hình hiện tại.
