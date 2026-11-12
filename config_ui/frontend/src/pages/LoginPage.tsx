import { useState } from "react";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { useAuth } from "../auth";
import { errorText } from "../components/common";

export default function LoginPage() {
  const { login } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (values: { username: string; password: string }) => {
    setLoading(true);
    setError(null);
    try {
      await login(values.username, values.password);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Card style={{ width: 360 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          Moda config
        </Typography.Title>
        <Typography.Paragraph type="secondary">Đăng nhập để cấu hình hệ thống</Typography.Paragraph>
        {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="username" label="Tài khoản" rules={[{ required: true, message: "Nhập tài khoản" }]}>
            <Input prefix={<UserOutlined />} autoFocus autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="Mật khẩu" rules={[{ required: true, message: "Nhập mật khẩu" }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            Đăng nhập
          </Button>
        </Form>
      </Card>
    </div>
  );
}
