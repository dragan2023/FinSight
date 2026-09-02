import { Alert, Button, Radio, Space, Tag, Typography } from "antd";
import { useState } from "react";

/**
 * SourceConflict (plan 04 sections 2.4/2.6): 同一指标不同口径/取值的来源
 * 并列展示——禁止平均、择大或静默覆盖；用户选择只影响本次新报告，历史
 * 证据与旧版本报告不可变。
 */
export interface ConflictSourceView {
  provider: string;
  label: string;
  value: string;
}

export interface ConflictView {
  id: string;
  dimension: string;
  field?: string;
  sources: ConflictSourceView[];
  explanation?: string;
}

export default function SourceConflict({
  conflicts,
  onChoose,
}: {
  conflicts: ConflictView[];
  onChoose?: (conflictId: string, provider: string) => void;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [applied, setApplied] = useState<string[]>([]);

  if (conflicts.length === 0) {
    return null;
  }
  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="small">
      <Typography.Text strong>来源冲突（{conflicts.length}）</Typography.Text>
      {conflicts.map((conflict) => (
        <Alert
          key={conflict.id}
          type="warning"
          showIcon
          title={
            <Space size="small" wrap>
              <span>{"口径冲突：" + conflict.dimension}</span>
              {conflict.field ? <Tag>{conflict.field}</Tag> : null}
            </Space>
          }
          description={
            <Space orientation="vertical" size={4} style={{ width: "100%" }}>
              {conflict.explanation && <span>{conflict.explanation}</span>}
              {conflict.sources.map((source) => (
                <Radio
                  key={source.provider}
                  checked={choices[conflict.id] === source.provider}
                  onChange={() =>
                    setChoices((prev) => ({ ...prev, [conflict.id]: source.provider }))
                  }
                >
                  {"[" + source.label + "（" + source.provider + "）] " + source.value}
                </Radio>
              ))}
              <Space size="small" wrap>
                <Button
                  size="small"
                  disabled={choices[conflict.id] == null}
                  onClick={() => {
                    const provider = choices[conflict.id];
                    if (provider == null) return;
                    setApplied((prev) => (prev.includes(conflict.id) ? prev : [...prev, conflict.id]));
                    onChoose?.(conflict.id, provider);
                  }}
                >
                  仅用于本次新报告
                </Button>
                <Typography.Text type="secondary">
                  并列显示，不平均、不静默取舍；选择只影响本次新报告，历史证据与旧版本不可变。
                </Typography.Text>
                {applied.includes(conflict.id) && <Tag color="green">已记录本次选择</Tag>}
              </Space>
            </Space>
          }
        />
      ))}
    </Space>
  );
}
