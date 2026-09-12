import { Component, useMemo, type ReactNode } from "react";
import { Alert, Card, Col, Row, Table, Tag, Typography } from "antd";
import { ChartPanel, KpiCards, Provenance } from "../charts/panels";
import LadderPulseMatrix from "../charts/LadderPulseMatrix";
import RankBarsChart from "../charts/RankBarsChart";
import ReasonTreemapChart from "../charts/ReasonTreemapChart";
import ToneBarsChart from "../charts/ToneBarsChart";
import { moduleAccent, type FinTone } from "../charts/tokens";
import { toNum } from "../charts/series";

import type { MarketReviewDashboardData } from "./types";

export class MarketReviewDashboardBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <Card>
          <Alert
            type="warning"
            showIcon
            title="市场复盘图表暂时无法展示"
            description="本条复盘数据不完整或图表初始化失败。页面其余功能不受影响；请点击“显式刷新盘后观察”重新生成本地摘要。"
          />
        </Card>
      );
    }
    return this.props.children;
  }
}

const kpiTone = (tone: string): FinTone | undefined =>
  tone === "positive"
    ? "up"
    : tone === "negative"
      ? "down"
      : tone === "warning"
        ? "warn"
        : undefined;

const structureTone = (tone: string): FinTone =>
  tone === "positive"
    ? "up"
    : tone === "negative"
      ? "down"
      : tone === "warning"
        ? "warn"
        : "flat";

const formatNumber = (value: number | null) =>
  value === null
    ? "--"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(
        value,
      );
const trendLabel = (trend: string | null) =>
  trend === "up"
    ? "排名上升"
    : trend === "down"
      ? "排名下降"
      : trend === "flat"
        ? "排名不变"
        : "--";

