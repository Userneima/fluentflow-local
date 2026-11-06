// First-use hints on the editor page (decision 6.59): a small bubble beside a
// control that is easy to miss, saying what it does. Each is shown until the
// person clicks 知道了, then never again; Settings can bring them all back.
//
// One hint per visit to the page: after one is dismissed, the next waits for
// the next time the editor is opened, so a first visit is not a tour of five
// bubbles in a row. The order below is the order they come in.

export const FEATURE_HINTS = [
    {
        id: 'note-follow',
        zh: '视频播到哪，笔记就高亮哪一节。自己滚动笔记会暂停跟随；点标题下的时间，视频跳到那里。',
        en: 'The note highlights the section the video is on. Scrolling the note pauses this; click the time under a heading to jump the video there.',
    },
    {
        id: 'transcript-follow',
        zh: '自己滚动转录稿会停下跟随。点这里，或点任意一句的时间，就接着跟。',
        en: 'Scrolling the transcript stops it following. Click here, or any sentence\'s time, to follow again.',
    },
    {
        id: 'transcript-edit',
        zh: '转录稿可以直接点进去改字，自动保存；改过的句子记在「修改记录」里。',
        en: 'Click into the transcript to fix words; it saves by itself, and changed sentences are listed under Edit records.',
    },
    {
        id: 'review-note',
        zh: '想要一篇不看视频也能学的复习笔记，跟你的 agent 说「给这篇写复习笔记」，写好放在产出文件夹里。',
        en: 'For a review note you can study without the video, ask your agent to write one for this recording. It goes in the output folder.',
    },
    {
        id: 'note-versions',
        zh: '笔记每次被改都留了旧版本。改坏了，跟你的 agent 说「把笔记恢复到之前的版本」。',
        en: 'Every change to the note keeps the earlier version. If it goes wrong, ask your agent to restore an earlier version.',
    },
];

export const FEATURE_HINTS_SEEN = 'fluentflow_feature_hints_seen';

const storage = () => {
    try { return typeof window !== 'undefined' ? window.localStorage : null; } catch (_) { return null; }
};

export const readSeenHints = () => {
    try {
        const list = JSON.parse(storage()?.getItem(FEATURE_HINTS_SEEN) || '[]');
        return new Set(Array.isArray(list) ? list : []);
    } catch (_) {
        return new Set();
    }
};

export const markHintSeen = (id) => {
    const seen = readSeenHints();
    seen.add(id);
    try { storage()?.setItem(FEATURE_HINTS_SEEN, JSON.stringify([...seen])); } catch (_) { /* private mode: it shows again next time */ }
};

export const resetFeatureHints = () => {
    try { storage()?.removeItem(FEATURE_HINTS_SEEN); } catch (_) { /* nothing stored */ }
};

export const anyHintSeen = () => FEATURE_HINTS.some((hint) => readSeenHints().has(hint.id));

// The hint to show now: the one already on screen while its control is still
// there (a hint does not jump to another control because a more important one
// appeared), else the first in order whose control is on the page and that
// has not been seen. None once one has been dismissed on this visit.
export const pickHint = ({present, seen, current, dismissedThisVisit}) => {
    if (dismissedThisVisit) return null;
    if (current && present.has(current) && !seen.has(current)) return current;
    return FEATURE_HINTS.find((hint) => present.has(hint.id) && !seen.has(hint.id))?.id || null;
};
