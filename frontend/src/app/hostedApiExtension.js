// Optional hosted API-method registry.
//
// The hosted composition root registers a factory that builds the hosted-only
// fetch helpers (guest trial, account quota, admin, hosted Feishu OAuth,
// desktop sync). Shared code (`useApi` in shared.jsx) only consumes whatever
// was registered: it calls the factory with the base request primitives and
// spreads the returned methods onto its result. When nothing is registered —
// the local edition — those methods are simply absent, so the hosted-only
// route strings never enter the shared/local import graph. This mirrors
// `directUploadTransport.js`.
let hostedApiExtension = null;

export const registerHostedApiExtension = (factory) => {
    hostedApiExtension = typeof factory === 'function' ? factory : null;
};

export const getHostedApiExtension = () => hostedApiExtension;
