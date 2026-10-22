// Asks the service for the first byte of a media file, retrying a failure that
// says nothing about the file. A request that never got an answer (the service
// was busy, the browser dropped it) or a server error is tried again; an answer
// that the file is not there (404 and the other 4xx) is final at once.
//
// 2026-10-22: one dropped request on page load marked a present cut file as
// unreadable, and the page kept saying so until it was reloaded.

const RETRY_DELAYS_MS = [500, 1500];

const retryableStatus = (status) => status >= 500 || status === 408 || status === 429;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export const probeMedia = async (fetchFn, url, {delays = RETRY_DELAYS_MS, sleep = wait} = {}) => {
    let lastError = null;
    for (let attempt = 0; attempt <= delays.length; attempt += 1) {
        if (attempt > 0) await sleep(delays[attempt - 1]);
        let response;
        try {
            response = await fetchFn(url, {headers: {Range: 'bytes=0-0'}});
        } catch (err) {
            lastError = err;
            continue;
        }
        if (response.ok || !retryableStatus(response.status)) return response;
        lastError = new Error(`HTTP ${response.status}`);
        try { await response.body?.cancel?.(); } catch (_) { /* nothing left to read */ }
    }
    throw lastError || new Error('Media unavailable');
};
