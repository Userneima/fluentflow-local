import {useEffect, useState, useRef, useCallback} from 'react';
import {Link, useLocation} from 'react-router-dom';
import {
    FilePenLine,
    FileText,
    History,
    Languages,
    ChevronRight,
    ClipboardCopy,
    Menu,
    Moon,
    PanelLeftClose,
    PanelLeftOpen,
    SlidersHorizontal,
    Settings,
    ShieldCheck,
    Sun,
    Video,
} from 'lucide-react';
import {useI18n, useSettings} from '../app/shared.jsx';
import {useNewRelease} from '../lib/useNewRelease.js';
import {useDismissable} from '../lib/useDismissable.js';
import {useHoverPeek} from '../lib/useHoverPeek.js';
import {button, iconButton, navItem, menuItem, rowButton, floatingLayer} from '../lib/ui.js';

const FluentFlowLogo = ({compact = false}) => (
    <div className={`relative flex shrink-0 items-center justify-center bg-[#111111] text-white [--ff-logo-line:#111111] dark:bg-white dark:text-[#111111] dark:[--ff-logo-line:#ffffff] ${compact ? 'size-6 rounded-[8px]' : 'size-8 rounded-[10px]'}`}>
        <svg viewBox="0 0 64 64" className={compact ? 'size-[18px]' : 'size-[24px]'} fill="none" aria-hidden="true">
            <rect x="17" y="19" width="28" height="26" rx="8" fill="currentColor"/>
            <rect x="43" y="25" width="8" height="14" rx="4" fill="currentColor"/>
            <path d="M24 29h13M24 36h9" stroke="var(--ff-logo-line, #111111)" strokeWidth="4.2" strokeLinecap="round"/>
        </svg>
    </div>
);

const isAgentWorkflowRoute = (pathname) => (
    pathname === '/agent' || pathname === '/processing' || /^\/tasks\/[^/]+\/agent\/?$/.test(pathname)
);

const isNavItemActive = (itemPath, pathname) => {
    if (itemPath === '/app') return pathname === '/app';
    if (itemPath === '/agent') return isAgentWorkflowRoute(pathname);
    return pathname === itemPath || pathname.startsWith(`${itemPath}/`);
};

