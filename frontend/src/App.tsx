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
            <Route path="company" element={<Suspense fallback={<Skeleton active />}><Company /></Suspense>} />
            <Route path="watchlist" element={<Suspense fallback={<Skeleton active />}><Watchlist /></Suspense>} />
            <Route path="library" element={<Suspense fallback={<Skeleton active />}><Library /></Suspense>} />
            <Route path="compare" element={<Suspense fallback={<Skeleton active />}><Compare /></Suspense>} />
            <Route path="sectors" element={<Suspense fallback={<Skeleton active />}><MarketResearch kind="sectors" /></Suspense>} />
            <Route path="funds" element={<Suspense fallback={<Skeleton active />}><MarketResearch kind="funds" /></Suspense>} />
            <Route path="market-review" element={<Suspense fallback={<Skeleton active />}><MarketResearch kind="market" /></Suspense>} />
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
