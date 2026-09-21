// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import RouteErrorBoundary from './RouteErrorBoundary.jsx';

afterEach(cleanup);

const STALE = 'Failed to fetch dynamically imported module: /assets/settings-Cc3dBdjv.js';

const Boom = ({message}) => {
    throw new Error(message);
};

// A caught error still reaches console.error (React) and jsdom's uncaught
// handler; both are expected here, so silence them per render and keep the
// suite output readable.
const renderQuietly = (ui) => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const swallow = (event) => event.preventDefault();
    window.addEventListener('error', swallow);
    try {
        return render(ui);
    } finally {
        window.removeEventListener('error', swallow);
        spy.mockRestore();
    }
};

describe('RouteErrorBoundary', () => {
    it('renders the route while nothing is wrong', () => {
        render(<RouteErrorBoundary recover={() => true}><p>笔记正文</p></RouteErrorBoundary>);
        expect(screen.getByText('笔记正文')).toBeTruthy();
    });

    it('reloads the page when a route chunk is gone, instead of leaving it blank', () => {
        const recover = vi.fn(() => true);
        renderQuietly(<RouteErrorBoundary recover={recover}><Boom message={STALE}/></RouteErrorBoundary>);

        expect(recover).toHaveBeenCalledTimes(1);
        expect(screen.getByText('正在加载新版本…')).toBeTruthy();
    });

    it('explains itself instead of reloading forever when the reload did not help', () => {
        const recover = vi.fn(() => false);
        renderQuietly(<RouteErrorBoundary recover={recover}><Boom message={STALE}/></RouteErrorBoundary>);

        expect(recover).toHaveBeenCalledTimes(1);
        expect(screen.getByText('前端文件版本不一致')).toBeTruthy();
        expect(screen.getByRole('button', {name: '重新加载'})).toBeTruthy();
    });

    it('shows an ordinary application error without reloading', () => {
        const recover = vi.fn(() => true);
        renderQuietly(<RouteErrorBoundary recover={recover}><Boom message="HTTP 500 /jobs"/></RouteErrorBoundary>);

        expect(recover).not.toHaveBeenCalled();
        expect(screen.getByText('这个页面出错了')).toBeTruthy();
        expect(screen.getByText(/HTTP 500 \/jobs/)).toBeTruthy();
    });
});
