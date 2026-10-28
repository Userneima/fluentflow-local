// @vitest-environment jsdom

// The time each step took, on a task's page, written from what the person
// reads there: the whole run first, then each step that ran with its time;
// the cut broken into its parts; a step still running says so and counts up;
// a failed step says it failed; an agent's note says the agent wrote it; when
// steps ran side by side the page says why the rows add up to more.

import {afterEach, describe, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';

import StepTimingsCard from './StepTimingsCard.jsx';
import {fmtDuration} from '../lib/format.js';

afterEach(cleanup);

const row = (fields) => ({status: 'done', by_agent: false, running: false, parts: [], ...fields});

describe('StepTimingsCard', () => {
    it('shows nothing before the timings arrive or when no step ran', () => {
        const {container, rerender} = render(<StepTimingsCard lang="zh"/>);
        expect(container.textContent).toBe('');
        rerender(<StepTimingsCard lang="zh" timings={{rows: [], total_seconds: null}}/>);
        expect(container.textContent).toBe('');
    });

    it('leads with the whole run and gives each step its time', () => {
        render(<StepTimingsCard lang="zh" timings={{
            total_seconds: 446, running: false, overlapping: true,
            rows: [
                row({step: 'queue', label: '排队', seconds: 11}),
                row({step: 'fetch', label: '取素材', seconds: 65}),
                row({step: 'cut', label: '剪辑', seconds: 68, parts: [
                    {label: '判断剪哪里', seconds: 7}, {label: '生成剪后视频', seconds: 50}, {label: '整理处理报告', seconds: 11},
                ]}),
                row({step: 'note', label: '写笔记', seconds: 201, by_agent: true}),
                row({step: 'correct', label: '修正转录稿', seconds: 0}),
            ],
        }}/>);
        expect(screen.getByTestId('timings-total').textContent).toBe(`全程 ${fmtDuration(446)}`);
        const rows = screen.getAllByTestId('timing-step');
        expect(rows.map((el) => el.textContent)).toEqual([
            `排队${fmtDuration(11)}`,
            `取素材${fmtDuration(65)}`,
            expect.stringContaining(`剪辑${fmtDuration(68)}`),
            `写笔记你的 agent${fmtDuration(201)}`,
            '修正转录稿不到一秒',
        ]);
        expect(screen.getAllByTestId('timing-part').map((el) => el.textContent)).toEqual([
            `判断剪哪里${fmtDuration(7)}`, `生成剪后视频${fmtDuration(50)}`, `整理处理报告${fmtDuration(11)}`,
        ]);
        expect(screen.getByText('有的环节同时进行，各环节加起来比全程长。')).toBeTruthy();
    });

    it('says when a step is still running or failed', () => {
        render(<StepTimingsCard lang="zh" timings={{
            total_seconds: 300, running: true, overlapping: false,
            rows: [
                row({step: 'fetch', label: '取素材', status: 'failed', seconds: 265}),
                row({step: 'transcribe', label: '转录', status: 'running', running: true, seconds: 35}),
            ],
        }}/>);
        expect(screen.getByTestId('timings-total').textContent).toBe(`已用 ${fmtDuration(300)}`);
        const rows = screen.getAllByTestId('timing-step').map((el) => el.textContent);
        expect(rows[0]).toBe(`取素材失败${fmtDuration(265)}`);
        expect(rows[1]).toBe(`转录进行中已用 ${fmtDuration(35)}`);
        expect(screen.queryByText(/同时进行/)).toBeNull();
    });
});
