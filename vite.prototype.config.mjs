import base from './vite.local.config.mjs';

// Dev server for the editor prototype: serve local.html for app routes like /editor.
export default {
  ...base,
  plugins: [
    ...base.plugins,
    {
      name: 'local-html-fallback',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          const path = (req.url || '').split('?')[0];
          if (!path.includes('.') && !path.startsWith('/@') && !path.startsWith('/node_modules')) {
            req.url = `/local.html${req.url.slice(path.length)}`;
          }
          next();
        });
      },
    },
  ],
};
