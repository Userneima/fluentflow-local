// @vitest-environment jsdom

// This strip answers "what was this note written from", and the reason it exists is
// a design decision worth pinning: it shows the *evidence*, never the reasoning.
//
// The properties that matter:
//
// - the frames the note did NOT cite are reachable, because that is the only place a
//   missed slide can be noticed at all;
// - the count comes from the note's own citations, so it can contradict a confident
//   note — and it must say so out loud when nothing was cited;
// - the model's self-report is present but labelled as not being the evidence, which
//   is the same rule the backend enforces for `basis`;
// - it stays one line until asked, because the page had just lost a bar for
//   narrating the normal outcome.

import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import NoteEvidenceStrip from './NoteEvidenceStrip.jsx';

afterEach(cleanup);

const frame = (name, seconds) => ({
    filename: name,
    timestamp_seconds: seconds,
    url: `/jobs/t1/artifacts/frame?file=${name}`,
});

const result = (extra = {}) => ({
    task_id: 't1',
    summary_written_from: 'debreath_media_note',
    transcript_media: 'debreath_media',
    visual_note: {
        status: 'completed',
        basis: 'transcript_and_frames',
        basis_note: '第一张有公式，其余是同一块底色。',
        frames_sent: [frame('note_0001.jpg', 12), frame('note_0002.jpg', 95), frame('note_0003.jpg', 310)],
        frames_cited: [frame('note_0001.jpg', 12)],
        subtitle_timeline: {source: 'cut_media_native', dropped_segments: 0},
        ...extra,
    },
});

describe('NoteEvidenceStrip', () => {
    it('says what the note was based on, in one line', () => {
        render(<NoteEvidenceStrip result={result()} lang="zh"/>);

        expect(screen.getByText('剪后画面 1/3 张 + 字幕')).toBeTruthy();
        // Nothing is expanded until asked.
        expect(screen.queryByRole('img')).toBeNull();
    });

    it('shows nothing for a note this flow did not write', () => {
        const {container} = render(
            <NoteEvidenceStrip result={{task_id: 't1', visual_note: {status: 'completed'}}} lang="zh"/>
        );
        expect(container.textContent).toBe('');
    });

    it('shows nothing for a task with no note of this kind at all', () => {
        const {container} = render(<NoteEvidenceStrip result={{task_id: 't1'}} lang="zh"/>);
        expect(container.textContent).toBe('');
    });

    it('opens the frames it read, including the ones it ignored', () => {
        render(<NoteEvidenceStrip result={result()} lang="zh"/>);

        fireEvent.click(screen.getByRole('button', {name: /看它读了哪些画面/}));

        const images = screen.getAllByRole('img');
        expect(images.length).toBe(3);
        // The uncited ones are the point: a missed slide is only visible here.
        expect(screen.getByTitle(/01:35.*没有引用/)).toBeTruthy();
        expect(screen.getByTitle(/00:12.*正文引用了/)).toBeTruthy();
    });

    it('jumps the player to the moment a frame came from', () => {
        const onSeek = vi.fn();
        render(<NoteEvidenceStrip result={result()} lang="zh" onSeek={onSeek}/>);
        fireEvent.click(screen.getByRole('button', {name: /看它读了哪些画面/}));

        fireEvent.click(screen.getByTitle(/05:10/));

        expect(onSeek).toHaveBeenCalledWith(310);
    });

    it('contradicts a note that cites nothing, out loud', () => {
        render(<NoteEvidenceStrip result={result({basis: 'transcript_only', frames_cited: []})} lang="zh"/>);

        expect(screen.getByText('剪后画面 0/3 张 + 字幕')).toBeTruthy();
        expect(screen.getByText('正文没有引用任何画面')).toBeTruthy();
    });

    it('keeps the model self-report out of the evidence, and labels it', () => {
        render(<NoteEvidenceStrip result={result()} lang="zh"/>);
        // Not on the collapsed line at all.
        expect(screen.queryByText(/第一张有公式/)).toBeNull();

        fireEvent.click(screen.getByRole('button', {name: /看它读了哪些画面/}));

        expect(screen.getByText(/不作为判据/)).toBeTruthy();
        expect(screen.getByText(/第一张有公式/)).toBeTruthy();
    });

    it('does not call them cut frames when the note read the recording', () => {
        // The cut declines or is skipped in four ordinary cases, and then the note is
        // written from the recording. Saying "剪后画面" there would be the product
        // claiming to have watched a file it never opened.
        const {container} = render(
            <NoteEvidenceStrip result={{...result(), transcript_media: 'source'}} lang="zh"/>
        );

        expect(screen.getByText('原片画面 1/3 张 + 字幕')).toBeTruthy();
        expect(container.textContent).not.toMatch(/剪后/);
    });

    it('never shows the reasoning, because it cannot be checked', () => {
        render(<NoteEvidenceStrip result={result()} lang="zh"/>);
        fireEvent.click(screen.getByRole('button', {name: /看它读了哪些画面/}));

        const text = screen.getByText(/这些是从剪后视频里/).textContent;
        expect(text).toMatch(/点任意一张跳到播放器/);
        expect(text).not.toMatch(/思考|推理|分析过程/);
    });

    it('says an audio note used subtitles alone, with nothing to expand', () => {
        render(
            <NoteEvidenceStrip
                result={result({basis: 'transcript_only', frames_sent: [], frames_cited: []})}
                lang="zh"
            />
        );

        expect(screen.getByText('只用了字幕')).toBeTruthy();
        expect(screen.queryByRole('button')).toBeNull();
    });

    it('reports subtitle lines whose audio the cut removed', () => {
        render(
            <NoteEvidenceStrip
                result={result({subtitle_timeline: {source: 'debreath_cut_list_remap', dropped_segments: 4}})}
                lang="zh"
            />
        );

        expect(screen.getByText('4 句字幕的声音被剪掉了')).toBeTruthy();
    });
});

// A lecture longer than one request can hold is the one case where this strip
// reports the note being wrong rather than explaining it being right. Nothing
// else on the page differs: the note reads like a finished one and stops
// somewhere in the middle of the recording.
describe('a note that could not read the whole talk', () => {
    it('says where it stops and what to do about it', () => {
        render(<NoteEvidenceStrip result={result({
            transcript_chars_dropped: 108024,
            transcript_covered_until: '147:03',
        })} lang="zh"/>);

        const line = screen.getByText(/只讲到 147:03/);
        expect(line.textContent).toMatch(/一次请求装不下/);
        expect(line.textContent).toMatch(/切成几段/);
    });

    it('still says so when the cut point has no timestamp to name', () => {
        render(<NoteEvidenceStrip result={result({transcript_chars_dropped: 4000})} lang="zh"/>);

        expect(screen.getByText(/没有覆盖整段录音/)).toBeTruthy();
    });

    it('says nothing at all about length for a recording that fitted', () => {
        render(<NoteEvidenceStrip result={result()} lang="zh"/>);

        expect(screen.queryByText(/一次请求装不下/)).toBeNull();
    });
});
