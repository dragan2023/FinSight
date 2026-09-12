import { useQuery } from "@tanstack/react-query";
import { Skeleton, Tag } from "antd";
import { sourcesQuery } from "../../api/sources";

const managementPaths: Record<string, { href: string; label: string }> = {
  ths: { href: "/settings", label: "查看能力验证" },
  warehouse: { href: "/market-explorer", label: "同步与查询" },
  web_search: { href: "/settings", label: "管理配置" },
  rss: { href: "/news", label: "管理订阅源" },
  kb: { href: "/knowledge", label: "管理知识库" },
};

/**
 * 当前启用的数据来源清单（V2-01）。
 * 只渲染后端下发的中文标签与状态，不解释任何枚举值；
 * 本机服务异常时静默隐藏——服务状态卡片已负责提示连接问题。
 */
export default function SourcesPanel() {
  const sources = useQuery(sourcesQuery);
  if (sources.isPending) {
    return <Skeleton active paragraph={{ rows: 3 }} />;
  }
  if (sources.isError || !sources.data) {
    return null;
  }
  return (
    <div className="sources-grid">
      {sources.data.sources.map((source) => {
        const management = managementPaths[source.id];
        const statusColor = source.enabled
          ? "success"
          : source.status_label === "建设中"
            ? undefined
            : "warning";
        return (
          <article className="source-card" key={source.id}>
            <div className="source-head">
              <h3>{source.name}</h3>
              <Tag variant="filled" color={statusColor}>
                {source.status_label}
              </Tag>
            </div>
            <p className="source-role">{source.role}</p>
            <div className="source-tags">
              <Tag>{source.trust_label}</Tag>
              <Tag>{source.pricing_label}</Tag>
            </div>
            <p className="source-detail">{source.detail}</p>
            <p className="source-caps">
              {source.capabilities.map((cap) => cap.label).join(" · ")}
            </p>
            {management && (
              <a className="source-manage-link" href={management.href}>
                {management.label} →
              </a>
            )}
          </article>
        );
      })}
    </div>
  );
}
