import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Modal, Row, Segmented, Space, Spin, Tag, Typography } from "antd";
import {
  ExpandOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { api, Camera } from "../api";

type Group = "all" | Camera["purpose"];
type Columns = 1 | 2 | 3;

// Browsers allow ~6 HTTP/1.1 connections per host and every MJPEG stream holds one open.
// Keep two free for the API calls of the rest of the UI.
const MAX_PLAYING = 4;

const PURPOSE_LABEL: Record<Camera["purpose"], string> = {
  lane: "Làn xe",
  weight: "Cân",
  cargo: "Hàng",
};

function LiveImage({
  camera,
  nonce,
  onError,
  height,
}: {
  camera: Camera;
  nonce: number;
  onError: () => void;
  height?: number | string;
}) {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => setLoaded(false), [nonce]);
  return (
    <div
      style={{
        position: "relative",
        background: "#000",
        aspectRatio: height ? undefined : "16 / 9",
        height,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      {!loaded && <Spin tip="Đang kết nối camera..." style={{ position: "absolute" }} />}
      <img
        key={nonce}
        src={api.liveUrl(camera.id, nonce)}
        alt={camera.name}
        onLoad={() => setLoaded(true)}
        onError={onError}
        style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
      />
    </div>
  );
}

export default function LivePage() {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [group, setGroup] = useState<Group>("lane");
  const [columns, setColumns] = useState<Columns>(2);
  // Order matters: the oldest stream is stopped when a fifth one starts.
  const [playing, setPlaying] = useState<number[]>([]);
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const [nonce, setNonce] = useState<Record<number, number>>({});
  const [expanded, setExpandedCamera] = useState<Camera | null>(null);
  const [expandedNonce, setExpandedNonce] = useState(0);
  const setExpanded = (camera: Camera | null) => {
    setExpandedNonce(Date.now());
    setExpandedCamera(camera);
  };

  const visible = useMemo(
    () => (cameras.data ?? []).filter((camera) => group === "all" || camera.purpose === group),
    [cameras.data, group],
  );

  // Start the first cameras of the selected group automatically.
  useEffect(() => {
    if (!cameras.data) return;
    setPlaying(visible.slice(0, MAX_PLAYING).map((camera) => camera.id));
    setFailed({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, cameras.data]);

  // The expanded view uses its own connection: pause the grid copy while it is open.
  const gridPlaying = playing.filter((id) => id !== expanded?.id);

  const start = (id: number) => {
    setFailed((current) => ({ ...current, [id]: false }));
    setNonce((current) => ({ ...current, [id]: Date.now() }));
    setPlaying((current) => [...current.filter((item) => item !== id), id].slice(-MAX_PLAYING));
  };
  const stop = (id: number) => setPlaying((current) => current.filter((item) => item !== id));
  const fail = (id: number) => {
    setFailed((current) => ({ ...current, [id]: true }));
    stop(id);
  };

  if (cameras.isLoading) return <Spin />;
  if (cameras.isError) return <Alert type="error" showIcon message={(cameras.error as Error).message} />;

  return (
    <>
      <Space wrap size={[12, 12]} style={{ marginBottom: 16 }}>
        <Segmented<Group>
          value={group}
          options={[
            { value: "lane", label: "Làn xe" },
            { value: "weight", label: "Cân" },
            { value: "cargo", label: "Hàng" },
            { value: "all", label: "Tất cả" },
          ]}
          onChange={setGroup}
        />
        <Segmented<Columns>
          value={columns}
          options={[
            { value: 1, label: "1 cột" },
            { value: 2, label: "2 cột" },
            { value: 3, label: "3 cột" },
          ]}
          onChange={setColumns}
        />
        <Typography.Text type="secondary">
          Phát tối đa {MAX_PLAYING} camera cùng lúc · luồng phụ 640×360 của camera, tối đa 15 khung hình/giây
        </Typography.Text>
      </Space>

      {visible.length === 0 ? (
        <Empty description="Chưa có camera nào trong nhóm này" />
      ) : (
        <Row gutter={[16, 16]}>
          {visible.map((camera) => {
            const isPlaying = gridPlaying.includes(camera.id);
            return (
              <Col key={camera.id} span={24 / columns}>
                <Card
                  size="small"
                  title={
                    <Space>
                      {camera.name}
                      <Tag>{PURPOSE_LABEL[camera.purpose]}</Tag>
                      {isPlaying && <Tag color="red">LIVE</Tag>}
                    </Space>
                  }
                  extra={
                    <Space size={4}>
                      {isPlaying ? (
                        <Button size="small" icon={<PauseCircleOutlined />} onClick={() => stop(camera.id)}>
                          Dừng
                        </Button>
                      ) : (
                        <Button size="small" icon={<PlayCircleOutlined />} onClick={() => start(camera.id)}>
                          Phát
                        </Button>
                      )}
                      <Button size="small" icon={<ExpandOutlined />} onClick={() => setExpanded(camera)} />
                    </Space>
                  }
                >
                  {isPlaying ? (
                    <LiveImage camera={camera} nonce={nonce[camera.id] ?? 0} onError={() => fail(camera.id)} />
                  ) : (
                    <div
                      style={{
                        aspectRatio: "16 / 9",
                        background: "#1f1f1f",
                        borderRadius: 4,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: 8,
                        color: "#bfbfbf",
                      }}
                    >
                      {failed[camera.id] ? (
                        <>
                          <Typography.Text style={{ color: "#ff7875" }}>Không kết nối được camera</Typography.Text>
                          <Button size="small" icon={<ReloadOutlined />} onClick={() => start(camera.id)}>
                            Thử lại
                          </Button>
                        </>
                      ) : (
                        <Button type="primary" ghost icon={<PlayCircleOutlined />} onClick={() => start(camera.id)}>
                          Phát trực tiếp
                        </Button>
                      )}
                    </div>
                  )}
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      <Modal
        open={!!expanded}
        onCancel={() => setExpanded(null)}
        footer={null}
        width="90vw"
        title={expanded?.name}
        destroyOnClose
        styles={{ body: { padding: 0 } }}
      >
        {expanded && (
          <LiveImage
            camera={expanded}
            nonce={expandedNonce}
            height="75vh"
            onError={() => {
              fail(expanded.id);
              setExpanded(null);
            }}
          />
        )}
      </Modal>
    </>
  );
}
