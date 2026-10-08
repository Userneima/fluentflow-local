// @vitest-environment jsdom

// The terms and privacy pages. What the person should be told, in both
// languages: a note Claude writes from the frames sends the transcript too;
// the app is for this machine only unless LAN mode is turned on, and LAN mode
// lets anyone on that network read tasks without a token; Feishu export uses
// their own identity through lark-cli when signed in, otherwise their own app.

import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';

let lang = 'zh';
vi.mock('../app/shared.jsx', () => ({useI18n: () => ({t: (key) => key, lang})}));

const {default: About} = await import('./about.jsx');

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
        expect(service).toMatch(/局域网模式.*不需要令牌就能看到全部任务.*只在.*可信的网络里开/);
        expect(service).toMatch(/会把转录稿和从视频里截的画面一起发给 Claude/);
        expect(service).toMatch(/lark-cli 已经登录，默认用你本人的飞书身份；没有登录时用你自己建的飞书应用/);
        const privacy = pageText('privacy', 'zh');
        expect(privacy).toMatch(/发转录稿和截取的画面给 Claude/);
        expect(privacy).toMatch(/已登录 lark-cli 时以你本人身份/);
    });

    it('says the same in English', () => {
        const service = pageText('service', 'en');
        expect(service).toMatch(/by default only accepts connections from that computer/);
        expect(service).toMatch(/LAN mode.*without a token.*network you trust/);
        expect(service).toMatch(/send both the transcript and the frames/);
        expect(service).toMatch(/your own Feishu identity when lark-cli is signed in.*your own Feishu app otherwise/);
        const privacy = pageText('privacy', 'en');
        expect(privacy).toMatch(/send the transcript and the extracted frames to Claude/);
        expect(privacy).toMatch(/as yourself when lark-cli is signed in/);
    });
});
