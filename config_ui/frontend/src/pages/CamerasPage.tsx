import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  App,
  Button,
  Checkbox,
  Col,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { ApiOutlined, CameraOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { api, Camera, CameraInput } from "../api";
import { useAuth } from "../auth";
import { errorText, FieldLabel, PURPOSE_LABEL, useSave } from "../components/common";

interface UrlParts {
  host: string;
  port?: number;
  user?: string;
  password?: string;
  path?: string;
}

function buildUrl(parts: UrlParts): string {
  const auth = parts.user ? `${encodeURIComponent(parts.user)}:${encodeURIComponent(parts.password ?? "")}@` : "";
  const port = parts.port ? `:${parts.port}` : "";
  const path = parts.path ? (parts.path.startsWith("/") ? parts.path : `/${parts.path}`) : "";
  return `rtsp://${auth}${parts.host}${port}${path}`;
}

interface FormValues extends Omit<CameraInput, "rtsp_url"> {
  change_url: boolean;
  url_mode: "parts" | "raw";
  raw_url?: string;
  parts?: UrlParts;
}

export default function CamerasPage() {
  const { isAdmin } = useAuth();
  const { message } = App.useApp();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [editing, setEditing] = useState<Camera | "new" | null>(null);
  const [preview, setPreview] = useState<{ camera: Camera; nonce: number } | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const [form] = Form.useForm<FormValues>();

  const save = useSave(async (values: FormValues) => {
    const { change_url, url_mode, raw_url, parts, ...rest } = values;
    const payload: CameraInput = { ...rest };
    if (editing === "new" || change_url) {
      payload.rtsp_url = url_mode === "raw" ? raw_url : buildUrl(parts!);
    }
    return editing === "new" ? api.createCamera(payload) : api.updateCamera((editing as Camera).id, payload);
  });
  const remove = useSave((id: number) => api.deleteCamera(id), "Đã xoá camera");

  const openForm = (camera: Camera | "new") => {
    setEditing(camera);
    form.resetFields();
    form.setFieldsValue(
      camera === "new"
        ? { purpose: "lane", change_url: true, url_mode: "parts", parts: { host: "", port: 554 } }
        : { ...camera, change_url: false, url_mode: "parts", parts: { host: "", port: 554 } },
    );
  };

  const test = async (camera: Camera) => {
    setTesting(camera.id);
    try {
      const result = await api.testCamera(camera.id);
      message.success(`${camera.code}: kết nối được, ${result.width}x${result.height}`);
      void cameras.refetch();
    } catch (error) {
      message.error(`${camera.code}: ${errorText(error)}`);
    } finally {
      setTesting(null);
    }
  };

  const changeUrl = Form.useWatch("change_url", form);
  const urlMode = Form.useWatch("url_mode", form);

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          Camera
        </Typography.Title>
        {isAdmin && (
          <Button icon={<PlusOutlined />} onClick={() => openForm("new")}>
            Thêm camera
          </Button>
        )}
      </Space>
      <Table<Camera>
        rowKey="id"
        loading={cameras.isLoading}
        dataSource={cameras.data}
        pagination={false}
        size="middle"
        columns={[
          { title: "Mã", dataIndex: "code" },
          { title: "Tên", dataIndex: "name" },
          { title: "Loại", dataIndex: "purpose", render: (value) => <Tag>{PURPOSE_LABEL[value]}</Tag> },
          { title: "Biến môi trường", dataIndex: "env_name", render: (value) => <code>{value}</code> },
          { title: "URL", dataIndex: "rtsp_url_masked", render: (value) => <code>{value}</code> },
          {
            title: "Độ phân giải",
            render: (_, row) => (row.frame_width ? `${row.frame_width}x${row.frame_height}` : "—"),
          },
          {
            title: "",
            align: "right",
            render: (_, row) => (
              <Space>
                <Button size="small" icon={<ApiOutlined />} loading={testing === row.id} onClick={() => test(row)}>
                  Kiểm tra
                </Button>
                <Button size="small" icon={<CameraOutlined />} onClick={() => setPreview({ camera: row, nonce: Date.now() })}>
                  Ảnh
                </Button>
                {isAdmin && <Button size="small" icon={<EditOutlined />} onClick={() => openForm(row)} />}
                {isAdmin && (
                  <Popconfirm title="Xoá camera này?" onConfirm={() => remove.mutate(row.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing === "new" ? "Thêm camera" : "Sửa camera"}
        open={!!editing}
        width={640}
        okText="Lưu"
        confirmLoading={save.isPending}
        onCancel={() => setEditing(null)}
        onOk={() => form.validateFields().then((values) => save.mutate(values, { onSuccess: () => setEditing(null) }))}
      >
        <Form form={form} layout="vertical">
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="code" label={<FieldLabel label="Mã" field="code" />} rules={[{ required: true, pattern: /^[a-z0-9_]+$/, message: "Chữ thường, số, gạch dưới" }]}>
                <Input placeholder="weight_cam" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="name" label="Tên hiển thị" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="purpose" label="Loại" rules={[{ required: true }]}>
                <Select options={Object.entries(PURPOSE_LABEL).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="env_name"
                label={<FieldLabel label="Biến môi trường" field="env" />}
                rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]*$/, message: "VIẾT_HOA_GẠCH_DƯỚI" }]}
                extra="Tên biến trong .env / .env.jetson mà compose truyền vào container"
              >
                <Input placeholder="MODA_WEIGHT_CAMERA_URL" />
              </Form.Item>
            </Col>
          </Row>
          {editing !== "new" && (
            <Form.Item name="change_url" valuePropName="checked">
              <Checkbox>Đổi URL camera (URL hiện tại được giữ nếu không chọn)</Checkbox>
            </Form.Item>
          )}
          {(editing === "new" || changeUrl) && (
            <>
              <Form.Item name="url_mode">
                <Segmented
                  options={[
                    { value: "parts", label: "Nhập từng phần" },
                    { value: "raw", label: "Dán URL" },
                  ]}
                />
              </Form.Item>
              {urlMode === "raw" ? (
                <Form.Item name="raw_url" label="URL RTSP" rules={[{ required: true, pattern: /^(rtsps?|https?):\/\//, message: "Bắt đầu bằng rtsp://" }]}>
                  <Input.Password placeholder="rtsp://user:password@192.168.1.20:554/Streaming/Channels/101" visibilityToggle />
                </Form.Item>
              ) : (
                <Row gutter={12}>
                  <Col span={16}>
                    <Form.Item name={["parts", "host"]} label="Host / IP" rules={[{ required: true }]}>
                      <Input placeholder="192.168.1.20" />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name={["parts", "port"]} label="Port">
                      <InputNumber min={1} max={65535} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name={["parts", "user"]} label="User">
                      <Input autoComplete="off" />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item name={["parts", "password"]} label="Password">
                      <Input.Password autoComplete="new-password" />
                    </Form.Item>
                  </Col>
                  <Col span={24}>
                    <Form.Item name={["parts", "path"]} label="Đường dẫn stream">
                      <Input placeholder="/Streaming/Channels/101" />
                    </Form.Item>
                  </Col>
                </Row>
              )}
            </>
          )}
        </Form>
      </Modal>

      <Modal
        title={`Ảnh từ ${preview?.camera.code ?? ""}`}
        open={!!preview}
        footer={null}
        width={960}
        onCancel={() => setPreview(null)}
        destroyOnClose
      >
        {preview && (
          <Image
            src={api.snapshotUrl(preview.camera.id, preview.nonce)}
            fallback="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='60'%3E%3Ctext x='10' y='35'%3EKhông lấy được ảnh từ camera%3C/text%3E%3C/svg%3E"
            preview={false}
          />
        )}
      </Modal>
    </>
  );
}
