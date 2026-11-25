import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Modal,
  Progress,
  Row,
  Slider,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CaretRightOutlined,
  LeftOutlined,
  PauseOutlined,
  ReloadOutlined,
  RightOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import { api, PlateCheckFrame, PlateCheckSummary, PlateCheckVehicle } from "../api";

const LIST_INTERVAL_MS = 5000;
const PENDING_DETAIL_INTERVAL_MS = 3000;
const PLAY_INTERVAL_MS = 250;
const EMPTY_IMAGE = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";

// Row colours of the frame table; antd paints <td>, so the style targets cells.
const PAGE_CSS = `
.plate-check-row-match > td { background: #f6ffed !important; }
.plate-check-row-mismatch > td { background: #fff1f0 !important; }
.plate-check-row-current > td { box-shadow: inset 0 2px 0 #1677ff, inset 0 -2px 0 #1677ff; font-weight: 600; }
.plate-check-card.ant-card-hoverable:hover { border-color: #1677ff; }
`;

function formatTs(value: number | string | null | undefined, withDate = true) {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = typeof value === "number" ? dayjs(value > 1e12 ? value : value * 1000) : dayjs(value);
  if (!parsed.isValid()) return String(value);
  return parsed.format(withDate ? "HH:mm:ss.SSS DD/MM/YYYY" : "HH:mm:ss.SSS");
}

function duration(first: number | string | null, last: number | string | null) {
  if (typeof first !== "number" || typeof last !== "number") return null;
  const seconds = last - first > 1e6 ? (last - first) / 1000 : last - first;
  return `${seconds.toFixed(1)} s`;
}

/** Final plate of the vehicle, or the most voted valid frame label while it is still being collected. */
function finalPlate(summary: PlateCheckSummary | null | undefined, frames?: PlateCheckFrame[]) {
  if (summary) return { plate: summary.plate ?? null, provisional: false };
  if (!frames) return { plate: null, provisional: true };
  const counts = new Map<string, number>();
  for (const frame of frames) {
    if (frame.valid_plate && frame.plate_label) counts.set(frame.plate_label, (counts.get(frame.plate_label) ?? 0) + 1);
  }
  let best: string | null = null;
  for (const [label, count] of counts) if (best === null || count > (counts.get(best) ?? 0)) best = label;
  return { plate: best, provisional: true };
}

function PlateText({ value }: { value: string | null | undefined }) {
  return value ? (
    <Typography.Text strong code style={{ fontSize: 15 }}>
      {value}
    </Typography.Text>
  ) : (
    <Typography.Text type="secondary">—</Typography.Text>
  );
}

function VotesTable({ votes, plate }: { votes: Record<string, number> | undefined; plate: string | null | undefined }) {
  const rows = Object.entries(votes ?? {})
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count);
  if (rows.length === 0) return <Typography.Text type="secondary">Không có phiếu bầu</Typography.Text>;
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  return (
    <Table
      size="small"
      rowKey="label"
      pagination={false}
      dataSource={rows}
      rowClassName={(row) => (row.label === plate ? "plate-check-row-match" : "")}
      columns={[
        { title: "Chuỗi đọc", dataIndex: "label", render: (label: string) => <Typography.Text code>{label}</Typography.Text> },
        { title: "Số frame", dataIndex: "count", width: 80, align: "right" },
        {
          title: "Tỉ lệ",
          width: 110,
          render: (_: unknown, row) => <Progress percent={Math.round((row.count * 100) / total)} size="small" />,
        },
      ]}
    />
  );
}

