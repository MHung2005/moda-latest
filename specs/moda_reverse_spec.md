# Reverse-Engineered Specification: Moda traffic pipeline

Cập nhật theo code hiện tại (JetPack 6.2 / DeepStream 7.1). Luồng nhận diện thùng hàng
(`cargo_recognition`) đã bị gỡ ở commit `e56ae7e`; hệ thống chỉ còn một analyzer là đọc cân.

## Overview

Hệ thống gồm một module DeepStream phát hiện xe/biển số (`traffic_core`), một module phân tích
hậu kỳ đọc màn hình cân (`post_analysis`, service `weight_reader`) được kích hoạt qua Kafka, và
giao diện cấu hình/kết quả (`config_ui`).

## Architecture Summary

### Technology stack

- Python 3 trên NVIDIA Jetson, JetPack 6.2 (L4T R36.4).
- NVIDIA DeepStream 7.1 (`deepstream:7.1-triton-multiarch`, `pyds` 1.2.0, TensorRT 10) cho phát hiện
  xe, biển số và ký tự; tracker NvDCF. Engine TensorRT được build bằng `trtexec`
  (`docker/prepare_engines.py`), parser YOLO được build lại trong image.
- Kafka cho giao tiếp bất đồng bộ giữa detector, analyzer và recorder.
- Ultralytics (image `jetson-jetpack6`) gọi model `tram_can` trên NVIDIA Triton 24.01 (iGPU).
- OpenCV cho RTSP capture, crop và ghi ảnh.
- FastAPI + SQLite + React cho `config_ui` và `results_recorder`.

### Data flow

```text
RTSP lane camera
  -> traffic_core / DeepStream
  -> Kafka: VehicleDetection_Gate1
       |-> weight_reader -> tram_can (Triton) -> Kafka: AnalysisResult_Gate1
       |                                  `-> lỗi: AnalysisFailure_Gate1
       `-> results_recorder (cùng hai topic trên) -> SQLite -> config_ui
```

Topic Kafka là seam triển khai. Việc analyzer không xuất hiện trong import graph của
`traffic_core` là hành vi kiến trúc dự kiến, không phải dấu hiệu code không dùng.

## Observed Functional Requirements

### Vehicle detection

**OBS-VEHICLE-001**: Khi xe class `0` vượt confidence và đi vào capture polygon,
hệ thống shall gom kết quả biển số trong `plate_collection_seconds`, lưu ảnh và phát payload lên
`VehicleDetection_Gate1`.

**OBS-VEHICLE-002**: Khi EOS, error hoặc shutdown xảy ra, detector shall cố hoàn tất
event đang chờ, đóng Kafka và đưa pipeline về `NULL`.

**OBS-VEHICLE-003**: Khi một nguồn RTSP không gửi frame trong `MODA_SOURCE_STALL_SECONDS`
(mặc định 60 s), hoặc chưa có frame đầu tiên sau `MODA_SOURCE_STARTUP_SECONDS` (mặc định 120 s),
detector shall thoát với mã lỗi để Docker khởi động lại.

**OBS-VEHICLE-004**: Ảnh xe shall được lưu theo thư mục ngày giờ địa phương tính bằng
`utc_offset_hours` của site config; `timestamp` trong payload là UTC.

### Weight reader

**OBS-WEIGHT-001**: Khi nhận event có `trigger_code` thuộc gate 1, weight reader shall
đọc camera cân, crop ROI theo làn và gọi model Triton `tram_can`.

**OBS-WEIGHT-002**: Trong tối đa 60 giây, weight reader shall chọn số khác 0 xuất hiện nhiều
nhất; khi số đã giảm quá 3.000 trong 70 frame liên tiếp, nó shall chọn giá trị đỉnh.
Frame không phát hiện được chữ số shall không được tính là cân nặng bằng 0; giá trị 0 chỉ được
trả về khi không đọc được giá trị nào khác.

**OBS-WEIGHT-003**: Khi có kết quả, weight reader shall giữ payload trigger, đặt
`analysis_cam_id=1`, `analysis_code=weight_gate1_<lane>`, thêm ảnh/kết quả và phát lên
`AnalysisResult_Gate1`.

