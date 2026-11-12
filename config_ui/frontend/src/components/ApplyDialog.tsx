import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Collapse, Empty, Input, Modal, Result, Space, Spin, Tag, Typography } from "antd";
import { api, ApiError, ApplyResult } from "../api";
import DiffView from "./DiffView";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ApplyDialog({ open, onClose }: Props) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const preview = useQuery({ queryKey: ["preview", "dialog"], queryFn: api.preview, enabled: open });
  const apply = useMutation({
    mutationFn: () => api.apply(note),
    onSuccess: (value) => setResult(value),
    onError: (value) => setError(value as ApiError),
    onSettled: () => {
      void queryClient.invalidateQueries();
    },
  });
  const status = useQuery({
    queryKey: ["apply-status"],
    queryFn: api.applyStatus,
    enabled: apply.isPending,
    refetchInterval: 1000,
  });

  useEffect(() => {
    if (open) {
      setResult(null);
      setError(null);
      setNote("");
      void preview.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const data = preview.data;
  const services = Object.entries(data?.services ?? {}).flatMap(([project, names]) =>
    names.map((name) => `${project}/${name}`),
  );
  const blocked = !data || data.errors.length > 0 || data.pending === 0;

  let body;
  if (apply.isPending) {
    body = (
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space>
          <Spin /> <Typography.Text strong>{status.data?.step ?? "Đang áp dụng"}</Typography.Text>
        </Space>
        <pre className="log-box">{(status.data?.log ?? []).join("\n")}</pre>
      </Space>
    );
  } else if (result) {
    body = (
      <Result
        status={result.ok ? "success" : "error"}
        title={result.ok ? "Đã áp dụng cấu hình" : "Áp dụng thất bại, đã khôi phục cấu hình cũ"}
        subTitle={
          result.services.length
            ? `Đã recreate: ${result.services.join(", ")}`
            : "Không có service nào cần khởi động lại trên máy này"
        }
        extra={<pre className="log-box" style={{ textAlign: "left" }}>{result.log.join("\n")}</pre>}
      />
    );
  } else if (error) {
    body = (
      <Alert
        type="error"
        message={error.message}
        description={error.details.length ? <ul>{error.details.map((d) => <li key={d}>{d}</li>)}</ul> : undefined}
      />
    );
  } else if (preview.isLoading || !data) {
    body = <Spin />;
  } else {
    body = (
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        {data.errors.length > 0 && (
          <Alert
            type="error"
            message="Cấu hình chưa hợp lệ, chưa thể áp dụng"
            description={<ul style={{ margin: 0 }}>{data.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
          />
        )}
        {data.pending === 0 ? (
          <Empty description="Không có thay đổi nào so với file đang chạy" />
        ) : (
          <>
            <div>
              <Typography.Text strong>Service sẽ được khởi động lại: </Typography.Text>
              {services.length ? services.map((s) => <Tag key={s}>{s}</Tag>) : <Tag>không có</Tag>}
              <div>
                <Typography.Text type="secondary">
                  Service chưa chạy trên máy này sẽ được bỏ qua; file vẫn được ghi.
                </Typography.Text>
              </div>
            </div>
            <Collapse
              size="small"
              items={data.files.map((file) => ({
                key: file.path,
                label: (
                  <Space>
                    {file.path}
                    {file.created && <Tag color="blue">mới</Tag>}
                  </Space>
                ),
                children: <DiffView diff={file.diff} />,
              }))}
            />
            <Input.TextArea
              placeholder="Ghi chú cho lần thay đổi này, ví dụ: chỉnh ROI làn 1 sau khi dời camera"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={500}
              autoSize={{ minRows: 2 }}
            />
          </>
        )}
      </Space>
    );
  }

  const finished = result || error;
  return (
    <Modal
      title="Áp dụng cấu hình"
      open={open}
      width={860}
      onCancel={apply.isPending ? undefined : onClose}
      closable={!apply.isPending}
      maskClosable={false}
      footer={
        finished ? (
          <Button onClick={onClose}>Đóng</Button>
        ) : (
          <Space>
            <Button onClick={onClose} disabled={apply.isPending}>
              Huỷ
            </Button>
            <Button type="primary" danger disabled={blocked} loading={apply.isPending} onClick={() => apply.mutate()}>
              Xác nhận áp dụng
            </Button>
          </Space>
        )
      }
    >
      {body}
    </Modal>
  );
}
