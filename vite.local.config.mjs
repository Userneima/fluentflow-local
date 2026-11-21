import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';

// Local-edition frontend build: identical Vite setup to vite.config.mjs, but
// the only entry is frontend/local.html (the local composition root) and the
// output goes to frontend/dist-local, so `npm run build:frontend` and its
// hosted frontend/dist output are untouched.

// The dev server has no index for the local pages, so opening /editor?task=…
// or /agent directly answered 404. Page addresses (no dot in the path, an HTML
// request) are served local.html, as the service on 8000 does. Dev server only.
const localPageFallback = {
  name: 'local-page-fallback',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      const [path, query] = (req.url || '').split('?');
      const isPage = req.method === 'GET' && (req.headers.accept || '').includes('text/html')
        && !path.includes('.') && !path.startsWith('/@');
      if (isPage) req.url = '/local.html' + (query ? `?${query}` : '');
      next();
    });
  },
};

export default defineConfig({
  root: 'frontend',
  plugins: [react(), localPageFallback],
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
