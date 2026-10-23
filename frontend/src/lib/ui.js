// The only place button and card styles are written (docs/06-界面规矩.md).
// Every <button> in the app takes its className from here, plus layout classes
// (margin, width, flex placement) at most; ui-rules.test.js holds that.
//
// Actions come in four tones and three sizes:
//   primary    the one main action of an area: start, write the note, confirm
//   secondary  everything else that does something
//   quiet      low-key actions in a row of text: adjust, expand, close
//   danger     only where the action itself is the danger (cancel a running
//              task); delete lives in the "⋯" menu as a danger menu item
// Controls that switch or select (segments, filter chips, toggles, nav,
// menu items, toolbar) and whole rows that open something have their own
// helpers below; they are not actions and carry no tone.

const focusRing = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30';
const disabledState = 'disabled:cursor-not-allowed disabled:opacity-40';

const BUTTON_SIZES = {
    sm: 'h-7 px-2.5 text-[12px]',
    md: 'h-8 px-3 text-[13px]',
    lg: 'h-10 px-4 text-[14px]',
};

const ICON_SIZES = {
    sm: 'size-7',
    md: 'size-8',
    lg: 'size-10',
};

const BUTTON_TONES = {
    primary: 'bg-[#111111] text-white hover:bg-[#2a2a2a] dark:bg-white dark:text-[#111111] dark:hover:bg-white/85',
    secondary: 'border border-[#dedada] bg-[#ffffff] text-[#111111] hover:bg-[#efeeee] dark:border-white/[0.14] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.12]',
    quiet: 'text-[#57585d] hover:bg-[#efeeee] hover:text-[#111111] dark:text-white/70 dark:hover:bg-white/[0.08] dark:hover:text-white',
    danger: 'border border-red-200 bg-[#ffffff] text-[#b91c1c] hover:bg-[#fef2f2] dark:border-red-400/40 dark:bg-transparent dark:text-red-300 dark:hover:bg-red-500/15',
};

export const BUTTON_TONE_NAMES = Object.keys(BUTTON_TONES);
export const BUTTON_SIZE_NAMES = Object.keys(BUTTON_SIZES);

const pick = (table, key, fallback) => table[key] || table[fallback];

export const button = (tone = 'secondary', size = 'md') => [
    'inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-[10px] font-bold transition',
    pick(BUTTON_SIZES, size, 'md'),
    pick(BUTTON_TONES, tone, 'secondary'),
    focusRing,
    disabledState,
].join(' ');

// A square button holding only an icon. Give it an aria-label and a title.
export const iconButton = (tone = 'secondary', size = 'sm') => [
    'inline-flex shrink-0 items-center justify-center rounded-[10px] transition',
    pick(ICON_SIZES, size, 'sm'),
    tone === 'secondary'
        ? 'border border-[#dedada] bg-[#ffffff] text-[#57585d] hover:bg-[#efeeee] hover:text-[#111111] dark:border-white/[0.14] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.12] dark:hover:text-white'
        : pick(BUTTON_TONES, tone, 'quiet'),
    focusRing,
    disabledState,
].join(' ');

// A link-looking action inside a sentence ("处理过程", "去处理"). It keeps the
// colour of the sentence around it unless `tone` asks for the link blue or
// the muted grey of a secondary choice (取消, 撤回). A link standing on its own
// line passes a size; inside a sentence it takes the sentence's.
export const linkButton = (tone = 'inherit', size = '') => [
    'inline font-bold underline underline-offset-2 transition hover:no-underline disabled:cursor-not-allowed disabled:opacity-50',
    tone === 'blue' ? 'text-primary' : tone === 'muted' ? 'text-[#85868c] dark:text-white/55' : '',
    size === 'sm' ? 'text-[12px]' : size === 'md' ? 'text-[13px]' : '',
].filter(Boolean).join(' ');

