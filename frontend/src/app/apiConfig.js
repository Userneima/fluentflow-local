// Who the page is to the service (client id, access token) and how it calls
// the service. One copy: shared.jsx re-exports these for the pages.
import { shouldUseLocalSingleUserClientId } from '../lib/localExecution.js';
import { currentApiBase } from '../lib/apiBase.js';

/** API 根路径。判据见 lib/apiBase.js：默认同源，只有 Vite 开发服务器才跨端口。 */
export const API_BASE = currentApiBase();

export const ACCESS_TOKEN_KEY = 'fluentflow_access_token';
export const CLIENT_ID_KEY = 'fluentflow_client_id';
export const LOCAL_SINGLE_USER_CLIENT_ID = 'local-single-user';
export const getAccessToken = () => (localStorage.getItem(ACCESS_TOKEN_KEY) || '').trim();
// A cookie copy of the token, for the screenshots in notes: <img> tags cannot
// send the token header, and from another device in LAN mode every read needs
// it. The service accepts the cookie for reads only.
export const rememberAccessTokenCookie = (token) => {
    try {
        const value = String(token || '').trim();
        document.cookie = value
            ? `${ACCESS_TOKEN_KEY}=${encodeURIComponent(value)}; path=/; SameSite=Strict; max-age=31536000`
            : `${ACCESS_TOKEN_KEY}=; path=/; SameSite=Strict; max-age=0`;
    } catch (_) { /* no cookies in this context */ }
};
export const setAccessToken = (token) => {
    const value = String(token || '').trim();
    if (value) localStorage.setItem(ACCESS_TOKEN_KEY, value);
    else localStorage.removeItem(ACCESS_TOKEN_KEY);
    rememberAccessTokenCookie(value);
};
export const createClientId = () => (
    window.crypto?.randomUUID?.()
    || `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
);
// Also written as a cookie, not only to localStorage.
//
// The browser loads a note's inline frames itself, as <img>, and an <img> cannot
// carry the client-id header the fetch helper adds — so those requests arrived
// unidentified and the server answered 404 for a task it holds. The backend has
// always accepted this cookie as the second place to look for the same value;
// nothing ever set it. Now that the note's pictures *are* the note, an image that
// silently fails to load is a broken product feature, not a cosmetic gap.
export const rememberClientIdCookie = (value) => {
    const id = String(value || '').trim();
    if (!id) return;
    try {
        document.cookie = `${CLIENT_ID_KEY}=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax`;
    } catch (_) {
        // Cookies unavailable: fetches still work through the header, and inline
        // images stay broken. Not worth failing anything over.
    }
};
export const getClientId = () => {
    if (shouldUseLocalSingleUserClientId()) {
        localStorage.setItem(CLIENT_ID_KEY, LOCAL_SINGLE_USER_CLIENT_ID);
        rememberClientIdCookie(LOCAL_SINGLE_USER_CLIENT_ID);
        return LOCAL_SINGLE_USER_CLIENT_ID;
    }
    const existing = (localStorage.getItem(CLIENT_ID_KEY) || '').trim();
    if (existing) {
        rememberClientIdCookie(existing);
        return existing;
    }
    const next = createClientId();
    localStorage.setItem(CLIENT_ID_KEY, next);
    rememberClientIdCookie(next);
    return next;
};
let tokenCookieSynced = false;
export const apiFetch = (input, init={}) => {
    const token = getAccessToken();
    if (!tokenCookieSynced) { tokenCookieSynced = true; rememberAccessTokenCookie(token); }
    const headers = new Headers(init.headers || {});
    if (!headers.has('X-FluentFlow-Client-Id')) {
        headers.set('X-FluentFlow-Client-Id', getClientId());
    }
    if (token && !headers.has('X-FluentFlow-Access-Token')) {
        headers.set('X-FluentFlow-Access-Token', token);
    }
    return fetch(input, {...init, credentials: init.credentials || 'include', headers});
};
export const apiErrorMessage = (payload, fallback='Request failed') => {
    const detail = payload?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || item?.message || String(item)).join('; ');
    if (detail && typeof detail === 'object') {
        const message = detail.message || detail.detail || fallback;
        return String(message);
    }
    return fallback;
};
