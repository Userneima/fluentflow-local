export const LOCAL_EXECUTION_HEADER = 'X-FluentFlow-Execution-Target';
export const LOCAL_EXECUTION_TARGET = 'local';

export const shouldUseLocalSingleUserClientId = () => {
    const { hostname } = window.location;
    return hostname === '127.0.0.1' || hostname === 'localhost';
};

// Only the LOCAL route matters here: anything else (any cloud provider name,
// unknown values, empty) is non-local and gets no auth exemption on real
// hosts. Same semantics as the previous provider normalization, without
// naming any cloud provider.
const isLocalSttProvider = (provider) => (
    String(provider || '').trim().toLowerCase().replace(/-/g, '_') === 'local'
);

const isLocalLarkRoute = (route) => {
    const value = String(route || '').trim();
    return value === 'local_cli' || value === 'lark_cli';
};

export const shouldUseLocalExecution = (options={}) => (
    // On localhost single-user everything runs locally, so mark every request as
    // local execution regardless of STT provider — otherwise cloud-STT requests
    // are not exempted from account auth and get 401. The backend still verifies
    // the request originates from localhost, so public deployments are unaffected.
    !!options.localExecution
    || shouldUseLocalSingleUserClientId()
    || isLocalSttProvider(options.sttProvider)
    || isLocalLarkRoute(options.larkExportRoute)
    || !!options.larkViaCli
);

export const localExecutionHeaders = (options={}) => (
    shouldUseLocalExecution(options)
        ? {[LOCAL_EXECUTION_HEADER]: LOCAL_EXECUTION_TARGET}
        : {}
);

export const isLocalHistoryResult = (result={}) => (
    !!result?.imported_from_local_history ||
    result?.source === 'imported_local_history' ||
    result?.source === 'browser_local_history' ||
    String(result?.task_id || '').startsWith('imported_')
);
