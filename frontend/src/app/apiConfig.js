import { shouldUseLocalSingleUserClientId } from '../lib/localExecution.js';
import { createApiClient } from './apiClient.js';

export const API_BASE = (() => {
    const normalize = (value) => String(value || '').trim().replace(/\/+$/, '');
    const configured = normalize(window.FLUENTFLOW_CONFIG?.apiBase || localStorage.getItem('fluentflow_api_base'));
    if (configured) return configured;
    const { hostname, port } = window.location;
    if (!hostname) return "http://127.0.0.1:8000";
    const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
    if (local && port && port !== "8000") return "http://127.0.0.1:8000";
    return "";
})();

export const ACCESS_TOKEN_KEY = 'fluentflow_access_token';
const CLIENT_ID_KEY = 'fluentflow_client_id';
const LOCAL_SINGLE_USER_CLIENT_ID = 'local-single-user';

export const getAccessToken = () => (localStorage.getItem(ACCESS_TOKEN_KEY) || '').trim();
export const setAccessToken = (token) => {
    const value = String(token || '').trim();
    if (value) localStorage.setItem(ACCESS_TOKEN_KEY, value);
    else localStorage.removeItem(ACCESS_TOKEN_KEY);
};

const createClientId = () => (
    window.crypto?.randomUUID?.()
    || `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
);
const getClientId = () => {
    if (shouldUseLocalSingleUserClientId()) {
        localStorage.setItem(CLIENT_ID_KEY, LOCAL_SINGLE_USER_CLIENT_ID);
        return LOCAL_SINGLE_USER_CLIENT_ID;
    }
    const existing = (localStorage.getItem(CLIENT_ID_KEY) || '').trim();
    if (existing) return existing;
    const next = createClientId();
    localStorage.setItem(CLIENT_ID_KEY, next);
    return next;
};

export const apiFetch = (input, init={}) => {
    const token = getAccessToken();
    const headers = new Headers(init.headers || {});
    if (!headers.has('X-FluentFlow-Client-Id')) {
        headers.set('X-FluentFlow-Client-Id', getClientId());
    }
    if (token && !headers.has('X-FluentFlow-Access-Token')) {
        headers.set('X-FluentFlow-Access-Token', token);
    }
    return fetch(input, {...init, credentials: init.credentials || 'include', headers});
};

export const fluentFlowApi = createApiClient({apiBase: API_BASE, fetcher: apiFetch});

export const apiErrorMessage = (payload, fallback='Request failed') => {
    const detail = payload?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg || item?.message || String(item)).join('; ');
    if (detail && typeof detail === 'object') {
        const message = detail.message || detail.detail || fallback;
        if (detail.required_units != null && detail.balance_units != null) {
            return `${message} 当前 ${detail.balance_units}，预计需要 ${detail.required_units}。`;
        }
        return String(message);
    }
    return fallback;
};
