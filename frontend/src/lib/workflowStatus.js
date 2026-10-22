import {normalizeTextProvider} from './textProviders.js';

// The choices kept only in this browser that change who does a step, in the
// form GET /workflow-status takes them (backend/core/workflow_status.py).
// Settings and the start page both send these, so they get the same answer.
export const workflowChoicesFrom = (settings = {}) => ({
    ai_provider: normalizeTextProvider(settings.aiProvider),
    ai_model: settings.aiModel || '',
    default_source: settings.defaultSourceMode || '',
    speaker_diarization: settings.speakerDiarization ? '1' : '',
    voice_enhance: settings.voiceEnhance ? '1' : '',
    skip_ai_summary: settings.skipAiSummary ? '1' : '',
    export_to_lark: settings.exportToLark ? '1' : '',
});

// What the service says needs the person before a note can be written: an
// expired Claude login, or nothing that can write a note.
export const NOTE_ATTENTION_IDS = ['claude-login', 'no-writer'];
