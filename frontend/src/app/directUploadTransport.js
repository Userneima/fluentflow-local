// Optional direct-upload transport registry.
//
// The hosted composition root registers its cloud direct-upload implementation
// (OSS multipart) here; shared code only consumes whatever was registered and
// falls back to the regular queue upload when nothing is. This inversion keeps
// the cloud-only upload client out of the shared/local import graph.
let directUploadTransport = null;

export const registerDirectUploadTransport = (transport) => {
    directUploadTransport = typeof transport === 'function' ? transport : null;
};

export const getDirectUploadTransport = () => directUploadTransport;
