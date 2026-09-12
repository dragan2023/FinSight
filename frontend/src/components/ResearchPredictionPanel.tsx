import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Collapse, Form, Input, InputNumber, List, Popconfirm, Select, Space, Tag, Typography } from "antd";
import { useState } from "react";
import {
  listResearchPredictions, sealResearchPrediction, settleResearchPrediction,
  verifyAllResearchPredictions, verifyResearchPrediction,
  type ResearchPrediction, type ResearchPredictionHorizon, type ResearchPredictionInput,
} from "../api/advisor";
import ErrorNotice from "./ErrorNotice";

const { TextArea } = Input;

const HORIZON_LABEL: Record<ResearchPredictionHorizon, string> = {
  days_5: "5 个交易日", days_20: "20 个交易日", quarter: "一个季度", year: "一年", custom: "自定义（按 20 个交易日结算）",
};
const SCENARIO_LABEL = { up: "上涨", flat: "震荡", down: "下跌" } as const;
const STATUS_LABEL = { sealed: "已封存，等待结算", settled: "已结算", void: "已作废" } as const;

type Draft = {
  thscode: string; name: string; as_of_date: string; horizon: ResearchPredictionHorizon;
  p_up: number | null; p_flat: number | null; p_down: number | null; return_threshold_pct: number | null;
  rationale: string; counter_evidence: string; invalidations: string;
};

const EMPTY_DRAFT: Draft = {
  thscode: "", name: "", as_of_date: "", horizon: "days_20",
  p_up: null, p_flat: null, p_down: null, return_threshold_pct: 0.2,
  rationale: "", counter_evidence: "", invalidations: "",
};

