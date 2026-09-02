import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  // Only public port values are read here. Nothing from .env is injected into the bundle.
  const root = fileURLToPath(new URL("..", import.meta.url));
  const env = loadEnv(mode, root, "FINSIGHT_");
  const backendPort = Number(
    process.env.FINSIGHT_PORT || env.FINSIGHT_PORT || 8000,
  );
  const frontendPort = Number(
    process.env.FINSIGHT_FRONTEND_PORT || env.FINSIGHT_FRONTEND_PORT || 5173,
  );
  for (const port of [backendPort, frontendPort]) {
    if (!Number.isInteger(port) || port < 1024 || port > 65535)
      throw new Error("FinSight 端口配置无效");
  }
  const proxy = {
    "/api": { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false },
    "/health": {
      target: `http://127.0.0.1:${backendPort}`,
      changeOrigin: false,
    },
  };
  const headers = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  };
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: frontendPort,
      strictPort: true,
      proxy,
      headers,
    },
    preview: {
      host: "127.0.0.1",
      port: frontendPort,
      strictPort: true,
      proxy,
      headers,
    },
  };
});
