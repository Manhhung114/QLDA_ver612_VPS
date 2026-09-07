# QLDA Xây dựng V6.22 - VPS

Đây là repository thử nghiệm VPS tách riêng từ `Manhhung114/QLDA_ver612` để chạy QLDA trên **Ubuntu 24.04 LTS** mà không ảnh hưởng bản Streamlit Community Cloud.

## Kiến trúc

`Internet -> HTTPS/Nginx -> Streamlit 127.0.0.1:8501 -> PostgreSQL + Google Drive Gateway + AI`

- Streamlit chỉ lắng nghe `127.0.0.1:8501`, không mở trực tiếp ra Internet.
- Nginx phục vụ HTTP/HTTPS và WebSocket cho Streamlit.
- `systemd` giữ app chạy liên tục và tự khởi động lại khi VPS reboot.
- Secrets nằm tại `/opt/qlda/shared/qlda.env`, không commit lên GitHub.
- PostgreSQL và Google Drive vẫn dùng theo cấu hình QLDA V6.22 hiện tại.

## Cài nhanh trên Ubuntu 24.04

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/Manhhung114/QLDA_ver612_VPS.git
cd QLDA_ver612_VPS
sudo bash vps/install.sh _
```

Sau đó cấu hình secrets:

```bash
sudo nano /opt/qlda/shared/qlda.env
sudo systemctl restart qlda
sudo systemctl status qlda --no-pager
sudo /opt/qlda/app/vps/healthcheck.sh
```

Nếu có domain, ví dụ `qlda.example.com`, cài bằng:

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
sudo nginx -t
sudo systemctl status nginx --no-pager
curl http://127.0.0.1:8501/_stcore/health
```

## F5 / Refresh

Repo VPS giữ riêng cơ chế persistent authentication để thử nghiệm F5/Refresh trên domain/IP VPS. Các thay đổi ở repository này không ảnh hưởng `Manhhung114/QLDA_ver612` đang chạy trên Streamlit Community Cloud.
