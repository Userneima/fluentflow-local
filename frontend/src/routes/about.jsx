import {Link, NavLink, useParams} from 'react-router-dom';
import {useEffect, useState} from 'react';
import {useI18n} from '../app/shared.jsx';
import SvgIcon from '../components/SvgIcon.jsx';

const UPDATED_AT = '2026-06-29';
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
        title: '服务范围',
        body: [
            'FluentFlow 用于把视频、音频、字幕文件和视频链接整理为转录文本、字幕文件、结构化笔记和导出内容。',
            '它是学习、研究和知识整理工具，不提供法律、医疗、投资、考试报名或其他专业决策建议。',
        ],
    },
    {
        title: '账号、额度与任务',
        body: [
            '账号用于隔离处理记录、额度、导出记录和任务恢复数据。额度可能按任务预估、预留和最终结算，实际消耗取决于音视频长度、转录路线、摘要模式和第三方服务成本。',
            '管理员或测试账号可能显示额度豁免。系统可能限制提交频率、上传体积、每日任务数或异常请求，以保护服务稳定性。',
        ],
    },
    {
        title: '用户内容与授权',
        body: [
            '你应确认自己有权上传、转录、下载、分析或导出相关材料，包括抖音等平台链接、课程视频、会议录音、字幕文件和文档。',
            '不要上传违法、侵权、未授权、包含敏感个人信息或商业秘密且你无权处理的内容。',
        ],
    },
    {
        title: '本地与云端处理',
        body: [
            '本地路线会尽量在你的本机后端处理音视频和转录任务，适合隐私材料和个人工作流。',
            '云端路线会把完成任务所需的文件、链接、文本、任务状态或摘要请求发送到 FluentFlow 后端及配置的第三方服务。线上纯云端环境可能不提供本地转录入口。',
        ],
    },
    {
        title: '第三方服务',
        body: [
            'FluentFlow 可能调用 ElevenLabs、OpenAI、DeepSeek、飞书 / Lark、视频下载工具或其他配置的服务商完成转录、摘要、翻译、导出和账号能力。',
            '第三方服务的可用性、价格、速度、配额和内容政策可能变化，这些变化可能影响 FluentFlow 的结果和成本。',
        ],
    },
    {
        title: '结果责任',
        body: [
            '转录、翻译、字幕切分和 AI 笔记可能出现错字、遗漏、断句错误、理解偏差或幻觉。重要材料应回听原音频、检查字幕并人工确认。',
            '重新转录、重生笔记和删除记录等操作可能覆盖或移除当前结果。涉及删除或覆盖的入口会尽量提供确认提示，但你仍应在关键操作前自行确认。',
        ],
    },
    {
        title: '服务变更',
        body: [
            'FluentFlow 仍在快速迭代中，页面、接口、任务路线、额度规则、数据结构和第三方服务商可能调整。',
            '用户可见变化会尽量记录在版本更新页；重大不兼容变化应在部署和发布说明中标注。',
        ],
    },
];

const privacySections = [
    {
        title: '我们处理哪些数据',
        body: [
            '为完成任务，FluentFlow 可能处理你提交的音视频文件、字幕文件、视频链接、视频标题、转录文本、翻译文本、AI 笔记、导出记录、任务状态、错误信息和必要的账号信息。',
            '如果启用账号系统，系统会处理邮箱、用户 ID、角色、额度和登录状态。管理员页面可能显示用于运营和排障的账号与任务摘要。',
        ],
    },
    {
        title: '本地历史与服务器任务',
        body: [
            '浏览器本地历史主要用于快速打开最近处理结果，它保存在当前浏览器环境中。清除本地历史不会删除服务器任务。',
            '服务器任务用于后台处理、跨设备同步、恢复进度、下载产物和排查失败。删除服务器任务会按后端规则清理可删除的任务记录与产物。',
        ],
    },
    {
        title: '文件、链接和原始材料',
        body: [
            '上传文件或粘贴视频链接后，系统可能下载、提取音频、保存临时文件、生成字幕和缓存中间结果，以便转录、重试、重新生成摘要或导出。',
            '本地路线的文件通常保留在本机运行环境；云端路线的文件和中间产物可能保存在服务器或对象存储中，具体取决于部署配置。',
        ],
    },
    {
        title: '第三方共享',
        body: [
            '当任务需要云端转录、AI 摘要、翻译或飞书导出时，完成任务所需的音频、文本、标题、摘要或导出内容可能发送给相应服务商。',
            '请不要把 FluentFlow 当作加密保险箱使用。高敏感材料应优先使用本地路线，并确认本机和后端配置符合你的隐私要求。',
        ],
    },
    {
        title: '保留与删除',
        body: [
            '不同部署可能采用不同的数据保留策略。当前产品会为任务恢复、历史查看和导出下载保留必要记录，直到用户删除、管理员清理或部署策略到期。',
            '你可以在记录或设置页删除可删除记录。某些日志、账务、配额或安全记录可能因排障、审计或防滥用需要保留一段时间。',
        ],
    },
    {
        title: '密钥与凭证',
        body: [
            'OpenAI、DeepSeek、ElevenLabs、飞书和 pyannote 等密钥应只保存在服务端或本机安全配置中，不应提交到代码仓库。',
            '设置页只显示凭证是否已配置，不应回显完整密钥。更换密钥时需要重新输入。',
        ],
    },
    {
        title: '你的选择',
        body: [
            '你可以选择本地或云端转录路线、清除浏览器本地历史、删除可删除的处理记录，或退出账号。',
            '如果你不希望材料发送给第三方服务，请不要使用云端转录、云端摘要、翻译或飞书导出等依赖外部服务的能力。',
        ],
    },
];

