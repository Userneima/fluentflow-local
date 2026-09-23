import {useEffect, useState, useRef, useCallback} from 'react';
import {Link, useLocation} from 'react-router-dom';
import {
    FilePenLine,
    FileText,
    History,
    Languages,
    ChevronRight,
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

const FluentFlowLogo = ({compact = false}) => (
    <div className={`relative flex shrink-0 items-center justify-center bg-[#111111] text-white shadow-[0_18px_42px_-26px_rgba(17,17,17,.75)] [--ff-logo-line:#111111] dark:bg-white dark:text-[#111111] dark:[--ff-logo-line:#ffffff] ${compact ? 'size-6 rounded-[9px]' : 'size-10 rounded-[14px]'}`}>
        <svg viewBox="0 0 64 64" className={compact ? 'size-[18px]' : 'size-[30px]'} fill="none" aria-hidden="true">
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
const SideNav = ({
    collapsed = false,
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
    const menuRef = useRef(null);
    const loc = useLocation();

    const toggleTheme = useCallback(() => {
        const next = !isDark;
        setIsDark(next);
        const s = loadSettings();
        saveSettings({...s, theme: next ? 'dark' : 'light'});
        document.documentElement.classList.toggle('dark', next);
    }, [isDark, loadSettings, saveSettings]);

    useEffect(() => {
        if (!menuOpen) return;
        const handler = (e) => {
            if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [menuOpen]);

    useEffect(() => {
        if (!menuOpen) setLegalMenuOpen(false);
    }, [menuOpen]);

    useEffect(() => {
        if (!agentAccessOpen) return;
        const handler = (e) => {
            if (e.key === 'Escape') setAgentAccessOpen(false);
        };
        document.addEventListener('keydown', handler);
        return () => document.removeEventListener('keydown', handler);
    }, [agentAccessOpen]);

    const items = [
        {path:'/media-text', icon:Video, label: lang === 'zh' ? '视频转写与总结' : 'Media notes'},
        {path:'/agent', icon:SlidersHorizontal, k:'nav.processing'},
        {path:'/editor', icon:FilePenLine, k:'nav.editor'},
        {path:'/settings', icon:Settings, k:'nav.settings'},
    ];

    const versionInfo = window.FLUENTFLOW_CONFIG?.version || {};
    const versionLabel = versionInfo.version ? `v${versionInfo.version}` : 'local';
    const versionDetail = [versionInfo.shortCommit, versionInfo.dirty ? 'dirty' : null].filter(Boolean).join(' · ');
    const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
    const ThemeIcon = isDark ? Sun : Moon;
    const entryTitle = lang === 'zh' ? '菜单' : 'Menu';
    const entrySubtitle = lang === 'zh' ? '语言、主题与关于' : 'Language, theme & about';
    const legalLinks = [
        {path: '/about/service', icon: FileText, label: lang === 'zh' ? '服务条款' : 'Terms'},
        {path: '/about/privacy', icon: ShieldCheck, label: lang === 'zh' ? '隐私政策' : 'Privacy'},
        {path: '/about/changelog', icon: History, label: lang === 'zh' ? '版本更新' : 'Changelog'},
    ];
    return (
        <aside className={`fixed left-0 top-0 z-50 flex h-dvh flex-col border-r border-[#e5e5e5] bg-[#fbfbfb] text-[#111111] transition-[width] duration-200 ease-out dark:border-white/[0.12] dark:bg-[#0a0a0a] dark:text-white/[0.92] ${collapsed ? 'w-[72px]' : 'w-56'}`}>
            <div className={`flex h-full min-h-0 flex-col ${collapsed ? 'px-2.5 py-5' : 'px-4 py-5'}`}>
                <div className={`flex h-16 items-center ${collapsed ? 'mb-3 flex-col justify-center gap-1' : 'mb-3 justify-between gap-2'}`}>
                    <Link
                        to="/media-text"
                        className={`flex min-w-0 items-center rounded-[14px] transition hover:bg-surface-container-low focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${collapsed ? 'h-8 w-10 justify-center p-0' : 'gap-2.5 w-full px-2.5 py-2'}`}
                        aria-label="FluentFlow"
                        title={collapsed ? 'FluentFlow' : undefined}
                    >
                        <FluentFlowLogo compact={collapsed}/>
                        {!collapsed && (
                            <div className="min-w-0">
                                <h1 className="truncate font-headline text-[16px] font-extrabold leading-tight">FluentFlow</h1>
                                <p className="truncate text-[10px] font-bold text-on-surface-variant">{t('nav.subtitle')}</p>
                            </div>
                        )}
                    </Link>
                    <button
                        type="button"
                        onClick={onToggle}
                        className={`flex shrink-0 items-center justify-center text-[#5f6368] transition hover:bg-[#efeeee] hover:text-[#111111] active:translate-y-px focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary dark:text-white/70 dark:hover:bg-white/[0.08] dark:hover:text-white ${collapsed ? 'h-5 w-10 rounded-[10px]' : 'h-8 w-8 rounded-xl'}`}
                        aria-label={collapsed ? (lang === 'zh' ? '展开侧边栏' : 'Expand sidebar') : (lang === 'zh' ? '收起侧边栏' : 'Collapse sidebar')}
                        title={collapsed ? (lang === 'zh' ? '展开侧边栏' : 'Expand sidebar') : (lang === 'zh' ? '收起侧边栏' : 'Collapse sidebar')}
                    >
                        <CollapseIcon className={collapsed ? 'size-[14px]' : 'size-[18px]'} strokeWidth={1.9}/>
                    </button>
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
                                className={`flex items-center rounded-[16px] text-[14px] font-semibold tracking-normal transition ${
                                    active
                                        ? 'bg-[#e8e5e5] text-[#111111] dark:bg-white/[0.12] dark:text-white'
                                        : 'text-[#111111] hover:bg-[#efeeee] dark:text-white/[0.72] dark:hover:bg-white/[0.08] dark:hover:text-white'
                                } ${collapsed ? 'mx-auto h-10 w-12 justify-center rounded-[16px] p-0' : 'gap-3 px-3.5 py-2.5'}`}
                            >
                                <Icon className="size-5 shrink-0" strokeWidth={2.15}/>
                                <span className={collapsed ? 'sr-only' : 'truncate'}>{it.label || t(it.k)}</span>
                            </Link>
                        );
                    })}
                </nav>

                <div className={`relative mt-auto border-t border-[#e5e5e5] dark:border-white/[0.12] ${collapsed ? 'pt-5' : 'pt-4'}`} ref={menuRef}>
                    <button
                        type="button"
                        onClick={() => setMenuOpen((v) => !v)}
                        className={`w-full rounded-[14px] border border-[#e5e5e5] bg-white text-left shadow-[0_1px_2px_rgba(17,17,17,0.03)] transition hover:border-[#d9d9d9] hover:bg-[#f7f7f7] dark:border-white/[0.12] dark:bg-white/[0.06] dark:hover:border-white/[0.18] dark:hover:bg-white/[0.09] ${collapsed ? 'mx-auto flex size-12 justify-center px-0 py-2' : 'flex items-center gap-2.5 px-2.5 py-2'}`}
                        title={collapsed ? entryTitle : undefined}
                    >
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-[#efeeee] text-[#6b6c72] dark:bg-white/[0.12] dark:text-white/70">
                            <Menu className="size-4" strokeWidth={2.15}/>
                        </span>
                        {!collapsed && (
                            <span className="min-w-0">
                                <span className="block truncate text-[13px] font-semibold leading-4 text-[#111111] dark:text-white">{entryTitle}</span>
                                <span className="block truncate text-[11px] leading-4 text-[#85868c] dark:text-white/55">{entrySubtitle}</span>
                            </span>
                        )}
                    </button>

                    {menuOpen && (
                        <div className="absolute bottom-0 left-full z-50 ml-2 w-52 rounded-[14px] border border-[#e5e5e5] bg-white p-2 shadow-[0_12px_40px_-18px_rgba(17,17,17,.35)] dark:border-white/[0.12] dark:bg-[#101010]">
                            <div className="flex flex-col gap-0.5">
                                <button
                                    type="button"
                                    onClick={toggleLang}
                                    className="flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] font-semibold text-[#111111] transition hover:bg-[#efeeee] dark:text-white dark:hover:bg-white/[0.08]"
                                >
                                    <Languages className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{lang === 'zh' ? '界面语言' : 'Language'}</span>
                                    <span className="text-[11px] font-bold text-[#6b6c72] dark:text-white/70">{lang === 'en' ? '中文' : 'EN'}</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={toggleTheme}
                                    className="flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] font-semibold text-[#111111] transition hover:bg-[#efeeee] dark:text-white dark:hover:bg-white/[0.08]"
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
                                    className="flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] font-semibold text-[#111111] transition hover:bg-[#efeeee] dark:text-white dark:hover:bg-white/[0.08]"
                                >
                                    <SlidersHorizontal className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{lang === 'zh' ? 'Agent 接入' : 'Agent access'}</span>
                                </button>
                            )}
                            <div className="relative">
                                <button
                                    type="button"
                                    onClick={() => setLegalMenuOpen((value) => !value)}
                                    aria-expanded={legalMenuOpen}
                                    className={`flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] font-semibold text-[#111111] transition hover:bg-[#efeeee] dark:text-white dark:hover:bg-white/[0.08] ${legalMenuOpen ? 'bg-[#efeeee] dark:bg-white/[0.08]' : ''}`}
                                >
                                    <FileText className="size-[18px] shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                    <span className="flex-1 text-left">{lang === 'zh' ? '关于与协议' : 'About & terms'}</span>
                                    <ChevronRight className="size-4 shrink-0 text-[#6b6c72] dark:text-white/70" strokeWidth={2.15}/>
                                </button>
                                {legalMenuOpen && (
                                    <div className="absolute left-full top-0 z-[60] ml-3 w-52 rounded-[14px] border border-[#e5e5e5] bg-white p-2 shadow-[0_12px_40px_-18px_rgba(17,17,17,.35)] dark:border-white/[0.12] dark:bg-[#101010]">
                                        {legalLinks.map((item) => {
                                            const Icon = item.icon;
                                            return (
                                                <Link
                                                    key={item.path}
                                                    to={item.path}
                                                    onClick={() => setMenuOpen(false)}
                                                    className="flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] font-semibold text-[#111111] transition hover:bg-[#efeeee] dark:text-white dark:hover:bg-white/[0.08]"
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
                                    <div className="mt-0.5 truncate text-[10px]" title={versionDetail}>{versionDetail}</div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
            {agentAccessOpen && AgentAccessPanel && (
                <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[#111111]/35 px-4 py-6 backdrop-blur-[2px] dark:bg-[#050505]/65" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setAgentAccessOpen(false); }}>
                    <section
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="agent-access-title"
                        className="flex max-h-[min(860px,calc(100dvh-48px))] w-full max-w-[1120px] flex-col overflow-hidden rounded-[18px] border border-[#dedada] bg-white text-[#111111] shadow-[0_24px_80px_-28px_rgba(17,17,17,.45)] dark:border-white/[0.14] dark:bg-[#101010] dark:text-white/[0.92]"
                    >
                        <AgentAccessPanel compact onClose={() => setAgentAccessOpen(false)}/>
                    </section>
                </div>
            )}
        </aside>
    );
};

export default SideNav;
