import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { useState } from "react";
import {
  fetchData,
  resultStatusLabels,
  type DataCapability,
  type DataResultView,
  type SecurityIdentity,
} from "../../api/marketData";
import EvidenceDrawer from "../EvidenceDrawer";
import SecurityPicker from "../SecurityPicker";
import ErrorNotice from "../ErrorNotice";

const fetchActions: { capability: DataCapability; label: string; hint: string }[] = [
  { capability: "price_snapshot", label: "行情快照", hint: "最新成交与涨跌（60 秒缓存）" },
  { capability: "price_history", label: "近期日线", hint: "最近 30 个自然日的日线区间" },
  { capability: "valuations", label: "估值快照", hint: "PE / PB / PS / PCF（60 秒缓存）" },
  { capability: "financial_statements", label: "财务三表", hint: "最近 4 期年报，逐表出结果" },
];

function lastNDays(n: number): { start: number; end: number } {
  const dayMs = 24 * 60 * 60 * 1000;
  const end = Date.now();
  return { start: end - n * dayMs, end };
}

export default function DataPreview() {
  const [thscode, setThscode] = useState<string | null>(null);
  const [identity, setIdentity] = useState<SecurityIdentity | null>(null);
  const [results, setResults] = useState<DataResultView[]>([]);
  const [loading, setLoading] = useState<DataCapability | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const run = async (capability: DataCapability) => {
    if (!thscode) return;
    setLoading(capability);
    setError(null);
    try {
      const window = capability === "price_history" ? lastNDays(30) : {};
      const body = await fetchData({
        capability,
        thscode,
        ...(capability === "price_history" ? window : {}),
        ...(capability === "financial_statements" ? { period: "annual" as const, statement: "all" as const } : {}),
      });
      setResults(body.results);
      setDrawerOpen(true);
    } catch (failure) {
      setError(failure);
    } finally {
      setLoading(null);
    }
  };

  return (
    <Card
      title="选标的、取研究资料"
      extra={identity ? <Tag color="blue">{identity.name}</Tag> : <Tag>未确认标的</Tag>}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="large">
        <SecurityPicker
          value={thscode}
          onChange={(code, confirmed) => {
            setThscode(code);
            setIdentity(confirmed);
            setResults([]);
          }}
        />
        <Space size="small" wrap>
          {fetchActions.map((action) => (
            <Button
              key={action.capability}
              disabled={!thscode}
              loading={loading === action.capability}
              onClick={() => void run(action.capability)}
              title={action.hint}
            >
              {action.label}
            </Button>
          ))}
        </Space>
        <Typography.Text type="secondary">
          取数需先确认标的。缺失数据会以“缺失原因 + 影响 + 建议”呈现，不会用 0 或估算填充。
        </Typography.Text>
        {error ? <ErrorNotice error={error} retry={() => thscode && void run("price_snapshot")} /> : null}
        {results.length > 0 && (
          <Space size="small" wrap>
            {results.map((result, index) => (
              <Tag key={index} color={result.status === "available" ? "green" : result.status === "partial" ? "orange" : "red"}>
                {resultStatusLabels[result.status]} · {result.requested_scope.capability}
              </Tag>
            ))}
            <Button type="link" onClick={() => setDrawerOpen(true)}>
              查看数据与证据
            </Button>
          </Space>
        )}
        {results.some((result) => result.status === "unavailable") && (
          <Alert
            type="info"
            showIcon
            message="部分能力未取得数据"
            description="缺失以证据缺口形式记录，可在数据与证据抽屉中查看原因与建议。"
          />
        )}
      </Space>
      <EvidenceDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} results={results} />
    </Card>
  );
}