**OBS-WEIGHT-004**: Khi event mới của cùng làn đến, weight reader shall hủy lần đọc
cũ trước khi bắt đầu lần đọc mới để tránh hai phiên cùng tranh camera/ROI; event bị thay thế
shall được ghi vào `AnalysisFailure_Gate1`.

### Configuration UI

**OBS-UI-001**: SQLite của `config_ui` là nguồn dữ liệu gốc; `config/sites/*.json`,
`config/post_analysis.json`, `config/reference_plates/*.txt`, `.env` và `.env.jetson` shall được
sinh lại từ DB và kiểm tra bằng loader của runtime trước khi ghi.

**OBS-UI-002**: Apply shall sao lưu file cũ, ghi atomic, chỉ recreate service bị ảnh hưởng đang
chạy trên máy, và khôi phục file cũ nếu service không chạy ổn định.

**OBS-UI-003**: Múi giờ (`utc_offset_hours`) là một thiết lập hệ thống duy nhất, được sinh vào
mọi site config và `post_analysis.json`; runtime không giả định múi giờ mặc định.

## Observed Non-Functional Requirements

- Các replica của analyzer dùng chung consumer group để tránh xử lý trùng.
- URL có credential, Kafka bootstrap và Triton URL phải đến từ environment.
- Ảnh analyzer nằm dưới `/workspace/traffic` để tương thích volume legacy.
- Model server dùng repository `models/triton` với model `tram_can`.
- Lỗi capture/inference của một event được log và không làm dừng consumer chính.
- Input offset chỉ được commit sau khi output Kafka được broker xác nhận hoặc failure
  event đã được ghi vào `AnalysisFailure_Gate1`.
- Delivery là at-least-once; consumer kết quả phải xử lý idempotent
  (`section_id + analysis_code`).
- Số process và số event đang chờ phải bị chặn bởi `worker_count`.
- Mỗi job phải có deadline ngoài process để có thể kết thúc cả lời gọi native bị treo.
- Detector, post-analysis và config UI có Compose độc lập; khởi động analyzer không kéo
  theo bốn camera DeepStream.
- Deployment post-analysis phải kiểm tra biến Kafka/RTSP, model Triton và cấu
  hình Compose trước khi khởi động container (`scripts/analysis-service.sh`).

## Inferred Acceptance Criteria

### AC-001: Fan-out trigger

Given weight reader đang chạy
When detector phát một event hợp lệ lên `VehicleDetection_Gate1`
Then weight reader nhận được event đó.

### AC-002: Preserve trigger contract

Given một trigger payload hợp lệ
When analyzer phát kết quả
Then các field trigger ban đầu vẫn còn và chỉ các field `analysis_*` được cập nhật.

### AC-003: Commit only after durable outcome

Given một trigger Kafka đã được nhận
When analyzer chưa publish thành công output hoặc failure event
Then offset của trigger đó shall chưa được commit.

### AC-004: No implicit time zone

Given một site config hoặc `post_analysis.json` không có `utc_offset_hours`
When runtime nạp cấu hình
Then nó shall từ chối khởi động với lỗi cấu hình.

## Uncertainties and Questions

- Model ONNX và Triton image cần được xác minh trên thiết bị sau mỗi lần đổi model.
- Giá trị cân trên 100.000 vẫn được chia 10 theo logic legacy; quy tắc nghiệp vụ này
  chưa có tài liệu bên ngoài source.
- Đường dẫn ảnh tuyệt đối của weight được giữ để tương thích consumer hiện hữu; cần
  xác nhận consumer cuối có phụ thuộc chính xác vào quy ước này.
- Ngưỡng, ROI và nhãn model cần được kiểm chứng bằng recording tại hiện trường.
- Crash giữa output publish và input commit có thể tạo output lặp; chưa có Kafka
  transaction xuyên hai topic.
- `runtime/` chưa có retention tự động vì chưa có chính sách lưu giữ được xác nhận.
- Múi giờ chỉ hỗ trợ số giờ nguyên; vùng có lệch nửa giờ hoặc giờ mùa hè chưa được hỗ trợ.

## Recommendations

1. Chạy smoke test trên Jetson với một recording cho mỗi làn.
2. Chốt schema/version cho các topic Kafka.
3. Sau smoke test, pin digest của image Ultralytics JetPack 6, DeepStream 7.1 và Triton, và ghi
   nhận version của model `tram_can`.
