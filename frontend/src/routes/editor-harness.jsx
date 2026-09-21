// Renders the real editor against a stubbed network, for tests that need to
// observe what it *sends* — not just what its helpers compute.
//
// Every fix to the editor so far was verified by unit-testing extracted pure
// functions. That left the one path that actually destroyed a user's note
// untested end to end: state change -> 800ms debounce -> PATCH. The bug was
// never in a helper; it was in which value reached the request body.
//
// So the seam here is `fetch`. Everything above it is the real thing: the real
// component, the real context providers, the real `useApi` client. A test can
// then assert on requests, which is the only place the damage was ever visible.

import {useState} from 'react';
import {MemoryRouter} from 'react-router-dom';
import {render} from '@testing-library/react';
import {AppCtx} from '../app/AppContext.jsx';
import {I18nProvider} from '../app/shared.jsx';
import Editor from './editor.jsx';

/** A `fetch` stub that records calls and answers from a route table. */
export const createFetchStub = (routes = []) => {
    const calls = [];
    const handlers = [...routes];

    // An SSE reply needs a real readable body: the client reads it with
    // `body.getReader()`, and a stub without one fails in a way that looks
    // exactly like a dropped connection — which the client then retries.
    const streamOf = (text) => {
        const chunks = [new TextEncoder().encode(text)];
        let index = 0;
        return {
            getReader: () => ({
                read: async () => (index < chunks.length
                    ? {value: chunks[index++], done: false}
                    : {value: undefined, done: true}),
                releaseLock: () => {},
                cancel: async () => {},
            }),
        };
    };

    const respond = (body, {status = 200, sse = false} = {}) => ({
        ok: status >= 200 && status < 300,
        status,
        headers: new Headers(sse ? {'Content-Type': 'text/event-stream'} : {'Content-Type': 'application/json'}),
        body: sse ? streamOf(typeof body === 'string' ? body : JSON.stringify(body)) : null,
        json: async () => body,
        text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
        blob: async () => new Blob([typeof body === 'string' ? body : JSON.stringify(body)]),
    });

    const stub = async (input, init = {}) => {
        const url = String(input);
        const method = (init.method || 'GET').toUpperCase();
        const body = init.body && typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
        calls.push({url, method, body, headers: init.headers});
        const handler = handlers.find((route) => (
            (!route.method || route.method.toUpperCase() === method) && url.includes(route.match)
        ));
        if (!handler) return respond({detail: `no stub for ${method} ${url}`}, {status: 404});
        const result = typeof handler.reply === 'function' ? await handler.reply({url, method, body}) : handler.reply;
        return respond(result?.body ?? result, {status: result?.status ?? 200, sse: !!result?.sse});
    };

    stub.calls = calls;
    stub.calling = (match, method) => calls.filter((call) => (
        call.url.includes(match) && (!method || call.method === method.toUpperCase())
    ));
    stub.addRoute = (route) => handlers.unshift(route);
    return stub;
};

/** A stream body the editor's SSE reader can consume, from a list of events. */
export const sseBody = (events) => events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');

const noop = () => {};

const Harness = ({initialResult, onResultChange}) => {
    const [lastResult, setLastResultState] = useState(initialResult);
    const setLastResult = (next) => {
        const value = typeof next === 'function' ? next(lastResult) : next;
        onResultChange?.(value);
        setLastResultState(value);
    };
    const app = {
        tasks: [], history: [], ingestJobs: noop, markCancelled: noop, revertCancelled: noop,
        restoreTask: noop, addToHistory: noop, removeFromHistory: noop, clearHistory: noop,
        currentJob: null, setCurrentJob: noop,
        lastResult, setLastResult,
        lastSourceFile: null, setLastSourceFile: noop,
        stats: {totalMinutes: 0, notesGenerated: 0},
        larkExports: [], addLarkExport: noop,
        runtimeConfig: {},
        setPendingUploadAbort: noop, abortPendingUpload: noop,
    };
    return (
        <MemoryRouter>
            <I18nProvider>
                <AppCtx.Provider value={app}>
                    <Editor/>
                </AppCtx.Provider>
            </I18nProvider>
        </MemoryRouter>
    );
};

/**
 * Render the editor with `result` loaded and `fetch` stubbed.
 * Returns the testing-library view plus the stub, for asserting on requests.
 */
export const renderEditor = ({result, routes = [], onResultChange} = {}) => {
    const fetchStub = createFetchStub(routes);
    globalThis.fetch = fetchStub;
    const view = render(<Harness initialResult={result} onResultChange={onResultChange}/>);
    return {...view, fetch: fetchStub};
};

/** Browser APIs jsdom lacks that the editor's dependencies reach for. */
export const installEditorDomStubs = () => {
    const originals = {
        rects: window.Range.prototype.getClientRects,
        rect: window.Range.prototype.getBoundingClientRect,
        observer: globalThis.ResizeObserver,
        createObjectURL: globalThis.URL.createObjectURL,
        revokeObjectURL: globalThis.URL.revokeObjectURL,
        scrollIntoView: window.Element.prototype.scrollIntoView,
    };
    const emptyRect = {bottom: 0, height: 0, left: 0, right: 0, top: 0, width: 0, x: 0, y: 0, toJSON: () => ({})};
    window.Range.prototype.getClientRects = () => [];
    window.Range.prototype.getBoundingClientRect = () => emptyRect;
    window.Element.prototype.scrollIntoView = noop;
    globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    globalThis.URL.createObjectURL = () => 'blob:stub';
    globalThis.URL.revokeObjectURL = noop;
    return () => {
        window.Range.prototype.getClientRects = originals.rects;
        window.Range.prototype.getBoundingClientRect = originals.rect;
        window.Element.prototype.scrollIntoView = originals.scrollIntoView;
        globalThis.ResizeObserver = originals.observer;
        globalThis.URL.createObjectURL = originals.createObjectURL;
        globalThis.URL.revokeObjectURL = originals.revokeObjectURL;
    };
};
