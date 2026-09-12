import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Popconfirm,
  Space,
  Table,
  Typography,
} from "antd";
import { useQuery } from "@tanstack/react-query";
import ErrorNotice from "../../components/ErrorNotice";
import RankBarsChart from "../../components/charts/RankBarsChart";
import ToneBarsChart from "../../components/charts/ToneBarsChart";
import {
  ChartPanel,
  KpiCards,
  Provenance,
} from "../../components/charts/panels";
import { finNum, moduleAccent } from "../../components/charts/tokens";
import {
  cancelWarehouseSync,
  clearWarehouseDerived,
  getWarehouseStatus,
  getWarehouseSync,
  importWarehouseParquet,
  queryWarehouse,
  syncWarehouse,
  type WarehouseSyncRun,
} from "../../api/marketWarehouse";

export default function MarketExplorer() {
  const status = useQuery({
    queryKey: ["market-warehouse-status"],
    queryFn: getWarehouseStatus,
    retry: false,
  });
  const [date, setDate] = useState("");
  const [data, setData] = useState<{
    breadth: Record<string, unknown>;
    liquidity: Record<string, unknown>;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [syncRun, setSyncRun] = useState<WarehouseSyncRun | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  useEffect(() => {
    if (!syncRun || !["queued", "running"].includes(syncRun.status)) return;
    const timer = window.setTimeout(() => {
      void getWarehouseSync(syncRun.id)
        .then(async (run) => {
          setSyncRun(run);
          if (run.status === "completed") await status.refetch();
        })
        .catch(setError);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [status, syncRun]);
  const sync = async () => {
    try {
      setBusy(true);
      setError(null);
      setSyncRun(await syncWarehouse("daily_k_10d"));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const syncFullHistory = async () => {
    try {
      setBusy(true);
      setError(null);
      setSyncRun(await syncWarehouse("daily_k"));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const syncAdjustmentFactors = async () => {
    try {
      setBusy(true);
      setError(null);
      setSyncRun(await syncWarehouse("adjustment_factors"));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const importParquet = async () => {
    if (!importFile) return;
    try {
      setBusy(true);
      setError(null);
      const result = await importWarehouseParquet(importFile);
      setImportFile(null);
      await status.refetch();
      setSyncRun({
        id: result.sync_id,
        kind: "daily_k_import",
        status: result.status === "completed" ? "completed" : "failed",
        created_at: new Date().toISOString(),
      });
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const cancelSync = async () => {
    if (!syncRun) return;
    try {
      setBusy(true);
      setError(null);
      setSyncRun(await cancelWarehouseSync(syncRun.id));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const clearDerived = async () => {
    try {
      setBusy(true);
      setError(null);
      await clearWarehouseDerived();
      await status.refetch();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const query = async () => {
    const dateMs = Date.parse(date + "T00:00:00Z");
    if (!Number.isFinite(dateMs)) return;
    try {
      setBusy(true);
      setError(null);
      const [breadth, liquidity] = await Promise.all([
        queryWarehouse({ date_ms: dateMs, metric: "breadth" }),
        queryWarehouse({ date_ms: dateMs, metric: "liquidity", page_size: 10 }),
      ]);
      setData({ breadth, liquidity });
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const breadth = data?.breadth;
  const count = (key: string) => Number(breadth?.[key] ?? 0);
  const sample = count("sample");
  const liquidItems = useMemo(
    () =>
      Array.isArray(data?.liquidity.items)
        ? (data.liquidity.items as Array<{
            thscode: string;
            close_price: number | null;
            turnover: number | null;
          }>)
        : [],
    [data],
  );

  const kpis = useMemo(() => {
    const get = (key: string) => Number(data?.breadth[key] ?? 0);
    const sampleCount = get("sample");
    const rate = sampleCount ? (get("up") / sampleCount) * 100 : null;
    return [
      {
        label: "上涨家数",
        value: finNum(get("up"), 0),
        sub: "收盘价高于开盘价",
        tone: "up" as const,
      },
      {
        label: "下跌家数",
        value: finNum(get("down"), 0),
        sub: "收盘价低于开盘价",
        tone: "down" as const,
      },
      {
        label: "平盘家数",
        value: finNum(get("flat"), 0),
        sub: "收盘价等于开盘价",
      },
      {
        label: "上涨占比",
        value: rate === null ? "--" : finNum(rate, 1) + "%",
        sub: "已下载样本内",
      },
    ];
  }, [data]);

  const liquidityRank = useMemo(
    () =>
      liquidItems.map((item, index) => ({
        label: item.thscode,
        value: item.turnover === null ? null : item.turnover / 1e8,
        badge: "#" + (index + 1),
        note:
          item.close_price === null
            ? null
            : "收盘价 ¥" + finNum(item.close_price),
      })),
    [liquidItems],
  );

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">LOCAL MARKET WAREHOUSE</div>
          <h1>全市场观察</h1>
          <p>仅查询本机 DuckDB；不自动下载，日 K 固定未复权口径。</p>
        </div>
      </div>
      <Card title="数据状态">
        <Typography.Paragraph>
          {status.data?.available
            ? "本地数据可用：" + status.data.row_count + " 行"
            : "尚无本地全市场数据。无下载权限不会影响其他研究功能。"}
        </Typography.Paragraph>
        <Space wrap>
          <Popconfirm
            title="同步近 10 日全市场日 K？"
            description="用于市场宽度观察；会由同花顺下载并保存在本机 data/market。"
            onConfirm={() => void sync()}
          >
            <Button
              type="primary"
              loading={busy}
              disabled={Boolean(
                syncRun && ["queued", "running"].includes(syncRun.status),
              )}
            >
              显式同步近 10 日
            </Button>
          </Popconfirm>
          <Popconfirm
            title="同步全市场公司行动事件？"
            description="从同花顺 Financial API 下载除权除息、送配等原始事件并保存于本机；这不会自动生成复权价格、回测或荐股。"
            okText="开始同步事件"
            cancelText="取消"
            onConfirm={() => void syncAdjustmentFactors()}
          >
            <Button
              loading={busy}
              disabled={Boolean(syncRun && ["queued", "running"].includes(syncRun.status))}
            >
              同步公司行动事件（研究用）
            </Button>
          </Popconfirm>
          <Popconfirm
            title="同步全历史全市场日 K？"
            description="用于低换手横截面策略研究。数据量可能较大；下载完成前，不会自动开始回测或生成荐股。"
            okText="开始全历史同步"
            cancelText="取消"
            onConfirm={() => void syncFullHistory()}
          >
            <Button
              loading={busy}
              disabled={Boolean(
                syncRun && ["queued", "running"].includes(syncRun.status),
              )}
            >
              同步全历史（研究用）
            </Button>
          </Popconfirm>
          <input
            aria-label="选择全市场日K Parquet文件"
            type="file"
            accept=".parquet,application/vnd.apache.parquet"
            onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
          />
          <Popconfirm
            title="导入所选全市场日 K 文件？"
            description="文件仅在本机校验并写入本地仓库；不会上传到第三方，也不会自动回测或荐股。"
            okText="开始本机导入"
            cancelText="取消"
            onConfirm={() => void importParquet()}
          >
            <Button loading={busy} disabled={!importFile || busy}>
              导入所选 Parquet
            </Button>
          </Popconfirm>
          <Popconfirm
            title="清理可重建的派生数据？"
            description="不会删除原始日 K、SQLite 证据、配置或研究报告；如当前没有派生表，本操作不会删除任何数据。"
            okText="确认清理"
            cancelText="取消"
            onConfirm={() => void clearDerived()}
          >
            <Button danger loading={busy} disabled={!status.data?.available}>
              清理可重建派生数据
            </Button>
          </Popconfirm>
          {syncRun && (
            <Space>
              <Typography.Text type="secondary">
                同步状态：{syncRun.status}
              </Typography.Text>
              {["queued", "running"].includes(syncRun.status) && (
                <Button danger loading={busy} onClick={() => void cancelSync()}>
                  取消同步
                </Button>
              )}
            </Space>
          )}
        </Space>
        {syncRun?.status === "failed" && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            title="全市场同步失败"
            description={syncRun.failure_message || "未取得可诊断原因。请保留本次任务编号后重试。"}
          />
        )}
      </Card>
      <Card title="策略研究数据门禁" style={{ marginTop: 18 }}>
        {status.data && (
          <>
            <Alert
              type={status.data.readiness.price_panel.status === "ready_for_research" ? "info" : "warning"}
              showIcon
              title={status.data.readiness.price_panel.status === "ready_for_research" ? "价格面板可用于研究" : "价格面板尚不能用于横截面研究"}
              description={status.data.readiness.price_panel.status === "ready_for_research"
                ? status.data.readiness.price_panel.purpose
                : status.data.readiness.price_panel.missing.join("；")}
            />
            {status.data.available && (
              <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 12 }}>
                当前已入库 {status.data.row_count.toLocaleString()} 行，覆盖 {status.data.symbol_count?.toLocaleString() ?? 0} 只标的、{status.data.trading_day_count?.toLocaleString() ?? 0} 个交易日；最近交易日覆盖 {status.data.latest_day_symbol_count?.toLocaleString() ?? 0} 只。
                公司行动事件 {status.data.adjustment_factor_row_count?.toLocaleString() ?? 0} 条。
              </Typography.Paragraph>
            )}
            <Alert
              type="warning"
              showIcon
              title="当前数据不能证明“可以有效荐股”"
              description={status.data.readiness.recommendation_evidence.missing.join("；") + "。这些数据补齐并完成独立样本验证前，横截面结果只能标为研究结论。"}
              style={{ marginTop: 12 }}
            />
          </>
        )}
      </Card>
      {(error ?? status.error) && (
        <ErrorNotice
          error={error ?? status.error}
          retry={() => void status.refetch()}
        />
      )}
      <Card title="市场宽度与成交活跃度" style={{ marginTop: 18 }}>
        <Space wrap>
          <Input
            aria-label="全市场查询日期"
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
          <Button
            disabled={!status.data?.available}
            loading={busy}
            onClick={() => void query()}
          >
            生成当日全市场看板
          </Button>
          {date && (
            <a
              href={
                "/research?scope=market_breadth&end_ms=" +
                Date.parse(date + "T00:00:00Z")
              }
            >
              基于当前资料开始研究
            </a>
          )}
        </Space>
        {data && (
          <>
            <Alert
              style={{ marginTop: 16 }}
              type="info"
              showIcon
              title="本地计算的全市场横截面"
              description="涨跌家数以当日开盘价与收盘价比较；样本仅包含已下载的未复权人民币日 K，不代表实时行情。"
            />
            <div style={{ marginTop: 16 }}>
              <KpiCards items={kpis} />
            </div>
            <ChartPanel
              eyebrow="MARKET BREADTH"
              title="涨跌分布"
              scope="红=上涨、绿=下跌、灰=平盘；样本为当日已下载的未复权日 K。"
              tone={moduleAccent.review}
              footnote={
                <Provenance>
                  横截面统计由本机 DuckDB 计算；样本数 {sample} 只。
                </Provenance>
              }
            >
              <ToneBarsChart
                items={[
                  { label: "上涨", value: count("up"), tone: "up" },
                  { label: "下跌", value: count("down"), tone: "down" },
                  { label: "平盘", value: count("flat"), tone: "flat" },
                ]}
                unit=" 家"
                height={230}
                ariaLabel="全市场涨跌分布柱状图"
              />
            </ChartPanel>
            <ChartPanel
              eyebrow="LIQUIDITY TOP 10"
              title="成交额前十"
              scope="按当日成交额降序；成交额为未复权人民币日 K 汇总。"
              tone={moduleAccent.sector}
              footnote={
                <Provenance>
                  仅展示前十名；完整样本以研究工作台横截面为准。
                </Provenance>
              }
            >
              <RankBarsChart
                items={liquidityRank}
                accent={moduleAccent.sector}
                unit=" 亿"
                valueFormat={(value) => finNum(value)}
                ariaLabel="成交额前十排名条形图"
              />
              <Table
                size="small"
                style={{ marginTop: 12 }}
                pagination={false}
                rowKey="thscode"
                dataSource={liquidItems}
                columns={[
                  { title: "代码", dataIndex: "thscode" },
                  {
                    title: "收盘价",
                    dataIndex: "close_price",
                    render: (value) =>
                      value === null ? "--" : "¥" + Number(value).toFixed(2),
                  },
                  {
                    title: "成交额",
                    dataIndex: "turnover",
                    render: (value) =>
                      value === null
                        ? "--"
                        : (Number(value) / 1e8).toFixed(2) + " 亿元",
                  },
                ]}
              />
            </ChartPanel>
          </>
        )}
      </Card>
    </>
  );
}
