/** 前端把请求发到哪里。
 *
 * 本地版的页面是后端自己发出来的，所以默认同源、用相对路径。唯一需要绝对地址的
 * 场景是 Vite 的开发服务器：它跑在自己的端口上，后端在另一个端口，页面必须跨过
 * 去找它。
 *
 * 判据是「这是不是开发服务器的端口」，不是「端口是不是 8000」。原来写的是后者，
 * 于是 FLUENTFLOW_LOCAL_PORT 换过端口的用户——启动器、.env 和 README 都说这个
 * 变量可以改——页面从新端口打开，请求却全被送去 8000。那里要么什么都没有，要么
 * 是另一个实例，而且两种情况都不报错：界面照常渲染，显示的是别人的任务。
 */

// Vite 的开发端口（本地版配置的 5186、Vite 默认的 5173）和 preview 的 4173。
const DEV_SERVER_PORTS = new Set(['5173', '5186', '4173']);
const DEV_BACKEND = 'http://127.0.0.1:8000';

const normalize = (value) => String(value || '').trim().replace(/\/+$/, '');

/**
 * @param {{hostname?: string, port?: string}} location 页面地址
 * @param {string} [configured] 显式配置的地址，优先于一切推断
 */
export const resolveApiBase = (location, configured) => {
    const explicit = normalize(configured);
    if (explicit) return explicit;

    const hostname = location?.hostname || '';
    const port = String(location?.port || '');
    // 没有 hostname 的只有 file:// 之类，那时同源没有意义。
    if (!hostname) return DEV_BACKEND;

    const isLocal = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';
    if (isLocal && DEV_SERVER_PORTS.has(port)) return DEV_BACKEND;
    return '';
};

export const readConfiguredApiBase = () => {
    const fromConfig = typeof window === 'undefined' ? '' : window.FLUENTFLOW_CONFIG?.apiBase;
    let fromStorage = '';
    try {
        fromStorage = localStorage.getItem('fluentflow_api_base') || '';
    } catch (_) { /* storage unavailable; the config value still applies */ }
    return fromConfig || fromStorage;
};

export const currentApiBase = () => resolveApiBase(
    typeof window === 'undefined' ? {} : window.location,
    readConfiguredApiBase(),
);
