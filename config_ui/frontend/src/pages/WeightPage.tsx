import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Typography,
} from "antd";
import { CameraOutlined, DeleteOutlined, PlusOutlined, SaveOutlined } from "@ant-design/icons";
import { AnalyzerLane, api } from "../api";
import { useAuth } from "../auth";
import AnalyzerSettingsForm, { numberField } from "../components/AnalyzerSettingsForm";
import { FieldLabel, tritonOptions, useSave } from "../components/common";
import ConfidenceInput from "../components/ConfidenceInput";
import { Box, RectEditor, useImage } from "../components/ShapeEditor";

const DEFAULT_SIZE = { width: 3840, height: 2160 };

export default function WeightPage() {
  const { isAdmin } = useAuth();
  const analyzer = useQuery({ queryKey: ["analyzer", "weight"], queryFn: () => api.analyzer("weight") });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const models = useQuery({ queryKey: ["models"], queryFn: api.modelOptions });
  const [selected, setSelected] = useState<string | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [analysisCode, setAnalysisCode] = useState("");
  const [nonce, setNonce] = useState(() => Date.now());
  const [newTrigger, setNewTrigger] = useState("");

  const settings = analyzer.data?.settings ?? {};
  const cameraId = Number(settings.camera_id);
  const camera = cameras.data?.find((item) => item.id === cameraId);
  const { image, failed, loading } = useImage(cameraId ? api.snapshotUrl(cameraId, nonce) : null);
  const size = image
    ? { width: image.naturalWidth, height: image.naturalHeight }
    : camera?.frame_width
      ? { width: camera.frame_width, height: camera.frame_height! }
      : DEFAULT_SIZE;

  const lanes = analyzer.data?.lanes ?? [];
  const lane = lanes.find((item) => item.trigger_code === selected);

  useEffect(() => {
    if (!selected && lanes.length) setSelected(lanes[0].trigger_code);
  }, [lanes, selected]);

  useEffect(() => {
    if (lane) {
      setBox({ x_min: lane.crop_x_min!, x_max: lane.crop_x_max!, y_min: lane.crop_y_min!, y_max: lane.crop_y_max! });
      setAnalysisCode(lane.analysis_code);
    } else if (selected) {
      setBox(null);
      setAnalysisCode("");
    }
  }, [lane, selected]);

  const saveLane = useSave(
    () =>
      api.upsertWeightLane(selected!, {
        analysis_code: analysisCode,
        crop_x_min: box!.x_min,
        crop_x_max: box!.x_max,
        crop_y_min: box!.y_min,
        crop_y_max: box!.y_max,
      }),
    "Đã lưu vùng crop (chưa áp dụng)",
  );
  const deleteLane = useSave((trigger: string) => api.deleteLane("weight", trigger), "Đã xoá làn");

  if (analyzer.isLoading) return <Spin />;
  const outside = box && (box.x_max > size.width || box.y_max > size.height);

  return (
    <Row gutter={16}>
      <Col xs={24} xl={15}>
        <Card
          size="small"
          title={
            <Space>
              Vùng màn hình cân
              <Typography.Text type="secondary">camera {camera?.code ?? "?"}</Typography.Text>
            </Space>
          }
          extra={
            <Button size="small" icon={<CameraOutlined />} loading={loading} onClick={() => setNonce(Date.now())}>
              Chụp ảnh
            </Button>
          }
        >
          <Table<AnalyzerLane>
            size="small"
            rowKey="trigger_code"
            pagination={false}
            dataSource={lanes}
            rowSelection={{ type: "radio", selectedRowKeys: selected ? [selected] : [], onChange: (keys) => setSelected(String(keys[0])) }}
            onRow={(row) => ({ onClick: () => setSelected(row.trigger_code) })}
            columns={[
              { title: <FieldLabel label="Trigger code" field="crops" />, dataIndex: "trigger_code" },
              { title: <FieldLabel label="Mã kết quả" field="analysis_codes" />, dataIndex: "analysis_code" },
              {
                title: "[x_min, x_max, y_min, y_max]",
                render: (_, row) => <code>[{row.crop_x_min}, {row.crop_x_max}, {row.crop_y_min}, {row.crop_y_max}]</code>,
              },
              {
                title: "",
                render: (_, row) =>
                  isAdmin && (
                    <Popconfirm title={`Xoá làn ${row.trigger_code}?`} onConfirm={() => deleteLane.mutate(row.trigger_code)}>
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
              },
            ]}
          />
          {isAdmin && (
            <Space style={{ margin: "8px 0" }}>
              <Input placeholder="trigger code mới, ví dụ in_gate2_lane1" value={newTrigger} onChange={(e) => setNewTrigger(e.target.value)} />
              <Button
                icon={<PlusOutlined />}
                disabled={!newTrigger || lanes.some((l) => l.trigger_code === newTrigger)}
                onClick={() => {
                  setSelected(newTrigger);
                  setBox({ x_min: 0, x_max: Math.round(size.width / 4), y_min: 0, y_max: Math.round(size.height / 4) });
                  setNewTrigger("");
                }}
              >
                Thêm làn
              </Button>
            </Space>
          )}
          {failed && <Alert type="warning" showIcon message="Không lấy được ảnh camera cân" style={{ marginBottom: 8 }} />}
          {selected && (
            <Space direction="vertical" style={{ width: "100%" }}>
              <RectEditor image={image} width={size.width} height={size.height} box={box} color="#D85A30" readOnly={!isAdmin} onChange={setBox} />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Toạ độ theo độ phân giải gốc của camera cân ({size.width}×{size.height}). Kéo trên ảnh để vẽ, kéo góc để chỉnh.
              </Typography.Text>
              {outside && <Alert type="error" showIcon message="Vùng crop vượt ra ngoài frame camera; weight_reader sẽ báo lỗi." />}
              {box && (
                <Space wrap>
                  {(["x_min", "x_max", "y_min", "y_max"] as const).map((key) => (
                    <InputNumber
                      key={key}
                      addonBefore={key}
                      min={0}
                      value={box[key]}
                      disabled={!isAdmin}
                      onChange={(value) => value !== null && setBox({ ...box, [key]: value })}
                      style={{ width: 150 }}
                    />
                  ))}
                  <Input
                    addonBefore="analysis_code"
                    value={analysisCode}
                    disabled={!isAdmin}
                    onChange={(e) => setAnalysisCode(e.target.value)}
                    style={{ width: 300 }}
                  />
                  {isAdmin && (
                    <Button
                      type="primary"
                      icon={<SaveOutlined />}
                      disabled={!analysisCode || box.x_min >= box.x_max || box.y_min >= box.y_max}
                      loading={saveLane.isPending}
                      onClick={() => saveLane.mutate(undefined)}
                    >
                      Lưu làn {selected}
                    </Button>
                  )}
                </Space>
              )}
            </Space>
          )}
        </Card>
      </Col>
      <Col xs={24} xl={9}>
        <AnalyzerSettingsForm
          analyzer="weight"
          settings={settings}
          main={
            <>
              <Form.Item name="camera_id" label={<FieldLabel label="Camera màn hình cân" field="camera_url_env" />}>
                <Select
                  options={(cameras.data ?? [])
                    .filter((item) => item.purpose === "weight")
                    .map((item) => ({ value: item.id, label: `${item.code} · ${item.rtsp_url_masked}` }))}
                />
              </Form.Item>
              <Form.Item name="confidence" label={<FieldLabel label="Ngưỡng tin cậy chữ số" field="confidence" />}>
                <ConfidenceInput />
              </Form.Item>
              <Row gutter={12}>
                {numberField("Thời gian đọc tối đa (s)", "max_seconds", 1)}
                {numberField("Số lần sụt tối đa", "max_drop_count", 1)}
                {numberField("Ngưỡng sụt số cân", "drop_threshold", 0, 100)}
              </Row>
            </>
          }
          modelFields={
            <Col span={24}>
              <Form.Item name="model" label={<FieldLabel label="Model Triton" field="model" />} rules={[{ required: true }]}>
                <Select options={tritonOptions(models.data?.triton, [settings.model])} />
              </Form.Item>
            </Col>
          }
        />
      </Col>
    </Row>
  );
}
