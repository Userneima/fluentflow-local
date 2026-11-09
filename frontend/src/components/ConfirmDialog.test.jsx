// @vitest-environment jsdom

// The product's own confirmation dialog, which replaced window.confirm
// (user report, 2026-11-09). What the person must be able to count on:
// it names the action on its button, it starts on the safe answer, and every
// way out other than that button (Escape, a click on the dimmed page, the
// cancel button) means no.

import {afterEach, describe, expect, it} from 'vitest';
import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {useState} from 'react';
import {I18nProvider, msgs} from '../app/shared.jsx';
import {useConfirm} from './ConfirmDialog.jsx';

const Asker = ({options}) => {
    const [confirm, dialog] = useConfirm();
    const [answer, setAnswer] = useState('none');
    return (
        <I18nProvider>
            <button type="button" onClick={async () => setAnswer(String(await confirm(options)))}>ask</button>
            <output data-testid="answer">{answer}</output>
            {dialog}
        </I18nProvider>
    );
};

const DELETE = {title: '删除这条处理记录？', message: '第一行\n第二行', confirmLabel: '删除记录'};

const open = (options = DELETE) => {
    render(<I18nProvider><Asker options={options}/></I18nProvider>);
    fireEvent.click(screen.getByRole('button', {name: 'ask'}));
    return screen.getByRole('alertdialog');
};

const answer = () => screen.getByTestId('answer').textContent;

afterEach(() => {
    cleanup();
    localStorage.clear();
});

describe('useConfirm', () => {
    it('shows the title, the explanation and a button named for the action', () => {
        const dialog = open();
        expect(dialog.textContent).toContain('删除这条处理记录？');
        expect(dialog.textContent).toContain('第一行');
        expect(screen.getByRole('button', {name: '删除记录'})).toBeTruthy();
        expect(screen.queryByRole('button', {name: '确定'})).toBeNull();
    });

    it('starts with focus on cancel, so a habitual Enter deletes nothing', () => {
        open();
        expect(document.activeElement).toBe(screen.getByRole('button', {name: '取消'}));
    });

    it('answers yes only from the confirm button, and closes', async () => {
        open();
        await act(async () => { fireEvent.click(screen.getByRole('button', {name: '删除记录'})); });
        expect(answer()).toBe('true');
        expect(screen.queryByRole('alertdialog')).toBeNull();
    });

    it('answers no from the cancel button', async () => {
        open();
        await act(async () => { fireEvent.click(screen.getByRole('button', {name: '取消'})); });
        expect(answer()).toBe('false');
        expect(screen.queryByRole('alertdialog')).toBeNull();
    });

    it('answers no from Escape', async () => {
        open();
        await act(async () => { fireEvent.keyDown(document, {key: 'Escape'}); });
        expect(answer()).toBe('false');
        expect(screen.queryByRole('alertdialog')).toBeNull();
    });

    it('answers no from a click on the dimmed page around it', async () => {
        const dialog = open();
        await act(async () => { fireEvent.mouseDown(dialog.parentElement); });
        expect(answer()).toBe('false');
        // A press inside the dialog (selecting its text) is not a no.
        fireEvent.click(screen.getByRole('button', {name: 'ask'}));
        fireEvent.mouseDown(screen.getByText('删除这条处理记录？'));
        expect(screen.getByRole('alertdialog')).toBeTruthy();
    });

    it('takes a custom cancel label, for a dialog whose action is itself a cancel', () => {
        open({title: '取消这个任务？', confirmLabel: '取消任务', cancelLabel: '继续处理'});
        expect(document.activeElement).toBe(screen.getByRole('button', {name: '继续处理'}));
    });

    it('colours the confirm button red for a dangerous action, and not for a go-ahead', () => {
        open();
        expect(screen.getByTestId('confirm-dialog-confirm').className).toContain('bg-[#dc2626]');
        cleanup();
        open({title: '处理这 3 个文件？', confirmLabel: '开始处理', tone: 'primary'});
        expect(screen.getByTestId('confirm-dialog-confirm').className).not.toContain('bg-[#dc2626]');
    });
});

describe('the delete-record wording', () => {
    // What a delete does and does not touch was read from the backend
    // (storage_cleanup.cleanup_task_all_files and remove_tree): only the app's
    // own storage folders are emptied; the original recording, the output
    // folder beside it or under Videos, and Lark documents are outside them.
    it('says what goes and what stays, in both languages', () => {
        const zh = msgs.zh['confirm.deleteRecordDesc'];
        expect(zh).toMatch(/转录稿/);
        expect(zh).toMatch(/原片/);
        expect(zh).toMatch(/打开文件夹/);
        expect(zh).toMatch(/飞书/);
        const en = msgs.en['confirm.deleteRecordDesc'];
        expect(en).toMatch(/original recording/);
        expect(en).toMatch(/Lark/);
    });

    it('every confirm text exists in both languages, and none uses a dash', () => {
        const keys = (lang) => Object.keys(msgs[lang]).filter((key) => key.startsWith('confirm.')).sort();
        expect(keys('zh')).toEqual(keys('en'));
        for (const lang of ['zh', 'en']) {
            for (const key of keys(lang)) expect(msgs[lang][key]).not.toMatch(/[—–]/);
        }
    });
});
