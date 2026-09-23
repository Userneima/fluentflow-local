import {Link, NavLink, useParams} from 'react-router-dom';
import {useEffect, useState} from 'react';
import {useI18n} from '../app/shared.jsx';
import SvgIcon from '../components/SvgIcon.jsx';

const UPDATED_AT = '2026-09-23';
const CHANGELOG_UPDATED_AT = globalThis.window?.FLUENTFLOW_CONFIG?.version?.changelogUpdatedAt
    || globalThis.window?.FLUENTFLOW_CONFIG?.version?.buildTime
    || `${UPDATED_AT}T00:00:00+08:00`;

const legalNav = [
    {key: 'service', zh: '服务条款', en: 'Terms of service', icon: 'description'},
    {key: 'privacy', zh: '隐私政策', en: 'Privacy policy', icon: 'shield'},
    {key: 'changelog', zh: '版本更新', en: 'Changelog', icon: 'history'},
];

const serviceSections = [
    {
        title: '它做什么',
        body: [
            'FluentFlow Local 把视频、音频、字幕文件和视频链接整理成转录文本、字幕和笔记，也能导出。',
            '它是学习和整理资料的工具，不提供法律、医疗、投资或其他专业建议。',
        ],
    },
    {
        title: '在哪里处理',
        body: [
            '程序装在你自己的电脑上，只接受这台电脑自己的访问。转录用本机的语音识别模型完成，音频不会因为转录离开这台电脑。',
            '写笔记、翻译、导出飞书和下载视频链接需要联网，见下一条。',
        ],
    },
    {
        title: '用到的外部服务',
        body: [
            '写笔记和翻译会把转录文本和标题发给你在设置里填的 AI 服务商（DeepSeek、OpenAI、通义千问等），费用记在你自己的账号上。图文笔记会把从视频里截的画面发给 Claude。',
            '导出飞书用的是你自己的飞书应用；下载视频链接会连接对应的视频平台；第一次用说话人区分时，会从 Hugging Face 下载模型。这些服务的价格、速度和内容政策由服务商决定，随时可能变。',
        ],
    },
    {
        title: '你的内容',
        body: [
            '你要确认自己有权转录、下载、分析或导出这些材料，包括平台视频链接、课程视频、会议录音和字幕文件。',
            '没有权利处理的违法、侵权或涉密内容，不要放进来。',
        ],
    },
    {
        title: '结果要人工核对',
        body: [
            '转录、翻译、字幕切分和 AI 笔记都可能有错字、遗漏、断句错误或理解偏差，重要材料请回听原音频核对。',
            '重新转录、重新生成笔记和删除任务会覆盖或删除当前结果，操作前会有确认提示。',
        ],
    },
    {
        title: '版本变化',
        body: [
            'FluentFlow Local 还在快速迭代，页面、功能和数据格式都可能调整，看得到的变化会记在「版本更新」页。',
        ],
    },
];

const privacySections = [
    {
        title: '存在哪里',
        body: [
            '任务记录、原始文件、转录、笔记和导出文件都存在这台电脑的应用数据目录里。FluentFlow Local 没有自己的服务器，也没有账号。',
        ],
    },
    {
        title: '什么时候会发出去',
        body: [
            '只在你用到下面这些功能时才发，而且只发这一项需要的内容：写笔记和翻译，发转录文本和标题给你选的 AI 服务商；图文笔记，发截取的画面给 Claude；导出飞书，发笔记内容到你的飞书；下载视频链接，访问对应的视频平台。',
        ],
    },
    {
        title: '使用记录',
        body: [
            '程序会在本机记下每个任务的处理步骤和出错信息，用来排查失败。这些记录不会发到任何地方。',
        ],
    },
    {
        title: '保留与删除',
        body: [
            '原始文件默认保留 7 天，处理结果默认保留 30 天，到期自动清理，你也可以随时在任务列表里删掉某条任务。',
            '浏览器里的最近记录只存在当前浏览器，清掉它不会删除电脑上的任务。',
        ],
    },
    {
        title: '密钥',
        body: [
            '你填的 AI 服务商密钥和飞书应用凭证存在本机配置里，只在调用对应服务时使用。',
            '设置页只显示有没有配置，不显示完整密钥，更换时要重新输入。',
        ],
    },
    {
        title: '你能控制什么',
        body: [
            '如果想让材料完全留在本机，只用转录就行，不开 AI 笔记、翻译、飞书导出和视频链接下载。',
        ],
    },
];