// One row of mutually exclusive choices (视频链接 / 本机文件 / 字幕文件,
// 文本校对 / 视频复查). The selected one is white on the grey track.
export const segmentGroup = 'inline-flex shrink-0 items-center gap-1 rounded-[12px] bg-[#efeeee] p-1 dark:bg-white/[0.08]';
export const segmentItem = (active, size = 'md') => [
    'inline-flex items-center justify-center whitespace-nowrap rounded-[8px] font-bold transition',
    size === 'sm' ? 'h-7 px-2.5 text-[12px]' : 'h-8 px-3.5 text-[13px]',
    active
        ? 'bg-[#ffffff] text-[#111111] shadow-[0_1px_2px_rgba(17,17,17,0.12)] dark:bg-white/[0.18] dark:text-white'
        : 'text-[#666] hover:text-[#111111] dark:text-white/60 dark:hover:text-white',
    focusRing,
    disabledState,
].join(' ');

// A status that can be clicked to deal with it (剪辑有 N 处需要你判断, 笔记没生成).
// Orange asks for a decision, red is a failure, busy is work in progress.
const STATUS_TONES = {
    warning: 'border-[#f5c86b] bg-[#fffaf0] text-[#8a5a00] hover:bg-[#fff3dc] dark:border-[#fdb022]/30 dark:bg-[#fdb022]/[0.10] dark:text-[#fdb022] dark:hover:bg-[#fdb022]/[0.16]',
    error: 'border-[#fda29b] bg-[#fef3f2] text-[#b42318] hover:bg-[#fee4e2] dark:border-[#f04438]/40 dark:bg-[#f04438]/[0.14] dark:text-[#f97066] dark:hover:bg-[#f04438]/[0.2]',
    busy: 'border-[#e4e0e0] bg-[#ffffff] text-[#666] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/65',
};
export const statusButton = (tone = 'warning') => [
    'inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-[10px] border px-2.5 text-[12px] font-bold transition disabled:cursor-default',
    STATUS_TONES[tone] || STATUS_TONES.warning,
    focusRing,
].join(' ');

// Filter chips and on/off toggles (全部 / 待你判断, 跟随播放). Selected is solid
// dark, the same weight as the selected nav item.
export const chip = (active) => [
    'inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-[10px] border px-2.5 text-[12px] font-bold transition',
    active
        ? 'border-[#111111] bg-[#111111] text-white dark:border-white dark:bg-white dark:text-[#111111]'
        : 'border-[#dedada] bg-[#ffffff] text-[#57585d] hover:bg-[#efeeee] hover:text-[#111111] dark:border-white/[0.14] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.12] dark:hover:text-white',
    focusRing,
    disabledState,
].join(' ');

// Sidebar entries. Selected: darker fill and bold text; hover only tints.
// Folded to icons, each entry is a centred square.
export const navItem = (active, collapsed = false) => [
    'flex items-center rounded-[10px] text-[13px] transition',
    collapsed ? 'mx-auto size-10 justify-center' : 'w-full gap-3 px-3 py-2.5',
    active
        ? 'bg-[#e4e1e1] font-extrabold text-[#111111] dark:bg-white/[0.14] dark:text-white'
        : 'font-semibold text-[#3a3b3f] hover:bg-[#f1f0f0] hover:text-[#111111] dark:text-white/75 dark:hover:bg-white/[0.06] dark:hover:text-white',
    focusRing,
].join(' ');

// An entry in a "⋯" or menu popover; `open` marks the entry whose submenu is
// showing.
export const menuItem = (tone = 'default', open = false) => [
    'flex w-full items-center gap-3 rounded-[8px] px-3 py-2.5 text-left text-[13px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-40',
    tone === 'danger'
        ? 'text-[#dc2626] hover:bg-[#fef2f2] dark:text-red-300 dark:hover:bg-red-500/15'
        : 'text-on-surface hover:bg-[#f4f3f3] dark:hover:bg-white/[0.08]',
    open ? 'bg-[#f4f3f3] dark:bg-white/[0.08]' : '',
].filter(Boolean).join(' ');

// The note editor's formatting toolbar. No display class: the toolbar decides
// which buttons show at which width.
export const toolbarButton = (active = false) => [
    'size-8 shrink-0 items-center justify-center rounded-[8px] transition',
    active
        ? 'bg-surface-container-highest text-on-surface'
        : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface',
    focusRing,
    disabledState,
].join(' ');

