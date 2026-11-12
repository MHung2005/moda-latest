import { useQuery } from "@tanstack/react-query";
import { Button, Card, Col, Form, Input, Popconfirm, Row, Select, Space, Spin, Table } from "antd";
import { DeleteOutlined, PlusOutlined, SaveOutlined } from "@ant-design/icons";
import { AnalyzerLane, api } from "../api";
import { useAuth } from "../auth";
import AnalyzerSettingsForm, { numberField } from "../components/AnalyzerSettingsForm";
import { FieldLabel, tritonOptions, useSave } from "../components/common";
import ConfidenceInput from "../components/ConfidenceInput";

export default function CargoPage() {
  const { isAdmin } = useAuth();
  const analyzer = useQuery({ queryKey: ["analyzer", "cargo"], queryFn: () => api.analyzer("cargo") });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const models = useQuery({ queryKey: ["models"], queryFn: api.modelOptions });
  const [form] = Form.useForm<{ trigger_code: string; analysis_code: string; camera_id: number }>();

  const saveLane = useSave(
    (values: { trigger_code: string; analysis_code: string; camera_id: number }) =>
      api.upsertCargoLane(values.trigger_code, { analysis_code: values.analysis_code, camera_id: values.camera_id }),
    "Đã lưu làn (chưa áp dụng)",
  );
  const deleteLane = useSave((trigger: string) => api.deleteLane("cargo", trigger), "Đã xoá làn");

  if (analyzer.isLoading) return <Spin />;
  const settings = analyzer.data?.settings ?? {};
  const cargoCameras = (cameras.data ?? []).filter((item) => item.purpose === "cargo");
  const cameraLabel = (id: number | null) => cameras.data?.find((item) => item.id === id)?.code ?? "?";

  return (
    <Row gutter={16}>
      <Col xs={24} xl={14}>
        <Card size="small" title="Camera thùng hàng theo làn">
          <Table<AnalyzerLane>
            size="small"
            rowKey="trigger_code"
            pagination={false}
            dataSource={analyzer.data?.lanes}
            onRow={(row) => ({ onClick: () => form.setFieldsValue({ ...row, camera_id: row.camera_id ?? undefined }) })}
            columns={[
              { title: <FieldLabel label="Trigger code" field="camera_url_envs" />, dataIndex: "trigger_code" },
              { title: "Camera", dataIndex: "camera_id", render: (value) => cameraLabel(value) },
              { title: <FieldLabel label="Mã kết quả" field="analysis_codes" />, dataIndex: "analysis_code" },
              {
                title: "",
                render: (_, row) =>
                  isAdmin && (
                    <Popconfirm title={`Xoá làn ${row.trigger_code}?`} onConfirm={() => deleteLane.mutate(row.trigger_code)}>
                      <Button size="small" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                    </Popconfirm>
                  ),
              },
            ]}
          />
          {isAdmin && (
            <Form form={form} layout="inline" style={{ marginTop: 12, rowGap: 8 }} onFinish={(values) => saveLane.mutate(values)}>
              <Form.Item name="trigger_code" rules={[{ required: true, message: "Trigger code" }]}>
                <Input placeholder="trigger code" style={{ width: 170 }} />
              </Form.Item>
              <Form.Item name="camera_id" rules={[{ required: true, message: "Chọn camera" }]}>
                <Select placeholder="camera" style={{ width: 180 }} options={cargoCameras.map((c) => ({ value: c.id, label: c.code }))} />
              </Form.Item>
              <Form.Item name="analysis_code" rules={[{ required: true, message: "Mã kết quả" }]}>
                <Input placeholder="analysis code" style={{ width: 180 }} />
              </Form.Item>
              <Space>
                <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saveLane.isPending}>
                  Lưu
                </Button>
                <Button icon={<PlusOutlined />} onClick={() => form.resetFields()}>
                  Làn mới
                </Button>
              </Space>
            </Form>
          )}
        </Card>
      </Col>
      <Col xs={24} xl={10}>
        <AnalyzerSettingsForm
          analyzer="cargo"
          settings={settings}
          main={
            <>
              <Form.Item name="detection_confidence" label={<FieldLabel label="Ngưỡng phát hiện thùng hàng" field="detection_confidence" />}>
                <ConfidenceInput />
              </Form.Item>
              <Form.Item
                name="preferred_class_confidence"
                label={<FieldLabel label="Ngưỡng phân loại đá" field="preferred_class_confidence" />}
              >
                <ConfidenceInput />
              </Form.Item>
              <Row gutter={12}>
                {numberField("Chờ xe dừng (s)", "settle_seconds", 0, 0.5)}
                {numberField("Số lần chụp lại", "capture_retries", 1)}
              </Row>
            </>
          }
          modelFields={
            <>
              <Col span={12}>
                <Form.Item name="detector_model" label={<FieldLabel label="Model phát hiện" field="detector_model" />} rules={[{ required: true }]}>
                  <Select options={tritonOptions(models.data?.triton, [settings.detector_model])} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="classifier_model" label={<FieldLabel label="Model phân loại" field="classifier_model" />} rules={[{ required: true }]}>
                  <Select options={tritonOptions(models.data?.triton, [settings.classifier_model])} />
                </Form.Item>
              </Col>
            </>
          }
        />
      </Col>
    </Row>
  );
}
