// @vitest-environment jsdom

// The model usage on a task's page, written from what the person reads there:
// the whole task's money first, then each step with its model, calls, tokens
// and money; the subscription is never shown as money spent, and a model
// without a price shows tokens without an amount.

import {afterEach, describe, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';

import ModelUsageCard from './ModelUsageCard.jsx';
import {fmtCosts, fmtMoney, fmtTokens} from '../lib/format.js';

afterEach(cleanup);

const empty = {calls: 0, failed_calls: 0, input_tokens: 0, cached_input_tokens: 0, output_tokens: 0,
    cost: {}, unpriced_calls: 0, subscription_calls: 0, reported_cost: {}, models: []};
const usage = (fields) => ({...empty, ...fields});

describe('ModelUsageCard', () => {
    it('shows nothing for a task that called no model', () => {
        const {container} = render(<ModelUsageCard usage={{total: empty, steps: []}} lang="zh"/>);
        expect(container.textContent).toBe('');
    });

    it('leads with the total and gives each step its model, calls, tokens and money', () => {
        render(<ModelUsageCard lang="zh" usage={{
            total: usage({calls: 4, input_tokens: 120000, cached_input_tokens: 98000, output_tokens: 8000,
                cost: {CNY: 0.0123}, subscription_calls: 1, reported_cost_usd: 0.42, models: ['deepseek-flash', 'claude-opus-5-5']}),
            steps: [
                {...usage({calls: 3, input_tokens: 9000, output_tokens: 3000, cost: {CNY: 0.0123}, models: ['deepseek-flash']}),
                    step: 'correct', label: '修正转录稿', items: [{...empty, item: '词表纠错', calls: 3}]},
                {...usage({calls: 1, input_tokens: 111000, output_tokens: 5000, subscription_calls: 1,
                    reported_cost_usd: 0.42, models: ['claude-opus-5-5']}), step: 'note', label: '写笔记', items: []},
            ],
        }}/>);
        expect(screen.getByTestId('usage-total-money').textContent).toBe('约 ¥0.012，1 次走订阅，不按次计费');
        const rows = screen.getAllByTestId('usage-step');
        expect(rows[0].textContent).toContain('修正转录稿');
        expect(rows[0].textContent).toContain('词表纠错 3 次');
        expect(rows[0].textContent).toContain('deepseek-flash · 3 次调用');
        expect(rows[0].textContent).toContain('约 ¥0.012');
        expect(rows[1].textContent).toContain('订阅，不按次计费');
        expect(rows[1].textContent).not.toContain('$0.42');
        expect(screen.getByText(/按 API 价折算约 \$0\.42，不是实际花费/)).toBeTruthy();
    });

    it('a model without a price shows tokens and no amount', () => {
        render(<ModelUsageCard lang="zh" usage={{
            total: usage({calls: 1, input_tokens: 500, output_tokens: 100, unpriced_calls: 1, models: ['kimi-k2.6']}),
            steps: [{...usage({calls: 1, input_tokens: 500, output_tokens: 100, unpriced_calls: 1, models: ['kimi-k2.6']}),
                step: 'cut', label: '剪辑', items: []}],
        }}/>);
        const row = screen.getByTestId('usage-step');
        expect(row.textContent).toContain('输入 500 token · 输出 100 token');
        expect(row.textContent).toContain('没有这个模型的价格');
        expect(row.textContent).not.toMatch(/[$¥]/);
    });

    it('a step whose calls failed says why instead of showing no tokens', () => {
        render(<ModelUsageCard lang="zh" usage={{
            total: usage({calls: 2, failed_calls: 2, failure: '账户余额不足', models: ['deepseek-flash']}),
            steps: [{...usage({calls: 2, failed_calls: 2, failure: '账户余额不足', models: ['deepseek-flash']}),
                step: 'cut', label: '剪辑', items: []}],
        }}/>);
        const row = screen.getByTestId('usage-step');
        expect(row.textContent).toContain('都没成功：账户余额不足');
        expect(row.textContent).not.toContain('0 token');
    });

    it('names the calls that were not put in a step', () => {
        render(<ModelUsageCard lang="zh" usage={{
            total: usage({calls: 1, input_tokens: 10, output_tokens: 1}),
            steps: [{...usage({calls: 1, input_tokens: 10, output_tokens: 1}), step: 'unattributed', label: '没归到步骤', items: []}],
        }}/>);
        expect(screen.getByTestId('usage-step').textContent).toContain('没归到步骤');
    });
});

describe('token and money wording', () => {
    it('writes tokens as whole numbers, then in 万', () => {
        expect(fmtTokens(8000)).toBe('8000 token');
        expect(fmtTokens(123456)).toBe('12.3 万 token');
        expect(fmtTokens(123456, 'en')).toBe('123.5K tokens');
    });
    it('keeps a fraction of a cent readable, in the currency billed', () => {
        expect(fmtMoney(1.234, 'USD')).toBe('$1.23');
        expect(fmtMoney(0.42, 'USD')).toBe('$0.42');
        expect(fmtMoney(0.0123, 'CNY')).toBe('¥0.012');
        expect(fmtMoney(0.000432, 'CNY')).toBe('¥0.00043');
        expect(fmtMoney(0.00000001, 'USD')).toBe('< $0.0001');
    });
    it('keeps two currencies apart, yuan first', () => {
        expect(fmtCosts({USD: 0.4, CNY: 0.021})).toBe('¥0.021 + $0.40');
        expect(fmtCosts({})).toBe('');
    });
});
