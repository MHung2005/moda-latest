# Moda traffic and downstream analysis

Ứng dụng nhận luồng RTSP trên NVIDIA Jetson. DeepStream nhận diện xe/biển số và phát
sự kiện Kafka; service phía sau đọc cân từ sự kiện đó.

## Luồng chạy

```text
Camera làn xe -> traffic_core (DeepStream) -> VehicleDetection_Gate1
                                                   |-> weight_reader
                                                         -> đọc màn hình cân
                                                         -> AnalysisResult_Gate1
```

`weight_reader` là Kafka consumer độc lập. Nó không được import bởi `traffic_core`;
topic Kafka là seam giữa các module.

## Cấu trúc

```text
traffic_core/                 Runtime DeepStream dùng chung cho bốn camera/làn
post_analysis/                Runtime đọc cân
config/sites/                 Cấu hình riêng cho camera/làn
config/post_analysis.json     ROI, model và quy tắc của analyzer đọc cân
config/reference_plates/      Danh sách biển số tham chiếu
models/                       Model DeepStream
models/triton/                Model đọc cân (tram_can) qua Triton
compose.yaml                  Bốn detector DeepStream
compose.analysis.yaml         Triton và hai service post-analysis
Dockerfile.analysis           Image post-analysis dành cho JetPack 6
scripts/analysis-service.sh   Preflight và start/stop post-analysis
runtime/                      Ảnh đầu ra và cache TensorRT, không commit
```

Credential camera và địa chỉ Kafka/Triton chỉ lấy từ environment. Detector dùng
`.env` tạo từ `.env.example`; post-analysis dùng `.env.jetson` tạo từ
`.env.jetson.example`. Không commit hai file chứa giá trị thật.

## JetPack 6.2 / DeepStream 7.1

Bản này chạy trên JetPack 6.2 (L4T R36.4). So với bản JetPack 5 / DeepStream 6.3:

- `Dockerfile`: base `deepstream:7.1-triton-multiarch`, `pyds 1.2.0` (Python 3.10), `numpy<2`.
- Parser YOLO (`NvDsInferParseYolo`) được build lại trong image vào
  `/opt/moda/libnvdsinfer_custom_impl_Yolo.so` (`docker/yolo_parser.mk`); các file `.so`
  trong `models/` là bản JetPack 5 và không còn được dùng.
- TensorRT 10 bỏ API build engine của DeepStream-Yolo, nên `engine-create-func-name` bị tắt;
  `docker/prepare_engines.py` (chạy trong `docker/entrypoint.sh`) build engine bằng `trtexec`
  vào `runtime/engines` ở lần chạy đầu và tự xóa engine cũ khi phiên bản TensorRT đổi.
- `models/tracker/config_tracker.yml` đã chuyển sang định dạng NvDCF của DeepStream 7.1
  (bản cũ: `config_tracker.ds5.yml.bak`).
- Image phân tích dùng Ultralytics `jetson-jetpack6`.

## Chạy detector DeepStream

Mỗi Jetson chỉ nên khởi động detector mà nó phụ trách, ví dụ:

```bash
docker compose up -d --build in_gate1_land1
```

Các detector hợp lệ là `in_gate1_land1`, `in_gate1_land2`, `out_gate1_land1` và
`out_gate1_land2`. Model được mount read-only từ `./models` vào `/home/jetson/models`.
Engine TensorRT sinh từ ONNX được ghi riêng vào `runtime/engines`; lần chạy đầu có thể mất
thêm thời gian để build engine, các lần sau dùng lại cache này.

Có thể chạy trực tiếp trong môi trường đã cài DeepStream 7.1 (cần tự build parser và engine như `docker/`):

```bash
python3 -m traffic_core --site config/sites/in_gate1_land1.json --gpu 0
```

## Chạy service sau DeepStream

Deployment này nhắm JetPack 6.2 (L4T R36.4)/DeepStream 7.1. Image analyzer được build từ image
Ultralytics dành cho JetPack 6; `Dockerfile.analysis` bổ sung Kafka và Triton client.
Triton và `weight_reader` được tách khỏi Compose của detector để
không vô tình khởi động cả bốn camera.

```bash
cp .env.jetson.example .env.jetson
# Sửa Kafka và RTSP URL camera cân trong .env.jetson

chmod +x scripts/analysis-service.sh
./scripts/analysis-service.sh config
./scripts/analysis-service.sh start
```

Script kiểm tra biến bắt buộc, model Triton, thư mục output và cấu hình Compose trước
khi build/start. Các lệnh vận hành khác:

```bash
./scripts/analysis-service.sh status
./scripts/analysis-service.sh logs
./scripts/analysis-service.sh restart
./scripts/analysis-service.sh stop
```

Có thể gọi Compose trực tiếp khi cần:

```bash
docker compose --env-file .env.jetson -f compose.analysis.yaml \
  up -d --build triton weight_reader
```

`MODA_ANALYSIS_BASE_IMAGE` mặc định là tag JetPack 6 `8.3.153` của Ultralytics.
Sau khi smoke-test trên thiết bị, nên pin thêm digest đã xác nhận để build có thể lặp
lại tuyệt đối.

Hoặc chạy riêng trong môi trường đã có các dependency trên:

```bash
python3 -m post_analysis weight --config config/post_analysis.json
```

`weight_reader` đọc `MODA_WEIGHT_CAMERA_URL`.

### Quy tắc xử lý event

- Consumer dùng manual commit. Offset đầu vào chỉ được commit sau khi ảnh đã lưu và
  message `AnalysisResult_Gate1` được Kafka xác nhận.
- Lỗi tạm thời được retry với exponential backoff. Sau số lần cấu hình, lỗi được ghi
  vào `AnalysisFailure_Gate1` rồi event đầu vào mới được commit.
- Cơ chế này là at-least-once: crash ngay sau khi publish output nhưng trước commit có
  thể tạo kết quả lặp. Consumer cuối nên dùng `section_id + analysis_code` làm khóa
  idempotency.
- Mỗi job có deadline và chạy trong process riêng.
- Weight giữ bộ đếm cùng tối đa hai frame đại diện; frame không thấy chữ số không bị
  coi là giá trị cân `0`.
- Event weight mới của cùng lane thay thế event đang đọc; event cũ được ghi vào dead
  letter topic thay vì mất im lặng.

Các timeout, số worker, retry và topic lỗi nằm trong `config/post_analysis.json`.
Ảnh trong `runtime/` chưa có chính sách retention tự động; cần giám sát dung lượng hoặc
đặt retention ở tầng vận hành.

## Giới hạn xác minh

Pipeline đầy đủ cần Jetson JetPack 6.2, DeepStream 7.1, model TensorRT/ONNX tương thích, camera
RTSP, Kafka và Triton. Trên máy phát triển chỉ có thể kiểm tra cú pháp, cấu hình và
Compose; đó không thay thế kiểm thử runtime trên thiết bị.

Healthcheck Compose chỉ xác nhận Triton HTTP ready. Việc ba model load đúng, OpenCV
hỗ trợ timeout RTSP và inference qua Triton chạy đúng vẫn phải smoke-test trên Jetson.
