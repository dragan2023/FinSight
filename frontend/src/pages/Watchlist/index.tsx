import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Form, Input, Modal, Space, Table, Tag } from "antd";
import {
  addWatchlist,
  latestWatchlistObservations,
  listWatchlist,
  observeWatchlist,
  removeWatchlist,
} from "../../api/company";
import ErrorNotice from "../../components/ErrorNotice";
import SecurityPickerModal from "../../components/SecurityPickerModal";
import {
  ChartPanel,
  KpiCards,
  Provenance,
  ToneBadge,
} from "../../components/charts/panels";
import { finNum, finYi, moduleAccent } from "../../components/charts/tokens";
import { toNum } from "../../components/charts/series";

/** 日内区间条（实践 05 基型）：绿-黄-红渐变轨道 + 最新价位置标记。 */
function DayRange({
  low,
  high,
  last,
}: {
  low: number | null;
  high: number | null;
  last: number | null;
}) {
  if (low === null || high === null || last === null || high <= low)
    return <span className="muted">--</span>;
  const pos = Math.max(0, Math.min(1, (last - low) / (high - low)));
  return (
    <span
      className="fin-range"
      title={
        "最低 " +
        finNum(low) +
        " · 最新 " +
        finNum(last) +
        " · 最高 " +
        finNum(high)
      }
    >
      <i style={{ left: (pos * 100).toFixed(1) + "%" }} />
    </span>
  );
}

