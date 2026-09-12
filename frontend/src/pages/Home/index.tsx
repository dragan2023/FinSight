import {
  ArrowRightOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LinkOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Button, Skeleton, Tag } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { systemQuery } from "../../api/system";
import DataPreview from "../../components/DataPreview";
import ErrorNotice from "../../components/ErrorNotice";
import SourcesPanel from "../../components/SourcesPanel";

export default function Home() {
  const navigate = useNavigate();
  const system = useQuery(systemQuery);
  const ready = system.data?.model.status === "ready";
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">RESEARCH WORKSPACE</div>
          <h1>研究概览</h1>
          <p>连接可信数据，建立可追溯的研究判断。</p>
        </div>
        <Tag variant="filled">基础工程 · 阶段 02</Tag>
      </div>
      {system.error && (
        <ErrorNotice
          error={system.error}
          retry={() => {
            void system.refetch();
          }}
        />
      )}
      <section className="welcome-panel">
        <div className="welcome-copy">
          <span className="section-kicker">从一个好问题开始</span>
          <h2>
            让每一个判断，
            <br />
            都有据可循。
          </h2>
          <p>
            从问题到数据，从计算到结论。
            <br />
            FinSight 正在构建属于你的金融研究工作台。
          </p>
          <Button
            onClick={() => navigate("/settings")}
            type="primary"
            size="large"
            icon={<ArrowRightOutlined aria-hidden />}
            iconPlacement="end"
          >
            {ready ? "管理研究模型" : "配置研究模型"}
          </Button>
          <span className="welcome-note">
            模型与同花顺数据接入已开放，可检索标的并获取有据可查的数据。
          </span>
        </div>
        <div className="evidence-flow" aria-label="计划中的证据研究流程">
          <div className="flow-caption">一条可检查的研究路径</div>
          <div className="flow-node">
            <span className="flow-icon">
              <FileSearchOutlined aria-hidden />
            </span>
            <div>
              <strong>提出研究问题</strong>
              <small>明确标的与时间范围</small>
            </div>
            <span className="flow-number">01</span>
          </div>
          <div className="flow-line" />
          <div className="flow-node">
            <span className="flow-icon">
              <DatabaseOutlined aria-hidden />
            </span>
            <div>
              <strong>数据与确定性计算</strong>
              <small>保留来源、口径和计算过程</small>
            </div>
            <span className="flow-number">02</span>
          </div>
          <div className="flow-line" />
          <div className="flow-node final">
            <span className="flow-icon">
              <LinkOutlined aria-hidden />
            </span>
            <div>
              <strong>有证据的研究结论</strong>
              <small>引用可追溯，限制可检查</small>
            </div>
            <span className="flow-number">03</span>
          </div>
        </div>
      </section>
      <div className="section-heading">
        <h2>工作空间状态</h2>
        <span>仅检查本机配置，不发起外部请求</span>
      </div>
      <div className="status-grid">
        <section className="status-card">
          <div className="card-icon">
            <SafetyCertificateOutlined aria-hidden />
          </div>
          <span>本机服务</span>
          {system.isPending ? (
            <Skeleton.Input active size="small" />
          ) : (
            <h3>{system.data ? "已就绪" : "未连接"}</h3>
          )}
          <p>
            {system.data
              ? "独立后端与 SQLite 存储可用"
              : "启动后端后查看服务状态"}
          </p>
          <div className="card-foot">
            <span className={`status-dot ${system.data ? "" : "pending"}`} />
            {system.data ? "127.0.0.1 · 仅本机" : "等待本机服务"}
          </div>
        </section>
        <section className="status-card">
          <div className="card-icon">
            <ExperimentOutlined aria-hidden />
          </div>
          <span>研究模型</span>
          <h3>{ready ? "已配置" : system.data ? "待配置" : "状态未知"}</h3>
          <p>{system.data?.model.model_label || "添加一个 OpenAI 兼容模型"}</p>
          <div className="card-foot">
            <Link to="/settings">
              {ready ? "检查模型与凭据" : "前往模型设置"}{" "}
              <ArrowRightOutlined aria-hidden />
            </Link>
          </div>
        </section>
        <section className="status-card">
          <div className="card-icon">
            <DatabaseOutlined aria-hidden />
          </div>
          <span>金融数据</span>
          <h3>{system.data?.market_data.key_configured ? "已接入" : "可配置"}</h3>
          <p>同花顺 Financial-API</p>
          <div className="card-foot">
            <span className={`status-dot ${system.data?.market_data.key_configured ? "" : "pending"}`} />
            已开放 · 检索、价格、估值与财务
          </div>
        </section>
      </div>
      <div className="section-heading">
        <h2>数据来源</h2>
        <span>当前注册的研究数据来源、可信级别与本机启用状态</span>
      </div>
      <SourcesPanel />
      <section className="section-heading">
        <h2>选标的、取研究资料</h2>
        <span>检索同花顺代码表，确认标的后获取快照、日线、估值与三表</span>
      </section>
      <DataPreview />
      <section className="next-panel">
        <div className="next-icon">
          <SettingOutlined aria-hidden />
        </div>
        <div>
          <h3>{ready ? "模型已保存，研究能力仍在建设中" : "先完成模型设置"}</h3>
          <p>
            设置服务地址和模型名称，安全保存凭据。连接、聊天和工具能力可以分别测试；不会自动调用或消费额度。
          </p>
        </div>
        <Button
          className="settings-shortcut"
          onClick={() => navigate("/settings")}
        >
          打开设置 <ArrowRightOutlined aria-hidden />
        </Button>
      </section>
    </>
  );
}