// One option in a group where exactly one is chosen (transcription route,
// which model writes the note): the chosen card has the blue edge.
export const optionCard = (active, disabled = false) => [
    'flex min-h-[74px] flex-1 items-start gap-3 rounded-[12px] border px-4 py-3 text-left transition',
    active
        ? 'border-primary bg-primary/10 text-[#111111] dark:text-white'
        : 'border-[#dedada] bg-[#f8f7f7] text-[#555] hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white/70 dark:hover:bg-white/[0.1]',
    disabled ? 'cursor-not-allowed opacity-45 hover:bg-[#f8f7f7] dark:hover:bg-white/[0.06]' : 'cursor-pointer',
    focusRing,
].join(' ');

// A record's small fact blocks (时长和大小, 笔记字数). The one that opens
// something is a white block with a visible border, so it reads as a button
// without hovering (decision 6.32); open, it turns solid.
export const factBlock = 'inline-flex items-center gap-1 whitespace-nowrap rounded-[8px] border border-transparent bg-[#f2f1f1] px-2 py-0.5 text-[12px] font-semibold text-[#57585d] dark:bg-white/[0.07] dark:text-white/60';
export const factToggle = (open) => [
    'inline-flex cursor-pointer items-center gap-1 whitespace-nowrap rounded-[8px] border px-2 py-0.5 text-[12px] font-semibold transition',
    open
        ? 'border-[#111111] bg-[#111111] text-white dark:border-white dark:bg-white dark:text-[#111111]'
        : 'border-[#b9b5b5] bg-[#ffffff] text-[#111111] shadow-[0_1px_0_rgba(17,17,17,0.06)] hover:bg-[#efeeee] dark:border-white/30 dark:bg-white/[0.04] dark:text-white dark:hover:bg-white/[0.12]',
    focusRing,
].join(' ');

// A time in a recording that jumps the player there (00:31). Blue while it is
// the sentence playing.
export const timestampButton = (active = false) => [
    'font-mono text-[12px] tabular-nums transition hover:underline',
    active ? 'font-bold text-primary' : 'text-[#8a8a8a] hover:text-primary dark:text-white/40',
    focusRing,
].join(' ');

// The large dashed area that a file is dropped on or clicked to choose one.
export const dropZone = 'flex w-full flex-col items-center justify-center rounded-[16px] border border-dashed border-[#cfcaca] bg-[#fbfbfb] px-6 py-8 text-center transition hover:border-[#111111] hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.16] dark:bg-white/[0.04] dark:hover:border-white/40 dark:hover:bg-white/[0.08] ' + focusRing;

// A small grey tile that opens a task (the start page's recent list).
export const tileButton = `min-w-0 rounded-[12px] bg-[#f4f3f3] p-4 text-left transition hover:bg-[#efeeee] dark:bg-white/[0.08] dark:hover:bg-white/[0.12] ${focusRing}`;

// A wrapper that turns any element into a button without changing its look.
export const bareButton = 'block appearance-none border-0 bg-transparent p-0 text-left';

// A whole row or card that opens something (a recent task, a settings step).
// It carries no colour of its own: the row's container gives the look.
export const rowButton = `w-full text-left transition ${focusRing}`;

// Text that can be clicked inline without looking like a link (a record's
// title, a timestamp). Colour comes from the surrounding text.
export const textButton = `text-left transition hover:underline ${focusRing} ${disabledState}`;

// Cards and blocks. Cards on a page have a border and no shadow; only layers
// floating above the page (dialogs, menus, toasts) cast one.
export const surfaceCard = 'rounded-[16px] border border-[#e4e0e0] bg-[#ffffff] dark:border-white/[0.12] dark:bg-white/[0.04]';
export const insetBlock = 'rounded-[12px] border border-[#ece8e8] bg-[#fbfbfb] dark:border-white/[0.10] dark:bg-white/[0.03]';
export const floatingLayer = 'rounded-[16px] border border-[#e4e0e0] bg-[#ffffff] shadow-[0_24px_60px_-30px_rgba(17,17,17,0.45)] dark:border-white/[0.12] dark:bg-[#1e2024]';
