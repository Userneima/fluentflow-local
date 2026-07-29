import {createContext, useContext} from 'react';

// Edition-neutral state contract. Hosted and local providers intentionally
// supply the same context so workspace routes do not branch on edition.
export const AppCtx = createContext();

export const useApp = () => useContext(AppCtx);
