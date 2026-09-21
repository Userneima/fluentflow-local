// @vitest-environment jsdom

// The bar exists to replace two panels, so what it must never do is turn back into
// one: no action to take, no directory named, no next step. What it must do is make
// the page explain itself — the player is shorter than the file that was uploaded,
// and this is the only thing on screen that says why.
//
// The unavailable case is the one worth a test of its own. A page whose transcript
// and note describe a shortened file must not quietly play the original instead;
// the timings would be wrong everywhere and nothing would look broken.

import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
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
    it('renders nothing when there is nothing to report', () => {
        const {container} = render(<CutFlowBar summary={null} lang="zh"/>);
        expect(container.textContent).toBe('');
    });

    it('reports the step that already happened, with the numbers', () => {
        render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh"/>);

        expect(screen.getByText(/已自动去掉气口/)).toBeTruthy();
        expect(screen.getByText(/剪掉 4 处/)).toBeTruthy();
    });

    it('says the player, the subtitles and the note are all the one cut file', () => {
        render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh"/>);

        expect(screen.getByText(/播放、字幕、笔记都是这一份剪后版本/)).toBeTruthy();
        expect(screen.getByText(/原文件没有被改动/)).toBeTruthy();
    });

    it('asks the user for nothing: no processing action, no directory', () => {
        render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh"/>);

        const bar = screen.getByTestId('cut-flow-bar');
        expect(bar.textContent).not.toMatch(/剪掉空白|重写笔记|下一步/);
        expect(bar.textContent).not.toMatch(/目录|debreath\//);
        const buttons = screen.getAllByRole('button').map((b) => b.textContent);
        expect(buttons).toEqual([expect.stringMatching(/下载剪后文件/)]);
    });

    it('hands over the file rather than describing where it is', async () => {
        const onDownload = vi.fn().mockResolvedValue(undefined);
        render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh" onDownload={onDownload}/>);

        fireEvent.click(screen.getByRole('button', {name: /下载剪后文件/}));
        await waitFor(() => expect(onDownload).toHaveBeenCalledTimes(1));
    });

    it('flags a cut file that failed its own check instead of presenting it as clean', () => {
        const result = autoResult();
        result.debreath.render_verified = false;
        render(<CutFlowBar summary={cutFlowSummary(result)} lang="zh"/>);

        expect(screen.getByText(/没通过自检/)).toBeTruthy();
    });

    it('says the material is unavailable rather than letting the original stand in', () => {
        render(<CutFlowBar summary={cutFlowSummary(autoResult())} lang="zh" unavailable/>);

        expect(screen.getByText(/剪后文件读不到了/)).toBeTruthy();
        expect(screen.getByText(/不能拿原文件顶上/)).toBeTruthy();
        expect(screen.queryByRole('button')).toBeNull();
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
