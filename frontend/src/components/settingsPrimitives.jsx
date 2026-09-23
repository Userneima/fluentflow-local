import SvgIcon from './SvgIcon.jsx';

// Edition-neutral building blocks for the settings pages. Both editions build
// their own page out of these plus the rows in settingsRows.jsx; nothing here
// knows which edition is rendering it.
export const Section = ({id, title, description, children}) => (
    <section id={id} className="scroll-mt-7 rounded-[18px] border border-[#e4e0e0] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
        <div className="border-b border-[#ece8e8] px-5 py-4 dark:border-white/[0.1]">
            <h2 className="font-headline text-base font-extrabold">{title}</h2>
            {description && <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">{description}</p>}
        </div>
        {children}
    </section>
);

export const SettingCheckbox = ({id, checked, disabled, onChange}) => (
    <span className="flex justify-end pt-0.5">
        <input
            id={id}
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={onChange}
            className="peer sr-only"
        />
        <span
            aria-hidden="true"
            className="flex size-5 items-center justify-center rounded-[6px] border border-outline-variant bg-surface-container-lowest text-transparent transition peer-checked:border-primary peer-checked:bg-primary peer-checked:text-on-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary/30 peer-disabled:opacity-40 dark:border-white/30 dark:bg-white/[0.04] dark:peer-checked:border-primary dark:peer-checked:bg-primary"
        >
            <SvgIcon name="check" className="text-[15px]"/>
        </span>
    </span>
);

export const inputClass = 'w-full rounded-[14px] border border-[#dedada] bg-[#fbfbfb] px-4 py-3 text-sm font-semibold text-[#111111] outline-none transition placeholder:text-[#aaa] focus:border-[#111111] focus:bg-white dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:placeholder:text-white/30 dark:focus:border-white/40';
export const fieldLabelClass = 'text-[11px] font-extrabold uppercase tracking-wider text-[#676970] dark:text-white/50';
export const saveButtonClass = 'shrink-0 whitespace-nowrap rounded-[14px] bg-[#111111] px-4 py-3 text-sm font-extrabold text-white transition hover:bg-[#2a2a2a] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-[#111111] dark:hover:bg-white/[0.88]';
export const cellBase = 'rounded-[14px] border border-[#ece8e8] bg-[#fbfbfb] px-4 py-3.5 dark:border-white/[0.1] dark:bg-white/[0.02]';

const radioClass = (active) => [
    'mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border-2 transition',
    active ? 'border-primary' : 'border-[#c9c9c9] dark:border-white/30',
].join(' ');

const routeButtonClass = (active, disabled) => [
    'flex min-h-[74px] flex-1 items-start gap-3 rounded-[14px] border px-4 py-3 text-left transition',
    active
        ? 'border-primary bg-primary/10 text-[#111111] dark:text-white'
        : 'border-[#dedada] bg-[#f8f7f7] text-[#555] hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.1]',
    disabled ? 'cursor-not-allowed opacity-45 hover:bg-[#f8f7f7] dark:hover:bg-white/[0.06]' : 'cursor-pointer active:translate-y-px',
].join(' ');

// One card in a radio group of routes (transcription route, default source).
export const RouteCard = ({label, description, active, disabled = false, onClick}) => (
    <button
        type="button"
        disabled={disabled}
        className={routeButtonClass(active, disabled)}
        onClick={() => { if (!disabled) onClick(); }}
    >
        <span className={radioClass(active)} aria-hidden="true">
            {active && <span className="size-2.5 rounded-full bg-primary"/>}
        </span>
        <span>
            <span className="block text-sm font-bold">{label}</span>
            <span className="mt-1 block text-xs leading-relaxed text-on-surface-variant">{description}</span>
        </span>
    </button>
);

// Result of the last secret save, shown under the field it belongs to.
export const SecretFeedback = ({feedback, keyName, lang}) => (
    feedback?.key === keyName ? (
        <p className={`text-[11px] font-semibold ${feedback.ok ? 'text-green-600' : 'text-red-600'}`}>
            {feedback.ok
                ? (lang === 'zh' ? '已保存' : 'Saved')
                : `${lang === 'zh' ? '保存失败' : 'Save failed'}: ${feedback.message || ''}`}
        </p>
    ) : null
);
