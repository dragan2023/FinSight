import { useQuery } from "@tanstack/react-query";
import { Button, Input, List, Select, Space, Tag, Typography } from "antd";
import { useState } from "react";
import {
  confirmTicker,
  listIdentities,
  resolveTickers,
  type SecurityIdentity,
  type TickerCandidate,
} from "../../api/marketData";
import ErrorNotice from "../ErrorNotice";

interface SecurityPickerProps {
  value?: string | null;
  onChange?: (thscode: string, identity: SecurityIdentity | null) => void;
}

export default function SecurityPicker({ value, onChange }: SecurityPickerProps) {
  const [keyword, setKeyword] = useState("");
  const [candidates, setCandidates] = useState<TickerCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const identitiesQuery = useQuery({ queryKey: ["data-identities"], queryFn: listIdentities });
  const identities = identitiesQuery.data?.identities ?? [];
  const selected = identities.find((item) => item.thscode === value) ?? null;

  const search = async () => {
    const query = keyword.trim();
    if (!query) return;
    setSearching(true);
    try {
      const body = await resolveTickers(query);
      setCandidates(body.candidates);
    } finally {
      setSearching(false);
    }
  };

  const confirm = async (candidate: TickerCandidate) => {
    setConfirming(candidate.thscode);
    try {
      const body = await confirmTicker(candidate.thscode);
      await identitiesQuery.refetch();
      onChange?.(body.identity.thscode, body.identity);
    } finally {
      setConfirming(null);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space.Compact style={{ width: "100%" }}>
        <Input
          aria-label="标的检索"
          placeholder="输入代码或名称，如 600519 / 贵州茅台"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onPressEnter={() => void search()}
          allowClear
        />
        <Button type="primary" loading={searching} onClick={() => void search()}>
          检索
        </Button>
      </Space.Compact>
      {identities.length > 0 && (
        <Select<string>
          aria-label="已确认标的"
          style={{ width: "100%" }}
          placeholder="选择已确认的标的"
          value={value ?? undefined}
          onChange={(thscode) => onChange?.(thscode, identities.find((item) => item.thscode === thscode) ?? null)}
          options={identities.map((item) => ({ value: item.thscode, label: `${item.name}（${item.thscode}）` }))}
        />
      )}
      {candidates.length > 0 && (
        <List
          size="small"
          dataSource={candidates}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="confirm"
                  size="small"
                  loading={confirming === item.thscode}
                  onClick={() => void confirm(item)}
                >
                  确认
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space size="small">
                    <span>{item.name}</span>
                    <Typography.Text type="secondary">{item.thscode}</Typography.Text>
                  </Space>
                }
                description={item.currency}
              />
              <Tag>{item.asset_type}</Tag>
            </List.Item>
          )}
        />
      )}
      {selected && (
        <Typography.Text type="secondary">
          当前已确认：{selected.name}（{selected.thscode}）· 确认时间 {selected.confirmed_at.slice(0, 19).replace("T", " ")}
        </Typography.Text>
      )}
      {identitiesQuery.error && (
        <ErrorNotice error={identitiesQuery.error} retry={() => void identitiesQuery.refetch()} />
      )}
    </Space>
  );
}
