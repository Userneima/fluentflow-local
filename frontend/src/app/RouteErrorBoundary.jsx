import {Component} from 'react';
import {isStaleChunkError, recoverFromStaleBuild} from './staleBuildRecovery.js';

// The routed area's error boundary. Without one, a lazy route that fails to
// load — the usual cause is a page outliving its build, see
// staleBuildRecovery.js — leaves the content area blank with only a console
// TypeError to go on.
//
// Its copy is self-contained rather than read from I18nCtx: a boundary that
// needs a working provider to render its own message is a boundary that fails
// exactly when it is needed.
const COPY = {
    zh: {
        reloading: '正在加载新版本…',
        staleTitle: '前端文件版本不一致',
        staleDesc: '这个页面加载的前端文件已经被新的构建替换。重新加载即可继续；如果反复出现，请重新构建前端后再刷新。',
        errorTitle: '这个页面出错了',
        errorDesc: '其他页面通常仍可使用。重新加载后如果依旧失败，请把下面这行信息一起反馈。',
        reload: '重新加载',
    },
    en: {
        reloading: 'Loading the new build…',
        staleTitle: 'Frontend files are out of sync',
        staleDesc: 'This page loaded frontend files that a newer build has replaced. Reloading picks up the current build; if it keeps happening, rebuild the frontend and refresh.',
        errorTitle: 'This page failed',
        errorDesc: 'Other pages usually still work. If reloading does not help, include the line below when reporting it.',
        reload: 'Reload',
    },
};

class RouteErrorBoundary extends Component {
    state = {error: null, reloading: false};

    static getDerivedStateFromError(error) {
        return {error};
    }

    componentDidCatch(error) {
        const {isStale = isStaleChunkError, recover = recoverFromStaleBuild, storage, reload} = this.props;
        if (!isStale(error)) return;

        // A stale chunk is not an application bug and has one real fix: get the
        // current index.html. Recovery declines when it just tried, which is
        // when the message below is the honest answer.
        const reloading = recover({
            storage: storage ?? (typeof sessionStorage === 'undefined' ? null : sessionStorage),
            reload: reload ?? (() => window.location.reload()),
        });
        this.setState({reloading});
    }

    componentDidUpdate(prevProps) {
        // Navigating away from a broken route must not stay broken.
        if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
            this.setState({error: null, reloading: false});
        }
    }

    render() {
        const {children, lang = 'zh', reload} = this.props;
        const {error, reloading} = this.state;
        if (!error) return children;

        const copy = COPY[lang] ?? COPY.zh;
        const stale = (this.props.isStale ?? isStaleChunkError)(error);
        const onReload = reload ?? (() => window.location.reload());

        if (stale && reloading) {
            return (
                <div className="flex h-full items-center justify-center text-sm font-semibold text-on-surface-variant">
                    {copy.reloading}
                </div>
            );
        }

        return (
            <div className="flex h-full items-center justify-center p-8">
                <div className="max-w-md space-y-3 text-center">
                    <h2 className="text-lg font-bold text-on-surface">{stale ? copy.staleTitle : copy.errorTitle}</h2>
                    <p className="text-sm leading-relaxed text-on-surface-variant">{stale ? copy.staleDesc : copy.errorDesc}</p>
                    {!stale && (
                        <p className="break-all rounded-2xl bg-surface-container px-4 py-3 text-left font-mono text-xs text-on-surface-variant">
                            {String(error?.message || error)}
                        </p>
                    )}
                    <button
                        type="button"
                        onClick={onReload}
                        className="rounded-full bg-primary px-5 py-2 text-sm font-semibold text-on-primary"
                    >
                        {copy.reload}
                    </button>
                </div>
            </div>
        );
    }
}

export default RouteErrorBoundary;
