// Whether an error means the local service did not answer at all.
//
// fetch rejects with a TypeError ("Failed to fetch" in Chromium, "Load failed"
// in WebKit, "NetworkError when attempting to fetch resource." in Gecko) when
// nothing is listening. An HTTP error status is the opposite case: the service
// answered, so it is not evidence of an outage.
export const isBackendUnreachableError = (error) => {
    if (!error) return false;
    if (error.name === 'AbortError' || error.aborted) return false;
    if (typeof error.status === 'number' && error.status > 0) return false;
    const text = `${error.name || ''} ${error.message || ''}`.toLowerCase();
    return error instanceof TypeError
        || text.includes('failed to fetch')
        || text.includes('load failed')
        || text.includes('networkerror')
        || text.includes('network error')
        || text.includes('err_connection_refused')
        || text.includes('econnrefused');
};

export const BACKEND_DOWN_MESSAGE = {
    zh: '本机服务没有响应，请重新打开 FluentFlow Local；任务和记录都还在。',
    en: 'The local service is not responding. Reopen FluentFlow Local; your tasks and records are still there.',
};
