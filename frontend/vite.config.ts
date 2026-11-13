import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

// 前端与后端通过 dev proxy 保持同源，httpOnly Cookie 与 SSE 因此无需任何跨源配置。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // 绑定所有地址，WSL 开发时 Windows 浏览器才能访问
    // 原生 Linux/macOS 开发可用 '127.0.0.1' 更安全（仅本机访问）
    host: '0.0.0.0',
    port: 7301,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://localhost:7302', changeOrigin: true },
      '/events': { target: 'http://localhost:7302', changeOrigin: true },
    },
  },
})
