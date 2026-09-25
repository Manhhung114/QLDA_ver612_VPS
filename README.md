# QLDA Xây dựng – VPS

QLDA là hệ thống quản lý dự án xây dựng triển khai trên **Ubuntu 24.04 LTS**, sử dụng **Streamlit + FastAPI + PostgreSQL** và các worker nền cho nhập Excel, Contractor Data Hub và AI Supervisor.

## Kiến trúc hiện tại

```text
Internet -> HTTPS/Nginx
              |-> Streamlit 127.0.0.1:8501
              |-> FastAPI   127.0.0.1:8001
                        |
                 Application Use Cases
                        |
                     Domain
                        |
                  Infrastructure
                        |
             PostgreSQL / VPS / Google / AI
```

`src/qlda/runtime_core` hiện là **compatibility boundary** cho các hành vi legacy đã chạy production. Tính năng mới không được phát triển bằng cách thêm `*_fix.py`, `*_patch.py`, `*_recovery.py` hoặc monkey-patch mới. Runtime được lắp ghép rõ ràng qua `qlda.composition.runtime_features`; `runtime_core.__init__` không tự cài behavior khi import.

Streamlit production khởi động từ:

```text
src/qlda/presentation/streamlit/main.py
```

`app.py` cũ được giữ như compatibility shell và bị Architecture Quality Gate khóa không cho tiếp tục phình to. UI mới phải nằm trong `qlda.presentation`.

Tài liệu kiến trúc đang được duy trì:

- `docs/architecture/ARCHITECTURE_HARDENING.md` – nguyên tắc kiến trúc và quality gate hiện hành.
- `docs/architecture/RUNTIME_CORE_MIGRATION_LEDGER.md` – danh sách các compatibility slice còn phải chuyển sang native layer.
- `docs/architecture/AI_ARCHITECTURE.md` – boundary AI, RAG/provenance, native tool calling, telemetry và quality evaluation.

Các tài liệu migration trung gian V6.25–V7.6 đã được loại khỏi nhánh `main`; lịch sử đầy đủ vẫn có trong Git history.

## CI / Quality gates

Nhánh `main` được kiểm tra qua năm workflow:

- **Architecture Quality Gate** – boundary, tăng nợ kỹ thuật, kích thước Streamlit shell và dynamic source execution.
- **AI Quality Gate** – AI boundary, tenant isolation, RAG/provenance, native planner contract và deterministic quality eval.
- **Critical Domain Regression** – BOQ, IPC/Claim, VO, Schedule, Contract, Autonomy và tenant isolation.
- **V7 Native Regression** – regression tổng thể runtime native/compatibility.
- **V7 Docker Check** – kiểm tra Docker build và entrypoint production.

## Secrets

Secrets production nằm ngoài Git tại:

```text
/opt/qlda/shared/qlda.env
```

Không commit API key, database password hoặc OAuth secret vào repository.

## Cài nhanh trên Ubuntu 24.04

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/Manhhung114/QLDA_ver612_VPS.git
cd QLDA_ver612_VPS
sudo bash vps/install.sh _
```

Sau đó cấu hình secrets và kiểm tra dịch vụ:

```bash
sudo nano /opt/qlda/shared/qlda.env
sudo systemctl restart qlda
sudo systemctl status qlda --no-pager
sudo /opt/qlda/app/vps/healthcheck.sh
```

Nếu có domain, ví dụ `qlda.example.com`:

```bash
sudo bash vps/install.sh qlda.example.com
sudo certbot --nginx -d qlda.example.com
```

## Cập nhật từ GitHub

```bash
sudo /opt/qlda/app/vps/deploy.sh
```

`deploy.sh` ghi lại commit cũ, kéo `main`, cập nhật dependency, kiểm tra code, restart service và health-check. Nếu bản mới không khởi động, script tự rollback về commit trước.

Rollback thủ công:

```bash
sudo /opt/qlda/app/vps/rollback.sh
```

## Kiểm tra log

```bash
sudo journalctl -u qlda -f
sudo journalctl -u qlda-api -f
sudo journalctl -u qlda-excel-worker -f
sudo journalctl -u qlda-contractor-data-worker -f
sudo nginx -t
sudo systemctl status nginx --no-pager
curl http://127.0.0.1:8501/_stcore/health
curl http://127.0.0.1:8001/api/health
```
