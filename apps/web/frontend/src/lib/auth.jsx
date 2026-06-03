// Auth context: holds the current user, login/logout, and a role check.
import { createContext, useContext, useEffect, useState } from 'react';
import { endpoints, tokenStore } from './api';

const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };
const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(tokenStore.getUser());
  // Optimistic auth: if we already have a cached user, render the app immediately and
  // validate me() in the background — never block the whole UI on a slow/failing me()
  // (when the backend is down, gating here would hide the 可观测 degraded state, §7A.8).
  const [loading, setLoading] = useState(!!tokenStore.get() && !tokenStore.getUser());

  useEffect(() => {
    let alive = true;
    if (tokenStore.get()) {
      endpoints.me()
        .then((u) => { if (alive) setUser(u); })
        .catch((e) => {
          if (!alive) return;
          // Only force-logout on a genuine auth rejection. A network/5xx failure
          // means the BACKEND is down, not that the token is bad — keep the cached
          // user so the 可观测 page still renders the degraded state (绝不把后端宕机
          //当成登出, 否则用户连「后端故障」都看不到, 可观测层重构方案 §7A.8 / AC-11).
          const code = e?.apiError?.code || e?.response?.data?.error?.code;
          if (e?.response?.status === 401 || code === 'UNAUTHENTICATED') {
            tokenStore.clear();
            setUser(null);
          }
          // else: retain tokenStore.getUser() (already the initial state)
        })
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

  const hasRole = (min) => !!user && ROLE_RANK[user.role] >= ROLE_RANK[min];

  return <AuthCtx.Provider value={{ user, loading, login, logout, hasRole }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