function VehicleCard({ vehicle, onOpen }: { vehicle: PlateCheckVehicle; onOpen: () => void }) {
  const summary = vehicle.summary;
  const percent = vehicle.frame_count ? Math.round((vehicle.valid_frame_count * 100) / vehicle.frame_count) : 0;
  return (
    <Card
      hoverable
      className="plate-check-card"
      onClick={onOpen}
      cover={
        vehicle.capture_image_path ? (
          <img
            alt="Ảnh capture"
            src={api.resultImageUrl(vehicle.capture_image_path, 480)}
            onError={(event) => (event.currentTarget.src = EMPTY_IMAGE)}
            style={{ aspectRatio: "16 / 9", objectFit: "contain", background: "#f5f5f5" }}
          />
        ) : (
          <div
            style={{
              aspectRatio: "16 / 9",
              background: "#f5f5f5",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Typography.Text type="secondary">{summary ? "Không có ảnh capture" : "Đang thu thập…"}</Typography.Text>
          </div>
        )
      }
    >
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Space wrap>
          {summary ? <Tag color="success">Đã publish</Tag> : <Tag color="processing">Đang thu thập</Tag>}
          {summary?.requires_search && <Tag color="warning">Cần tra cứu</Tag>}
          <Typography.Text type="secondary">{formatTs(summary?.captured_at ?? vehicle.last_ts)}</Typography.Text>
        </Space>
        <Descriptions size="small" column={1} colon={false} labelStyle={{ width: 90 }}>
          <Descriptions.Item label="Biển cuối">
            <PlateText value={summary?.plate} />
          </Descriptions.Item>
          <Descriptions.Item label="Raw">
            <PlateText value={summary?.raw_plate} />
          </Descriptions.Item>
          <Descriptions.Item label="Best match">
            <Space>
              <PlateText value={summary?.best_match} />
              {typeof summary?.match_score === "number" && (
                <Typography.Text type="secondary">{summary.match_score.toFixed(2)}</Typography.Text>
              )}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="Frame có biển">
            <Tooltip title={`${vehicle.valid_frame_count}/${vehicle.frame_count} frame có biển hợp lệ`}>
              <div style={{ width: "100%" }}>
                <Progress percent={percent} size="small" format={() => `${vehicle.valid_frame_count}/${vehicle.frame_count}`} />
              </div>
            </Tooltip>
          </Descriptions.Item>
        </Descriptions>
        <VotesTable votes={summary?.plate_votes} plate={summary?.plate} />
        <Typography.Text type="secondary" copyable={{ text: vehicle.section_id }} style={{ fontSize: 12 }}>
          {vehicle.section_id.slice(0, 8)}
        </Typography.Text>
      </Space>
    </Card>
  );
}

function rowClass(frame: PlateCheckFrame, plate: string | null) {
  if (!plate || !frame.plate_label) return "";
  return frame.plate_label === plate ? "plate-check-row-match" : "plate-check-row-mismatch";
}

function FrameViewer({ lane, sectionId }: { lane: string; sectionId: string }) {
  const detail = useQuery({
    queryKey: ["plate-check", lane, sectionId],
    queryFn: () => api.plateCheckVehicle(lane, sectionId),
    refetchInterval: (query) => (query.state.data?.summary ? false : PENDING_DETAIL_INTERVAL_MS),
  });
  const [onlyValid, setOnlyValid] = useState(false);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);

  const frames = detail.data?.frames ?? [];
  const visible = useMemo(() => (onlyValid ? frames.filter((frame) => frame.valid_plate) : frames), [frames, onlyValid]);
  const { plate, provisional } = finalPlate(detail.data?.summary, frames);
  const last = Math.max(visible.length - 1, 0);
  const current = visible[Math.min(index, last)];
  const captureFrame = detail.data?.summary?.capture_frame ?? null;

  useEffect(() => setIndex((value) => Math.min(value, last)), [last]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setIndex((value) => {
        if (value >= last) {
          setPlaying(false);
          return value;
        }
        return value + 1;
      });
    }, PLAY_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [playing, last]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setPlaying(false);
        setIndex((value) => Math.max(0, value - 1));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        setPlaying(false);
        setIndex((value) => Math.min(last, value + 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last]);

  // Warm the browser cache for the next frames so playback does not flicker.
  useEffect(() => {
    for (const frame of visible.slice(index + 1, index + 4)) {
      if (frame.image_path) new Image().src = api.resultImageUrl(frame.image_path);
    }
  }, [visible, index]);

  if (detail.isLoading) return <Spin style={{ display: "block", margin: 40 }} />;
  if (detail.error) return <Alert type="error" showIcon message={(detail.error as Error).message} />;

  const columns: ColumnsType<PlateCheckFrame> = [
    {
      title: "Frame",
      dataIndex: "frame_num",
      width: 90,
      render: (value: number | null) => (
        <Space size={4}>
          {value ?? "—"}
          {value !== null && value === captureFrame && <Tag color="blue">capture</Tag>}
        </Space>
      ),
    },
    { title: "Thời điểm", dataIndex: "ts", width: 120, render: (value) => formatTs(value, false) },
    {
      title: "Biển đọc",
      dataIndex: "plate_label",
      render: (value: string | null) => (value ? <Typography.Text code>{value}</Typography.Text> : "—"),
    },
    {
      title: "Hợp lệ",
      dataIndex: "valid_plate",
      width: 70,
      render: (value: boolean) => (value ? <Tag color="success">có</Tag> : <Tag>không</Tag>),
    },
    {
      title: "Trong vùng",
      dataIndex: "in_capture_zone",
      width: 90,
      render: (value: boolean) => (value ? <Tag color="blue">có</Tag> : <Tag>không</Tag>),
    },
    {
      title: "Conf ký tự (min)",
      dataIndex: "plate_confidences",
      width: 110,
      render: (value: number[] | null) => (value && value.length ? Math.min(...value).toFixed(2) : "—"),
    },
    {
      title: "Conf xe",
      dataIndex: "vehicle_confidence",
      width: 80,
      render: (value: number | null) => (typeof value === "number" ? value.toFixed(2) : "—"),
    },
  ];

  const summary = detail.data?.summary;
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap size={16}>
        <span>
          Biển cuối: <PlateText value={plate} /> {provisional && plate && <Tag color="processing">tạm tính</Tag>}
        </span>
        <span>
          Raw: <PlateText value={summary?.raw_plate} />
        </span>
        <span>
          Best match: <PlateText value={summary?.best_match} />
          {typeof summary?.match_score === "number" && ` (${summary.match_score.toFixed(2)})`}
        </span>
        <Typography.Text type="secondary">
          {frames.length} frame, {frames.filter((frame) => frame.valid_plate).length} có biển, thời gian{" "}
          {duration(frames[0]?.ts ?? null, frames[frames.length - 1]?.ts ?? null) ?? "—"}
        </Typography.Text>
      </Space>

      {visible.length === 0 ? (
        <Empty description={onlyValid ? "Không có frame nào có biển hợp lệ" : "Chưa có frame"} />
      ) : (
        <Row gutter={16}>
          <Col xs={24} lg={15}>
            <div
              style={{
                background: "#141414",
                borderRadius: 6,
                height: "52vh",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                overflow: "hidden",
              }}
            >
              {current?.image_path ? (
                <img
                  alt={`Frame ${current.frame_num}`}
                  src={api.resultImageUrl(current.image_path)}
                  style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
                />
              ) : (
                <Typography.Text style={{ color: "#bbb" }}>Không có ảnh cho frame này</Typography.Text>
              )}
            </div>
          </Col>
          <Col xs={24} lg={9}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="Frame">
                {current?.frame_num ?? "—"} ({Math.min(index, last) + 1}/{visible.length})
              </Descriptions.Item>
              <Descriptions.Item label="Thời điểm">{formatTs(current?.ts)}</Descriptions.Item>
              <Descriptions.Item label="Biển đọc">
                <Space>
                  <PlateText value={current?.plate_label} />
                  {current?.plate_label && plate && (
                    <Tag color={current.plate_label === plate ? "success" : "error"}>
                      {current.plate_label === plate ? "khớp biển cuối" : "khác biển cuối"}
                    </Tag>
                  )}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="Biển hợp lệ">{current?.valid_plate ? "có" : "không"}</Descriptions.Item>
              <Descriptions.Item label="Trong vùng capture">{current?.in_capture_zone ? "có" : "không"}</Descriptions.Item>
              <Descriptions.Item label="Conf xe">
                {typeof current?.vehicle_confidence === "number" ? current.vehicle_confidence.toFixed(3) : "—"}
              </Descriptions.Item>
              <Descriptions.Item label="Conf từng ký tự">
                {current?.plate_confidences?.length ? (
                  <Space size={2} wrap>
                    {current.plate_confidences.map((value, i) => (
                      <Tooltip key={i} title={current.plate_label?.[i] ?? ""}>
                        <Tag color={value < 0.5 ? "error" : value < 0.8 ? "warning" : "default"} style={{ marginInlineEnd: 0 }}>
                          {current.plate_label?.[i] ?? "?"} {value.toFixed(2)}
                        </Tag>
                      </Tooltip>
                    ))}
                  </Space>
                ) : (
                  "—"
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Box">{current?.box ? current.box.join(", ") : "—"}</Descriptions.Item>
            </Descriptions>
          </Col>
        </Row>
      )}

      <Space wrap align="center" style={{ width: "100%" }}>
        <Tooltip title="Frame trước (←)">
          <Button
            icon={<LeftOutlined />}
            disabled={index <= 0}
            onClick={() => {
              setPlaying(false);
              setIndex((value) => Math.max(0, value - 1));
            }}
          />
        </Tooltip>
        <Button
          type="primary"
          icon={playing ? <PauseOutlined /> : <CaretRightOutlined />}
          disabled={visible.length < 2}
          onClick={() => {
            if (!playing && index >= last) setIndex(0);
            setPlaying((value) => !value);
          }}
        >
          {playing ? "Dừng" : "Phát"}
        </Button>
        <Tooltip title="Frame sau (→)">
          <Button
            icon={<RightOutlined />}
            disabled={index >= last}
            onClick={() => {
              setPlaying(false);
              setIndex((value) => Math.min(last, value + 1));
            }}
          />
        </Tooltip>
        <Space>
          <Switch
            checked={onlyValid}
            onChange={(checked) => {
              setOnlyValid(checked);
              setIndex(0);
              setPlaying(false);
            }}
          />
          Chỉ frame có biển
        </Space>
      </Space>
      <Slider
        min={0}
        max={last}
        value={Math.min(index, last)}
        disabled={visible.length < 2}
        onChange={(value) => {
          setPlaying(false);
          setIndex(value);
        }}
        tooltip={{ formatter: (value) => `Frame ${visible[value ?? 0]?.frame_num ?? ""}` }}
      />

      <Table
        size="small"
        rowKey={(frame) => String(frame.frame_num)}
        dataSource={visible}
        columns={columns}
        pagination={false}
        scroll={{ y: 260 }}
        rowClassName={(frame, i) =>
          [rowClass(frame, plate), i === Math.min(index, last) ? "plate-check-row-current" : ""].join(" ")
        }
        onRow={(_, i) => ({
          onClick: () => {
            setPlaying(false);
            setIndex(i ?? 0);
          },
          style: { cursor: "pointer" },
        })}
      />
    </Space>
  );
}

export default function PlateCheckPage() {
  const list = useQuery({ queryKey: ["plate-check"], queryFn: api.plateCheck, refetchInterval: LIST_INTERVAL_MS });
  const [activeLane, setActiveLane] = useState<string>();
  const [selected, setSelected] = useState<{ lane: string; sectionId: string } | null>(null);
  const lanes = list.data?.lanes ?? [];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <style>{PAGE_CSS}</style>
      <Space style={{ width: "100%", justifyContent: "space-between" }} wrap>
        <Typography.Text type="secondary">
          Mọi frame đọc biển của 5 xe gần nhất mỗi làn. Bấm vào một xe để xem từng frame.
        </Typography.Text>
        <Button icon={<ReloadOutlined />} loading={list.isFetching} onClick={() => void list.refetch()}>
          Tải lại
        </Button>
      </Space>
      {list.error && <Alert type="error" showIcon message={(list.error as Error).message} />}
      {list.isLoading ? (
        <Spin style={{ display: "block", margin: 40 }} />
      ) : lanes.length === 0 ? (
        <Empty description="Chưa có dữ liệu frame nào trong runtime/vehicle/<lane>/frames" />
      ) : (
        <Tabs
          activeKey={activeLane ?? lanes[0].lane}
          onChange={setActiveLane}
          items={lanes.map((lane) => ({
            key: lane.lane,
            label: (
              <Space size={6}>
                {lane.lane}
                <Tag style={{ marginInlineEnd: 0 }}>{lane.vehicles.length}</Tag>
              </Space>
            ),
            children:
              lane.vehicles.length === 0 ? (
                <Empty description="Làn này chưa có xe nào" />
              ) : (
                <Row gutter={[16, 16]}>
                  {lane.vehicles.map((vehicle) => (
                    <Col key={vehicle.section_id} xs={24} md={12} xl={8} xxl={6}>
                      <VehicleCard
                        vehicle={vehicle}
                        onOpen={() => setSelected({ lane: lane.lane, sectionId: vehicle.section_id })}
                      />
                    </Col>
                  ))}
                </Row>
              ),
          }))}
        />
      )}
      <Modal
        open={selected !== null}
        onCancel={() => setSelected(null)}
        footer={null}
        width={1280}
        style={{ top: 20 }}
        destroyOnClose
        title={selected ? `${selected.lane} · ${selected.sectionId}` : ""}
      >
        {selected && <FrameViewer key={`${selected.lane}/${selected.sectionId}`} {...selected} />}
      </Modal>
    </Space>
  );
}
