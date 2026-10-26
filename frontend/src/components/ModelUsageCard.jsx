import {fmtCosts, fmtMoney, fmtTokens} from '../lib/format.js';
import {surfaceCard} from '../lib/ui.js';

// What this task's model calls used and cost, the total first, then one row
// per step (model_usage on the server). Money is worked out there from the
// providers' published prices; a model without a price shows its tokens only,
// and the Claude subscription is not billed per call.

const callsText = (n, zh) => (zh ? `${n} 次调用` : `${n} ${n === 1 ? 'call' : 'calls'}`);

const tokensText = (usage, zh) => {
    const input = fmtTokens(usage.input_tokens, zh ? 'zh' : 'en');
    const output = fmtTokens(usage.output_tokens, zh ? 'zh' : 'en');
    const cached = Number(usage.cached_input_tokens) || 0;
    const fromCache = cached > 0
        ? (zh ? `（${fmtTokens(cached, 'zh')}来自缓存）` : ` (${fmtTokens(cached, 'en')} from cache)`)
        : '';
    return zh ? `输入 ${input}${fromCache} · 输出 ${output}` : `in ${input}${fromCache} · out ${output}`;
};

// The money line for a total or a step: what keys cost, then what the
// subscription and unpriced calls add to the picture.
const moneyText = (usage, zh) => {
    const parts = [];
    const money = fmtCosts(usage.cost);
    if (money) parts.push(zh ? `约 ${money}` : `about ${money}`);
    if (usage.subscription_calls > 0) {
        const all = usage.subscription_calls === usage.calls;
        parts.push(zh
            ? (all ? '订阅，不按次计费' : `${usage.subscription_calls} 次走订阅，不按次计费`)
            : (all ? 'Subscription, not billed per call' : `${usage.subscription_calls} by subscription, not billed per call`));
    }
    if (usage.unpriced_calls > 0 && !parts.length) parts.push(zh ? '没有这个模型的价格' : 'No price for this model');
    return parts.join(zh ? '，' : '; ');
};

const notes = (total, zh) => {
    const lines = [];
    if (total.reported_cost_usd) {
        lines.push(zh
            ? `订阅的调用按 API 价折算约 ${fmtMoney(total.reported_cost_usd, 'USD')}，不是实际花费。`
            : `The subscription calls would cost about ${fmtMoney(total.reported_cost_usd, 'USD')} at API prices; that is not money spent.`);
    }
    if (total.unpriced_calls > 0 && fmtCosts(total.cost)) {
        lines.push(zh
            ? `另有 ${total.unpriced_calls} 次调用的模型没有价格，金额里没算。`
            : `${total.unpriced_calls} calls used a model without a price and are not in the amount.`);
    }
    if (total.failed_calls > 0) {
        lines.push(zh ? `${total.failed_calls} 次调用没成功。` : `${total.failed_calls} calls failed.`);
    }
    return lines;
};

const StepRow = ({step, zh}) => {
    const loose = step.step === 'unattributed';
    const items = (step.items || []).map((item) => (zh ? `${item.item} ${item.calls} 次` : `${item.item} ×${item.calls}`));
    const money = moneyText(step, zh);
    return (
        <li data-testid="usage-step" className="grid gap-x-6 gap-y-1 py-3 md:grid-cols-[minmax(10rem,1fr)_minmax(0,2fr)]">
            <div className="min-w-0">
                <p className={`text-[14px] font-extrabold ${loose ? 'text-[#8a5a00] dark:text-[#fdb022]' : 'text-[#111111] dark:text-white'}`}>
                    {loose ? (zh ? '没归到步骤' : 'Not in a step') : step.label}
                </p>
                {items.length > 0 && (
                    <p className="mt-0.5 text-[12px] font-semibold text-[#85868c] dark:text-white/[0.65]">{items.join(' · ')}</p>
                )}
            </div>
            <div className="min-w-0 text-[13px] leading-5 text-[#57585d] dark:text-white/[0.75]">
                <p className="font-semibold">
                    {[(step.models || []).join('、'), callsText(step.calls, zh)].filter(Boolean).join(' · ')}
                </p>
                <p>{tokensText(step, zh)}</p>
                {money && <p className="font-extrabold text-[#111111] dark:text-white">{money}</p>}
            </div>
        </li>
    );
};

const ModelUsageCard = ({usage, lang}) => {
    const total = usage?.total;
    if (!total || !total.calls) return null;
    const zh = lang === 'zh';
    const money = moneyText(total, zh);
    return (
        <section data-testid="model-usage" className={`${surfaceCard} p-5`}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <h2 className="font-headline text-[15px] font-extrabold text-[#111111] dark:text-white">
                    {zh ? '模型用量' : 'Model usage'}
                </h2>
                {money && <p data-testid="usage-total-money" className="text-[15px] font-extrabold text-[#111111] dark:text-white">{money}</p>}
            </div>
            <p className="mt-1 text-[13px] font-semibold text-[#676970] dark:text-white/[0.70]">
                {callsText(total.calls, zh)} · {tokensText(total, zh)}
            </p>
            {notes(total, zh).map((line) => (
                <p key={line} className="mt-1 text-[12px] font-semibold text-[#85868c] dark:text-white/[0.65]">{line}</p>
            ))}
            <ul className="mt-3 divide-y divide-[#ece8e8] border-t border-[#ece8e8] dark:divide-white/[0.08] dark:border-white/[0.08]">
                {(usage.steps || []).map((step) => <StepRow key={step.step} step={step} zh={zh}/>)}
            </ul>
        </section>
    );
};

export default ModelUsageCard;