const enServiceSections = [
    {title: 'What it does', body: ['FluentFlow Local turns videos, audio, subtitle files, and video links into transcripts, subtitles, and notes, and can export them.', 'It is a tool for studying and organizing material. It does not give legal, medical, financial, or other professional advice.']},
    {title: 'Where it runs', body: ['The app runs on your own computer and only accepts connections from that computer. Transcription uses a speech recognition model on this machine, so your audio does not leave it to be transcribed.', 'Writing notes, translating, exporting to Feishu, and downloading video links need the internet. See the next section.']},
    {title: 'Outside services it uses', body: ['Notes and translations send the transcript and title to the AI provider you set up in Settings (DeepSeek, OpenAI, Qwen, and others), billed to your own account. Illustrated notes send frames taken from the video to Claude.', 'Feishu export uses your own Feishu app. Downloading a video link connects to that video platform. The first time you use speaker labels, the model is downloaded from Hugging Face. Pricing, speed, and content policies of these services are set by the providers and can change at any time.']},
    {title: 'Your content', body: ['Make sure you have the right to transcribe, download, analyze, or export the material, including platform video links, course videos, meeting recordings, and subtitle files.', 'Do not add unlawful, infringing, or confidential material you are not allowed to handle.']},
    {title: 'Check the results', body: ['Transcripts, translations, subtitle breaks, and AI notes can contain typos, gaps, bad line breaks, or misreadings. Listen back to the original for anything important.', 'Retranscribing, regenerating a note, and deleting a task overwrite or remove the current result. You will be asked to confirm first.']},
    {title: 'Changes', body: ['FluentFlow Local is changing quickly. Pages, features, and data formats may change, and visible changes are listed on the Changelog page.']},
];

const enPrivacySections = [
    {title: 'Where your data lives', body: ['Task records, source files, transcripts, notes, and exports are stored in the app data folder on this computer. FluentFlow Local has no server of its own and no accounts.']},
    {title: 'When anything leaves this computer', body: ['Only when you use one of these features, and only what that feature needs: notes and translations send the transcript and title to the AI provider you chose; illustrated notes send extracted frames to Claude; Feishu export sends the note to your Feishu; downloading a video link contacts that video platform.']},
    {title: 'Activity records', body: ['The app records each task\'s processing steps and errors on this computer to help diagnose failures. These records are not sent anywhere.']},
    {title: 'Retention and deletion', body: ['Source files are kept for 7 days and results for 30 days by default, then cleaned up automatically. You can delete any task from the task list at any time.', 'Recent items in the browser live only in that browser. Clearing them does not delete tasks on this computer.']},
    {title: 'Keys', body: ['The AI provider keys and Feishu app credentials you enter are stored in local configuration and used only when calling that service.', 'Settings only shows whether a key is set, never the full key. To change one, enter it again.']},
    {title: 'Your choices', body: ['To keep material entirely on this computer, use transcription only and leave AI notes, translation, Feishu export, and video link downloads off.']},
];

const isChangelogEntryTitle = (title) => (
    /^(Unreleased|v?\d+\.\d+\.\d+|\d{4}-\d{2}|\d{4}-\d{2}-\d{2}|\d{4}年)/.test(title)
);

const formatChangelogTitle = (title, zh) => {
    if (!zh) return title;
    return title.replace(/^Unreleased\b/, '待发布');
};

const formatDateTimeMinute = (value, zh) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || '').trim();
    const locale = zh ? 'zh-CN' : 'en-US';
    return new Intl.DateTimeFormat(locale, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    }).format(date).replace(/\//g, '-');
};

const changelogTitleTime = (title) => {
    const match = title.match(/(\d{4}-\d{2}-\d{2})(?:[ T｜]+(\d{2}:\d{2}))?/);
    if (!match || !match[2]) return null;
    return `${match[1]} ${match[2]}`;
};

