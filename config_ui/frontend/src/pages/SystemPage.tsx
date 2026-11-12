import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, Col, Form, Input, InputNumber, Modal, Popconfirm, Row, Select, Space, Spin, Table, Tag } from "antd";
import { DeleteOutlined, KeyOutlined, PlusOutlined, SaveOutlined } from "@ant-design/icons";
import { api, Role, SystemSettings, User } from "../api";
import { useAuth } from "../auth";
import { FieldLabel, useSave } from "../components/common";

export default function SystemPage() {
  const { isAdmin, user } = useAuth();
  const system = useQuery({ queryKey: ["system"], queryFn: api.system });
  const save = useSave((values: SystemSettings) => api.updateSystem(values), "Đã lưu (chưa áp dụng)");

  if (system.isLoading) return <Spin />;
  return (
    <Row gutter={16}>
      <Col xs={24} lg={12}>
        <Card size="small" title="Kết nối và topic">
          <Form layout="vertical" initialValues={system.data} onFinish={(values) => save.mutate(values)} disabled={!isAdmin}>
            <Form.Item
              name="kafka_bootstrap_servers"
              label={<FieldLabel label="Kafka bootstrap servers" field="MODA_KAFKA_BOOTSTRAP_SERVERS" />}
              rules={[{ required: true }]}
              extra="Ghi vào cả .env (detector) và .env.jetson (post-analysis)"
            >
              <Input placeholder="192.168.1.10:29092" />
            </Form.Item>
            <Form.Item name="triton_url" label={<FieldLabel label="Triton URL" field="MODA_TRITON_URL" />} rules={[{ required: true }]}>
              <Input placeholder="http://triton:8000" />
            </Form.Item>
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item name="input_topic" label={<FieldLabel label="Topic đầu vào" field="input_topic" />} rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="output_topic" label={<FieldLabel label="Topic kết quả" field="output_topic" />} rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="dead_letter_topic" label={<FieldLabel label="Topic lỗi" field="dead_letter_topic" />} rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="publish_timeout_seconds" label={<FieldLabel label="Timeout (s)" field="publish_timeout_seconds" />}>
                  <InputNumber min={1} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="utc_offset_hours" label={<FieldLabel label="Múi giờ" field="utc_offset_hours" />}>
                  <InputNumber min={-23} max={23} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
            </Row>
            {isAdmin && (
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={save.isPending}>
                Lưu
              </Button>
            )}
          </Form>
        </Card>
      </Col>
      <Col xs={24} lg={12}>{isAdmin ? <UsersCard /> : <PasswordCard userId={user!.id} />}</Col>
    </Row>
  );
}

function PasswordCard({ userId }: { userId: number }) {
  const save = useSave((password: string) => api.changePassword(userId, password), "Đã đổi mật khẩu");
  return (
    <Card size="small" title="Đổi mật khẩu">
      <Form layout="inline" onFinish={(values) => save.mutate(values.password)}>
        <Form.Item name="password" rules={[{ required: true, min: 8, message: "Ít nhất 8 ký tự" }]}>
          <Input.Password placeholder="Mật khẩu mới" autoComplete="new-password" />
        </Form.Item>
        <Button htmlType="submit" loading={save.isPending}>
          Đổi
        </Button>
      </Form>
    </Card>
  );
}

function UsersCard() {
  const { user } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [creating, setCreating] = useState(false);
  const [passwordFor, setPasswordFor] = useState<User | null>(null);
  const [form] = Form.useForm<{ username: string; password: string; role: Role }>();
  const [passwordForm] = Form.useForm<{ password: string }>();
  const create = useSave((values: { username: string; password: string; role: Role }) => api.createUser(values), "Đã tạo tài khoản");
  const remove = useSave((id: number) => api.deleteUser(id), "Đã xoá tài khoản");
  const changePassword = useSave(
    ({ id, password }: { id: number; password: string }) => api.changePassword(id, password),
    "Đã đổi mật khẩu",
  );

  return (
    <Card
      size="small"
      title="Tài khoản"
      extra={
        <Button size="small" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          Thêm
        </Button>
      }
    >
      <Table<User>
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={users.data}
        columns={[
          { title: "Tài khoản", dataIndex: "username" },
          { title: "Vai trò", dataIndex: "role", render: (role) => <Tag color={role === "admin" ? "blue" : "default"}>{role}</Tag> },
          { title: "Tạo lúc", dataIndex: "created_at" },
          {
            title: "",
            align: "right",
            render: (_, row) => (
              <Space>
                <Button size="small" icon={<KeyOutlined />} onClick={() => setPasswordFor(row)} />
                {row.id !== user?.id && (
                  <Popconfirm title={`Xoá ${row.username}?`} onConfirm={() => remove.mutate(row.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title="Thêm tài khoản"
        open={creating}
        okText="Tạo"
        confirmLoading={create.isPending}
        onCancel={() => setCreating(false)}
        onOk={() =>
          form.validateFields().then((values) =>
            create.mutate(values, {
              onSuccess: () => {
                setCreating(false);
                form.resetFields();
              },
            }),
          )
        }
      >
        <Form form={form} layout="vertical" initialValues={{ role: "viewer" }}>
          <Form.Item name="username" label="Tài khoản" rules={[{ required: true, min: 3 }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="password" label="Mật khẩu" rules={[{ required: true, min: 8, message: "Ít nhất 8 ký tự" }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="Vai trò">
            <Select
              options={[
                { value: "viewer", label: "viewer: chỉ xem" },
                { value: "admin", label: "admin: sửa và áp dụng" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`Đổi mật khẩu ${passwordFor?.username ?? ""}`}
        open={!!passwordFor}
        okText="Đổi"
        confirmLoading={changePassword.isPending}
        onCancel={() => setPasswordFor(null)}
        onOk={() =>
          passwordForm.validateFields().then(({ password }) =>
            changePassword.mutate(
              { id: passwordFor!.id, password },
              {
                onSuccess: () => {
                  setPasswordFor(null);
                  passwordForm.resetFields();
                },
              },
            ),
          )
        }
      >
        <Form form={passwordForm}>
          <Form.Item name="password" rules={[{ required: true, min: 8, message: "Ít nhất 8 ký tự" }]}>
            <Input.Password placeholder="Mật khẩu mới" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
