import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { App, Button, Drawer, Popconfirm, Space, Table, Tabs, Tag, Typography } from "antd";
import { RollbackOutlined } from "@ant-design/icons";
import { api, Revision } from "../api";
import { useAuth } from "../auth";
import DiffView from "../components/DiffView";
import { useSave } from "../components/common";

const STATUS: Record<Revision["status"], { color: string; label: string }> = {
  applied: { color: "green", label: "đã áp dụng" },
  failed: { color: "red", label: "thất bại" },
  rolled_back: { color: "default", label: "đã rollback" },
};

export default function RevisionsPage() {
  const { isAdmin } = useAuth();
  const { modal } = App.useApp();
  const revisions = useQuery({ queryKey: ["revisions"], queryFn: api.revisions });
  const [openId, setOpenId] = useState<number | null>(null);
  const detail = useQuery({ queryKey: ["revision", openId], queryFn: () => api.revision(openId!), enabled: openId !== null });
  const rollback = useSave((id: number) => api.rollback(id), "Đã gửi yêu cầu rollback");

  const doRollback = (id: number) =>
    rollback.mutate(id, {
      onSuccess: (result) =>
        modal[result.ok ? "success" : "error"]({
          title: result.ok ? `Đã rollback về #${id}` : "Rollback thất bại",
          width: 720,
          content: <pre className="log-box">{result.log.join("\n")}</pre>,
        }),
    });

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Lịch sử áp dụng
      </Typography.Title>
      <Table<Revision>
        rowKey="id"
        size="middle"
        loading={revisions.isLoading}
        dataSource={revisions.data}
        columns={[
          { title: "#", dataIndex: "id", width: 60 },
          { title: "Thời gian (UTC)", dataIndex: "created_at", width: 170 },
          { title: "Người thực hiện", dataIndex: "username", width: 130 },
          { title: "Ghi chú", dataIndex: "note" },
          {
            title: "Service",
            dataIndex: "services",
            render: (services: string[]) => (services.length ? services.map((s) => <Tag key={s}>{s}</Tag>) : "—"),
          },
          {
            title: "Trạng thái",
            dataIndex: "status",
            width: 120,
            render: (status: Revision["status"]) => <Tag color={STATUS[status].color}>{STATUS[status].label}</Tag>,
          },
          {
            title: "",
            width: 190,
            render: (_, row) => (
              <Space>
                <Button size="small" onClick={() => setOpenId(row.id)}>
                  Chi tiết
                </Button>
                {isAdmin && row.status !== "failed" && (
                  <Popconfirm
                    title={`Khôi phục cấu hình của revision #${row.id}?`}
                    description="Dữ liệu trong DB sẽ được thay bằng bản này rồi áp dụng ngay."
                    onConfirm={() => doRollback(row.id)}
                  >
                    <Button size="small" icon={<RollbackOutlined />} loading={rollback.isPending && rollback.variables === row.id}>
                      Rollback
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Drawer title={`Revision #${openId ?? ""}`} width={900} open={openId !== null} onClose={() => setOpenId(null)}>
        {detail.data && (
          <Tabs
            items={[
              { key: "diff", label: "Thay đổi file", children: <DiffView diff={detail.data.files_diff ?? ""} /> },
              { key: "log", label: "Log", children: <pre className="log-box">{detail.data.log}</pre> },
            ]}
          />
        )}
      </Drawer>
    </>
  );
}
