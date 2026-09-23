import {createContext, useContext} from 'react';

// App-wide state contract, supplied by LocalAppProvider.
export const AppCtx = createContext();

export const useApp = () => useContext(AppCtx);
