import { localExecutionHeaders } from '../lib/localExecution.js';

export const createApiClient = ({apiBase='', fetcher}) => {
    const resolveUrl = (path) => {
        const value = String(path || '');
        if (/^https?:\/\//i.test(value)) return value;
        return `${apiBase}${value.startsWith('/') ? value : `/${value}`}`;
    };
    const request = (path, init={}, executionOptions={}) => {
        const headers = new Headers(init.headers || {});
        Object.entries(localExecutionHeaders(executionOptions)).forEach(([key, value]) => {
            if (!headers.has(key)) headers.set(key, value);
        });
        return fetcher(resolveUrl(path), {...init, headers});
    };
    return {
        request,
        local: {
            request: (path, init={}) => request(path, init, {localExecution: true}),
        },
        cloud: {
            request: (path, init={}) => request(path, init, {}),
        },
        forExecution: (executionOptions={}) => ({
            request: (path, init={}) => request(path, init, executionOptions),
        }),
    };
};
