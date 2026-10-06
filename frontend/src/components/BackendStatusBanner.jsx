import {AlertTriangle} from 'lucide-react';
import {useApp} from '../app/AppContext.jsx';
import {useI18n} from '../app/shared.jsx';
import {BACKEND_DOWN_MESSAGE} from '../lib/backendHealth.js';

// One line across the top of every page while the local service is not
// answering. It says what to do and that nothing is lost; the provider keeps
// re-checking and the line goes away by itself once the service is back.
const BackendStatusBanner = () => {
    const {backendDown} = useApp() || {};
    const {lang} = useI18n() || {};
    if (!backendDown) return null;
    return (
        <div
            role="alert"
            data-testid="backend-down-banner"
            className="flex items-start gap-2 border-b border-red-200 bg-red-50 px-4 py-2.5 text-sm font-semibold text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200"
        >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" strokeWidth={2.15}/>
            <span>{lang === 'en' ? BACKEND_DOWN_MESSAGE.en : BACKEND_DOWN_MESSAGE.zh}</span>
        </div>
    );
};

export default BackendStatusBanner;
