// @vitest-environment jsdom

// The local settings page, rendered. The rest of this split's guards search
// source text, which is exactly what missed the last edition mix-up: a page can
// contain all the right strings and still render the wrong controls (or throw).
// This one mounts the real page and looks at what a person would see.

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';

const runtimeConfig = {
    publicMode: false,
    allowedSttProviders: ['local'],
    defaultSttProvider: 'local',
    showMaintainerSettings: true,
    writesItsOwnNote: true,
    limits: {},
    guestTrial: {enabled: false},
};

let stored = {};

vi.mock('../app/AppContext.jsx', () => ({
    useApp: () => ({
        clearHistory: () => {},
        history: [],
        larkExports: [],
        runtimeConfig,
    }),
}));

vi.mock('../app/shared.jsx', async () => {
    const actual = await vi.importActual('../app/shared.jsx');
    return {
        ...actual,
        useI18n: () => ({t: (key) => key, lang: 'zh'}),
        useSettings: () => ({
            loadSettings: () => stored,
            saveSettings: (next) => { stored = next; },
        }),
        useApi: () => ({
            getCredentialsStatus: async () => ({}),
            saveCredentials: async () => ({}),
            getSpeakerDiarizationStatus: async () => ({available: false}),
            checkVideoCookies: async () => ({ok: true}),
        }),
    };
});

const {default: Settings} = await import('./settings.jsx');

describe('local settings page', () => {
    beforeEach(() => {
        stored = {};
        runtimeConfig.writesItsOwnNote = true;
    });

    // No globals in this project's vitest config, so auto-cleanup is off.
    afterEach(cleanup);

    it('renders the sections this edition has', () => {
        render(<Settings/>);
        expect(screen.getByText('转录')).toBeTruthy();
        expect(screen.getByText('开始处理')).toBeTruthy();
        expect(screen.getByText('导出')).toBeTruthy();
        expect(screen.getByText('数据')).toBeTruthy();
        expect(screen.getByText('高级 · 其他凭证')).toBeTruthy();
    });

    it('puts the note key first, where a first-time user looks', () => {
        render(<Settings/>);
        const headings = screen.getAllByRole('heading', {level: 2}).map((node) => node.textContent);
        expect(headings[0]).toBe('笔记');
        expect(screen.getByText('set.deepseekKey')).toBeTruthy();
    });

    it('offers no transcription-route choice, because there is only one route', () => {
        render(<Settings/>);
        expect(screen.queryByText('转录路线')).toBeNull();
        expect(screen.getByText('把音视频变成文字的方式。转录在本机完成。')).toBeTruthy();
    });

    it('shows the device-side controls without asking whether local transcription is allowed', () => {
        render(<Settings/>);
        expect(screen.getByText('set.sttSpeed')).toBeTruthy();
        expect(screen.getByText('视频链接下载登录态')).toBeTruthy();
        expect(screen.getByText('PYANNOTE AUTH TOKEN')).toBeTruthy();
    });

    it('drops the note settings only while an upload writes the note itself', () => {
        render(<Settings/>);
        expect(screen.queryByText('给笔记自动配图')).toBeNull();
        cleanup();

        runtimeConfig.writesItsOwnNote = false;
        render(<Settings/>);
        expect(screen.getByText('给笔记自动配图')).toBeTruthy();
    });

    it('carries no hosted account surface', () => {
        render(<Settings/>);
        expect(screen.queryByText('设备与云端')).toBeNull();
        expect(screen.queryByText('账号')).toBeNull();
    });
});
