import {Suspense, useEffect, useState} from 'react';
import {useLocation} from 'react-router-dom';
import SideNav from '../components/SideNav.jsx';
import InterruptedTasksDialog from '../components/InterruptedTasksDialog.jsx';
import BackendStatusBanner from '../components/BackendStatusBanner.jsx';

// Shared workspace frame: sidebar (with persisted collapse state) plus the
// routed content area, and the app-level notice for tasks a service restart
// interrupted (it has to appear on whichever page is open).
// Below this window width the sidebar folds to icons on its own (records page
// redesign, 2026-10-23). Mockup: only with ?layout=v2 in the address.
const NARROW_SIDEBAR_WIDTH = 1000;

const useNarrowWindow = (enabled) => {
    const query = `(max-width: ${NARROW_SIDEBAR_WIDTH - 1}px)`;
    const [narrow, setNarrow] = useState(() => enabled && typeof window !== 'undefined' && !!window.matchMedia?.(query).matches);
    useEffect(() => {
        if (!enabled || !window.matchMedia) {
            setNarrow(false);
            return undefined;
        }
        const list = window.matchMedia(query);
        const update = () => setNarrow(list.matches);
        update();
        list.addEventListener?.('change', update);
        return () => list.removeEventListener?.('change', update);
    }, [enabled, query]);
    return narrow;
};

const ShellLayout = ({sideNavProps = {}, children}) => {
    const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('fluentflow_sidebar_collapsed') === '1');
    const location = useLocation();
    const layoutV2 = new URLSearchParams(location.search).get('layout') === 'v2';
    // A narrow window folds the sidebar without touching the remembered choice,
    // so widening the window brings back whatever the owner picked.
    const narrowWindow = useNarrowWindow(layoutV2);
    const collapsed = sidebarCollapsed || narrowWindow;

    useEffect(() => {
        localStorage.setItem('fluentflow_sidebar_collapsed', sidebarCollapsed ? '1' : '0');
    }, [sidebarCollapsed]);

    return (
        <div
            className="flex h-dvh w-full overflow-clip bg-surface dark:bg-[#101010]"
            style={{'--sidebar-offset': collapsed ? '4.5rem' : '14rem'}}
        >
            <SideNav collapsed={collapsed} onToggle={narrowWindow ? null : () => setSidebarCollapsed((value) => !value)} {...sideNavProps}/>
            {/* clip, not hidden: a box with hidden overflow can still be scrolled by
                the browser bringing a focused control into view, which slid the
                whole page out of sight when a settings switch was clicked. Only
                each page's own <main> scrolls. */}
            <div className="relative flex h-dvh min-h-0 w-full flex-1 flex-col overflow-clip">
                <BackendStatusBanner/>
                {/* A page's code is still loading: a spinner, no words, since the
                    language setting may not be read yet. */}
                <Suspense fallback={<div role="status" aria-label="加载中" className="flex h-full items-center justify-center"><span className="size-5 animate-spin rounded-full border-2 border-[#d6d3d3] border-t-[#111111] dark:border-white/20 dark:border-t-white"/></div>}>
                    {children}
                </Suspense>
            </div>
            <InterruptedTasksDialog/>
        </div>
    );
};

export default ShellLayout;
