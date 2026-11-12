import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { App, Button, Card, Col, Input, List, Popconfirm, Row, Space, Table, Tag, Typography, Upload } from "antd";
import { DeleteOutlined, DownloadOutlined, PlusOutlined, SaveOutlined, UploadOutlined } from "@ant-design/icons";
import { api } from "../api";
import { useAuth } from "../auth";
import { useSave } from "../components/common";

const PLATE_PATTERN = /^[0-9]{2}[A-Z]{1,2}[0-9]{4,6}$/;

function normalise(value: string): string {
  return value.trim().toUpperCase().replace(/[-.\s]/g, "");
}

export default function PlatesPage() {
  const { isAdmin } = useAuth();
  const { message } = App.useApp();
  const lists = useQuery({ queryKey: ["plate-lists"], queryFn: api.plateLists });
  const [selected, setSelected] = useState<number | null>(null);
  const plates = useQuery({
    queryKey: ["plates", selected],
    queryFn: () => api.plates(selected!),
    enabled: selected !== null,
  });
  const [draft, setDraft] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [newPlate, setNewPlate] = useState("");
  const [newList, setNewList] = useState("");

  useEffect(() => {
    if (selected === null && lists.data?.length) setSelected(lists.data[0].id);
  }, [lists.data, selected]);
  useEffect(() => setDraft(plates.data?.plates ?? []), [plates.data]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(plates.data?.plates ?? []);
  const shown = useMemo(() => draft.filter((plate) => plate.includes(normalise(search))), [draft, search]);
  const unusual = draft.filter((plate) => !PLATE_PATTERN.test(plate)).length;

  const save = useSave(() => api.replacePlates(selected!, draft), "Đã lưu danh sách (chưa áp dụng)");
  const create = useSave((name: string) => api.createPlateList(name), "Đã tạo danh sách");
  const remove = useSave((id: number) => api.deletePlateList(id), "Đã xoá danh sách");

  const add = () => {
    const plate = normalise(newPlate);
    if (!plate) return;
    if (draft.includes(plate)) {
      message.warning(`${plate} đã có trong danh sách`);
      return;
    }
    setDraft([plate, ...draft]);
    setNewPlate("");
  };

  const importFile = async (file: File) => {
    const text = await file.text();
    const imported = text.split(/\r?\n/).map(normalise).filter(Boolean);
    const merged = [...new Set([...draft, ...imported])];
    message.info(`Đã thêm ${merged.length - draft.length} biển số mới từ ${file.name} (bỏ ${imported.length - (merged.length - draft.length)} trùng)`);
    setDraft(merged);
    return false;
  };

  return (
    <Row gutter={16}>
      <Col xs={24} md={7}>
        <Card size="small" title="Danh sách">
          <List
            size="small"
            dataSource={lists.data ?? []}
            renderItem={(item) => (
              <List.Item
                onClick={() => setSelected(item.id)}
                style={{ cursor: "pointer", background: item.id === selected ? "#e6f4ff" : undefined, paddingInline: 8 }}
                actions={
                  isAdmin
                    ? [
                        <Popconfirm key="d" title={`Xoá danh sách ${item.name}?`} onConfirm={() => remove.mutate(item.id)}>
                          <DeleteOutlined />
                        </Popconfirm>,
                      ]
                    : []
                }
              >
                <span>
                  {item.name} <Typography.Text type="secondary">({item.plate_count})</Typography.Text>
                </span>
              </List.Item>
            )}
          />
          {isAdmin && (
            <Space.Compact style={{ width: "100%", marginTop: 8 }}>
              <Input placeholder="tên danh sách mới" value={newList} onChange={(e) => setNewList(e.target.value)} />
              <Button icon={<PlusOutlined />} disabled={!/^[A-Za-z0-9_-]+$/.test(newList)} onClick={() => create.mutate(newList, { onSuccess: () => setNewList("") })} />
            </Space.Compact>
          )}
        </Card>
      </Col>
      <Col xs={24} md={17}>
        <Card
          size="small"
          title={
            <Space>
              {lists.data?.find((item) => item.id === selected)?.file_name}
              <Tag>{draft.length} biển số</Tag>
              {unusual > 0 && <Tag color="warning">{unusual} biển số khác định dạng thường gặp</Tag>}
              {dirty && <Tag color="orange">chưa lưu</Tag>}
            </Space>
          }
          extra={
            <Space>
              {selected !== null && (
                <Button size="small" icon={<DownloadOutlined />} href={api.exportPlatesUrl(selected)} download>
                  Xuất .txt
                </Button>
              )}
              {isAdmin && (
                <Upload accept=".txt" showUploadList={false} beforeUpload={importFile}>
                  <Button size="small" icon={<UploadOutlined />}>
                    Nhập .txt
                  </Button>
                </Upload>
              )}
              {isAdmin && (
                <Button size="small" type="primary" icon={<SaveOutlined />} disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(undefined)}>
                  Lưu
                </Button>
              )}
            </Space>
          }
        >
          <Space style={{ marginBottom: 12 }} wrap>
            <Input.Search placeholder="Tìm biển số" allowClear value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 220 }} />
            {isAdmin && (
              <Space.Compact>
                <Input placeholder="29H12345" value={newPlate} onChange={(e) => setNewPlate(e.target.value)} onPressEnter={add} />
                <Button icon={<PlusOutlined />} onClick={add}>
                  Thêm
                </Button>
              </Space.Compact>
            )}
          </Space>
          <Table
            size="small"
            loading={plates.isLoading}
            rowKey={(plate) => plate}
            dataSource={shown}
            pagination={{ pageSize: 50, showSizeChanger: false }}
            columns={[
              {
                title: "Biển số",
                render: (_, plate: string) => (
                  <Space>
                    <code>{plate}</code>
                    {!PLATE_PATTERN.test(plate) && <Tag color="warning">định dạng lạ</Tag>}
                  </Space>
                ),
              },
              {
                title: "",
                align: "right",
                render: (_, plate: string) =>
                  isAdmin && <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => setDraft(draft.filter((p) => p !== plate))} />,
              },
            ]}
          />
        </Card>
      </Col>
    </Row>
  );
}
