import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  CameraOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SaveOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import { api, Point, Site, SiteFields } from "../api";
import { useAuth } from "../auth";
import { FieldLabel, useSave } from "../components/common";
import ConfidenceInput from "../components/ConfidenceInput";
import { PolygonEditor, useImage } from "../components/ShapeEditor";
import { polygonWarnings } from "../geometry";

// traffic_core/deepstream.py: streammux width/height. ROI coordinates live in this space.
const STREAM_WIDTH = 2688;
const STREAM_HEIGHT = 1520;
const COLORS = { capture: "#1D9E75", inference: "#7F77DD" } as const;
type Kind = keyof typeof COLORS;

export default function SitesPage() {
  const { code } = useParams();
  const navigate = useNavigate();
  const sites = useQuery({ queryKey: ["sites"], queryFn: api.sites });

  useEffect(() => {
    if (!code && sites.data?.length) navigate(`/sites/${sites.data[0].code}`, { replace: true });
  }, [code, sites.data, navigate]);

  if (sites.isLoading) return <Spin />;
  if (!sites.data?.length) return <Empty description="Chưa có làn xe. Chạy lệnh import để nạp cấu hình hiện có." />;
  const site = sites.data.find((item) => item.code === code);

  return (
    <>
      <Tabs
        activeKey={code}
        onChange={(key) => navigate(`/sites/${key}`)}
        items={sites.data.map((item) => ({ key: item.code, label: item.code }))}
        tabBarStyle={{ marginBottom: 12 }}
      />
      {/* Keyed by code only: saving one zone must not discard unsaved edits of the other. */}
      {site ? <SiteEditor key={site.code} site={site} /> : <Spin />}
    </>
  );
}

