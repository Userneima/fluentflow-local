import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import './tailwind.css';
import {I18nProvider} from './app/shared.jsx';
import {LocalAppProvider} from './app/LocalAppProvider.jsx';
import ShellLayout from './app/ShellLayout.jsx';
import {RegistryRoutes} from './app/routeRegistry.jsx';
import {localRouteRegistry} from './app/localRoutes.jsx';
import LocalAgentAccessPanel from './components/LocalAgentAccessPanel.jsx';
import {installStaleBuildRecovery} from './app/staleBuildRecovery.js';

// Local-edition composition root (built from frontend/local.html).
//
// The STT and Lark-route policy seams (lib/sttPolicy.js, lib/settingsModel.js)
// DEFAULT to the local edition, so nothing is registered here: there is one
// local transcription route, the Feishu export falls back to the user's own
// app credentials, and stored hosted-OAuth route values remap to it. The
// sidebar is likewise the default account-free SideNav — only the hosted
// AppShell passes HostedSideNav into ShellLayout.
//
// There is no landing page, AccessGate, or admin surface here: the app opens
// the processing workspace directly and AuthCtx keeps its default open
// single-user value, so account and guest branches inside shared pages stay
// inactive. No direct-upload transport is registered, so uploads always take
// the local queue path.
// Chunk preloads reject outside React, where no error boundary can see them;
// ShellLayout's RouteErrorBoundary covers the rest. Both routes lead to the
// same one-shot reload in app/staleBuildRecovery.js.
installStaleBuildRecovery();

createRoot(document.getElementById('root')).render(
    <BrowserRouter>
        <I18nProvider>
            <LocalAppProvider>
                <ShellLayout sideNavProps={{agentAccessPanel: LocalAgentAccessPanel}}>
                    <RegistryRoutes registry={localRouteRegistry} ctx={{}}/>
                </ShellLayout>
            </LocalAppProvider>
        </I18nProvider>
    </BrowserRouter>
);
