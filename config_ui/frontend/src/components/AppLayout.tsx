import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Dropdown, Layout, Menu, Space, Tag, Typography } from "antd";
import {
  AppstoreOutlined,
  CameraOutlined,
  CarOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  InboxOutlined,
  SettingOutlined,
  UnorderedListOutlined,
  UserOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { api } from "../api";
import { useAuth } from "../auth";
import ApplyDialog from "./ApplyDialog";

const MENU = [
  { key: "/", icon: <DashboardOutlined />, label: "Tổng quan" },
  { key: "/live", icon: <VideoCameraOutlined />, label: "Camera trực tiếp" },
  { key: "/results", icon: <UnorderedListOutlined />, label: "Kết quả nhận diện" },
  { key: "/cameras", icon: <CameraOutlined />, label: "Camera" },
  { key: "/sites", icon: <CarOutlined />, label: "Làn xe" },
  { key: "/weight", icon: <AppstoreOutlined />, label: "Đọc cân" },
  { key: "/cargo", icon: <InboxOutlined />, label: "Nhận diện hàng" },
  { key: "/plates", icon: <DatabaseOutlined />, label: "Biển số tham chiếu" },
  { key: "/system", icon: <SettingOutlined />, label: "Hệ thống" },
  { key: "/revisions", icon: <HistoryOutlined />, label: "Lịch sử" },
];

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, isAdmin, logout } = useAuth();
  const [applyOpen, setApplyOpen] = useState(false);
  const preview = useQuery({ queryKey: ["preview"], queryFn: api.preview, refetchInterval: 15000 });

  const selected = MENU.slice()
    .reverse()
    .find((item) => (item.key === "/" ? location.pathname === "/" : location.pathname.startsWith(item.key)));
  const pending = preview.data?.pending ?? 0;
  const hasErrors = (preview.data?.errors.length ?? 0) > 0;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider width={210} theme="light" breakpoint="lg" collapsedWidth={56} style={{ borderRight: "1px solid #f0f0f0" }}>
        <div style={{ padding: "16px 20px", fontWeight: 600, fontSize: 16 }}>Moda config</div>
        <Menu
          mode="inline"
          selectedKeys={[selected?.key ?? "/"]}
          items={MENU}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 0 }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: "#fff",
            borderBottom: "1px solid #f0f0f0",
            padding: "0 20px",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <Typography.Text type="secondary">{selected?.label}</Typography.Text>
          <div style={{ flex: 1 }} />
          {hasErrors ? (
            <Tag color="error">Cấu hình chưa hợp lệ</Tag>
          ) : pending > 0 ? (
            <Tag color="warning">{pending} file chưa áp dụng</Tag>
          ) : (
            <Tag color="success">Đã đồng bộ</Tag>
          )}
          {isAdmin && (
            <Badge count={pending} size="small">
              <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setApplyOpen(true)}>
                Áp dụng
              </Button>
            </Badge>
          )}
          <Dropdown
            menu={{
              items: [
                { key: "role", label: `Vai trò: ${user?.role}`, disabled: true },
                { key: "logout", label: "Đăng xuất", onClick: () => void logout() },
              ],
            }}
          >
            <Space style={{ cursor: "pointer" }}>
              <UserOutlined />
              {user?.username}
            </Space>
          </Dropdown>
        </Layout.Header>
        <Layout.Content style={{ padding: 20 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
      <ApplyDialog open={applyOpen} onClose={() => setApplyOpen(false)} />
    </Layout>
  );
}