function SiteEditor({ site }: { site: Site }) {
  const { isAdmin } = useAuth();
  const [sourceIndex, setSourceIndex] = useState(0);
  const source = site.sources.find((item) => item.source_index === sourceIndex) ?? site.sources[0];
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const laneCameras = (cameras.data ?? []).filter((camera) => camera.purpose === "lane");
  const [adding, setAdding] = useState(false);
  const addSource = useSave(
    (data: { camera_id: number; trigger_code: string }) => api.upsertSource(site.code, site.sources.length, data),
    "Đã thêm nguồn",
  );
  const removeSource = useSave(() => api.deleteSource(site.code, site.sources.length - 1), "Đã xoá nguồn");

  return (
    <Row gutter={16}>
      <Col xs={24} xl={15}>
        <Card
          size="small"
          title={
            <Space>
              Nguồn camera
              <Segmented
                size="small"
                value={source?.source_index}
                onChange={(value) => setSourceIndex(Number(value))}
                options={site.sources.map((item) => ({ value: item.source_index, label: `Nguồn ${item.source_index}` }))}
              />
            </Space>
          }
          extra={
            isAdmin && (
              <Space>
                {site.sources.length < 4 && (
                  <Button size="small" icon={<PlusOutlined />} onClick={() => setAdding(true)}>
                    Thêm nguồn
                  </Button>
                )}
                {site.sources.length > 1 && (
                  <Popconfirm title={`Xoá nguồn ${site.sources.length - 1}?`} onConfirm={() => removeSource.mutate(undefined)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                )}
              </Space>
            )
          }
        >
          {adding && (
            <Form
              layout="inline"
              style={{ marginBottom: 12 }}
              onFinish={(values) => addSource.mutate(values, { onSuccess: () => setAdding(false) })}
            >
              <Form.Item name="camera_id" rules={[{ required: true, message: "Chọn camera" }]}>
                <Select style={{ width: 220 }} placeholder="Camera" options={laneCameras.map((c) => ({ value: c.id, label: c.code }))} />
              </Form.Item>
              <Form.Item name="trigger_code" rules={[{ required: true, message: "Nhập trigger code" }]}>
                <Input placeholder="trigger code" />
              </Form.Item>
              <Button htmlType="submit" type="primary" loading={addSource.isPending}>
                Thêm
              </Button>
              <Button type="link" onClick={() => setAdding(false)}>
                Huỷ
              </Button>
              <Alert
                type="info"
                style={{ marginTop: 8, width: "100%" }}
                message="Compose của detector phải truyền thêm biến môi trường của camera mới; nếu chưa, bước Áp dụng sẽ báo lỗi."
              />
            </Form>
          )}
          {source && <ZoneEditor site={site} sourceIndex={source.source_index} key={source.source_index} />}
        </Card>
      </Col>
      <Col xs={24} xl={9}>
        <SiteForm site={site} />
      </Col>
    </Row>
  );
}

function ZoneEditor({ site, sourceIndex }: { site: Site; sourceIndex: number }) {
  const { isAdmin } = useAuth();
  const { message } = App.useApp();
  const source = site.sources.find((item) => item.source_index === sourceIndex)!;
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const laneCameras = (cameras.data ?? []).filter((camera) => camera.purpose === "lane");
  const [kind, setKind] = useState<Kind>("capture");
  const [zones, setZones] = useState<Record<Kind, Point[]>>({
    capture: source.capture_zone,
    inference: source.inference_zone,
  });
  const [history, setHistory] = useState<Record<Kind, Point[][]>>({ capture: [], inference: [] });
  const [drawing, setDrawing] = useState(false);
  const [nonce, setNonce] = useState(() => Date.now());
  const [jsonText, setJsonText] = useState("");
  const { image, failed, loading } = useImage(api.snapshotUrl(source.camera_id, nonce));

  const dirty = {
    capture: JSON.stringify(zones.capture) !== JSON.stringify(source.capture_zone),
    inference: JSON.stringify(zones.inference) !== JSON.stringify(source.inference_zone),
  };
  const warnings = useMemo(() => polygonWarnings(zones.capture, zones.inference), [zones]);

  useEffect(() => setJsonText(JSON.stringify(zones[kind])), [zones, kind]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const change = (points: Point[]) => {
    setHistory((current) => ({ ...current, [kind]: [...current[kind].slice(-49), zones[kind]] }));
    setZones((current) => ({ ...current, [kind]: points }));
  };
  const undo = () => {
    const stack = history[kind];
    if (!stack.length) return;
    setZones((current) => ({ ...current, [kind]: stack[stack.length - 1] }));
    setHistory((current) => ({ ...current, [kind]: stack.slice(0, -1) }));
  };

  const saveZone = useSave(
    (target: Kind) => api.setZone(site.code, sourceIndex, target, zones[target]),
    "Đã lưu vùng (chưa áp dụng)",
  );
  const saveSource = useSave(
    (data: { camera_id: number; trigger_code: string }) => api.upsertSource(site.code, sourceIndex, data),
  );

  const applyJson = () => {
    try {
      const parsed = JSON.parse(jsonText);
      if (!Array.isArray(parsed) || parsed.some((p) => !Array.isArray(p) || p.length !== 2)) throw new Error();
      change(parsed.map(([x, y]: [number, number]) => [Math.round(x), Math.round(y)] as Point));
    } catch {
      message.error("JSON phải có dạng [[x, y], [x, y], ...]");
    }
  };

  const other: Kind = kind === "capture" ? "inference" : "capture";

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Form
        layout="inline"
        initialValues={{ camera_id: source.camera_id, trigger_code: source.trigger_code }}
        onFinish={(values) => saveSource.mutate(values)}
        disabled={!isAdmin}
      >
        <Form.Item name="camera_id" label="Camera">
          <Select style={{ width: 220 }} options={laneCameras.map((c) => ({ value: c.id, label: `${c.code} · ${c.rtsp_url_masked}` }))} />
        </Form.Item>
        <Form.Item name="trigger_code" label={<FieldLabel label="Trigger code" field="trigger_codes" />} rules={[{ required: true }]}>
          <Input style={{ width: 170 }} />
        </Form.Item>
        {isAdmin && (
          <Button htmlType="submit" loading={saveSource.isPending}>
            Lưu nguồn
          </Button>
        )}
      </Form>

      <Space wrap>
        <Segmented
          value={kind}
          onChange={(value) => {
            setDrawing(false);
            setKind(value as Kind);
          }}
          options={[
            { value: "capture", label: <span style={{ color: COLORS.capture }}>Capture{dirty.capture ? " •" : ""}</span> },
            { value: "inference", label: <span style={{ color: COLORS.inference }}>Inference{dirty.inference ? " •" : ""}</span> },
          ]}
        />
        <Button icon={<CameraOutlined />} loading={loading} onClick={() => setNonce(Date.now())}>
          Chụp ảnh
        </Button>
        {isAdmin && (
          <>
            <Button icon={<EditOutlined />} type={drawing ? "primary" : "default"} onClick={() => setDrawing(!drawing)}>
              {drawing ? "Đang vẽ… (Enter để xong)" : "Vẽ lại"}
            </Button>
            <Tooltip title="Ctrl+Z">
              <Button icon={<UndoOutlined />} disabled={!history[kind].length} onClick={undo} />
            </Tooltip>
            <Button
              onClick={() => setZones((current) => ({ ...current, [kind]: kind === "capture" ? source.capture_zone : source.inference_zone }))}
              disabled={!dirty[kind]}
            >
              Bỏ thay đổi
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              disabled={!dirty[kind] || zones[kind].length < 3}
              loading={saveZone.isPending}
              onClick={() => saveZone.mutate(kind)}
            >
              Lưu vùng {kind}
            </Button>
          </>
        )}
      </Space>

      {failed && <Alert type="warning" showIcon message="Không lấy được ảnh từ camera; vẫn có thể sửa toạ độ trên nền trống." />}

      <PolygonEditor
        image={image}
        width={STREAM_WIDTH}
        height={STREAM_HEIGHT}
        points={zones[kind]}
        color={COLORS[kind]}
        dashed={kind === "inference"}
        overlays={[{ points: zones[other], color: COLORS[other], dashed: other === "inference" }]}
        readOnly={!isAdmin}
        drawing={drawing}
        onDrawingDone={() => setDrawing(false)}
        onChange={change}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Khung {STREAM_WIDTH}×{STREAM_HEIGHT} (streammux). Kéo điểm để di chuyển · nhấp đúp lên cạnh để thêm điểm · chuột phải vào điểm
        để xoá · "Vẽ lại": click từng điểm, click điểm đầu hoặc Enter để đóng, Backspace bỏ điểm cuối, Esc huỷ.
      </Typography.Text>

      {warnings.map((warning) => (
        <Alert key={warning} type="warning" showIcon message={warning} />
      ))}

      <Collapse
        size="small"
        items={[
          {
            key: "json",
            label: `Toạ độ vùng ${kind} (${zones[kind].length} điểm)`,
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Input.TextArea value={jsonText} onChange={(e) => setJsonText(e.target.value)} autoSize={{ minRows: 2 }} disabled={!isAdmin} />
                {isAdmin && <Button onClick={applyJson}>Dùng toạ độ này</Button>}
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );
}

function SiteForm({ site }: { site: Site }) {
  const { isAdmin } = useAuth();
  const [form] = Form.useForm<SiteFields>();
  const models = useQuery({ queryKey: ["models"], queryFn: api.modelOptions });
  const plateLists = useQuery({ queryKey: ["plate-lists"], queryFn: api.plateLists });
  const save = useSave((values: SiteFields) => api.updateSite(site.code, values), "Đã lưu tham số (chưa áp dụng)");

  const inference = (models.data?.inference ?? []).map((value) => ({ value, label: value.replace("/home/jetson/models/", "") }));
  const tracker = (models.data?.tracker ?? []).map((value) => ({ value, label: value.replace("/home/jetson/models/", "") }));

  return (
    <Card
      size="small"
      title={
        <Space>
          Tham số nhận diện <Tag>{site.name}</Tag>
        </Space>
      }
    >
      <Form form={form} layout="vertical" initialValues={site} onFinish={(values) => save.mutate(values)} disabled={!isAdmin}>
        <Form.Item name="name" label={<FieldLabel label="Tên site" field="name" />} rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="vehicle_confidence" label={<FieldLabel label="Ngưỡng tin cậy phát hiện xe" field="vehicle_confidence" />}>
          <ConfidenceInput />
        </Form.Item>
        <Form.Item
          name="plate_character_confidence"
          label={<FieldLabel label="Ngưỡng tin cậy ký tự biển số" field="plate_character_confidence" />}
        >
          <ConfidenceInput />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item
              name="plate_collection_seconds"
              label={<FieldLabel label="Thời gian gom biển số (s)" field="plate_collection_seconds" />}
              rules={[{ required: true }]}
            >
              <InputNumber min={0.1} step={0.5} style={{ width: "100%" }} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="reference_plate_list_id" label={<FieldLabel label="Biển số tham chiếu" field="reference_plate_path" />}>
              <Select options={(plateLists.data ?? []).map((l) => ({ value: l.id, label: `${l.name} (${l.plate_count})` }))} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="model_vehicle_detection" label={<FieldLabel label="Model phát hiện xe" field="models.vehicle_detection" />}>
          <Select options={inference} showSearch />
        </Form.Item>
        <Collapse
          size="small"
          ghost
          items={[
            {
              key: "advanced",
              label: "Nâng cao",
              forceRender: true,
              children: (
                <>
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item name="video_fps" label={<FieldLabel label="FPS video lưu" field="video_fps" />} rules={[{ required: true }]}>
                        <InputNumber min={1} max={60} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="publish_timeout_seconds" label={<FieldLabel label="Publish timeout (s)" field="publish_timeout_seconds" />}>
                        <InputNumber min={1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={16}>
                      <Form.Item name="output_topic" label={<FieldLabel label="Kafka topic" field="output_topic" />} rules={[{ required: true }]}>
                        <Input />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name="output_partition" label={<FieldLabel label="Partition" field="output_partition" />}>
                        <InputNumber min={0} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item name="model_tracker" label={<FieldLabel label="Tracker" field="models.tracker" />}>
                    <Select options={tracker} />
                  </Form.Item>
                  <Form.Item name="model_plate_detection" label={<FieldLabel label="Model phát hiện biển số" field="models.plate_detection" />}>
                    <Select options={inference} showSearch />
                  </Form.Item>
                  <Form.Item name="model_plate_recognition" label={<FieldLabel label="Model đọc ký tự" field="models.plate_recognition" />}>
                    <Select options={inference} showSearch />
                  </Form.Item>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Thư mục ảnh xe: <code>{site.vehicle_image_root}</code> (gắn với volume Docker, không sửa ở đây)
                  </Typography.Text>
                </>
              ),
            },
          ]}
        />
        {isAdmin && (
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending} style={{ marginTop: 12 }}>
            Lưu tham số
          </Button>
        )}
      </Form>
    </Card>
  );
}
