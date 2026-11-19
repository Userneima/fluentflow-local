import {
    pickDisplayTranscriptSegments,
    pickTranscriptSegments,
} from './format.js';
import { normalizeTaskState } from './taskState.js';

export const RESULT_SCHEMA_VERSION = '2';

/**
 * @typedef {Object} ResultPayloadV2
 * @property {string} result_schema_version
 * @property {string=} task_id
 * @property {string=} filename
 * @property {string=} raw_title
 * @property {string=} display_title
 * @property {string=} transcript_text
 * @property {Array<Object>} raw_segments
 * @property {Array<Object>} display_segments
 * @property {string=} summary_markdown
 * @property {string=} summary_status
 * @property {string=} summary_error
 * @property {boolean=} summary_skipped
 * @property {Object=} artifacts
 * @property {Object=} processing_plan
 * @property {Object=} chapter_coverage
 */

/**
 * @typedef {Object} JobPayload
 * @property {string} task_id
 * @property {string} status
 * @property {string} task_state
 * @property {ResultPayloadV2} result
 */

/**
 * @typedef {Object} AgentTaskPackage
 * @property {string} agent_task_package_version
 * @property {Object} task
 * @property {Object} transcript
 * @property {Object} note
 * @property {Object=} artifacts
 * @property {Array<Object>=} next_actions
 * @property {Object=} processing_plan
 */

const asObject = (value) => (value && typeof value === 'object' && !Array.isArray(value) ? value : {});
const text = (value) => String(value || '');

// The note's own states. Link downloads once stored their progress text
// ("正在保存视频信息") in summary_status, and old rows still carry it; anything
// outside this set is not a note status and is never shown as one.
const NOTE_STATUSES = new Set(['completed', 'failed', 'skipped', 'pending']);
export const noteStatusOf = (status) => {
    const value = text(status).trim().toLowerCase();
    return NOTE_STATUSES.has(value) ? value : null;
};

export const normalizeSummaryStatus = (status, result={}) => {
    const value = noteStatusOf(status);
    if (value) return value;
    if (text(result.summary_markdown).trim()) return 'completed';
    if (result.summary_skipped) return 'skipped';
    if (text(result.summary_error).trim()) return 'failed';
    return null;
};

export const normalizeResultPayload = (value={}) => {
    const source = asObject(value);
    const rawSegments = pickTranscriptSegments(source);
    const displaySegments = pickDisplayTranscriptSegments(source, rawSegments);
    const schemaVersion = text(source.result_schema_version).trim();

    return {
        ...source,
        result_schema_version: RESULT_SCHEMA_VERSION,
        ...(schemaVersion && schemaVersion !== RESULT_SCHEMA_VERSION
            ? {result_schema_migrated_from: schemaVersion}
            : {}),
        transcript_text: source.transcript_text || source.transcript_text_preview || '',
        raw_segments: rawSegments,
        display_segments: displaySegments,
        summary_markdown: source.summary_markdown || '',
        summary_status: normalizeSummaryStatus(source.summary_status, source),
    };
};

export const normalizeJobPayload = (value={}) => {
    const source = asObject(value);
    const taskState = normalizeTaskState(source);
    return {
        ...source,
        status: source.status || taskState,
        task_state: taskState,
        result: normalizeResultPayload(source.result || {}),
    };
};
