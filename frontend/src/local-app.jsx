import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import './tailwind.css';
import {I18nProvider} from './app/shared.jsx';
import {LocalAppProvider} from './app/LocalAppProvider.jsx';
import ShellLayout from './app/ShellLayout.jsx';
import {RegistryRoutes} from './app/routeRegistry.jsx';
import {localRouteRegistry} from './app/localRoutes.jsx';
import LocalAgentAccessPanel from './components/LocalAgentAccessPanel.jsx';

// App entry (built from frontend/local.html). There is no landing page, access
// gate, or admin surface: the app opens the processing workspace directly as a
// single local user.
createRoot(document.getElementById('root')).render(
    <BrowserRouter>
        <I18nProvider>
            <LocalAppProvider>
                <ShellLayout sideNavProps={{agentAccessPanel: LocalAgentAccessPanel}}>
                    <RegistryRoutes registry={localRouteRegistry}/>
                </ShellLayout>
            </LocalAppProvider>
        </I18nProvider>
    </BrowserRouter>
);
