# Moda config UI

Giao diện web để cấu hình hệ thống Moda thay cho việc sửa tay `config/sites/*.json`,
`config/post_analysis.json`, `config/reference_plates/*.txt`, `.env` và `.env.jetson`.

```text
Trình duyệt ──► config_ui (FastAPI + SQLite, cổng 8080)
                  1. Kiểm tra bằng loader gốc (traffic_core.config, post_analysis.config)
                  2. Sao lưu file cũ → ghi file mới (atomic)
                  3. docker compose up -d --no-deps --no-build --force-recreate <service bị ảnh hưởng>
                  4. Theo dõi container N giây; lỗi → khôi phục file cũ và recreate lại
```

SQLite là nguồn dữ liệu gốc. Các file cấu hình được **sinh lại** từ DB, nên không nên sửa tay
các file trên khi đã dùng UI (lần Apply sau sẽ ghi đè). `traffic_core` và `post_analysis`
không bị sửa.

## Cấu trúc

```text
config_ui/
  backend/moda_ui/     FastAPI: schema.sql, repository, generator, validation, applier, api
  backend/tests/       Test round-trip và luồng Apply (docker giả lập)
  frontend/src/        React + Ant Design + react-konva (vẽ ROI)
  Dockerfile           Build frontend + backend thành một image
  data/                SQLite và thư mục backups/ (tạo khi chạy, không commit)
compose.ui.yaml        Service config_ui (project compose riêng)
.env.ui.example        Biến môi trường của config_ui
```

## Cài đặt trên Jetson

Lần đầu, chạy trong thư mục `moda-jetson`:

```bash
cp .env.ui.example .env.ui
```

```bash
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Sửa `.env.ui`: `MODA_PROJECT_DIR` = kết quả `pwd`, `MODA_CONFIG_SECRET` = chuỗi vừa sinh. Cất giữ
chuỗi này cẩn thận; mất nó thì phải nhập lại toàn bộ URL camera.

```bash
docker compose -f compose.ui.yaml --env-file .env.ui build
```

Nạp cấu hình đang chạy (`config/`, `.env`, `.env.jetson`) vào SQLite:

```bash
docker compose -f compose.ui.yaml --env-file .env.ui run --rm config_ui import
```

Tạo tài khoản admin (sẽ hỏi mật khẩu):

```bash
docker compose -f compose.ui.yaml --env-file .env.ui run --rm config_ui create-admin admin
```

Chạy UI:

```bash
docker compose -f compose.ui.yaml --env-file .env.ui up -d
```

`compose.yaml` của detector đã được thêm mount `./config:/app/config:ro`. Trước đây `config/`
được copy vào image lúc build, nên sửa file sẽ không có tác dụng. Detector đang chạy cần
recreate **một lần** để nhận mount mới, ví dụ:

```bash
docker compose up -d in_gate1_lane1
```

Mở `http://<ip-jetson>:8080`.

## Lần Apply đầu tiên

Lần đầu, UI sẽ báo mọi file đều thay đổi dù chưa sửa gì, vì:

- file sinh ra ghi rõ các giá trị mặc định (`video_fps`, `output_partition`, `vehicle_image_root`);
- `reference_plates/*.txt` bị bỏ các dòng trùng (bản gốc có ~143 biển số trùng; runtime chỉ dùng
  để so khớp nên kết quả không đổi);
- `.env`/`.env.jetson` được ghi lại theo định dạng chuẩn (giữ nguyên comment và các biến khác).

Vì vậy lần Apply đầu sẽ recreate mọi service đang chạy trên máy. Nên làm lúc không có xe qua.

## Lưu ý vận hành

- **Chỉ service đang chạy trên máy này** mới được recreate. Service chưa được tạo (detector
  của Jetson khác) chỉ được ghi file.
- Triton không bao giờ bị restart bởi UI.
- Thêm nguồn camera thứ 2 cho một làn: `compose.yaml` phải truyền thêm biến môi trường của
  camera đó vào service. Nếu thiếu, bước Apply sẽ báo lỗi và không ghi gì.
- Toạ độ ROI của làn xe tính trong khung streammux **2688×1520** (`traffic_core/deepstream.py`).
  Ảnh snapshot được kéo giãn về khung này giống DeepStream. Crop màn hình cân tính theo độ phân
  giải gốc của camera cân.
- Mỗi lần Apply lưu bản sao file cũ vào `config_ui/data/backups/` (giữ 30 bản gần nhất) và
  snapshot DB vào bảng `config_revisions` để rollback từ màn hình Lịch sử.
- Container này mount `/var/run/docker.sock`, tương đương quyền root trên Jetson. Chỉ mở cổng
  trong mạng nội bộ (đặt `MODA_UI_BIND` = IP LAN) và dùng mật khẩu mạnh.

## Lệnh CLI

Tất cả chạy qua `docker compose -f compose.ui.yaml --env-file .env.ui run --rm config_ui <lệnh>`:

| Lệnh | Tác dụng |
|---|---|
| `import [--force]` | Nạp file cấu hình hiện có vào DB (`--force` ghi đè DB) |
| `create-admin <user> [--role viewer]` | Tạo tài khoản |
| `generate` | In diff và service bị ảnh hưởng, không ghi file |
| `gen-key` | Sinh khoá Fernet |

## Phát triển

```bash
pip install -r config_ui/backend/requirements.txt
```

```bash
cd config_ui/backend && MODA_PROJECT_DIR=../.. MODA_CONFIG_SECRET=<key> python -m moda_ui serve --port 8080
```

```bash
cd config_ui/frontend && npm install && npm run dev
```

Test backend (từ thư mục `moda-jetson`):

```bash
python -m unittest discover -s config_ui/backend/tests -t config_ui/backend
```

## Chưa kiểm chứng trên thiết bị

Đã chạy được trên máy phát triển: test backend (import → sinh file → loader gốc chấp nhận;
Apply/khôi phục/rollback với docker giả lập), build frontend và thao tác UI trên dữ liệu thật.
Những phần **chưa** chạy thật, cần smoke-test trên Jetson:

- `docker compose ... up --force-recreate` từ trong container và kiểm tra trạng thái;
- snapshot RTSP bằng OpenCV từ camera thật;
- build image `config_ui/Dockerfile` cho arm64.