function splitLines(value: string) {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function resultText(prediction: ResearchPrediction) {
  const settlement = prediction.settlement;
  if (!settlement) return null;
  const hit = settlement.hit === true ? "命中" : settlement.hit === false ? "未命中" : "未评分";
  return `实际涨跌 ${String(settlement.change_pct ?? "—")}%，${hit}；基准日 ${String(settlement.base_date ?? "—")}，结算日 ${String(settlement.final_date ?? "—")}。`;
}

export default function ResearchPredictionPanel() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [integrityText, setIntegrityText] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["research-predictions"], queryFn: listResearchPredictions });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["research-predictions"] });
  const sealMutation = useMutation({
    mutationFn: (payload: ResearchPredictionInput) => sealResearchPrediction(payload),
    onSuccess: () => { setDraft(EMPTY_DRAFT); setIntegrityText(null); refresh(); },
  });
  const verifyAllMutation = useMutation({
    mutationFn: verifyAllResearchPredictions,
    onSuccess: (result) => setIntegrityText(result.intact
      ? `已核验 ${result.checked} 条封存记录，内容完整。`
      : `核验发现 ${result.violations.length} 条记录的封存内容不一致，请停止把它们用于评估。`),
  });
  const verifyMutation = useMutation({
    mutationFn: verifyResearchPrediction,
    onSuccess: (result) => setIntegrityText(result.intact ? "该条研究预测的封存内容完整。" : "该条研究预测的封存内容不一致。"),
  });
  const settleMutation = useMutation({
    mutationFn: settleResearchPrediction,
    onSuccess: refresh,
  });
  const error = query.error ?? sealMutation.error ?? verifyAllMutation.error ?? verifyMutation.error ?? settleMutation.error;
  const predictions = query.data?.predictions ?? [];
  const summary = query.data?.summary;

  const seal = () => {
    if (draft.p_up === null || draft.p_flat === null || draft.p_down === null || draft.return_threshold_pct === null) return;
    sealMutation.mutate({
      thscode: draft.thscode.trim(), name: draft.name.trim(), as_of_date: draft.as_of_date,
      horizon: draft.horizon, p_up: draft.p_up, p_flat: draft.p_flat, p_down: draft.p_down,
      return_threshold_pct: draft.return_threshold_pct, probability_source: "model_subjective",
      rationale: draft.rationale.trim(), counter_evidence: splitLines(draft.counter_evidence),
      invalidations: splitLines(draft.invalidations), source: "manual",
    });
  };

  return (
    <div>
      <Alert type="info" showIcon title="研究预测与执行建议分开管理"
        description="这里用于登记可大胆但可复盘的方向判断。它不会形成仓位建议，也不会改变顾问的推荐档位；请只填写资料实际截止日已知的信息。" />
      {error && <div style={{ marginTop: 12 }}><ErrorNotice error={error} retry={() => {
        sealMutation.reset(); verifyAllMutation.reset(); verifyMutation.reset(); settleMutation.reset();
      }} /></div>}
      {integrityText && <Alert style={{ marginTop: 12 }} type={integrityText.includes("不一致") ? "error" : "success"} showIcon title={integrityText} />}
      <Collapse style={{ marginTop: 12 }} items={[{
        key: "new", label: "登记一条研究预测（登记后不可改写）", children: (
          <Form layout="vertical">
            <Space wrap style={{ width: "100%" }}>
              <Form.Item label="标的代码" required><Input aria-label="研究标的代码" value={draft.thscode} placeholder="例如 600519.SH" onChange={(e) => setDraft({ ...draft, thscode: e.target.value })} /></Form.Item>
              <Form.Item label="标的名称" required><Input aria-label="研究标的名称" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Form.Item>
              <Form.Item label="资料截止日" required extra="不是登记日；必须是本次判断实际可见资料的最后日期。"><Input aria-label="资料截止日" type="date" value={draft.as_of_date} onChange={(e) => setDraft({ ...draft, as_of_date: e.target.value })} /></Form.Item>
              <Form.Item label="预测窗口" required><Select aria-label="预测窗口" value={draft.horizon} style={{ width: 180 }} onChange={(horizon: ResearchPredictionHorizon) => setDraft({ ...draft, horizon })} options={Object.entries(HORIZON_LABEL).map(([value, label]) => ({ value, label }))} /></Form.Item>
            </Space>
            <Typography.Paragraph type="secondary">三种情景概率必须合计 100%，且最高概率不能并列；无法给出主导方向时，不应登记为研究预测。</Typography.Paragraph>
            <Space wrap style={{ width: "100%" }}>
              <Form.Item label="上涨概率（%）" required><InputNumber aria-label="上涨概率" min={0} max={100} value={draft.p_up} onChange={(p_up) => setDraft({ ...draft, p_up })} /></Form.Item>
              <Form.Item label="震荡概率（%）" required><InputNumber aria-label="震荡概率" min={0} max={100} value={draft.p_flat} onChange={(p_flat) => setDraft({ ...draft, p_flat })} /></Form.Item>
              <Form.Item label="下跌概率（%）" required><InputNumber aria-label="下跌概率" min={0} max={100} value={draft.p_down} onChange={(p_down) => setDraft({ ...draft, p_down })} /></Form.Item>
              <Form.Item label="涨跌判定阈值（%）" required extra="绝对涨跌不超过该值记为震荡。"><InputNumber aria-label="涨跌判定阈值" min={0} max={50} step={0.1} value={draft.return_threshold_pct} onChange={(return_threshold_pct) => setDraft({ ...draft, return_threshold_pct })} /></Form.Item>
            </Space>
            <Form.Item label="判断依据" required><TextArea aria-label="研究判断依据" rows={3} maxLength={2000} value={draft.rationale} onChange={(e) => setDraft({ ...draft, rationale: e.target.value })} /></Form.Item>
            <Form.Item label="最强反方证据" required extra="每行一条；至少一条。"><TextArea aria-label="最强反方证据" rows={3} maxLength={2000} value={draft.counter_evidence} onChange={(e) => setDraft({ ...draft, counter_evidence: e.target.value })} /></Form.Item>
            <Form.Item label="何时作废" required extra="每行一条；至少一条。"><TextArea aria-label="何时作废" rows={3} maxLength={2000} value={draft.invalidations} onChange={(e) => setDraft({ ...draft, invalidations: e.target.value })} /></Form.Item>
            <Button type="primary" loading={sealMutation.isPending} onClick={seal}>封存研究预测</Button>
          </Form>
        ),
      }]} />
      <Space wrap style={{ marginTop: 16 }}>
        <Typography.Text>已封存 {summary?.sealed ?? 0} 条，已结算 {summary?.settled ?? 0} 条；独立样本 {summary?.independent_samples ?? 0} 条。</Typography.Text>
        {summary?.hit_rate !== null && summary?.hit_rate !== undefined && <Typography.Text>方向命中率 {Math.round(summary.hit_rate * 100)}%。</Typography.Text>}
        {summary?.brier !== null && summary?.brier !== undefined && <Typography.Text>Brier {summary.brier}。</Typography.Text>}
        {summary && !summary.sample_sufficient && <Typography.Text type="secondary">样本不足 30 条，不能据此评价预测能力。</Typography.Text>}
        <Button size="small" loading={verifyAllMutation.isPending} onClick={() => verifyAllMutation.mutate()}>核验全部封存记录</Button>
      </Space>
      <List style={{ marginTop: 8 }} size="small" loading={query.isLoading} dataSource={predictions}
        locale={{ emptyText: "尚无研究预测；可以手工登记一条已形成的方向判断。" }}
        renderItem={(prediction) => (
          <List.Item actions={[
            <Button key="verify" size="small" loading={verifyMutation.isPending} onClick={() => verifyMutation.mutate(prediction.id)}>核验</Button>,
            ...(prediction.status === "sealed" ? [<Popconfirm key="settle-confirm" title="将按当前本地行情结算这条预测；不会刷新行情，也不会调用模型。确认继续？" okText="确认结算" cancelText="取消" onConfirm={() => settleMutation.mutate(prediction.id)}><Button size="small" loading={settleMutation.isPending}>结算</Button></Popconfirm>] : []),
          ]}>
            <div>
              <Space wrap><Typography.Text strong>{prediction.name}（{prediction.thscode}）</Typography.Text><Tag>{HORIZON_LABEL[prediction.horizon]}</Tag><Tag color={prediction.status === "settled" ? "green" : "blue"}>{STATUS_LABEL[prediction.status]}</Tag><Tag color="purple">主导：{SCENARIO_LABEL[prediction.dominant_scenario]} {prediction.probabilities[prediction.dominant_scenario]}%</Tag></Space>
              <Typography.Paragraph style={{ margin: "4px 0" }} type="secondary">资料截至 {prediction.as_of_date}；上涨 {prediction.probabilities.up}%、震荡 {prediction.probabilities.flat}%、下跌 {prediction.probabilities.down}%；阈值 ±{prediction.return_threshold_pct}%。</Typography.Paragraph>
              <Typography.Paragraph style={{ margin: "4px 0" }}>{prediction.rationale}</Typography.Paragraph>
              <Typography.Paragraph style={{ margin: "4px 0" }} type="secondary">反方证据：{prediction.counter_evidence.join("；")}。作废条件：{prediction.invalidations.join("；")}。</Typography.Paragraph>
              {resultText(prediction) && <Typography.Text type="secondary">{resultText(prediction)}</Typography.Text>}
            </div>
          </List.Item>
        )}
      />
    </div>
  );
}
