// What the page was sent here to say: extra links that were ignored, and a
// link already submitted (the task that has it is pointed at). Read from the
// navigation state into page state, so a poll does not wipe them.
import {useEffect, useState} from 'react';

export const useNavigationNotices = (location) => {
    // Kept in page state rather than read from the navigation each render: the
    // polling banner is cleared on every refresh, and this has to stay readable.
    const [extraUrlsIgnored, setExtraUrlsIgnored] = useState(() => location.state?.extraUrlsIgnored === true);
    useEffect(() => {
        if (location.state?.extraUrlsIgnored === true) setExtraUrlsIgnored(true);
    }, [location.state]);
    // A link that was already submitted is not queued a second time: the start
    // page sends the person here and points at the task that has it. The
    // notice is said only when that task is still running; a finished one that
    // could not be opened is just pointed at.
    const highlightFromState = () => String(location.state?.highlightTaskId || '');
    const [highlightTaskId, setHighlightTaskId] = useState(highlightFromState);
    const [duplicateNotice, setDuplicateNotice] = useState(() => location.state?.duplicateOfActive === true);
    useEffect(() => {
        const id = String(location.state?.highlightTaskId || '');
        if (id) setHighlightTaskId(id);
        if (location.state?.duplicateOfActive === true) setDuplicateNotice(true);
    }, [location.state]);
    return {extraUrlsIgnored, setExtraUrlsIgnored, highlightTaskId, duplicateNotice, setDuplicateNotice};
};
