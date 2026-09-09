// @vitest-environment jsdom

// Why this test exists.
//
// "The product calls an audio file a video" is a recurring, owner-reported
// mistake, not a wording slip: it tells the user the product misread what they
// handed it.
//
//   - d677f8aa fixed it in the stage labels — an uploaded .m4a was showing
//     "提取音频" / "下载视频", so audio-only tasks now read 准备音频 / 下载音频.
//     That commit added ten tests, but all of them pinned NoteEvidenceStrip; the
//     stage-label fix and the classifier under it got none.
//   - b1907b72 (defect 2) fixed the same class again in NoteEvidenceStrip — the
//     evidence strip called an un-cut file "剪后视频"/"剪后画面" — and that one
//     instance did get its guard.
//
// `isAudioOnlySource` is the single classifier every audio/video word on the
// records page and the task-detail page is keyed off (two separate `stageLabel`
// maps both branch on it). If it misclassifies, "提取音频 on an .m4a" comes
// straight back, invisibly, everywhere at once. These cases pin the exact
// failure that was reported plus each classification path, so a regression fails
// here instead of on a user's screen.

import {describe, expect, it} from 'vitest';
import {isAudioOnlySource} from './agent-tasks.jsx';

describe('isAudioOnlySource', () => {
    it('classifies an uploaded .m4a as audio — the exact case the owner reported', () => {
        // The recording that read "提取音频" / a video word before d677f8aa.
        expect(isAudioOnlySource({source_filename: 'lecture.m4a'})).toBe(true);
        expect(isAudioOnlySource({result: {filename: 'lecture.m4a'}})).toBe(true);
    });

    it('classifies a video upload as video', () => {
        expect(isAudioOnlySource({source_filename: 'screen-recording.mp4'})).toBe(false);
        expect(isAudioOnlySource({result: {filename: 'talk.mov'}})).toBe(false);
    });

    it('lets an explicit source type decide before the filename', () => {
        // A video whose name has no extension must not fall through to "audio".
        expect(isAudioOnlySource({source_type: 'video', source_filename: 'clip'})).toBe(false);
        expect(isAudioOnlySource({source_type: 'queue_upload', source_filename: 'clip'})).toBe(false);
        expect(isAudioOnlySource({source_type: 'audio', source_filename: 'clip'})).toBe(true);
        expect(isAudioOnlySource({source_type: 'audio_file'})).toBe(true);
    });

    it('recognises the common audio and video containers by extension', () => {
        for (const name of ['a.mp3', 'a.wav', 'a.flac', 'a.aac', 'a.ogg', 'a.wma', 'a.opus']) {
            expect(isAudioOnlySource({source_filename: name})).toBe(true);
        }
        for (const name of ['v.mp4', 'v.mov', 'v.avi', 'v.mkv', 'v.webm', 'v.m4v', 'v.flv', 'v.wmv', 'v.mpeg']) {
            expect(isAudioOnlySource({source_filename: name})).toBe(false);
        }
    });

    it('does not claim audio-only when it cannot tell — video is the safe default', () => {
        // No type and no recognisable extension: the labels stay on their video
        // wording rather than asserting "audio" about something unknown.
        expect(isAudioOnlySource({})).toBe(false);
        expect(isAudioOnlySource({source_filename: 'notes.txt'})).toBe(false);
        expect(isAudioOnlySource(null)).toBe(false);
    });
});
