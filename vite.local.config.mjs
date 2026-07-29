import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';

// Local-edition frontend build: identical Vite setup to vite.config.mjs, but
// the only entry is frontend/local.html (the local composition root) and the
// output goes to frontend/dist-local, so `npm run build:frontend` and its
// hosted frontend/dist output are untouched.
export default defineConfig({
  root: 'frontend',
  plugins: [react()],
  publicDir: 'public',
  server: {
    port: 5186,
  },
  build: {
    outDir: 'dist-local',
    emptyOutDir: true,
    assetsDir: 'assets',
    rollupOptions: {
      input: fileURLToPath(new URL('./frontend/local.html', import.meta.url)),
    },
  },
});
