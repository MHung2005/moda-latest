import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Alert,
  Card,
  Col,
  DatePicker,
  Image,
  Input,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { Dayjs } from "dayjs";
import { AnalysisOutput, api, ResultFilter, VehicleResult } from "../api";

type Mode = "all" | "unmatched" | "no_plate";
const LIVE_INTERVAL_MS = 3000;
const THUMB_WIDTH = 160;

function formatTime(value: string | null | undefined) {
  return value ? dayjs(value).format("HH:mm:ss DD/MM/YYYY") : "—";
}

function Thumb({ path, width = THUMB_WIDTH }: { path: string | null; width?: number }) {
  if (!path) return <Typography.Text type="secondary">Không có ảnh</Typography.Text>;
  return (
    <Image
      width={width}
      src={api.resultImageUrl(path, 320)}
      preview={{ src: api.resultImageUrl(path) }}
      fallback="data:image/gif;base64,R0lGODlhAQABAAAAACw="
      style={{ borderRadius: 4, objectFit: "cover", aspectRatio: "16 / 9", background: "#f5f5f5" }}
    />
  );
}

function AnalysisCell({
  result,
  failure,
  pendingSince,
}: {
  result: AnalysisOutput | null;
  failure?: { error: string; failed_at: string | null };
  pendingSince: string;
}) {
  if (result) {
    return (
      <Space direction="vertical" size={4}>
        <Typography.Text strong>{result.output ?? "—"}</Typography.Text>
        {result.image_path && <Thumb path={result.image_path} width={110} />}
      </Space>
    );
  }
  if (failure) {
    return (
      <Tooltip title={`${failure.error}${failure.failed_at ? ` (${formatTime(failure.failed_at)})` : ""}`}>
        <Tag color="error">Lỗi</Tag>
      </Tooltip>
    );
  }
  const waiting = dayjs().diff(dayjs(pendingSince), "minute") < 2;
  return waiting ? <Tag color="processing">Đang xử lý</Tag> : <Typography.Text type="secondary">—</Typography.Text>;
}