const parseChangelog = (markdown) => (
    markdown
        .split(/\n(?=##\s+)/)
        .map((block) => {
            const lines = block.split('\n');
            const titleLine = lines.find((line) => line.startsWith('## '));
            if (!titleLine) return null;
            const title = titleLine.replace(/^##\s+/, '').trim();
            if (!isChangelogEntryTitle(title)) return null;
            const items = lines
                .filter((line) => line.startsWith('- '))
                .map((line) => line.replace(/^-\s+/, '').trim())
                .filter(Boolean)
                .slice(0, 4);
            if (items.length === 0) return null;
            return {title, updatedAt: changelogTitleTime(title), items};
        })
        .filter(Boolean)
        .slice(0, 6)
);

const SectionList = ({sections}) => (
    <div className="overflow-hidden rounded-[22px] border border-[#dedada] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
        {sections.map((section, index) => (
            <section key={section.title} className={`grid gap-3 px-5 py-5 md:grid-cols-[180px_minmax(0,1fr)] md:gap-8 ${index > 0 ? 'border-t border-[#ece8e8] dark:border-white/[0.10]' : ''}`}>
                <h2 className="text-sm font-extrabold text-[#111111] dark:text-white">{section.title}</h2>
                <div className="space-y-3">
                    {section.body.map((text) => (
                        <p key={text} className="max-w-[70ch] text-sm font-semibold leading-relaxed text-[#676970] dark:text-white/58">
                            {text}
                        </p>
                    ))}
                </div>
            </section>
        ))}
    </div>
);

const ChangelogPage = ({zh}) => {
    const [releases, setReleases] = useState([]);
    const [loadState, setLoadState] = useState('loading');

    useEffect(() => {
        let active = true;
        import('../../../docs/changelog.md?raw')
            .then((mod) => {
                if (!active) return;
                setReleases(parseChangelog(mod.default || ''));
                setLoadState('ready');
            })
            .catch(() => {
                if (!active) return;
                setReleases([]);
                setLoadState('error');
            });
        return () => { active = false; };
    }, []);

    return (
        <div className="overflow-hidden rounded-[22px] border border-[#dedada] bg-white dark:border-white/[0.12] dark:bg-white/[0.06]">
            <section className="grid gap-3 px-5 py-5 md:grid-cols-[180px_minmax(0,1fr)] md:gap-8">
                <h2 className="text-sm font-extrabold text-[#111111] dark:text-white">{zh ? '更新来源' : 'Source'}</h2>
                <div className="space-y-3">
                    <p className="max-w-[70ch] text-sm font-semibold leading-relaxed text-[#676970] dark:text-white/58">
                        {zh
                            ? '此页直接读取项目里的 docs/changelog.md。以后只维护一份更新日志，前端页面会随构建同步。'
                            : 'This page reads docs/changelog.md directly. The project keeps one changelog source and syncs this page at build time.'}
                    </p>
                    <div className="inline-flex h-10 items-center gap-2 rounded-[14px] border border-[#dedada] px-4 text-sm font-extrabold text-[#676970] dark:border-white/[0.12] dark:text-white/58">
                        <SvgIcon name="description" className="text-[18px]"/>
                        docs/changelog.md
                    </div>
                </div>
            </section>
            <section className="border-t border-[#ece8e8] px-5 py-5 dark:border-white/[0.10]">
                <h2 className="mb-4 text-sm font-extrabold text-[#111111] dark:text-white">{zh ? '最近记录' : 'Recent entries'}</h2>
                <div className="space-y-3">
                    {releases.length > 0 ? releases.map((release) => (
                        <div key={release.title} className="rounded-[14px] bg-[#f4f3f3] px-4 py-3 dark:bg-white/[0.08]">
                            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                                <p className="text-sm font-extrabold text-[#111111] dark:text-white">{formatChangelogTitle(release.title, zh)}</p>
                                <p className="text-xs font-semibold text-[#85868c] dark:text-white/42">
                                    {zh ? '更新于' : 'Updated'} {release.updatedAt || formatDateTimeMinute(CHANGELOG_UPDATED_AT, zh)}
                                </p>
                            </div>
                            {release.items.length > 0 && (
                                <ul className="mt-2 space-y-1.5">
                                    {release.items.map((item) => (
                                        <li key={item} className="text-sm font-semibold leading-relaxed text-[#676970] dark:text-white/58">
                                            {item}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )) : (
                        <div className="rounded-[14px] bg-[#f4f3f3] px-4 py-3 dark:bg-white/[0.08]">
                            <p className="text-sm font-extrabold text-[#111111] dark:text-white">
                                {loadState === 'error'
                                    ? (zh ? '暂时无法读取更新记录' : 'Unable to load changelog')
                                    : (zh ? '正在读取更新记录' : 'Loading changelog')}
                            </p>
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
};

const About = () => {
    const {lang} = useI18n();
    const {page} = useParams();
    const zh = lang === 'zh';
    const activePage = legalNav.some((item) => item.key === page) ? page : 'service';
    const activeMeta = legalNav.find((item) => item.key === activePage) || legalNav[0];
    const title = zh ? activeMeta.zh : activeMeta.en;
    const intro = {
        service: zh
            ? '这些条款说明 FluentFlow Local 做什么、在哪里处理你的材料，以及你和它各自负责的部分。'
            : 'These terms explain what FluentFlow Local does, where it handles your material, and what you and it are each responsible for.',
        privacy: zh
            ? '这里说明 FluentFlow Local 在你电脑上存了什么、什么时候会把什么发出去，以及怎么删除。'
            : 'This policy explains what FluentFlow Local stores on your computer, when anything is sent out, and how to delete it.',
        changelog: zh
            ? '这里展示最近的产品变化。它来自项目更新日志，不是单独维护的一份页面文案。'
            : 'This page shows recent product changes from the project changelog instead of a separately maintained copy.',
    }[activePage];
    const sections = activePage === 'service'
        ? (zh ? serviceSections : enServiceSections)
        : (zh ? privacySections : enPrivacySections);
    const headerUpdatedAt = activePage === 'changelog'
        ? formatDateTimeMinute(CHANGELOG_UPDATED_AT, zh)
        : UPDATED_AT;

    return (
        <main className="ml-[var(--sidebar-offset)] h-dvh overflow-y-auto bg-[#f8f7fb] px-8 py-7 text-[#111111] transition-[margin] duration-200 ease-out dark:bg-[#101010] dark:text-white/[0.92]">
            <div className="mx-auto max-w-[940px]">
                <header className="mb-6 flex flex-col gap-4 border-b border-[#dedada] pb-5 dark:border-white/[0.12] md:flex-row md:items-end md:justify-between">
                    <div className="min-w-0">
                        <p className="mb-2 text-xs font-extrabold text-[#85868c] dark:text-white/45">
                            {zh ? '关于与协议' : 'About and terms'}
                        </p>
                        <h1 className="font-headline text-2xl font-extrabold tracking-tight text-[#111111] dark:text-white">
                            {title}
                        </h1>
                        <p className="mt-2 max-w-[68ch] text-sm font-semibold leading-relaxed text-[#676970] dark:text-white/58">
                            {intro}
                        </p>
                        <p className="mt-2 text-xs font-semibold text-[#85868c] dark:text-white/45">
                            {zh ? `最后更新：${headerUpdatedAt}` : `Last updated: ${headerUpdatedAt}`}
                        </p>
                    </div>
                    <Link to="/media-text" className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-[14px] border border-[#dedada] bg-white px-4 text-sm font-extrabold text-[#111111] transition hover:bg-[#efeeee] active:translate-y-px dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]">
                        <SvgIcon name="arrow_back" className="text-[18px]"/>
                        {zh ? '返回开始处理' : 'Back to start'}
                    </Link>
                </header>

                <nav className="mb-5 flex flex-wrap gap-2" aria-label={zh ? '关于与协议页面' : 'About and terms pages'}>
                    {legalNav.map((item) => (
                        <NavLink
                            key={item.key}
                            to={`/about/${item.key}`}
                            className={({isActive}) => `inline-flex h-10 items-center gap-2 rounded-[14px] border px-4 text-sm font-extrabold transition active:translate-y-px ${
                                isActive || activePage === item.key
                                    ? 'border-[#111111] bg-[#111111] text-white dark:border-white dark:bg-white dark:text-[#111111]'
                                    : 'border-[#dedada] bg-white text-[#111111] hover:bg-[#efeeee] dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.10]'
                            }`}
                        >
                            <SvgIcon name={item.icon} className="text-[17px]"/>
                            {zh ? item.zh : item.en}
                        </NavLink>
                    ))}
                </nav>

                {activePage === 'changelog' ? <ChangelogPage zh={zh}/> : <SectionList sections={sections}/>}

                <p className="mt-4 text-xs font-semibold leading-relaxed text-[#85868c] dark:text-white/45">
                    {zh
                        ? '这份文字说明的是产品的实际行为，不是法律意见。'
                        : 'This text describes how the product actually behaves. It is not legal advice.'}
                </p>
            </div>
        </main>
    );
};

export default About;
