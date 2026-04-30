import { defineConfig } from 'vite';

export default defineConfig({
  base: '/auri-ai-survey/',
  build: {
    outDir: 'dist',
  },
  server: {
    proxy: {
      '/ai/api': {
        target: 'https://alris.ddns.net:8443',
        changeOrigin: true,
        secure: false,
      },
    },
  },
});
