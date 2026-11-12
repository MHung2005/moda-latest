# Reverse-Engineered Specification: Moda traffic pipeline

## Overview

Hệ thống gồm một module DeepStream phát hiện xe/biển số và hai module phân tích hậu
kỳ được kích hoạt qua Kafka: đọc màn hình cân và nhận diện trạng thái/loại hàng.

## Architecture Summary

### Technology stack

- Python 3 trên NVIDIA Jetson.
- NVIDIA DeepStream 6.3 cho phát hiện xe và biển số.
- Kafka cho giao tiếp bất đồng bộ giữa detector và analyzer.
- Ultralytics client gọi model `tram_can` trên NVIDIA Triton.
- OpenCV cho RTSP capture, crop và ghi ảnh.

### Data flow

```text
RTSP lane camera
  -> traffic_core / DeepStream
  -> Kafka: VehicleDetection_Gate1
       `-> weight_reader -> tram_can -> Kafka: AnalysisResult_Gate1
```

Topic Kafka là seam triển khai. Việc analyzer không xuất hiện trong import graph của
`traffic_core` là hành vi kiến trúc dự kiến, không phải dấu hiệu code không dùng.

## Observed Functional Requirements

### Vehicle detection

**OBS-VEHICLE-001**: Khi xe class `0` vượt confidence và đi vào capture polygon,
hệ thống shall gom kết quả biển số, lưu ảnh và phát payload lên
`VehicleDetection_Gate1`.

**OBS-VEHICLE-002**: Khi EOS, error hoặc shutdown xảy ra, detector shall cố hoàn tất
event đang chờ, đóng Kafka và đưa pipeline về `NULL`.

### Weight reader

**OBS-WEIGHT-001**: Khi nhận event có `trigger_code` thuộc gate 1, weight reader shall
đọc camera cân, crop ROI theo làn và gọi model Triton `tram_can`.

**OBS-WEIGHT-002**: Trong tối đa 60 giây, weight reader shall chọn số xuất hiện nhiều
nhất; khi số đã giảm quá 3.000 trong 70 frame liên tiếp, nó shall chọn giá trị đỉnh.
Frame không phát hiện được chữ số shall không được tính là cân nặng bằng 0.

**OBS-WEIGHT-003**: Khi có kết quả, weight reader shall giữ payload trigger, đặt
`analysis_cam_id=1`, `analysis_code=weight_gate1_<lane>`, thêm ảnh/kết quả và phát lên
`AnalysisResult_Gate1`.

**OBS-WEIGHT-004**: Khi event mới của cùng làn đến, weight reader shall hủy lần đọc
cũ trước khi bắt đầu lần đọc mới để tránh hai phiên cùng tranh camera/ROI.

## Observed Non-Functional Requirements

- Các replica của analyzer dùng chung consumer group để tránh xử lý trùng.
- URL có credential, Kafka bootstrap và Triton URL phải đến từ environment.
- Ảnh analyzer nằm dưới `/workspace/traffic` để tương thích volume legacy.
- Model server dùng repository `models/triton` với model `tram_can`.
- Lỗi capture/inference của một event được log và không làm dừng consumer chính.
- Input offset chỉ được commit sau khi output Kafka được broker xác nhận hoặc failure
  event đã được ghi vào `AnalysisFailure_Gate1`.
- Delivery là at-least-once; consumer kết quả phải xử lý idempotent.
- Số process và số event đang chờ phải bị chặn bởi `worker_count`.
- Mỗi job phải có deadline ngoài process để có thể kết thúc cả lời gọi native bị treo.
- Detector và post-analysis phải có Compose độc lập để khởi động analyzer không kéo
  theo bốn camera DeepStream.
- Deployment post-analysis phải kiểm tra biến Kafka/RTSP, đủ model Triton và cấu
  hình Compose trước khi khởi động container.

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

## Uncertainties and Questions

- Model ONNX và Triton image cần được xác minh trên đúng JetPack của thiết bị.
- Giá trị cân trên 100.000 vẫn được chia 10 theo logic legacy; quy tắc nghiệp vụ này
  chưa có tài liệu bên ngoài source.
- Đường dẫn ảnh tuyệt đối của weight được giữ để tương thích consumer hiện hữu; cần
  xác nhận consumer cuối có phụ thuộc chính xác vào quy ước này.
- Ngưỡng, ROI và nhãn model cần được kiểm chứng bằng recording tại hiện trường.
- Crash giữa output publish và input commit có thể tạo output lặp; chưa có Kafka
  transaction xuyên hai topic.
- `runtime/` chưa có retention tự động vì chưa có chính sách lưu giữ được xác nhận.

## Recommendations

1. Chạy smoke test trên Jetson với một recording cho mỗi làn.
2. Chốt schema/version cho hai topic Kafka.
3. Sau smoke test, pin version hoặc digest của image Ultralytics JetPack 5 và ghi nhận
   version của ba model Triton.
