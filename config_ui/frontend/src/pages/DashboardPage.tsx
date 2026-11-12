import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Badge, Button, Card, Col, Drawer, List, Row, Spin, Tag, Typography } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import { api, ServiceState } from "../api";

const STATUS_COLOR: Record<string, "success" | "error" | "warning" | "default" | "processing"> = {
  running: "success",
  restarting: "warning",
  exited: "error",
  dead: "error",
  created: "default",
  "not created": "default",
};

export default function DashboardPage() {
  const services = useQuery({ queryKey: ["services"], queryFn: api.services, refetchInterval: 10000 });
  const preview = useQuery({ queryKey: ["preview"], queryFn: api.preview });
  const revisions = useQuery({ queryKey: ["revisions"], queryFn: api.revisions });
  const [logTarget, setLogTarget] = useState<ServiceState | null>(null);
  const logs = useQuery({
    queryKey: ["logs", logTarget?.project, logTarget?.service],
    queryFn: () => api.serviceLogs(logTarget!.project, logTarget!.service),
    enabled: !!logTarget,
    refetchInterval: 5000,
  });

  const dockerError = services.data?.find((item) => item.error)?.error;
  const managed = (services.data ?? []).filter((item) => item.managed);

  return (
    <>
      {dockerError && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Không đọc được trạng thái Docker"
          description={dockerError}
        />
      )}
      <Typography.Title level={5}>Service</Typography.Title>
      {services.isLoading ? (
        <Spin />
      ) : (
        <Row gutter={[16, 16]}>
          {managed.map((item) => (
            <Col key={`${item.project}/${item.service}`} xs={24} sm={12} lg={8}>
              <Card
                size="small"
                title={item.service}
                extra={<Tag>{item.project === "detector" ? "DeepStream" : "Post-analysis"}</Tag>}
              >
                <Badge status={STATUS_COLOR[item.state] ?? "processing"} text={item.state} />
                <div>
                  <Typography.Text type="secondary">{item.status || "Chưa triển khai trên máy này"}</Typography.Text>
                </div>
                <Button
                  size="small"
                  icon={<FileTextOutlined />}
                  style={{ marginTop: 8 }}
                  disabled={!item.deployed}
                  onClick={() => setLogTarget(item)}
                >
                  Xem log
                </Button>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Row gutter={16} style={{ marginTop: 24 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Thay đổi chưa áp dụng">
            {preview.data?.errors.map((error) => (
              <Alert key={error} type="error" message={error} style={{ marginBottom: 8 }} />
            ))}
            <List
              size="small"
              dataSource={preview.data?.files ?? []}
              locale={{ emptyText: "Cấu hình trong DB khớp với file đang chạy" }}
              renderItem={(file) => <List.Item>{file.path}</List.Item>}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="Lần áp dụng gần đây">
            <List
              size="small"
              dataSource={(revisions.data ?? []).slice(0, 5)}
              locale={{ emptyText: "Chưa có lần áp dụng nào" }}
              renderItem={(revision) => (
                <List.Item>
                  <span>
                    #{revision.id} · {revision.created_at} · {revision.username ?? "?"}
                    {revision.note && ` · ${revision.note}`}
                  </span>
                  <Tag color={revision.status === "applied" ? "green" : revision.status === "failed" ? "red" : "default"}>
                    {revision.status}
                  </Tag>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      <Drawer
        title={`Log ${logTarget?.service ?? ""}`}
        width={900}
        open={!!logTarget}
        onClose={() => setLogTarget(null)}
      >
        <pre className="log-box" style={{ maxHeight: "none" }}>
          {logs.isLoading ? "Đang tải..." : logs.error ? String(logs.error) : logs.data}
        </pre>
      </Drawer>
    </>
  );
}
