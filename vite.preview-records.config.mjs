import base from './vite.preview-lane3.config.mjs';

// A read-only preview of the frontend against the real data on 8000, on its own
// port (5197): look at pages without restarting 8000. Every write to the
// service is refused here, so nothing clicked or autosaved in this preview can
// change a task or a note (a preview page once saved a note preview over the
// whole note, decision 6.27).
// The app's page is local.html, so every page address is served from it.
const readOnlyLocalPreview = {
    name: 'read-only-local-preview',
    configureServer(server) {
        server.middlewares.use((req, res, next) => {
            const path = (req.url || '').split('?')[0];
            if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method) && !path.startsWith('/@') && !path.startsWith('/src/')) {
                res.statusCode = 403;
                res.setHeader('content-type', 'application/json');
                res.end(JSON.stringify({detail: 'read-only preview'}));
                return;
            }
            if (req.headers.accept?.includes('text/html') && !path.includes('.')) {
                req.url = `/local.html${req.url.slice(path.length)}`;
            }
            next();
        });
    },
};

const target = process.env.PREVIEW_API || 'http://127.0.0.1:8000';

export default {
    ...base,
    plugins: [...(base.plugins || []), readOnlyLocalPreview],
    server: {
        ...base.server,
        port: 5197,
        proxy: {...base.server.proxy, '/workflow-status': {target, changeOrigin: false}},
    },
};
