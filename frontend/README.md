# FinSight 前端

## 1. 当前能力

React 19、TypeScript strict、Vite 8、Ant Design 6、React Query、Axios 与 React Router。页面为 `/` 研究概览与 `/settings` 模型设置，尚无行情或研究报告。

只访问同源 `/api`，开发与预览服务器转发到本机 FastAPI。会话令牌只存内存，Key 只留在编辑表单内、关闭后销毁，不进入浏览器持久存储。设置页按路由延迟加载。

## 2. 命令

在本目录执行 `npm ci`；`npm run dev` 开发，`npm run build` 构建，`npm run preview` 预览，`npm run lint` 静态检查，`npm run test` 交互验证，`npm run format` 格式化。

根目录 `.env` 的 `FINSIGHT_PORT` 与 `FINSIGHT_FRONTEND_PORT` 只用于代理与端口，不注入浏览器。默认 `127.0.0.1:5173 → 127.0.0.1:8000`。前台启动脚本为 `scripts/start_frontend.ps1`。

## 3. 边界

不复制旧前端或 node_modules，不把凭据放入 VITE 变量，不用静态行情假装接入。仅已实现页面显示在导航中，包含 768px 及以下布局断点。
