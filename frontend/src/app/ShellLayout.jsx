import {Suspense, useEffect, useState} from 'react';
import SideNav from '../components/SideNav.jsx';
import InterruptedTasksDialog from '../components/InterruptedTasksDialog.jsx';

// Shared workspace frame: sidebar (with persisted collapse state) plus the
// routed content area, and the app-level notice for tasks a service restart
// interrupted (it has to appear on whichever page is open).
const ShellLayout = ({sideNavProps = {}, children}) => {
    const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('fluentflow_sidebar_collapsed') === '1');

    useEffect(() => {
        localStorage.setItem('fluentflow_sidebar_collapsed', sidebarCollapsed ? '1' : '0');
    }, [sidebarCollapsed]);

    return (
        <div
            className="flex h-dvh w-full overflow-hidden bg-surface dark:bg-[#101010]"
            style={{'--sidebar-offset': sidebarCollapsed ? '4.5rem' : '14rem'}}
        >
            <SideNav collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((value) => !value)} {...sideNavProps}/>
            <div className="relative flex h-dvh min-h-0 w-full flex-1 flex-col overflow-hidden">
                <Suspense fallback={<div className="flex h-full items-center justify-center text-sm font-semibold text-on-surface-variant">Loading...</div>}>
                    {children}
                </Suspense>
            </div>
            <InterruptedTasksDialog/>
        </div>
    );
};

export default ShellLayout;
