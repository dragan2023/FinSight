import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Input, Space, Spin, Tabs, Tag } from "antd";
import { companyResearch, type QualityDimension } from "../../api/company";
import ErrorNotice from "../../components/ErrorNotice";
import SecurityPickerModal from "../../components/SecurityPickerModal";
import CandlestickChart from "../../components/charts/CandlestickChart";
import {
  ChartPanel,
  KpiCards,
  Provenance,
} from "../../components/charts/panels";
import {
  finNum,
  finPct,
  finYi,
  moduleAccent,
  signTone,
} from "../../components/charts/tokens";
import { toNum } from "../../components/charts/series";
import DataFreshness from "../../components/DataFreshness";
import MetricTable from "../../components/MetricTable";

export default function Company() {
  const [code, setCode] = useState("");
  const [queryCode, setQueryCode] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const query = useQuery({
    queryKey: ["company", queryCode],
    queryFn: () => companyResearch(queryCode),
    enabled: !!queryCode,
  });
  const dimensions = query.data?.quality.dimensions ?? [];
  const overview = query.data?.overview;

  const candleBars = useMemo(
    () =>
      (overview?.bars ?? []).map((item) => ({
        date: item.period_key,
        open: toNum(item.fields.open_price?.value),
        close: toNum(item.fields.close_price?.value),
        low: toNum(item.fields.low_price?.value),
        high: toNum(item.fields.high_price?.value),
        turnover: toNum(item.fields.turnover?.value),
      })),
    [overview?.bars],
  );

  const metricValue = useMemo(() => {
    const map = new Map<string, number | null>();
    for (const metric of overview?.metrics ?? [])
      map.set(metric.metric, toNum(metric.value));
    return (name: string) => map.get(name) ?? null;
  }, [overview?.metrics]);

  const kpis = useMemo(() => {
    const closes = candleBars.map((bar) => bar.close);
    const lastClose =
      [...closes].reverse().find((value) => value !== null) ?? null;
    const firstClose = closes.find((value) => value !== null) ?? null;
    const windowChange =
      lastClose !== null && firstClose !== null && firstClose !== 0
        ? (lastClose - firstClose) / firstClose
        : null;
    const lastDate =
      [...candleBars].reverse().find((bar) => bar.close !== null)?.date ?? "--";
    const ma20 = metricValue("ma_20");
    const ma60 = metricValue("ma_60");
    const avgTurnover = metricValue("avg_turnover_20");
    const drawdown = metricValue("max_drawdown_60");
    const fromHigh = metricValue("distance_from_high_120");
    return [
      {
        label: "最新收盘",
        value: (
          <>
            {lastClose === null ? "--" : finNum(lastClose)}
            <small>CNY</small>
          </>
        ),
        sub: lastDate,
      },
      {
        label: "区间涨跌",
        value: finPct(windowChange),
        tone: signTone(windowChange),
        sub: "已保存首尾收盘价",
      },
      {
        label: "MA20 / MA60",
        value: (
          <>
            {ma20 === null ? "--" : finNum(ma20)} /{" "}
            {ma60 === null ? "--" : finNum(ma60)}
          </>
        ),
        sub: "近窗口收盘均值",
      },
      {
        label: "20日均成交额",
        value: <>{avgTurnover === null ? "--" : finYi(avgTurnover)}</>,
        sub: "近20个可得交易日",
      },
      {
        label: "近60日最大回撤",
        value: finPct(drawdown),
        tone: "down" as const,
        sub: "峰谷收盘口径",
      },
      {
        label: "距120日高点",
        value: finPct(fromHigh),
        tone:
          fromHigh !== null && fromHigh < 0
            ? ("down" as const)
            : ("flat" as const),
        sub: "收盘价相对高点",
      },
    ];
  }, [candleBars, metricValue]);

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">LOCAL COMPANY RESEARCH</div>
          <h1>公司财务体检</h1>
          <p>仅读取已保存证据；更新数据请在研究工作台主动发起。</p>
        </div>
      </div>
      <Space.Compact style={{ maxWidth: 460, width: "100%" }}>
        <Input
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="已确认标的，例如 600519.SH"
        />
        <Button onClick={() => setPickerOpen(true)}>选择标的</Button>
        <Button type="primary" onClick={() => setQueryCode(code.trim())}>
          读取本地资料
        </Button>
      </Space.Compact>
      {query.isPending && <Spin style={{ margin: 24 }} />}
      {query.error && (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      )}
      {query.data && (
        <>
          <Alert
            style={{ marginTop: 20 }}
            type="info"
            showIcon
            title={
              query.data.identity.name +
              " · " +
              query.data.evidence_ids.length +
              " 份本地证据"
            }
            description="不自动刷新、不联网、不生成交易建议。"
          />
          <a
            href={
              "/research?scope=company_overview&thscode=" +
              encodeURIComponent(query.data.identity.thscode)
            }
          >
            基于当前资料开始研究
          </a>
          <Tabs
            style={{ marginTop: 18 }}
            items={[
              {
                key: "trend",
                label: "趋势概览",
                children: (
                  <>
                    <DataFreshness
                      sourceTime={overview?.source_time}
                      fetchedAt={overview?.fetched_at}
                      basis={overview?.basis}
                      evidenceCount={overview?.evidence_ids.length ?? 0}
                    />
                    <div style={{ marginTop: 16 }}>
                      <KpiCards items={kpis} />
                    </div>
                    <ChartPanel
                      eyebrow={
                        query.data.identity.thscode + " · DAILY WORKBENCH"
                      }
                      title={
                        <>
                          行情工作台：<em>{query.data.identity.name}</em>
                        </>
                      }
                      scope="价格、成交额与均线放进同一块可缩放画布；红涨绿跌，均线由已保存收盘价在本机计算。"
                      tone={moduleAccent.company}
                      footnote={
                        <Provenance>
                          口径：{overview?.basis ?? "daily_bar"} · 数据截至{" "}
                          {overview?.source_time ?? "--"} ·
                          仅信息展示，不构成投资建议
                        </Provenance>
                      }
                    >
                      <CandlestickChart
                        bars={candleBars}
                        height={380}
                        ariaLabel={query.data.identity.name + "日K线与成交额"}
                      />
                    </ChartPanel>
                    <ChartPanel
                      eyebrow="DETERMINISTIC METRICS"
                      title="趋势指标"
                      scope="全部由本机基于已保存证据计算，公式与缺口见各行备注。"
                      tone={moduleAccent.company}
                      footnote={
                        <Provenance>
                          同一段证据被显式刷新后，最新一条胜出；原始行不变、可追溯。
                        </Provenance>
                      }
                    >
                      <MetricTable metrics={overview?.metrics ?? []} />
                    </ChartPanel>
                  </>
                ),
              },
              {
                key: "quality",
                label: "财务体检",
                children: (
                  <>
                    <Alert
                      type="info"
                      showIcon
                      title="按财务维度阅读"
                      description="利润率、同比增速和资产负债率的单位与方向不同，不混入同一张原始值图表；每个指标均由本机基于已保存证据计算。"
                    />
                    <div className="status-grid" style={{ marginTop: 18 }}>
                      {dimensions.map((dimension: QualityDimension) => (
                        <ChartPanel
                          key={dimension.id}
                          eyebrow="FUNDAMENTAL CHECK"
                          title={dimension.label}
                          toolbar={
                            <Tag
                              color={
                                dimension.status === "available"
                                  ? "green"
                                  : "default"
                              }
                            >
                              {dimension.status === "available"
                                ? "有数据"
                                : dimension.status === "not_applicable"
                                  ? "不适用"
                                  : "尚无数据"}
                            </Tag>
                          }
                          tone={moduleAccent.company}
                        >
                          <MetricTable metrics={dimension.metrics} />
                          {dimension.notes.map((note) => (
                            <p key={note} className="muted">
                              {note}
                            </p>
                          ))}
                        </ChartPanel>
                      ))}
                    </div>
                  </>
                ),
              },
            ]}
          />
        </>
      )}
      <SecurityPickerModal
        open={pickerOpen}
        assetTypes={["a-share"]}
        onCancel={() => setPickerOpen(false)}
        onConfirm={(picked) => {
          setCode(picked[0].thscode);
          setPickerOpen(false);
        }}
      />
    </>
  );
}