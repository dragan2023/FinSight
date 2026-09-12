import { useMemo, useState } from "react";
import type { Key } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Empty, Input, Modal, Space, Table, Tag, Typography } from "antd";
import { Link } from "react-router-dom";
import { listIdentities, type SecurityIdentity } from "../../api/marketData";
import ErrorNotice from "../ErrorNotice";

export interface SecurityPickerModalProps {
  open: boolean;
  /** 多选模式；默认单选。 */
  multiple?: boolean;
  /** 仅显示这些 asset_type（如 ["a-share"]）；缺省显示全部已确认标的。 */
  assetTypes?: string[];
  /** 弹窗标题；缺省按模式生成。 */
  title?: string;
  onCancel: () => void;
  onConfirm: (selected: SecurityIdentity[]) => void;
}

const ASSET_TYPE_LABEL: Record<string, string> = {
  "a-share": "A股",
  "a-share-index": "指数",
  "fund-otc": "场外基金",
  "fund-etf": "ETF",
  "fund-lof": "LOF",
};

/**
 * 已确认标的表单：列出本机确认过的证券，支持搜索与多选，
 * 确认后由调用方把代码填回各自的表单。数据来自 /data/identities，
 * 只在本机读取，不发起外部请求。
 */
export default function SecurityPickerModal({
  open,
  multiple = false,
  assetTypes,
  title,
  onCancel,
  onConfirm,
}: SecurityPickerModalProps) {
  const [search, setSearch] = useState("");
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const query = useQuery({
    queryKey: ["confirmed-identities"],
    queryFn: listIdentities,
    enabled: open,
  });

  const rows = useMemo(() => {
    const list = query.data?.identities ?? [];
    const keyword = search.trim().toUpperCase();
    return list
      .filter((item) => (assetTypes ? assetTypes.includes(item.asset_type) : true))
      .filter(
        (item) =>
          !keyword ||
          item.thscode.includes(keyword) ||
          item.name.toUpperCase().includes(keyword) ||
          item.ticker.includes(keyword),
      )
      .sort((a, b) => a.thscode.localeCompare(b.thscode));
  }, [query.data, search, assetTypes]);

  const pickedRecords = useMemo(() => {
    const byCode = new Map(rows.map((row) => [row.thscode, row]));
    return selectedKeys
      .map((key) => byCode.get(String(key)))
      .filter((item): item is SecurityIdentity => Boolean(item));
  }, [rows, selectedKeys]);

  const close = () => {
    setSearch("");
    setSelectedKeys([]);
    onCancel();
  };

  const confirm = () => {
    if (pickedRecords.length === 0) return;
    const chosen = [...pickedRecords];
    setSearch("");
    setSelectedKeys([]);
    onConfirm(chosen);
  };

  const toggleRow = (thscode: string) => {
    setSelectedKeys((previous) => {
      if (previous.includes(thscode)) {
        return previous.filter((key) => key !== thscode);
      }
      return multiple ? [...previous, thscode] : [thscode];
    });
  };

  const columns = [
    { title: "代码", dataIndex: "thscode", width: 120 },
    { title: "名称", dataIndex: "name" },
    {
      title: "类型",
      dataIndex: "asset_type",
      width: 100,
      render: (value: string) => <Tag>{ASSET_TYPE_LABEL[value] ?? value}</Tag>,
    },
    {
      title: "确认时间",
      dataIndex: "confirmed_at",
      width: 160,
      render: (value: string) => (value ? value.slice(0, 19).replace("T", " ") : "--"),
    },
  ];

  return (
    <Modal
      open={open}
      title={title ?? (multiple ? "选择已确认标的（可多选）" : "选择已确认标的")}
      onCancel={close}
      width={640}
      footer={
        <Space>
          <Button onClick={close}>取消</Button>
          <Button type="primary" disabled={pickedRecords.length === 0} onClick={confirm}>
            {pickedRecords.length > 0 ? "填入表单（" + pickedRecords.length + "）" : "填入表单"}
          </Button>
        </Space>
      }
    >
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Input
          aria-label="搜索已确认标的"
          allowClear
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="按代码或名称过滤，例如 600519 或 茅台"
        />
        {query.error ? <ErrorNotice error={query.error} retry={() => void query.refetch()} /> : null}
        {!query.error && query.data && rows.length === 0 ? (
          <Empty
            description={
              <Typography.Text type="secondary">
                还没有符合条件的已确认标的。请先到{" "}
                <Link to="/" onClick={close}>
                  研究概览 · 数据预览
                </Link>{" "}
                检索并确认标的。
              </Typography.Text>
            }
          />
        ) : null}
        <Table
          size="small"
          aria-label="已确认标的列表"
          rowKey="thscode"
          loading={query.isPending}
          dataSource={rows}
          columns={columns}
          pagination={{ pageSize: 8, hideOnSinglePage: true, showSizeChanger: false }}
          onRow={(record) => ({ onClick: () => toggleRow(record.thscode) })}
          rowSelection={{
            type: multiple ? "checkbox" : "radio",
            selectedRowKeys: selectedKeys,
            onChange: (keys: Key[]) => setSelectedKeys([...keys]),
            columnWidth: 40,
          }}
        />
      </Space>
    </Modal>
  );
}
