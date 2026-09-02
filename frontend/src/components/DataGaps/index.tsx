import { Alert, Empty, Space, Typography } from "antd";
import { type DataGapView } from "../../api/marketData";

/**
 * Program-summarized data gaps (plan 03 section 2.1/2.7): 缺什么、影响什么、
 * 可以怎样补。LLM 漏写不影响这里——缺口清单只来自程序 DataGap，不提供折叠，
 * 关键缺口不允许被收起后消失。
 */
export default function DataGaps({ gaps }: { gaps: DataGapView[] }) {
  if (gaps.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="本轮没有记录数据缺口"
      />
    );
  }
  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="small">
      <Typography.Text strong>数据缺口（{gaps.length}）</Typography.Text>
      {gaps.map((gap) => (
        <Alert
          key={gap.id}
          type="warning"
          showIcon
          title="仍待补齐或核验的信息"
          description={
            <Space orientation="vertical" size={2} style={{ width: "100%" }}>
              <span>影响：{gap.impact}</span>
              {gap.remedies.length > 0 && (
                <span>可以怎样补：{gap.remedies.join("；")}</span>
              )}
            </Space>
          }
        />
      ))}
    </Space>
  );
}