export default function ResultsPage() {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().startOf("day"), dayjs().startOf("day")]);
  const [lane, setLane] = useState<string | undefined>();
  const [plate, setPlate] = useState("");
  const [mode, setMode] = useState<Mode>("all");
  const [live, setLive] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const dateFilter = useMemo(
    () => ({
      date_from: range[0].startOf("day").toISOString(),
      date_to: range[1].add(1, "day").startOf("day").toISOString(),
    }),
    [range],
  );
  const filter: ResultFilter = {
    ...dateFilter,
    lane,
    plate: plate.trim() || undefined,
    unmatched: mode === "unmatched",
    no_plate: mode === "no_plate",
    page,
    page_size: pageSize,
  };
  const refetchInterval = live ? LIVE_INTERVAL_MS : false;

  const results = useQuery({
    queryKey: ["results", filter],
    queryFn: () => api.results(filter),
    refetchInterval,
    placeholderData: keepPreviousData,
  });
  const stats = useQuery({
    queryKey: ["result-stats", dateFilter],
    queryFn: () => api.resultStats(dateFilter),
    refetchInterval,
  });
  const lanes = useQuery({ queryKey: ["result-lanes"], queryFn: api.resultLanes, refetchInterval: 60000 });

  const resetPage = () => setPage(1);

  const columns: ColumnsType<VehicleResult> = [
    {
      title: "Ảnh xe",
      dataIndex: "image_path",
      width: THUMB_WIDTH + 16,
      render: (path: string | null) => <Thumb path={path} />,
    },
    {
      title: "Thời gian",
      dataIndex: "event_time",
      width: 170,
      render: (value: string) => formatTime(value),
    },
    {
      title: "Làn",
      dataIndex: "trigger_code",
      width: 150,
      render: (value: string) => <Tag>{value}</Tag>,
    },
    {
      title: "Biển số",
      key: "plate",
      width: 170,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          {row.plate ? (
            <Typography.Text strong style={{ fontSize: 16, fontFamily: "monospace" }} copyable>
              {row.plate}
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">Không đọc được</Typography.Text>
          )}
          {row.raw_plate && row.raw_plate !== row.plate && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              OCR thô: {row.raw_plate}
            </Typography.Text>
          )}
          {row.requires_search && <Tag color="warning">Độ tin cậy thấp</Tag>}
        </Space>
      ),
    },
    {
      title: "Danh sách tham chiếu",
      key: "reference",
      width: 200,
      render: (_, row) =>
        row.in_reference ? (
          <Tag color="success">Có trong danh sách</Tag>
        ) : (
          <Space direction="vertical" size={2}>
            <Tag color="orange">Ngoài danh sách</Tag>
            {row.best_match && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Gần nhất: {row.best_match}
                {row.match_score != null && ` (${Math.round(row.match_score * 100)}%)`}
              </Typography.Text>
            )}
          </Space>
        ),
    },
    {
      title: "Cân",
      key: "weight",
      width: 140,
      render: (_, row) => (
        <AnalysisCell
          result={row.weight}
          failure={row.failures.find((item) => item.analyzer === "weight")}
          pendingSince={row.received_at}
        />
      ),
    },
  ];

  return (
    <>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Tổng lượt xe" value={stats.data?.total ?? 0} loading={stats.isLoading} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="Có trong danh sách"
              value={stats.data?.in_reference ?? 0}
              valueStyle={{ color: "#389e0d" }}
              loading={stats.isLoading}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="Ngoài danh sách"
              value={stats.data?.out_of_reference ?? 0}
              valueStyle={{ color: "#d46b08" }}
              loading={stats.isLoading}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Không đọc được biển" value={stats.data?.no_plate ?? 0} loading={stats.isLoading} />
          </Card>
        </Col>
      </Row>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap size={[12, 12]}>
          <DatePicker.RangePicker
            value={range}
            allowClear={false}
            format="DD/MM/YYYY"
            onChange={(value) => {
              if (value?.[0] && value?.[1]) {
                setRange([value[0], value[1]]);
                resetPage();
              }
            }}
          />
          <Select
            allowClear
            placeholder="Tất cả làn"
            style={{ minWidth: 170 }}
            value={lane}
            options={(lanes.data ?? []).map((item) => ({ value: item, label: item }))}
            onChange={(value) => {
              setLane(value);
              resetPage();
            }}
          />
          <Input.Search
            allowClear
            placeholder="Tìm biển số"
            style={{ width: 200 }}
            onSearch={(value) => {
              setPlate(value);
              resetPage();
            }}
          />
          <Segmented<Mode>
            value={mode}
            options={[
              { value: "all", label: "Tất cả" },
              { value: "unmatched", label: "Ngoài danh sách" },
              { value: "no_plate", label: "Không đọc được biển" },
            ]}
            onChange={(value) => {
              setMode(value);
              resetPage();
            }}
          />
          <Space>
            <Switch checked={live} onChange={setLive} />
            <Typography.Text>Trực tiếp</Typography.Text>
            {live && <Tag color="processing">cập nhật mỗi {LIVE_INTERVAL_MS / 1000}s</Tag>}
          </Space>
        </Space>
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">
            Lượt xe gần nhất: {formatTime(stats.data?.last_event)}
          </Typography.Text>
        </div>
      </Card>

      {results.isError && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={(results.error as Error).message} />
      )}

      <Table<VehicleResult>
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={results.data?.items ?? []}
        loading={results.isLoading}
        scroll={{ x: 1100 }}
        pagination={{
          current: page,
          pageSize,
          total: results.data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100],
          showTotal: (total) => `${total} lượt xe`,
          onChange: (nextPage, nextSize) => {
            setPage(nextSize !== pageSize ? 1 : nextPage);
            setPageSize(nextSize);
          },
        }}
      />
    </>
  );
}
