import base from './vite.preview-lane3.config.mjs';

// Records page mockup preview (?layout=v2): same proxy to 8000, its own port.
// The app's page is local.html, so every page address is served from it.
const serveLocalHtml = {
    name: 'serve-local-html',
    configureServer(server) {
        server.middlewares.use((req, _res, next) => {
            const path = (req.url || '').split('?')[0];
            if (req.headers.accept?.includes('text/html') && !path.includes('.')) {
                req.url = `/local.html${req.url.slice(path.length)}`;
            }
            next();
        });
    },
};

export default {...base, plugins: [...(base.plugins || []), serveLocalHtml], server: {...base.server, port: 5197}};
