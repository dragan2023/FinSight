import { lazy, Suspense } from "react";
import { App as AntApp, ConfigProvider, Skeleton } from "antd";
import zhCN from "antd/locale/zh_CN";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
const Settings = lazy(() => import("./pages/Settings"));
const Research = lazy(() => import("./pages/Research"));
const Company = lazy(() => import("./pages/Company"));
const Watchlist = lazy(() => import("./pages/Watchlist"));
const Library = lazy(() => import("./pages/Library"));
const Compare = lazy(() => import("./pages/Compare"));
const MarketResearch = lazy(() => import("./pages/MarketResearch"));
const Funds = lazy(() => import("./pages/Funds"));
const MarketExplorer = lazy(() => import("./pages/MarketExplorer"));
const AIChat = lazy(() => import("./pages/AIChat"));
const News = lazy(() => import("./pages/News"));
const Knowledge = lazy(() => import("./pages/Knowledge"));
const StrategyLab = lazy(() => import("./pages/StrategyLab"));
const Governance = lazy(() => import("./pages/Governance"));

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          colorPrimary: "#246653",
          colorInfo: "#246653",
          colorSuccess: "#246653",
          colorText: "#223832",
          colorTextSecondary: "#6c7a74",
          colorBorder: "#dce3de",
          borderRadius: 8,
          controlHeight: 38,
          fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <AntApp>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route
              path="research"
              element={
                <Suspense fallback={<Skeleton active />}>
                  <Research />
                </Suspense>
              }
            />
            <Route path="ai-chat" element={<Suspense fallback={<Skeleton active />}><AIChat /></Suspense>} />
            <Route path="company" element={<Suspense fallback={<Skeleton active />}><Company /></Suspense>} />
            <Route path="watchlist" element={<Suspense fallback={<Skeleton active />}><Watchlist /></Suspense>} />
            <Route path="library" element={<Suspense fallback={<Skeleton active />}><Library /></Suspense>} />
            <Route path="compare" element={<Suspense fallback={<Skeleton active />}><Compare /></Suspense>} />
            <Route path="sectors" element={<Suspense fallback={<Skeleton active />}><MarketResearch kind="sectors" /></Suspense>} />
            <Route path="funds" element={<Suspense fallback={<Skeleton active />}><Funds /></Suspense>} />
            <Route path="market-review" element={<Suspense fallback={<Skeleton active />}><MarketResearch kind="market" /></Suspense>} />
            <Route path="market-explorer" element={<Suspense fallback={<Skeleton active />}><MarketExplorer /></Suspense>} />
            <Route path="news" element={<Suspense fallback={<Skeleton active />}><News /></Suspense>} />
            <Route path="knowledge" element={<Suspense fallback={<Skeleton active />}><Knowledge /></Suspense>} />
            <Route path="strategy-lab" element={<Suspense fallback={<Skeleton active />}><StrategyLab /></Suspense>} />
            <Route path="governance" element={<Suspense fallback={<Skeleton active />}><Governance /></Suspense>} />
            <Route
              path="settings"
              element={
                <Suspense fallback={<Skeleton active />}>
                  <Settings />
                </Suspense>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AntApp>
    </ConfigProvider>
  );
}
