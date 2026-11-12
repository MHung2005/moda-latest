import { ReactNode } from "react";
import { Button, Card, Col, Collapse, Form, Input, InputNumber, Row } from "antd";
import { SaveOutlined } from "@ant-design/icons";
import { api } from "../api";
import { useAuth } from "../auth";
import { FieldLabel, useSave } from "./common";

const num = (label: string, field: string, min: number, step = 1) => (
  <Col span={12} key={field}>
    <Form.Item name={field} label={<FieldLabel label={label} field={field} />} rules={[{ required: true }]}>
      <InputNumber min={min} step={step} style={{ width: "100%" }} />
    </Form.Item>
  </Col>
);

/** Worker/retry/timeout block shared by weight and cargo (post_analysis/config.py). */
function AdvancedFields({ modelFields }: { modelFields: ReactNode }) {
  return (
    <Row gutter={12}>
      {modelFields}
      <Col span={12}>
        <Form.Item name="group_id" label={<FieldLabel label="Kafka consumer group" field="group_id" />} rules={[{ required: true }]}>
          <Input />
        </Form.Item>
      </Col>
      <Col span={12}>
        <Form.Item name="output_root" label={<FieldLabel label="Thư mục ảnh kết quả" field="output_root" />} rules={[{ required: true }]}>
          <Input />
        </Form.Item>
      </Col>
      {num("Số worker", "worker_count", 1)}
      {num("Deadline mỗi event (s)", "event_timeout_seconds", 1)}
      {num("Số lần thử tối đa", "max_attempts", 1)}
      {num("Backoff retry (s)", "retry_backoff_seconds", 0, 0.5)}
      {num("Thời gian chờ huỷ (s)", "cancellation_grace_seconds", 0, 0.5)}
      {num("Timeout mở RTSP (ms)", "camera_open_timeout_ms", 1, 500)}
      {num("Timeout đọc RTSP (ms)", "camera_read_timeout_ms", 1, 500)}
      {num("Số lần chờ model", "model_startup_attempts", 1)}
      {num("Khoảng chờ model (s)", "model_startup_delay_seconds", 0, 0.5)}
    </Row>
  );
}

interface Props {
  analyzer: "weight" | "cargo";
  settings: Record<string, unknown>;
  main: ReactNode;
  modelFields: ReactNode;
}

export default function AnalyzerSettingsForm({ analyzer, settings, main, modelFields }: Props) {
  const { isAdmin } = useAuth();
  const save = useSave((values: Record<string, unknown>) => api.updateAnalyzer(analyzer, { ...settings, ...values }), "Đã lưu (chưa áp dụng)");
  return (
    <Card size="small" title="Tham số">
      <Form layout="vertical" initialValues={settings} onFinish={(values) => save.mutate(values)} disabled={!isAdmin}>
        {main}
        <Collapse
          size="small"
          ghost
          items={[{ key: "adv", label: "Nâng cao", forceRender: true, children: <AdvancedFields modelFields={modelFields} /> }]}
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

export { num as numberField };
