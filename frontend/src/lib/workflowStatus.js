// The choices still kept only in this browser that change a step's line, in
// the form GET /workflow-status takes them (backend/core/workflow_status.py).
// The text model, speaker labels and auto export are read by the service
// itself (decision 6.31).
export const workflowChoicesFrom = (settings = {}) => ({
    default_source: settings.defaultSourceMode || '',
    voice_enhance: settings.voiceEnhance ? '1' : '',
});

// What the service says needs the person before a note can be written: an
// expired Claude login, or nothing that can write a note.
export const NOTE_ATTENTION_IDS = ['claude-login', 'no-writer'];
