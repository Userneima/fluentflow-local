// Local-edition frontend capability registry.
//
// Pure data with no imports: the local composition root and its tests consume
// it, and the manifest gate can walk it as a local_ready entry. Every surface
// the manifest's frontend_contract forbids must be declared false here; the
// contract test maps the manifest wording to these keys via
// FORBIDDEN_SURFACE_CAPABILITY_KEYS, so a new forbidden surface fails tests
// until it is mapped and disabled.
export const LOCAL_FRONTEND_CAPABILITIES = Object.freeze({
    edition: 'local',
    // Surfaces the local edition must not have.
    accounts: false,
    guestTrial: false,
    adminConsole: false,
    pricingOrQuota: false,
    commercialLanding: false,
    cloudTranscription: false,
    ossDirectUpload: false,
    desktopSync: false,
    hostedFeishuOAuth: false,
    // The local product surface.
    localTranscription: true,
    noteGeneration: true,
    transcriptAndNoteEditing: true,
    localDownloads: true,
    localFeishuExport: true,
    videoLinkResolution: true,
    jobCancellation: true,
    // Mechanical silence removal on a finished task's own source file. Local
    // because ffmpeg and the file are both on this machine; no model, no upload.
    breathGapRemoval: true,
    agentApi: true,
});

// manifest frontend_contract.forbidden_surfaces wording -> capability key.
export const FORBIDDEN_SURFACE_CAPABILITY_KEYS = Object.freeze({
    'commercial landing page': 'commercialLanding',
    'login or registration': 'accounts',
    'guest trial': 'guestTrial',
    'pricing or quota': 'pricingOrQuota',
    'cloud transcription': 'cloudTranscription',
    'OSS upload': 'ossDirectUpload',
    'cross-device sync': 'desktopSync',
    'hosted Feishu OAuth': 'hostedFeishuOAuth',
});
