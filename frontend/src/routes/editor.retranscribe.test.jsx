// @vitest-environment jsdom

// Retranscribing a result whose recording FluentFlow still has.
//
// Requirement (2026-10-14): the recording is run again from where it is on this
// machine. It is not fetched into the browser and posted back, which moved the
// whole file twice and stored a second copy of it.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let lastResult = null;
const calls = {retry: [], sourceFetch: 0, upload: 0, ingested: []};

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        lastResult,
        setLastResult: (next) => { lastResult = next; },
        lastSourceFile: null,
        setLastSourceFile: () => {},
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        addLarkExport: () => {},
        runtimeConfig,
        ingestJobs: (jobs) => { calls.ingested.push(...jobs); },
    }),
}));

// One object for every render: the editor's effects depend on these functions,
// and new ones on each render would refetch the player's source forever.
const api = {
    processVideoSSE: async () => { calls.upload += 1; return {}; },
    retryJob: async (taskId) => {
        calls.retry.push(taskId);
        return {ok: true, task_id: 't2', job: {task_id: 't2', status: 'queued'}};
    },
    fetchJobSourceFile: async () => { calls.sourceFetch += 1; return new File(['x'], 'lecture.mp4'); },
    fetchJobArtifactFile: async (taskId, kind, filename) => new File(['x'], filename || kind, {type: 'audio/mpeg'}),
    recordEvent: () => {},
    getJob: async () => ({}),
    saveTranscriptEdit: async () => ({}),
    saveSummaryEdit: async () => ({}),
    getCredentialsStatus: async () => null,
};

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({loadSettings: () => ({}), saveSettings: () => {}}),
        useApi: () => api,
    };
});

const {default: Editor} = await import('./editor.jsx');

describe('retranscribing in the editor', () => {
    beforeEach(() => {
        calls.retry = [];
        calls.sourceFetch = 0;
        calls.upload = 0;
        calls.ingested = [];
        URL.createObjectURL = () => 'blob:x';
        // The player reads media straight from the service; one byte is asked for
        // first to see the file is there.
        globalThis.fetch = vi.fn(async () => new Response('x', {status: 206, headers: {'content-type': 'video/mp4'}}));
        URL.revokeObjectURL = () => {};
        lastResult = {
            task_id: 't1',
            filename: 'lecture.mp4',
            transcript_text: 'hello',
            segments: [{start: 0, end: 3, text: 'hello'}],
            summary_markdown: 'note',
            source_file_available: true,
            artifacts: {},
        };
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    it('runs the recording again from this machine instead of moving it through the browser', async () => {
        render(<MemoryRouter><Editor/></MemoryRouter>);

        (await screen.findByRole('button', {name: '更多操作'})).click();
        (await screen.findByRole('menuitem', {name: /edit\.retranscribe$/})).click();
        (await screen.findByRole('button', {name: /edit\.retranscribeConfirmAction/})).click();

        await waitFor(() => expect(calls.retry).toEqual(['t1']));
        expect(calls.ingested.map((job) => job.task_id)).toEqual(['t2']);
        expect(calls.sourceFetch).toBe(0);
        expect(calls.upload).toBe(0);
    });
});

describe('retranscribing a recording read in place', () => {
    beforeEach(() => {
        calls.retry = [];
        calls.sourceFetch = 0;
        calls.upload = 0;
        calls.ingested = [];
        URL.createObjectURL = () => 'blob:x';
        URL.revokeObjectURL = () => {};
        globalThis.fetch = vi.fn(async () => new Response('x', {status: 206, headers: {'content-type': 'audio/mpeg'}}));
        // Chosen with the system picker: no stored copy, the original stays where it is.
        lastResult = {
            task_id: 't1',
            filename: 'lecture.mov',
            transcript_text: 'hello',
            segments: [{start: 0, end: 3, text: 'hello'}],
            summary_markdown: 'note',
            source_file_available: false,
            artifacts: {playback_audio: {filename: 'lecture_audio.mp3'}},
        };
    });

    afterEach(() => {
        vi.restoreAllMocks();
        cleanup();
    });

    it('asks the backend to re-read the original instead of uploading the playback audio', async () => {
        render(<MemoryRouter><Editor/></MemoryRouter>);

        (await screen.findByRole('button', {name: '更多操作'})).click();
        (await screen.findByRole('menuitem', {name: /edit\.retranscribe$/})).click();
        (await screen.findByRole('button', {name: /edit\.retranscribeConfirmAction/})).click();

        await waitFor(() => expect(calls.retry).toEqual(['t1']));
        expect(calls.upload).toBe(0);
    });
});