export default function MarketReviewDashboard({
  dashboard,
}: {
  dashboard: MarketReviewDashboardData;
}) {
  const ladderLevels = useMemo(
    () =>
      dashboard.ladder.levels.map((level) => {
        const parsed = Number.parseInt(level.label, 10);
        const stocks = Number.isNaN(parsed)
          ? []
          : dashboard.ladder.stocks.filter((stock) => stock.level === parsed);
        return {
          label: level.label,
          count: level.value,
          stocks: stocks.map((stock) => ({
            name: stock.name,
            thscode: stock.thscode,
            sealed: stock.next_day_sealed,
          })),
        };
      }),
    [dashboard.ladder],
  );

  const hotItems = useMemo(
    () =>
      dashboard.hot.top.map((item, index) => ({
        label: item.name,
        value: toNum(item.heat),
        badge: item.rank === null ? "#" + (index + 1) : "#" + item.rank,
        note:
          item.rank_change === null
            ? null
            : "较前榜 " +
              (item.rank_change > 0 ? "↑" : item.rank_change < 0 ? "↓" : "—") +
              " " +
              Math.abs(item.rank_change) +
              " 名 · " +
              trendLabel(item.trend),
      })),
    [dashboard.hot.top],
  );

  const kpiItems = useMemo(
    () =>
      dashboard.overview.cards.map((card) => ({
        label: card.label,
        value: (
          <>
            {formatNumber(card.value)}
            <small>{card.unit}</small>
          </>
        ),
        sub: card.description,
        tone: kpiTone(card.tone),
      })),
    [dashboard.overview.cards],
  );

  return (
    <>
      <Alert
        type="info"
        showIcon
        title="本地市场复盘"
        description={dashboard.notice}
      />
      <div style={{ marginTop: 16 }}>
        <KpiCards items={kpiItems} dark />
      </div>
      <ChartPanel
        eyebrow="MARKET STRUCTURE"
        title="涨跌停结构"
        scope={dashboard.overview.interpretation}
        tone={moduleAccent.review}
        footnote={
          <Provenance>
            同一条柱内单位一致；不同统计窗口不相加，缺口以 -- 呈现。
          </Provenance>
        }
      >
        <ToneBarsChart
          items={dashboard.overview.structure.map((item) => ({
            label: item.label,
            value: item.value,
            tone: structureTone(item.tone),
          }))}
          unit=" 家"
          height={230}
          ariaLabel="当日涨跌停结构柱状图"
        />
      </ChartPanel>
      <Row gutter={[18, 18]}>
        <Col xs={24} xl={12}>
          <ChartPanel
            eyebrow="LIMIT-UP LADDER PULSE"
            title="连板梯队脉冲"
            scope={dashboard.ladder.description}
            dark
            footnote={
              <Provenance>
                ● = 次日封住，○ = 次日未封，无标记 =
                接口未提供；梯队不代表推荐。
              </Provenance>
            }
          >
            <LadderPulseMatrix levels={ladderLevels} />
            {dashboard.ladder.stocks.length > 0 && (
              <Table
                size="small"
                style={{ marginTop: 14 }}
                pagination={{ pageSize: 8 }}
                rowKey={(item) => item.thscode + "-" + item.level}
                dataSource={dashboard.ladder.stocks}
                columns={[
                  {
                    title: "梯队",
                    dataIndex: "level",
                    render: (value) => value + " 连板",
                  },
                  { title: "股票", dataIndex: "name" },
                  { title: "代码", dataIndex: "thscode" },
                  {
                    title: "次日封板",
                    dataIndex: "next_day_sealed",
                    render: (value) =>
                      value === null ? "未提供" : value ? "是" : "否",
                  },
                ]}
              />
            )}
          </ChartPanel>
        </Col>
        <Col xs={24} xl={12}>
          <ChartPanel
            eyebrow="ATTENTION RADAR"
            title="热度排名"
            scope={dashboard.hot.description}
            tone={moduleAccent.heat}
            footnote={
              <Provenance>
                热度为榜单位次快照，不构成交易信号；排名变化与热度分属两套口径。
              </Provenance>
            }
          >
            {dashboard.hot.top.length ? (
              <RankBarsChart
                items={hotItems}
                accent={moduleAccent.heat}
                ariaLabel="热股榜排名条形图"
              />
            ) : (
              <Typography.Text type="secondary">
                当次未取得可解析的热度排名。
              </Typography.Text>
            )}
            {dashboard.hot.top.length > 0 && (
              <Table
                size="small"
                style={{ marginTop: 14 }}
                pagination={{ pageSize: 8 }}
                rowKey="thscode"
                dataSource={dashboard.hot.top}
                columns={[
                  { title: "排名", dataIndex: "rank" },
                  { title: "股票", dataIndex: "name" },
                  { title: "热度值", dataIndex: "heat", render: formatNumber },
                  {
                    title: "排名变化",
                    dataIndex: "rank_change",
                    render: (value) =>
                      value === null
                        ? "--"
                        : value > 0
                          ? "↑ " + value
                          : value < 0
                            ? "↓ " + Math.abs(value)
                            : "—",
                  },
                  {
                    title: "趋势",
                    dataIndex: "trend",
                    render: (value) => trendLabel(value),
                  },
                ]}
              />
            )}
          </ChartPanel>
        </Col>
      </Row>
      <ChartPanel
        eyebrow="DRAGON-TIGER KEYWORDS"
        title="龙虎榜上榜原因分布"
        scope={dashboard.dragon_tiger.description}
        tone={moduleAccent.dragon}
        footnote={
          <Provenance>
            面积编码上榜次数；接口未提供净额字段时不绘制资金流向，不凭模块名称伪造关系图。
          </Provenance>
        }
      >
        {dashboard.dragon_tiger.reason_distribution.length ? (
          <ReasonTreemapChart
            items={dashboard.dragon_tiger.reason_distribution}
            unit=" 次"
            height={260}
            ariaLabel="龙虎榜上榜原因关键词树图"
          />
        ) : (
          <Typography.Text type="secondary">
            当次未取得可归类的上榜原因；不会凭模块名称伪造关系图。
          </Typography.Text>
        )}
        {dashboard.dragon_tiger.stocks.length > 0 && (
          <Table
            size="small"
            style={{ marginTop: 14 }}
            pagination={{ pageSize: 8 }}
            rowKey={(item) =>
              item.thscode + "-" + item.window + "-" + item.reason
            }
            dataSource={dashboard.dragon_tiger.stocks}
            columns={[
              { title: "股票", dataIndex: "name" },
              { title: "代码", dataIndex: "thscode" },
              { title: "上榜原因", dataIndex: "reason" },
              { title: "统计窗口", dataIndex: "window" },
            ]}
          />
        )}
      </ChartPanel>
      <ChartPanel
        eyebrow="COVERAGE"
        title="数据覆盖情况"
        scope="缺行不代表市场无事件，只代表本次窗口未取得可解析数据。"
        tone={moduleAccent.review}
      >
        <Table
          size="small"
          pagination={false}
          rowKey="label"
          dataSource={dashboard.coverage}
          columns={[
            { title: "数据板块", dataIndex: "label" },
            {
              title: "获取状态",
              dataIndex: "status",
              render: (value) => (
                <Tag
                  color={
                    value === "已获取"
                      ? "green"
                      : value === "部分获取"
                        ? "gold"
                        : "default"
                  }
                >
                  {value}
                </Tag>
              ),
            },
            {
              title: "已获取记录",
              dataIndex: "count",
              render: (value) => value + " 条",
            },
            {
              title: "已知缺口",
              dataIndex: "gaps",
              render: (value) => (value ? value + " 项" : "无"),
            },
          ]}
        />
      </ChartPanel>
    </>
  );
}
