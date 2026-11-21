import {useEffect, useState} from 'react';
import {API_BASE, apiFetch} from '../app/apiConfig.js';

// The version of a newer release, or null (backend release_check reads the
// latest release at most once a day; this never waits on it).
export const useNewRelease = () => {
    const [latest, setLatest] = useState(null);
    useEffect(() => {
        let cancelled = false;
        apiFetch(`${API_BASE}/release-check`)
            .then((response) => (response.ok ? response.json() : null))
            .then((body) => { if (!cancelled && body?.update_available) setLatest(body.latest); })
            .catch(() => {});
        return () => { cancelled = true; };
    }, []);
    return latest;
};
