import { createContext, useContext } from 'react';

export const AuthCtx = createContext({
    authMode:'open',
    user:null,
    guestMode:false,
    guestTrial:null,
    canRegister:false,
    openAuth:()=>{},
    logout:async()=>{},
});
export const useAuth = () => useContext(AuthCtx);
