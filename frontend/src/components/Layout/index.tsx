import {
  ArrowUpOutlined,
  CompassOutlined,
  ExperimentOutlined,
  FundOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { NavLink, Outlet, useLocation } from "react-router-dom";

export default function Layout() {
  const location = useLocation();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink to="/" className="brand" aria-label="FinSight 首页">
          <span className="brand-mark">
            <ArrowUpOutlined aria-hidden />
          </span>
          <span>
            FinSight<small>金融洞察</small>
          </span>
        </NavLink>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">
          <NavLink
            end
            to="/"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            <CompassOutlined aria-hidden />
            研究概览
          </NavLink>
          <NavLink
            to="/research"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            <ExperimentOutlined aria-hidden />
            研究工作台
          </NavLink>
          <NavLink to="/company" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />公司体检</NavLink>
          <NavLink to="/compare" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />同行比较</NavLink>
          <NavLink to="/watchlist" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />关注与理由</NavLink>
          <NavLink to="/library" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />研究资产库</NavLink>
          <NavLink to="/sectors" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />板块研究</NavLink>
          <NavLink to="/funds" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />基金研究</NavLink>
          <NavLink to="/market-review" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}><FundOutlined aria-hidden />市场复盘</NavLink>
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            <SettingOutlined aria-hidden />
            模型与 API 配置
          </NavLink>
        </nav>
        <div className="sidebar-note">
          <SafetyCertificateOutlined aria-hidden />
          <strong>你的本地研究空间</strong>
          <p>
            数据在本机管理
            <br />
            仅用于研究与分析
          </p>
        </div>
        <div className="sidebar-footer">
          <span className="status-dot" />
          LOCAL WORKSPACE <span>v0.1</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <span className="muted">工作空间</span>
            <span className="slash">/</span>
            {location.pathname === "/settings"
              ? "模型与 API 配置"
              : location.pathname.startsWith("/research")
                ? "研究工作台"
                : location.pathname.startsWith("/company") ? "公司财务体检" : location.pathname.startsWith("/compare") ? "同行比较" : location.pathname.startsWith("/watchlist") ? "关注与理由" : location.pathname.startsWith("/library") ? "研究资产库" : location.pathname.startsWith("/sectors") ? "板块研究" : location.pathname.startsWith("/funds") ? "基金研究" : location.pathname.startsWith("/market-review") ? "市场复盘"
                : "研究概览"}
          </div>
          <span className="local-badge">
            <span className="status-dot" />
            仅本机访问
          </span>
        </header>
        <main className="page">
          <Outlet />
        </main>
        <footer className="page-footer">
          <span>FinSight · 让研究有据可循</span>
          <span>只读研究，不提供交易功能</span>
        </footer>
      </div>
    </div>
  );
}
