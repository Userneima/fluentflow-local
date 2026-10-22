// @vitest-environment jsdom

// The terms and privacy pages. What the person should be told, in both
// languages: a note Claude writes from the frames sends the transcript too;
// the app is for this machine only unless LAN mode is turned on, and LAN mode
// lets other devices in only after they enter the access token; Feishu export uses
// their own identity through lark-cli when signed in, otherwise their own app.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

let lang = 'zh';
vi.mock('../app/shared.jsx', () => ({useI18n: () => ({t: (key) => key, lang})}));

const {default: About, parseChangelog, userFacingItem, changelogLastUpdated} = await import('./about.jsx');

const pageText = (page, language) => {
    lang = language;
    render(
        <MemoryRouter initialEntries={[`/about/${page}`]}>
            <Routes><Route path="/about/:page" element={<About/>}/></Routes>
        </MemoryRouter>,
    );
    const text = document.body.textContent;
    cleanup();
    return text;
};

describe('about pages', () => {
    afterEach(cleanup);

    it('says, in Chinese, what the terms and privacy pages must say', () => {
        const service = pageText('service', 'zh');
        expect(service).toMatch(/默认只接受这台电脑自己的访问/);
        expect(service).toMatch(/局域网模式.*要先在那台设备.*填入访问令牌.*只在.*可信的网络里开/);
        expect(service).toMatch(/会把转录稿和从视频里截的画面一起发给 Claude/);
        expect(service).toMatch(/lark-cli 已经登录，默认用你本人的飞书身份；没有登录时用你自己建的飞书应用/);
        const privacy = pageText('privacy', 'zh');
        expect(privacy).toMatch(/发转录稿和截取的画面给 Claude/);
        expect(privacy).toMatch(/已登录 lark-cli 时以你本人身份/);
    });

    it('says the same in English', () => {
        const service = pageText('service', 'en');
        expect(service).toMatch(/by default only accepts connections from that computer/);
        expect(service).toMatch(/LAN mode.*must first enter the access token.*network you trust/);
        expect(service).toMatch(/send both the transcript and the frames/);
        expect(service).toMatch(/your own Feishu identity when lark-cli is signed in.*your own Feishu app otherwise/);
        const privacy = pageText('privacy', 'en');
        expect(privacy).toMatch(/send the transcript and the extracted frames to Claude/);
        expect(privacy).toMatch(/as yourself when lark-cli is signed in/);
    });

    // Checklist 2026-10-22 (lane 3, item 10): the changelog page is for people
    // using the app. It does not point at the source file, does not show
    // environment variable names, and its "last updated" is never older than
    // its newest entry.
    it('keeps the developer source note off the changelog page', async () => {
        lang = 'zh';
        render(
            <MemoryRouter initialEntries={['/about/changelog']}>
                <Routes><Route path="/about/:page" element={<About/>}/></Routes>
            </MemoryRouter>,
        );
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(document.body.textContent).not.toMatch(/docs\/03-更新记录\.md/);
        expect(document.body.textContent).not.toMatch(/更新来源/);
    });

    it('drops sentences that name an environment variable, keeping the rest', () => {
        expect(userFacingItem('录像不再有时长上限，多长都处理。想限制的仍可在 `.env` 里设 `FLUENTFLOW_MAX_MEDIA_DURATION_S`。'))
            .toBe('录像不再有时长上限，多长都处理。');
        expect(userFacingItem('用 `FLUENTFLOW_PROCESSING_PRESET` 选档位。')).toBe('');
        expect(userFacingItem('通义千问挑截图的模型换成 `qwen3.7-plus`。')).toBe('通义千问挑截图的模型换成 qwen3.7-plus。');
        const releases = parseChangelog('# Log\n\n## 0.9.1 - 2026-10-16\n\n- 用 `FLUENTFLOW_X_Y` 设。\n- 修好了导出。\n');
        expect(releases[0].items).toEqual(['修好了导出。']);
    });

    it('never says the changelog was last updated before its newest entry', () => {
        const releases = [{title: 'Unreleased', date: ''}, {title: '0.9.1 - 2026-10-16', date: '2026-10-16'}];
        expect(changelogLastUpdated(releases, '2026-10-12T09:00:00Z')).toBe('2026-10-16');
        expect(changelogLastUpdated(releases, '2026-10-18T09:00:00Z')).toBe('2026-10-18T09:00:00Z');
    });
});
