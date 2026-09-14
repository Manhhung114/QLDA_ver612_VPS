# QLDA Xây dựng V7.2 - VPS

Repository triển khai QLDA trên **Ubuntu 24.04 LTS** với PostgreSQL, Streamlit và FastAPI.

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
          Native PostgreSQL / VPS SSD adapters
```

V7.2 đã native hóa `sessions / project-access / files / jobs / search`. Legacy
adapter còn lại chỉ cho `AI` và pipeline `Excel import` trong thời gian tiếp tục
migration. Secrets nằm tại `/opt/qlda/shared/qlda.env`, không commit lên GitHub.

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

Nếu có domain, ví dụ `qlda.example.com`:

```bash
sudo bash vps/install.sh qlda.example.com
sudo certbot --nginx -d qlda.example.com
```

## Cập nhật từ GitHub

```bash
sudo /opt/qlda/app/vps/deploy.sh
```

`deploy.sh` ghi lại commit cũ, kéo `main`, cập nhật dependency, kiểm tra code,
restart service và health-check. Nếu bản mới không khởi động, script tự rollback
về commit trước.

Rollback thủ công:

```bash
sudo /opt/qlda/app/vps/rollback.sh
```

## Kiểm tra log

```bash
sudo journalctl -u qlda -f
sudo journalctl -u qlda-api -f
sudo nginx -t
sudo systemctl status nginx --no-pager
curl http://127.0.0.1:8501/_stcore/health
curl http://127.0.0.1:8001/api/health
```