const enServiceSections = [
    {title: 'Service scope', body: ['FluentFlow turns videos, audio, subtitle files, and video links into transcripts, subtitle files, structured notes, and exportable content.', 'It is a learning and knowledge-work tool, not legal, medical, financial, exam-registration, or other professional advice.']},
    {title: 'Accounts, balance, and records', body: ['Accounts isolate processing records, balance, exports, and recovery data. Balance may be estimated, reserved, and finalized based on media length, route, note mode, and third-party costs.', 'Admin or test accounts may be quota-exempt. The system may limit submission rate, upload size, daily jobs, or abnormal requests to keep the service stable.']},
    {title: 'User content and permission', body: ['You should confirm that you have permission to upload, transcribe, download, analyze, or export the material, including video links, course videos, meeting recordings, subtitles, and documents.', 'Do not upload unlawful, infringing, unauthorized, sensitive, or confidential material that you are not allowed to process.']},
    {title: 'Local and cloud processing', body: ['Local routes try to process media and transcription jobs on your local backend, which is better for private material and personal workflows.', 'Cloud routes send required files, links, text, job status, or note requests to the FluentFlow backend and configured third-party services. Cloud-only deployments may not provide local transcription.']},
    {title: 'Third-party services', body: ['FluentFlow may call ElevenLabs, OpenAI, DeepSeek, Feishu / Lark, video download tools, or other configured providers for transcription, notes, translation, exports, and account features.', 'Provider availability, pricing, speed, quota, and content policies may change and can affect FluentFlow results and costs.']},
    {title: 'Result responsibility', body: ['Transcription, translation, subtitle segmentation, and AI notes may contain typos, omissions, bad breaks, misunderstandings, or hallucinations. Review important material against the original source.', 'Retranscribing, regenerating notes, and deleting records may overwrite or remove current results. Dangerous actions should show confirmation, but you should still check before acting.']},
    {title: 'Service changes', body: ['FluentFlow is still evolving. Pages, APIs, processing routes, balance rules, data shapes, and providers may change.', 'User-visible changes should be tracked in the changelog. Major breaking changes should be called out in release notes.']},
];

const enPrivacySections = [
    {title: 'Data we process', body: ['To complete jobs, FluentFlow may process uploaded media, subtitle files, video links, video titles, transcripts, translations, AI notes, export records, job status, errors, and necessary account data.', 'When accounts are enabled, the system processes email, user ID, role, balance, and login status. Admin pages may show account and job summaries for operations and debugging.']},
    {title: 'Local history and server jobs', body: ['Browser history helps reopen recent results quickly and is stored in the current browser environment. Clearing local history does not delete server jobs.', 'Server jobs support background processing, cross-device sync, progress recovery, artifact downloads, and failure diagnosis. Deleting server jobs follows backend cleanup rules.']},
    {title: 'Files, links, and source material', body: ['After you upload a file or paste a video link, the system may download, extract audio, save temporary files, generate subtitles, and cache intermediate results for transcription, retry, note regeneration, or export.', 'Local route files usually stay in the local runtime. Cloud route files and artifacts may be stored on the server or object storage, depending on deployment.']},
    {title: 'Third-party sharing', body: ['When a job needs cloud transcription, AI notes, translation, or Feishu export, required audio, text, titles, notes, or export content may be sent to the relevant provider.', 'Do not treat FluentFlow as an encrypted vault. Use local routes for highly sensitive material and verify that your local/backend configuration fits your privacy needs.']},
    {title: 'Retention and deletion', body: ['Deployments may use different retention policies. The product keeps necessary records for job recovery, history, and downloads until users delete them, admins clean them, or deployment policies expire.', 'You can delete eligible records from Records or Settings. Some logs, billing, quota, or security records may be retained for troubleshooting, audit, or abuse prevention.']},
    {title: 'Secrets and credentials', body: ['OpenAI, DeepSeek, ElevenLabs, Feishu, pyannote, and other credentials should live only in server or local secure configuration, never in the repository.', 'Settings should only show whether a credential is configured and should not reveal full secrets. Replacing a secret requires entering it again.']},
    {title: 'Your choices', body: ['You can choose local or cloud transcription, clear browser history, delete eligible processing records, or sign out.', 'If you do not want material sent to third-party providers, do not use cloud transcription, cloud notes, translation, Feishu export, or other external-service features.']},
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
            ? '这些条款用来说明 FluentFlow 的服务边界、账号额度、用户内容责任、本地与云端处理方式。'
            : 'These terms explain FluentFlow service boundaries, account balance, user content responsibilities, and local/cloud processing.',
        privacy: zh
            ? '这里说明 FluentFlow 在完成转录、摘要、导出和任务同步时会处理哪些数据，以及你可以怎样控制这些数据。'
            : 'This policy explains what FluentFlow processes for transcription, notes, exports, and task sync, and how you can control that data.',
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
                        ? '这是一份产品说明级文本，不是法律意见。正式公开收费或大规模商用前，应该请法律专业人士按实际主体、地区和服务商协议复核。'
                        : 'This is product-level wording, not legal advice. Before public paid launch or large-scale commercial use, have legal counsel review it against the actual entity, region, and provider agreements.'}
                </p>
            </div>
        </main>
    );
};

export default About;
