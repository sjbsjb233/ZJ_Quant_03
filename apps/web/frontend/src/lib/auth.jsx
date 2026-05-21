// Auth context: holds the current user, login/logout, and a role check.
import { createContext, useContext, useEffect, useState } from 'react';
import { endpoints, tokenStore } from './api';

const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };
const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(tokenStore.getUser());
  const [loading, setLoading] = useState(!!tokenStore.get());

  useEffect(() => {
    let alive = true;
    if (tokenStore.get()) {
      endpoints.me()
        .then((u) => { if (alive) setUser(u); })
        .catch(() => { if (alive) { tokenStore.clear(); setUser(null); } })
        .finally(() => { if (alive) setLoading(false); });
    } else {
      setLoading(false);
    }
    return () => { alive = false; };
  }, []);

  const login = async (username, password) => {
    const res = await endpoints.login(username, password);
    tokenStore.set(res.access_token, res.refresh_token, res.user);
    setUser(res.user);
    return res.user;
  };

  const logout = () => {
    endpoints.logout().catch(() => {});
    tokenStore.clear();
    setUser(null);
    location.assign('/login');
  };

  const hasRole = (min) => user && ROLE_RANK[user.role] >= ROLE_RANK[min];

  return <AuthCtx.Provider value={{ user, loading, login, logout, hasRole }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
