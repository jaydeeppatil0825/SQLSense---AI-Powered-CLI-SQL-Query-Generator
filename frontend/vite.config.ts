import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: process.env.VITE_DEV_API_PROXY
      ? {
          "/health": process.env.VITE_DEV_API_PROXY,
          "/api/v1/gateway": process.env.VITE_DEV_API_PROXY,
        }
      : undefined,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