// Sidebar shell. The bottom entry is a plain menu (language, theme, agent
// access, about); the Agent access panel is passed in by the app entry.
// `collapsed` is the width the page makes room for. With `peekOnHover`, a
// folded sidebar opens over the page while the pointer rests on it and folds
// again after it leaves; the page underneath does not move (2026-11-19).
const SideNav = ({
    collapsed: layoutCollapsed = false,
    peekOnHover = false,
    onToggle = () => {},
    agentAccessPanel: AgentAccessPanel = null,
}) => {
    const {t, lang, toggleLang} = useI18n();
    const {loadSettings, saveSettings} = useSettings();
    const [isDark, setIsDark] = useState(() => {
        try { return (JSON.parse(localStorage.getItem('fluentflow_settings') || '{}').theme || 'light') === 'dark'; } catch (_) { return false; }
    });
    const [menuOpen, setMenuOpen] = useState(false);
    const [legalMenuOpen, setLegalMenuOpen] = useState(false);
    const [agentAccessOpen, setAgentAccessOpen] = useState(false);
    const [updateCommandCopied, setUpdateCommandCopied] = useState(false);
    const menuRef = useRef(null);
    const loc = useLocation();
    const hover = useHoverPeek(peekOnHover && layoutCollapsed, menuOpen || agentAccessOpen);
    const peeking = hover.peek;
    const collapsed = layoutCollapsed && !peeking;

    const toggleTheme = useCallback(() => {
        const next = !isDark;
        setIsDark(next);
        const s = loadSettings();
        saveSettings({...s, theme: next ? 'dark' : 'light'});
        document.documentElement.classList.toggle('dark', next);
    }, [isDark, loadSettings, saveSettings]);

    // Escape and a click outside close the menu; focus goes into it on open and
    // back to the button on close. The legal submenu nests inside it, so its
    // own Escape closes just the submenu first.
    const menuPanelRef = useDismissable(menuOpen, () => setMenuOpen(false), {outsideOf: menuRef});
    const legalPanelRef = useDismissable(legalMenuOpen, () => setLegalMenuOpen(false), {closeOnOutsideClick: false});
    const agentAccessRef = useDismissable(agentAccessOpen, () => setAgentAccessOpen(false), {closeOnOutsideClick: false, trapFocus: true});

    useEffect(() => {
        if (!menuOpen) setLegalMenuOpen(false);
    }, [menuOpen]);

    const newRelease = useNewRelease();
    // A git checkout is not updated by the install command (it says so and stops):
    // say what does update it (2026-11-26).
    // The command that updates this copy, with its own folder in it (the backend
    // writes it): the install command run without a folder installs into the
    // default place, a second copy beside an install the user put elsewhere.
    const copyUpdateCommand = async () => {
        try {
            await navigator.clipboard?.writeText(newRelease?.command || '');
            setUpdateCommandCopied(true);
            window.setTimeout(() => setUpdateCommandCopied(false), 1400);
        } catch (_) {
            setUpdateCommandCopied(false);
        }
    };

    const updateHint = newRelease?.checkout === 'git'
        ? (lang === 'zh'
            ? '这是 git 检出：在代码目录里 git pull，再运行一次安装脚本（launchers 里的 setup-local）；任务和设置都会保留。'
            : 'This is a git checkout: run git pull in the code folder, then the setup script in launchers; tasks and settings stay.')
        : (lang === 'zh'
            ? '让你的 agent「更新 FluentFlow」，或再运行一次安装命令；它更新的是正在运行的这一份，任务和设置都会保留。'
            : 'Ask your agent to update FluentFlow, or run the install command again; it updates this copy, and tasks and settings stay.');

    const items = [
        {path:'/media-text', icon:Video, label: lang === 'zh' ? '视频转录与总结' : 'Media notes'},
        {path:'/agent', icon:SlidersHorizontal, k:'nav.processing'},
        {path:'/editor', icon:FilePenLine, k:'nav.editor'},
        {path:'/settings', icon:Settings, k:'nav.settings'},
    ];

    const versionInfo = window.FLUENTFLOW_CONFIG?.version || {};
    const versionLabel = versionInfo.version ? `v${versionInfo.version}` : 'local';
    const versionDetail = [versionInfo.shortCommit, versionInfo.dirty ? 'dirty' : null].filter(Boolean).join(' · ');
    const CollapseIcon = layoutCollapsed ? PanelLeftOpen : PanelLeftClose;
    const toggleLabel = peeking
        ? (lang === 'zh' ? '固定展开侧边栏' : 'Keep sidebar open')
        : layoutCollapsed
            ? (lang === 'zh' ? '展开侧边栏' : 'Expand sidebar')
            : (lang === 'zh' ? '收起侧边栏' : 'Collapse sidebar');
    const ThemeIcon = isDark ? Sun : Moon;
    const entryTitle = lang === 'zh' ? '菜单' : 'Menu';
    const entrySubtitle = lang === 'zh' ? '语言、主题与关于' : 'Language, theme & about';
    const legalLinks = [
        {path: '/about/service', icon: FileText, label: lang === 'zh' ? '服务条款' : 'Terms'},
        {path: '/about/privacy', icon: ShieldCheck, label: lang === 'zh' ? '隐私政策' : 'Privacy'},
        {path: '/about/changelog', icon: History, label: lang === 'zh' ? '版本更新' : 'Changelog'},
    ];
    return (
        <aside
            data-peeking={peeking ? 'yes' : undefined}
            onMouseEnter={hover.onMouseEnter}
            onMouseLeave={hover.onMouseLeave}
            className={`fixed left-0 top-0 z-50 flex h-dvh flex-col border-r border-[#e5e5e5] bg-[#fbfbfb] text-[#111111] transition-[width,box-shadow] duration-200 ease-out dark:border-white/[0.12] dark:bg-[#0a0a0a] dark:text-white/[0.92] ${collapsed ? 'w-[72px]' : 'w-56'} ${peeking ? 'shadow-[0_24px_60px_-30px_rgba(17,17,17,0.45)]' : ''}`}
        >
            <div className={`flex h-full min-h-0 flex-col ${collapsed ? 'px-2.5 py-5' : 'px-4 py-5'}`}>
                <div className={`flex h-16 items-center ${collapsed ? 'mb-3 flex-col justify-center gap-1' : 'mb-3 justify-between gap-2'}`}>
                    <Link
                        to="/media-text"
                        className={`${rowButton} flex min-w-0 items-center ${collapsed ? 'justify-center' : 'gap-2.5'}`}
                        aria-label="FluentFlow Local"
                        title={collapsed ? 'FluentFlow Local' : undefined}
                    >
                        <FluentFlowLogo compact={collapsed}/>
                        {!collapsed && (
                            <div className="min-w-0">
                                <h1 className="whitespace-nowrap font-headline text-[14px] font-extrabold leading-tight">FluentFlow Local</h1>
                                <p className="whitespace-nowrap text-[11px] font-semibold text-on-surface-variant">{t('nav.subtitle')}</p>
                            </div>
                        )}
                    </Link>
                    {/* No toggle while a narrow window holds the sidebar folded:
                        it would do nothing. */}
                    {onToggle ? (
                    <button
                        type="button"
                        onClick={() => { hover.closeNow(); onToggle(); }}
                        className={iconButton('quiet', 'sm')}
                        aria-label={toggleLabel}
                        title={toggleLabel}
                    >
                        <CollapseIcon className="size-4" strokeWidth={1.9}/>
                    </button>
                    ) : null}
                </div>

                <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto overflow-x-hidden">
                    {items.map((it) => {
                        const active = isNavItemActive(it.path, loc.pathname);
                        const Icon = it.icon;
                        return (
                            <Link
                                key={it.path}
                                to={it.path}
                                title={collapsed ? (it.label || t(it.k)) : undefined}
                                aria-current={active ? 'page' : undefined}
                                className={navItem(active, collapsed)}
                            >
                                <Icon className="size-5 shrink-0" strokeWidth={2.15}/>
                                <span className={collapsed ? 'sr-only' : 'truncate'}>{it.label || t(it.k)}</span>
                            </Link>
                        );
                    })}
                </nav>

                <div className={`relative mt-auto border-t border-[#e5e5e5] dark:border-white/[0.12] ${collapsed ? 'pt-5' : 'pt-4'}`} ref={menuRef}>
                    {newRelease?.version && !collapsed && (
                        <button
                            type="button"
                            onClick={() => setMenuOpen(true)}
                            title={updateHint}
                            className={`${menuItem()} mb-2`}
                        >
                            <span className="size-2 shrink-0 rounded-full bg-[#2563eb]" aria-hidden="true"/>
                            <span className="min-w-0 break-words">{lang === 'zh' ? `新版本 v${newRelease.version}` : `New v${newRelease.version}`}</span>
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={() => setMenuOpen((v) => !v)}
                        aria-haspopup="menu"
                        aria-expanded={menuOpen}
                        className={`${navItem(menuOpen, collapsed)} text-left`}
                        title={collapsed ? entryTitle : undefined}
                    >
                        <Menu className="size-5 shrink-0" strokeWidth={2.15}/>
                        {!collapsed && (
                            <span className="min-w-0">
                                <span className="block truncate text-[13px] font-semibold leading-4 text-[#111111] dark:text-white">{entryTitle}</span>
                                <span className="block truncate text-[11px] leading-4 text-[#85868c] dark:text-white/55">{entrySubtitle}</span>
                            </span>
                        )}
                    </button>

                    {menuOpen && (
                        <div ref={menuPanelRef} role="menu" className={`absolute bottom-0 left-full z-50 ml-2 w-52 p-1 ${floatingLayer}`}>
                            <div className="flex flex-col gap-0.5">
                                <button
                                    type="button"
                                    onClick={toggleLang}
                                    className={menuItem()}
                                >
                                    <Languages className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{lang === 'zh' ? '界面语言' : 'Language'}</span>
                                    <span className="text-[11px] font-bold text-[#6b6c72] dark:text-white/70">{lang === 'en' ? '中文' : 'EN'}</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={toggleTheme}
                                    className={menuItem()}
                                >
                                    <ThemeIcon className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{isDark ? (lang === 'zh' ? '浅色模式' : 'Light mode') : (lang === 'zh' ? '暗色模式' : 'Dark mode')}</span>
                                </button>
                            </div>
                            <div className="my-1 border-t border-[#e5e5e5] dark:border-white/[0.12]"/>
                            {AgentAccessPanel && (
                                <button
                                    type="button"
                                    onClick={() => { setMenuOpen(false); setAgentAccessOpen(true); }}
                                    className={menuItem()}
                                >
                                    <SlidersHorizontal className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{lang === 'zh' ? 'Agent 接入' : 'Agent access'}</span>
                                </button>
                            )}
                            <div className="relative">
                                <button
                                    type="button"
                                    onClick={() => setLegalMenuOpen((value) => !value)}
                                    aria-haspopup="menu"
                                    aria-expanded={legalMenuOpen}
                                    className={menuItem('default', legalMenuOpen)}
                                >
                                    <FileText className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{lang === 'zh' ? '关于与协议' : 'About & terms'}</span>
                                    <ChevronRight className="size-4 shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                </button>
                                {legalMenuOpen && (
                                    <div ref={legalPanelRef} role="menu" className={`absolute left-full top-0 z-[60] ml-3 w-52 p-1 ${floatingLayer}`}>
                                        {legalLinks.map((item) => {
                                            const Icon = item.icon;
                                            return (
                                                <Link
                                                    key={item.path}
                                                    to={item.path}
                                                    onClick={() => setMenuOpen(false)}
                                                    className={menuItem()}
                                                >
                                                    <Icon className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                                    <span>{item.label}</span>
                                                </Link>
                                            );
                                        })}
                                    </div>
                                )}
                            </div>
                            <div className="mt-1 rounded-[10px] px-3 py-2 text-[11px] font-semibold leading-relaxed text-[#85868c] dark:text-white/45">
                                <div className="flex items-center justify-between gap-2">
                                    <span>FluentFlow</span>
                                    <span className="font-bold tabular-nums">{versionLabel}</span>
                                </div>
                                {versionDetail && (
                                    <div className="mt-0.5 truncate text-[11px]" title={versionDetail}>{versionDetail}</div>
                                )}
                                {newRelease?.version && (
                                    <div className="mt-1.5 text-[11px] font-semibold text-[#111111] dark:text-white/[0.92]">
                                        {lang === 'zh' ? `v${newRelease.version} 可以更新：` : `v${newRelease.version} is out: `}
                                        <span className="font-medium text-[#6b6c72] dark:text-white/60">{updateHint}</span>
                                        {newRelease.command && (
                                            <div className="mt-1.5 rounded-[8px] border border-[#e5e5e5] bg-white px-2 py-1.5 font-mono text-[11px] font-medium leading-relaxed break-all select-text text-[#6b6c72] dark:border-white/[0.12] dark:bg-white/[0.05] dark:text-white/60">
                                                {newRelease.command}
                                            </div>
                                        )}
                                        {newRelease.command && (
                                            <button
                                                type="button"
                                                onClick={copyUpdateCommand}
                                                className={`${button('quiet', 'sm')} mt-1`}
                                            >
                                                <ClipboardCopy className="size-3.5" strokeWidth={2.1}/>
                                                {updateCommandCopied
                                                    ? (lang === 'zh' ? '已复制' : 'Copied')
                                                    : (lang === 'zh' ? '复制更新命令' : 'Copy the command')}
                                            </button>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
            {agentAccessOpen && AgentAccessPanel && (
                <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[#111111]/35 px-4 py-6 backdrop-blur-[2px] dark:bg-[#050505]/65" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setAgentAccessOpen(false); }}>
                    <section
                        ref={agentAccessRef}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="agent-access-title"
                        className={`flex max-h-[min(860px,calc(100dvh-48px))] w-full max-w-[1120px] flex-col overflow-hidden text-[#111111] dark:text-white/[0.92] ${floatingLayer}`}
                    >
                        <AgentAccessPanel compact onClose={() => setAgentAccessOpen(false)}/>
                    </section>
                </div>
            )}
        </aside>
    );
};

export default SideNav;
