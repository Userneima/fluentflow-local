import {useEffect, useState} from 'react';
import {API_BASE, apiFetch} from '../app/apiConfig.js';

// While the service's first check is still out it answers latest: null, so the
// page asks again shortly; after that, every few hours for a page left open.
const RETRY_MS = 15_000;
const RETRIES = 4;
const RECHECK_MS = 6 * 3600 * 1000;

// The version of a newer release, or null (backend release_check reads the
// latest release at most once a day; asking never waits on it).
export const useNewRelease = () => {
    const [latest, setLatest] = useState(null);
    useEffect(() => {
        let cancelled = false;
        let timer = null;
        let retries = 0;
        const ask = () => {
            apiFetch(`${API_BASE}/release-check`)
                .then((response) => (response.ok ? response.json() : null))
                .then((body) => {
                    if (cancelled) return;
                    if (body?.update_available) setLatest(body.latest);
                    const unknown = body && !body.latest;
                    const wait = unknown && retries < RETRIES ? RETRY_MS : RECHECK_MS;
                    if (unknown) retries += 1;
                    timer = setTimeout(ask, wait);
                })
                .catch(() => { if (!cancelled) timer = setTimeout(ask, RECHECK_MS); });
        };
        ask();
        return () => { cancelled = true; clearTimeout(timer); };
    }, []);
    return latest;
};
