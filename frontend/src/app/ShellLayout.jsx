import {Suspense, useEffect, useState} from 'react';
import {useLocation} from 'react-router-dom';
import SideNav from '../components/SideNav.jsx';
import RouteErrorBoundary from './RouteErrorBoundary.jsx';
import {useI18n} from './shared.jsx';

// Shared workspace frame: sidebar (with persisted collapse state) plus the
// routed content area. Both edition shells render their route registry inside
// this layout; edition differences go through the sideNav component override
// (the hosted shell passes HostedSideNav) and the registry, not through
// copies of this markup.
//
// The routed area is wrapped in one error boundary here rather than per route:
// every registry entry is a lazy import, so every route shares the same way of
// failing (a chunk the current build no longer has) and the same recovery.
const ShellLayout = ({sideNav: SideNavComponent = SideNav, sideNavProps = {}, children}) => {
    const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('fluentflow_sidebar_collapsed') === '1');
    const {lang} = useI18n() ?? {};
    const {pathname} = useLocation();

    useEffect(() => {
        localStorage.setItem('fluentflow_sidebar_collapsed', sidebarCollapsed ? '1' : '0');
    }, [sidebarCollapsed]);

    return (
        <div
            className="flex h-dvh w-full overflow-hidden bg-surface dark:bg-[#101010]"
            style={{'--sidebar-offset': sidebarCollapsed ? '4.5rem' : '14rem'}}
        >
            <SideNavComponent collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((value) => !value)} {...sideNavProps}/>
            <div className="relative flex h-dvh min-h-0 w-full flex-1 flex-col overflow-hidden">
                <RouteErrorBoundary lang={lang} resetKey={pathname}>
                    <Suspense fallback={<div className="flex h-full items-center justify-center text-sm font-semibold text-on-surface-variant">Loading...</div>}>
                        {children}
                    </Suspense>
                </RouteErrorBoundary>
            </div>
        </div>
    );
};

export default ShellLayout;
