import {
    normalizeDisplaySegments,
    normalizeTranscriptSegments,
    pickDisplayTranscriptSegments,
    pickTranscriptSegments,
} from './format.js';
import { normalizeTaskState } from './taskState.js';

export const RESULT_SCHEMA_VERSION = '2';
export const AGENT_TASK_PACKAGE_VERSION = '1';

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

export const normalizeSummaryStatus = (status, result={}) => {
    const value = text(status).trim().toLowerCase();
    if (['completed', 'failed', 'skipped', 'pending'].includes(value)) return value;
    if (text(result.summary_markdown).trim()) return 'completed';
    if (result.summary_skipped) return 'skipped';
    if (text(result.summary_error).trim()) return 'failed';
    return value || null;
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

export const normalizeAgentTaskPackage = (value={}) => {
    const source = asObject(value);
    const task = asObject(source.task);
    const transcript = asObject(source.transcript);
    const rawSegments = normalizeTranscriptSegments(transcript.raw_segments);
    const displaySegments = normalizeDisplaySegments(transcript.display_segments);
    const note = asObject(source.note);

    return {
        ...source,
        agent_task_package_version: text(source.agent_task_package_version).trim() || AGENT_TASK_PACKAGE_VERSION,
        task: {
            ...task,
            task_state: normalizeTaskState(task),
        },
        transcript: {
            ...transcript,
            raw_segments: rawSegments,
            display_segments: displaySegments,
            available: !!(
                transcript.available
                || text(transcript.text).trim()
                || rawSegments.length
                || displaySegments.length
            ),
            raw_segment_count: Number(transcript.raw_segment_count ?? rawSegments.length) || rawSegments.length,
            display_segment_count: Number(transcript.display_segment_count ?? displaySegments.length) || displaySegments.length,
        },
        note: {
            ...note,
            status: normalizeSummaryStatus(note.status, {
                summary_markdown: note.markdown,
                summary_error: asObject(note.diagnosis).detail,
            }) || note.status || 'pending',
        },
    };
};
