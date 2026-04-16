import { defineConfig } from 'vite';

export default defineConfig({
  base: '/auri-ai-survey/',
  build: {
    outDir: 'dist',
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
});
