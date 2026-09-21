import { createContext, useContext } from 'react';

export const AuthCtx = createContext({
    authMode:'open',
    user:null,
    guestMode:false,
    canRegister:false,
    openAuth:()=>{},
    logout:async()=>{},
});
export const useAuth = () => useContext(AuthCtx);
