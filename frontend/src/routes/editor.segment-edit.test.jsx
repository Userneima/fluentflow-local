// @vitest-environment jsdom

// Editing one sentence of the transcript. What the person should get:
//   - the letter appears as they type,
//   - nothing outside this page is touched per keystroke (the whole app used
//     to re-render on every key, because each one replaced the shared result),
//   - once they pause, the shared result and the saved copy both carry the
//     change, with an edit record for that sentence.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const runtimeConfig = {allowedSttProviders: ['local'], defaultSttProvider: 'local', limits: {}};

let lastResult = null;
const setLastResult = vi.fn((next) => { lastResult = typeof next === 'function' ? next(lastResult) : next; });
const saveTranscriptEdit = vi.fn(async () => ({}));

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        lastResult,
        setLastResult,
        lastSourceFile: null,
        setLastSourceFile: () => {},
        addToHistory: () => {},
        currentJob: null,
        setCurrentJob: () => {},
        addLarkExport: () => {},
        runtimeConfig,
    }),
}));

const api = {
    processVideoSSE: async () => ({}),
    fetchJobSourceFile: async () => { throw new Error('no source'); },
    fetchJobArtifactFile: async () => { throw new Error('no artifact'); },
    recordEvent: () => {},
    getJob: async () => ({}),
    saveTranscriptEdit,
    saveSummaryEdit: async () => ({}),
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

const result = () => ({
    task_id: 't1',
    filename: 'lecture.mp4',
    transcript_text: 'hello world\nsecond line',
    raw_segments: [{start: 0, end: 3, text: 'hello world'}, {start: 3, end: 6, text: 'second line'}],
    segments: [{start: 0, end: 3, text: 'hello world'}, {start: 3, end: 6, text: 'second line'}],
    summary_markdown: 'note',
});

describe('editing a transcript segment', () => {
    beforeEach(() => {
        lastResult = result();
        setLastResult.mockClear();
        saveTranscriptEdit.mockClear();
        URL.createObjectURL = () => 'blob:x';
        URL.revokeObjectURL = () => {};
    });
    afterEach(cleanup);

    it('shows the keystroke at once, and shares and saves it only after a pause', async () => {
        render(<MemoryRouter><Editor/></MemoryRouter>);
        const box = await screen.findByRole('textbox', {name: '第 1 句'});
        await waitFor(() => expect(box.textContent).toBe('hello world'));

        box.textContent = 'hello worlds';
        fireEvent.input(box);
        expect(box.textContent).toBe('hello worlds');
        // The keystroke stayed on this page.
        expect(setLastResult).not.toHaveBeenCalled();
        expect(saveTranscriptEdit).not.toHaveBeenCalled();

        // After the pause, one shared update and one save, both with the edit.
        await waitFor(() => expect(saveTranscriptEdit).toHaveBeenCalledTimes(1), {timeout: 3000});
        expect(setLastResult).toHaveBeenCalled();
        const shared = setLastResult.mock.calls[0][0];
        expect(shared.transcript_edited).toBe(true);
        expect(shared.segments[0].text).toBe('hello worlds');
        expect(shared.transcript_text).toBe('hello worlds\nsecond line');
        expect(shared.transcript_edit_records).toHaveLength(1);
        expect(shared.transcript_edit_records[0]).toMatchObject({index: 0, before: 'hello world', after: 'hello worlds'});

        const [taskId, payload] = saveTranscriptEdit.mock.calls[0];
        expect(taskId).toBe('t1');
        expect(payload.segments[0].text).toBe('hello worlds');
        expect(payload.edit_records).toHaveLength(1);
        expect(payload.edit_records[0]).toMatchObject({index: 0, before: 'hello world', after: 'hello worlds'});
    });

    // The text view shows paragraphs; each sentence in one is still edited
    // and saved on its own.
    it('edits a sentence inside a paragraph and saves it the same way', async () => {
        render(<MemoryRouter initialEntries={['/editor']}><Editor/></MemoryRouter>);
        const box = await screen.findByRole('textbox', {name: '第 1 句'});
        await waitFor(() => expect(box.textContent).toBe('hello world'));

        box.textContent = 'hello worlds';
        fireEvent.input(box);

        await waitFor(() => expect(saveTranscriptEdit).toHaveBeenCalledTimes(1), {timeout: 3000});
        const [, payload] = saveTranscriptEdit.mock.calls[0];
        expect(payload.segments[0].text).toBe('hello worlds');
        expect(payload.segments[1].text).toBe('second line');
        expect(payload.edit_records[0]).toMatchObject({index: 0, before: 'hello world', after: 'hello worlds'});
    });
});
