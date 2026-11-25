// 学完的 (2026-11-25): the records he clicked 学完了 on, one group per course
// (the folder the recording came from), the course studied last first and in
// each group the last studied first.

import {describe, expect, it} from 'vitest';
import {RECORD_FILTERS, groupStudied} from './agent-tasks-list.js';

const job = (id, course, at) => ({task_id: id, metadata: {studied: {at, course, verdict: null}}});

describe('the 学完的 list', () => {
    it('lists only studied records', () => {
        const studied = RECORD_FILTERS.find((item) => item.id === 'studied');
        expect(studied.zh).toBe('学完的');
        expect(studied.match(job('a', '实战 1', '2026-11-25T01:00:00Z'))).toBe(true);
        expect(studied.match({task_id: 'b', metadata: {}})).toBe(false);
        expect(studied.match({task_id: 'c', metadata: {studied: null}})).toBe(false);
    });

    it('groups by course, latest course first, latest studied first within it', () => {
        const {ordered, courseStarts} = groupStudied([
            job('old-1', '实战 1', '2026-11-20T01:00:00Z'),
            job('new-2', '实战 2', '2026-11-24T01:00:00Z'),
            job('new-1', '实战 1', '2026-11-25T01:00:00Z'),
            job('old-2', '实战 2', '2026-11-21T01:00:00Z'),
        ]);
        expect(ordered.map((item) => item.task_id)).toEqual(['new-1', 'old-1', 'new-2', 'old-2']);
        expect([...courseStarts.entries()]).toEqual([['new-1', '实战 1'], ['new-2', '实战 2']]);
    });
});
