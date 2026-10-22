// @vitest-environment jsdom

// Requirements (owner, 2026-10-22): the page does not announce the cut; the bar
// appears only when the cut file cannot be read, says so in one sentence, and
// offers to process the recording again. The cut video itself is downloaded from
// the transcript's export menu.

import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import CutFlowBar from './CutFlowBar.jsx';
import {cutFlowSummary, isAutoCutFlow} from '../routes/editor-helpers.js';

afterEach(cleanup);

const autoResult = (extra = {}) => ({
    task_id: 't1',
    transcript_media: 'debreath_media',
    debreath: {
        status: 'completed',
        ran_before_transcription: true,
        used_for_transcription: true,
        rendered: true,
        render_verified: true,
        plan: {cut_count: 4, removed_seconds: 6.4, removed_percent: 46, source_duration_seconds: 13.9, kept_seconds: 7.5},
    },
    artifacts: {debreath_media: {filename: 'debreath/lecture_debreath.mp4'}},
    ...extra,
});

describe('isAutoCutFlow', () => {
    it('recognises a task that cut itself before transcribing', () => {
        expect(isAutoCutFlow(autoResult())).toBe(true);
    });

    it('does not claim the flow for a task cut after it was transcribed', () => {
        expect(isAutoCutFlow({
            task_id: 't1',
            transcript_media: 'source',
            debreath: {status: 'completed', rendered: true, plan: {cut_count: 4}},
        })).toBe(false);
    });

    it('does not claim it when the automatic cut declined and the original was used', () => {
        // The manual entries must stay visible here: the page is about the recording.
        expect(isAutoCutFlow(autoResult({
            transcript_media: 'source',
            debreath: {ran_before_transcription: true, used_for_transcription: false},
        }))).toBe(false);
    });

    it('leaves old results alone', () => {
        expect(isAutoCutFlow({task_id: 't1'})).toBe(false);
        expect(isAutoCutFlow(null)).toBe(false);
    });
});

describe('CutFlowBar', () => {
    it('shows nothing while the cut file can be read', () => {
        const {container} = render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh"/>);
        expect(container.textContent).toBe('');
    });

    it('shows nothing for a task that was not cut', () => {
        const {container} = render(<CutFlowBar summary={null} lang="zh" unavailable/>);
        expect(container.textContent).toBe('');
    });

    it('says in one sentence that the cut file is unreadable, with a way to process it again', () => {
        const onReprocess = vi.fn();
        render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh" unavailable onReprocess={onReprocess}/>);

        const bar = screen.getByTestId('cut-flow-bar');
        const button = screen.getByRole('button', {name: /重新处理/});
        expect(bar.textContent.replace(button.textContent, '').trim()).toBe('剪后文件读不到了');
        fireEvent.click(button);
        expect(onReprocess).toHaveBeenCalledTimes(1);
    });
});

describe('cutFlowSummary', () => {
    it('carries the numbers the bar shows, and the file to download', () => {
        expect(cutFlowSummary(autoResult())).toEqual({
            cutCount: 4,
            removedSeconds: 6.4,
            removedPercent: 46,
            sourceSeconds: 13.9,
            keptSeconds: 7.5,
            mediaArtifact: {filename: 'debreath/lecture_debreath.mp4'},
            renderVerified: true,
        });
    });

    it('is null for anything that did not go through the automatic flow', () => {
        expect(cutFlowSummary({task_id: 't1', transcript_media: 'source'})).toBeNull();
    });

    it('still summarises when the cut file record is gone, so the bar can say so', () => {
        const summary = cutFlowSummary(autoResult({artifacts: {}}));
        expect(summary.mediaArtifact).toBeNull();
        expect(summary.cutCount).toBe(4);
    });
});
