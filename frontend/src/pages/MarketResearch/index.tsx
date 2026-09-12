import { useCallback, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Input,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import { useQuery } from "@tanstack/react-query";
import {
  compareIndices,
  getIndexCatalog,
  getIndexResearch,
  refreshIndex,
  refreshIndexCatalog,
  selectIndex,
  type IndexCatalog,
  type IndexComparison,
  type IndexResearch,
} from "../../api/indices";
import ErrorNotice from "../../components/ErrorNotice";
import HeatmapChart from "../../components/charts/HeatmapChart";
import ScatterChart from "../../components/charts/ScatterChart";
import TimeSeriesChart from "../../components/charts/TimeSeriesChart";
import DivergingBarsChart from "../../components/charts/DivergingBarsChart";
import ToneBarsChart from "../../components/charts/ToneBarsChart";
import { ChartPanel, Provenance } from "../../components/charts/panels";
import { moduleAccent } from "../../components/charts/tokens";
import MetricTable from "../../components/MetricTable";
import {
  getLatestMarketObservation,
  refreshMarketObservation,
  type MarketObservation,
} from "../../api/marketReview";
import MarketReviewDashboard, {
  MarketReviewDashboardBoundary,
} from "../../components/MarketReviewDashboard";
import { isMarketReviewDashboard } from "../../components/MarketReviewDashboard/types";

function SectorResearch() {
  const [tag, setTag] = useState("industry");
  const [selected, setSelected] = useState<string[]>([]);
  const [focus, setFocus] = useState<string>();
  const [research, setResearch] = useState<IndexResearch | null>(null);
  const [comparison, setComparison] = useState<IndexComparison | null>(null);
  const [memberSnapshots, setMemberSnapshots] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const catalogQuery = useQuery({
    queryKey: ["index-catalog", tag],
    queryFn: () => getIndexCatalog(tag),
    retry: false,
  });
  const catalog: IndexCatalog | null = catalogQuery.data ?? null;
  const loadCatalog = async () => {
    await catalogQuery.refetch();
  };
  const refreshCatalog = async () => {
    try {
      setBusy(true);
      setError(null);
      await refreshIndexCatalog(tag);
      await loadCatalog();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const refreshFocus = async () => {
    if (!focus) return;
    try {
      setBusy(true);
      setError(null);
      await selectIndex(focus);
      await refreshIndex(focus, {
        history: true,
        constituents: true,
        member_snapshots: memberSnapshots,
      });
      setResearch(await getIndexResearch(focus));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const compare = async () => {
    if (selected.length < 2) return;
    try {
      setBusy(true);
      setError(null);
      for (const code of selected) await selectIndex(code);
      setComparison(
        await compareIndices(
          selected,
          focus && selected.includes(focus) ? focus : selected[0],
        ),
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const name = useCallback(
    (code: string) =>
      catalog?.items.find((item) => item.thscode === code)?.name ?? code,
    [catalog],
  );
  const heatRows = useMemo(
    () =>
      comparison
        ? selected.map((code) => ({
            name: name(code),
            values: [5, 20, 60].map((days) => {
              const value =
                comparison.comparison.period_returns[code]?.[days + "d"];
              const parsed = Number(value);
              return value === null ||
                value === undefined ||
                !Number.isFinite(parsed)
                ? null
                : parsed;
            }),
          }))
        : [],
    [comparison, name, selected],
  );
  const quadrantPoints = useMemo(
    () =>
      comparison
        ? selected.map((code) => {
            const d20 = Number(
              comparison.comparison.period_returns[code]?.["20d"],
            );
            const d5 = Number(
              comparison.comparison.period_returns[code]?.["5d"],
            );
            return {
              name: name(code),
              x: Number.isFinite(d20) ? d20 : null,
              y: Number.isFinite(d5) ? d5 : null,
            };
          })
        : [],
    [comparison, name, selected],
  );
  const returnBars = useMemo(
    () =>
      comparison
        ? selected.map((code) => {
            const value = Number(comparison.comparison.returns[code]);
            return {
              label: name(code),
              value: Number.isFinite(value) ? value : null,
              note:
                comparison.comparison.selected_ranks[code] === null ||
                comparison.comparison.selected_ranks[code] === undefined
                  ? null
                  : "共同窗口排名 " +
                    comparison.comparison.selected_ranks[code] +
                    " / " +
                    selected.length,
            };
          })
        : [],
    [comparison, name, selected],
  );
  const history = useMemo(() => research?.history?.prices ?? {}, [research]);
  const historyLabels = useMemo(() => Object.keys(history).sort(), [history]);
  const historyValues = useMemo(
    () =>
      historyLabels.map((key) => {
        const parsed = Number(history[key]);
        return history[key] === null || !Number.isFinite(parsed)
          ? null
          : parsed;
      }),
    [historyLabels, history],
  );
  const breadthItems = useMemo(() => {
    if (!research) return [];
    const pick = (key: string) => {
      const value = research.breadth[key];
      const parsed = Number(value);
      return value === null || value === undefined || !Number.isFinite(parsed)
        ? null
        : parsed;
    };
    return [
      { label: "上涨", value: pick("up"), tone: "up" as const },
      { label: "下跌", value: pick("down"), tone: "down" as const },
      { label: "平盘", value: pick("flat"), tone: "flat" as const },
    ];
  }, [research]);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">INDEX & CROSS-SECTION RESEARCH</div>
          <h1>指数板块与横截面研究</h1>
          <p>页面只读取本地证据；只有明确点击刷新时才调用数据源。</p>
        </div>
      </div>
      <Alert
        type="info"
        showIcon
        title="当前成分限制"
        description="当前成分回看存在存活者偏差。没有历史成分和权重时，仅展示等权涨跌代理，不展示行业贡献、alpha 或推荐。"
      />
      <Card title="1. 选择指数范围" style={{ marginTop: 18 }}>
        <Space orientation="vertical" style={{ width: "100%" }}>
          <Space wrap>
            <Select
              aria-label="指数类型"
              value={tag}
              onChange={(value) => {
                setTag(value);
                setSelected([]);
                setFocus(undefined);
              }}
              options={["industry", "cn_concept", "region", "tszs"].map(
                (value) => ({ value, label: value }),
              )}
            />
            <Button onClick={() => void refreshCatalog()} loading={busy}>
              显式刷新目录
            </Button>
          </Space>
          <Typography.Text type="secondary">
            {catalog?.notice ?? "尚未取得本地目录。"}
          </Typography.Text>
          <Select
            aria-label="所选行业指数"
            mode="multiple"
            maxCount={20}
            value={selected}
            onChange={(values) => {
              setSelected(values);
              setFocus(values[0]);
              setResearch(null);
            }}
            options={(catalog?.items ?? []).map((item) => ({
              value: item.thscode,
              label: item.name + " (" + item.thscode + ")",
            }))}
            placeholder="先刷新目录，再选择 2–20 个指数"
            style={{ width: "100%" }}
          />
          <Checkbox
            checked={memberSnapshots}
            onChange={(event) => setMemberSnapshots(event.target.checked)}
          >
            同时刷新当前成分股票快照（可部分失败）
          </Checkbox>
          <Space wrap>
            <Button
              type="primary"
              disabled={!focus}
              loading={busy}
              onClick={() => void refreshFocus()}
            >
              刷新选中指数与成分
            </Button>
            <Button
              disabled={selected.length < 2}
              loading={busy}
              onClick={() => void compare()}
            >
              比较所选行业
            </Button>
            {focus && (
              <a
                href={
                  "/research?scope=sector_breadth&thscode=" +
                  encodeURIComponent(focus)
                }
              >
                基于当前资料开始研究
              </a>
            )}
          </Space>
        </Space>
      </Card>
      {(error ?? catalogQuery.error) && (
        <ErrorNotice
          error={error ?? catalogQuery.error}
          retry={() => void (focus ? refreshFocus() : loadCatalog())}
        />
      )}
      {comparison && (
        <>
          <ChartPanel
            eyebrow="STRENGTH x ACCELERATION"
            title="行业强度作战矩阵"
            scope={comparison.ranking_scope + "；" + comparison.limitation}
            tone={moduleAccent.sector}
            footnote={
              <Provenance>
                横轴 = 20 日区间收益（中期强度），纵轴 = 5
                日区间收益（短期加速度）；右上 = 强且加速，左下 =
                弱且退潮。象限仅辅助观察，不产生强弱评级之外的结论。
              </Provenance>
            }
          >
            <ScatterChart
              points={quadrantPoints}
              mode="quadrant"
              xLabel="20 日区间收益"
              yLabel="5 日区间收益"
              height={360}
              ariaLabel="行业强度与加速度象限散点图"
            />
          </ChartPanel>
          <ChartPanel
            eyebrow="PERIOD HEAT STRIP"
            title="5 / 20 / 60 日热力带"
            scope="红=强势、绿=弱势；空单元格表示该周期可用数据不足，未做填补。"
            tone={moduleAccent.sector}
            footnote={
              <Provenance>数值为区间涨跌（比值口径），与象限同源。</Provenance>
            }
          >
            <HeatmapChart rows={heatRows} />
            <Table
              size="small"
              style={{ marginTop: 12 }}
              pagination={false}
              rowKey="name"
              dataSource={heatRows}
              columns={[
                { title: "行业", dataIndex: "name" },
                ...[0, 1, 2].map((index) => ({
                  title: [5, 20, 60][index] + " 日",
                  render: (
                    _: unknown,
                    row: { values: Array<number | null> },
                  ) =>
                    row.values[index] === null
                      ? "--"
                      : ((row.values[index] as number) * 100).toFixed(2) + "%",
                })),
              ]}
            />
          </ChartPanel>
          <ChartPanel
            eyebrow="COMMON WINDOW RETURNS"
            title="共同窗口表现分流"
            scope="以同一段共同日期窗口比较；正值在右（红），负值在左（绿）。"
            tone={moduleAccent.sector}
            footnote={
              <Provenance>排名基于本组所选指数，不代表全市场排序。</Provenance>
            }
          >
            <DivergingBarsChart
              items={returnBars.map((bar) => ({
                label: bar.label,
                value: bar.value,
                note: bar.note,
              }))}
              valueFormat={(value) => (value * 100).toFixed(2) + "%"}
              ariaLabel="所选行业共同窗口收益分流条形图"
            />
          </ChartPanel>
        </>
      )}
      {research && (
        <>
          <ChartPanel
            eyebrow="INDEX TREND"
            title={(focus ?? "指数") + " 走势"}
            scope="仅绘制已保存历史收盘价；缺口日期不连线。"
            tone={moduleAccent.sector}
            footnote={<Provenance>数据来自本地证据，不实时刷新。</Provenance>}
          >
            <TimeSeriesChart
              labels={historyLabels}
              values={historyValues}
              name={focus ?? "指数"}
              unit="CNY"
              height={300}
              ariaLabel="指数历史走势折线图"
            />
          </ChartPanel>
          <ChartPanel
            eyebrow="CONSTITUENT BREADTH"
            title="当前成分涨跌分布"
            scope={research.limitation}
            tone={moduleAccent.sector}
            footnote={
              <Provenance>
                等权涨跌代理口径；已覆盖 n / 当前成分 N 见表。
              </Provenance>
            }
          >
            <ToneBarsChart
              items={breadthItems}
              unit=" 家"
              height={220}
              ariaLabel="当前成分涨跌分布柱状图"
            />
            <MetricTable
              metrics={Object.entries(research.breadth)
                .filter(([key]) =>
                  [
                    "up",
                    "down",
                    "flat",
                    "unknown",
                    "covered",
                    "total",
                  ].includes(key),
                )
                .map(([metric, value]) => ({
                  metric,
                  value: value === null ? null : String(value),
                  note: metric === "covered" ? "已覆盖 n / 当前成分 N" : null,
                }))}
            />
          </ChartPanel>
        </>
      )}
    </>
  );
}

function MarketReview() {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [observation, setObservation] = useState<MarketObservation | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const latest = useQuery({
    queryKey: ["market-review-latest"],
    queryFn: getLatestMarketObservation,
    retry: false,
  });
  const refresh = async () => {
    const dateMs = Date.parse(date + "T00:00:00Z");
    if (!Number.isFinite(dateMs)) return;
    try {
      setBusy(true);
      setError(null);
      const response = await refreshMarketObservation({
        date_ms: dateMs,
        modules: [],
        max_calls: 6,
      });
      setObservation({
        id: "current",
        date,
        modules: Object.keys(response.summary),
        summary: response.summary,
        dashboard: response.dashboard,
        evidence_ids: [],
        created_at: "刚刚",
      });
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  };
  const current = observation ?? latest.data?.observation ?? null;
  const dashboard = isMarketReviewDashboard(current?.dashboard)
    ? current.dashboard
    : null;
  const emptyMessage = current
    ? "这条历史复盘记录尚未生成完整图表摘要。请点击“显式刷新盘后观察”，由本机重新计算市场结构、连板梯队、热度排名和龙虎榜分布。"
    : "尚无可视化复盘。选择日期并显式刷新后，本机将生成市场结构、连板梯队、热度排名和龙虎榜分布。";
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">MARKET OBSERVATION</div>
          <h1>市场复盘</h1>
          <p>
            默认只读本地事实；只有明确刷新时才获取数据，并由本机转换为可阅读的市场结构指标。
          </p>
        </div>
      </div>
      <Card>
        <Space wrap>
          <Input
            aria-label="复盘日期"
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
          <Button type="primary" loading={busy} onClick={() => void refresh()}>
            显式刷新盘后观察
          </Button>
          <a
            href={
              "/research?scope=market_pulse&end_ms=" +
              Date.parse(date + "T00:00:00Z")
            }
          >
            基于当前资料开始研究
          </a>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
          默认范围：涨停、跌停、炸板、连板梯队、热度榜与 1
          日龙虎榜。不同统计窗口不会相加；字段无法支持的结论会明确留空。
        </Typography.Paragraph>
      </Card>
      {(error ?? latest.error) && (
        <ErrorNotice
          error={error ?? latest.error}
          retry={() => void refresh()}
        />
      )}
      {dashboard ? (
        <div style={{ marginTop: 18 }}>
          <MarketReviewDashboardBoundary>
            <MarketReviewDashboard dashboard={dashboard} />
          </MarketReviewDashboardBoundary>
        </div>
      ) : (
        <Card style={{ marginTop: 18 }}>
          <Typography.Text type="secondary">{emptyMessage}</Typography.Text>
        </Card>
      )}
    </>
  );
}

export default function MarketResearch({
  kind,
}: {
  kind: "sectors" | "market";
}) {
  return kind === "sectors" ? <SectorResearch /> : <MarketReview />;
}