export default function Watchlist() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [form] = Form.useForm();
  const watch = useQuery({ queryKey: ["watchlist"], queryFn: listWatchlist });
  const observations = useQuery({
    queryKey: ["watchlist-observations"],
    queryFn: latestWatchlistObservations,
  });
  const add = useMutation({
    mutationFn: addWatchlist,
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const remove = useMutation({
    mutationFn: removeWatchlist,
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const observe = useMutation({
    mutationFn: () => observeWatchlist({ refresh: true }),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: ["watchlist-observations"],
      }),
  });
  const count = watch.data?.items.length ?? 0;
  const observationRows = useMemo(
    () => observations.data?.items ?? [],
    [observations.data?.items],
  );

  const rows = useMemo(
    () =>
      observationRows.map((item) => ({
        key: item.watchlist_item_id,
        thscode: item.thscode,
        snapshot: item.snapshot,
        anomalies: item.anomalies,
        pct: toNum(item.snapshot.price_change_ratio_pct),
        last: toNum(item.snapshot.last_price),
        low: toNum(item.snapshot.low_price),
        high: toNum(item.snapshot.high_price),
        turnover: toNum(item.snapshot.turnover),
      })),
    [observationRows],
  );

  const kpis = useMemo(() => {
    const upCount = rows.filter((row) => (row.pct ?? 0) > 0).length;
    const downCount = rows.filter((row) => (row.pct ?? 0) < 0).length;
    const totalTurnover = rows.reduce(
      (sum, row) => sum + (row.turnover ?? 0),
      0,
    );
    const anomalyCount = rows.reduce(
      (sum, row) => sum + row.anomalies.length,
      0,
    );
    return [
      { label: "自选标的", value: finNum(count, 0), sub: "本机关注清单" },
      {
        label: "上涨 / 下跌",
        value: (
          <>
            <span style={{ color: "#c9302c" }}>{finNum(upCount, 0)}</span> /{" "}
            <span style={{ color: "#1f9d66" }}>{finNum(downCount, 0)}</span>
          </>
        ),
        sub: "最近一次观察",
      },
      {
        label: "合计成交额",
        value: (
          <>
            {totalTurnover === 0 && rows.length === 0
              ? "--"
              : finYi(totalTurnover)}
          </>
        ),
        sub: "仅当前自选样本",
      },
      {
        label: "异动记录",
        value: finNum(anomalyCount, 0),
        sub: "接口返回的事实性原因",
      },
    ];
  }, [rows, count]);

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">LOCAL RESEARCH TRACKING</div>
          <h1>关注与理由</h1>
          <p>默认不监控、不通知、不保存持仓；更新仅在本次点击中执行。</p>
        </div>
        <Space>
          <Button onClick={() => setOpen(true)}>添加关注</Button>
          <Button
            type="primary"
            loading={observe.isPending}
            disabled={!count}
            onClick={() => observe.mutate()}
          >
            更新观察（{count}）
          </Button>
        </Space>
      </div>
      {watch.error && (
        <ErrorNotice error={watch.error} retry={() => void watch.refetch()} />
      )}
      {observe.error && (
        <ErrorNotice error={observe.error} retry={() => observe.mutate()} />
      )}
      <Card
        title="关注标的"
        extra={
          count ? (
            <a
              href={
                "/research?scope=watchlist_review&thscode=" +
                encodeURIComponent(watch.data?.items[0]?.thscode ?? "")
              }
            >
              基于当前资料开始研究
            </a>
          ) : null
        }
      >
        <div className="local-list" role="list">
          {count ? (
            watch.data?.items.map((item) => (
              <div className="local-list-item" role="listitem" key={item.id}>
                <div>
                  <strong>
                    {item.name} · {item.thscode}
                  </strong>
                  <div>
                    {item.tags.map((tag) => (
                      <Tag key={tag}>{tag}</Tag>
                    ))}
                  </div>
                </div>
                <Button
                  danger
                  type="link"
                  onClick={() => remove.mutate(item.id)}
                >
                  移除
                </Button>
              </div>
            ))
          ) : (
            <span className="muted">尚无关注标的</span>
          )}
        </div>
      </Card>
      <ChartPanel
        eyebrow="WATCHLIST BLOTTER"
        title="最近一次观察"
        scope="把自选样本按涨跌幅与异动原因组合成可排序的轻量监控页；空异动仅表示接口本次未返回匹配记录。"
        tone={moduleAccent.sector}
        footnote={
          <Provenance>
            行情为快照口径（today-only）；日内区间来自接口的高低价格字段，缺字段时显示
            --。
          </Provenance>
        }
      >
        <div style={{ marginBottom: 14 }}>
          <KpiCards items={kpis} />
        </div>
        <Table
          size="small"
          pagination={false}
          rowKey="key"
          dataSource={rows}
          columns={[
            {
              title: "标的",
              key: "name",
              render: (_, row) => (
                <strong>
                  {row.snapshot.name ?? row.thscode}
                  <small className="muted" style={{ marginLeft: 6 }}>
                    {row.thscode}
                  </small>
                </strong>
              ),
            },
            {
              title: "最新价",
              key: "last",
              render: (_, row) =>
                row.last === null ? "--" : "¥" + finNum(row.last),
            },
            {
              title: "涨跌幅",
              key: "pct",
              sorter: (a, b) => (a.pct ?? 0) - (b.pct ?? 0),
              render: (_, row) => <ToneBadge value={row.pct} />,
            },
            {
              title: "日内区间",
              key: "range",
              render: (_, row) => (
                <DayRange low={row.low} high={row.high} last={row.last} />
              ),
            },
            {
              title: "成交额",
              key: "turnover",
              sorter: (a, b) => (a.turnover ?? 0) - (b.turnover ?? 0),
              render: (_, row) =>
                row.turnover === null ? "--" : finYi(row.turnover),
            },
            {
              title: "异动",
              key: "anomalies",
              render: (_, row) =>
                row.anomalies.length ? (
                  row.anomalies.map((item) => (
                    <Tag
                      key={(item.tag ?? "异动") + "-" + (item.event_time ?? "")}
                    >
                      {item.tag ?? "异动"}
                    </Tag>
                  ))
                ) : (
                  <span className="muted">无记录</span>
                ),
            },
          ]}
        />
      </ChartPanel>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        title="添加关注"
        footer={null}
      >
        <Button
          style={{ marginBottom: 12 }}
          onClick={() => setPickerOpen(true)}
        >
          从已确认标的选择
        </Button>
        <SecurityPickerModal
          open={pickerOpen}
          assetTypes={["a-share"]}
          onCancel={() => setPickerOpen(false)}
          onConfirm={(picked) => {
            form.setFieldsValue({
              thscode: picked[0].thscode,
              name: picked[0].name,
              security_id: picked[0].thscode,
            });
            setPickerOpen(false);
          }}
        />
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            add.mutate({
              security_id: values.security_id,
              thscode: values.thscode,
              name: values.name,
              tags: (values.tags ?? "")
                .split(",")
                .map((tag: string) => tag.trim())
                .filter(Boolean),
            });
            setOpen(false);
          }}
        >
          <Form.Item name="thscode" label="代码" rules={[{ required: true }]}>
            <Input placeholder="例如 600519.SH" />
          </Form.Item>
          <Form.Item
            name="security_id"
            label="安全ID"
            extra="从已确认标的选择后自动填入；通常与代码一致。"
            rules={[{ required: true }]}
          >
            <Input placeholder="本机证据库中的 security_id" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="tags" label="标签（逗号分隔）">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={add.isPending}>
            保存
          </Button>
        </Form>
      </Modal>
    </>
  );
}
