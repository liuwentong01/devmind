// Vite 开发/预览配置。使用：npm run dev（5173）或 npm run build && npm run preview（4173）。
// 仅开发服务器代理 /api；生产同源代理由 nginx.conf 负责。
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发环境把 /api 代理到本地后端，避免跨域
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
  },
});
