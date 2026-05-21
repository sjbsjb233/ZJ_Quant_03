// Theme context: light/dark, persisted, applied via data-theme on <html>.
import { createContext, useContext, useEffect, useState } from 'react';

const ThemeCtx = createContext(null);

function readStoredTheme() {
  try { return localStorage.getItem('zjq_theme') || 'light'; } catch { return 'light'; }
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(readStoredTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('zjq_theme', theme); } catch { /* storage unavailable */ }
  }, [theme]);

  const setTheme = (t) => setThemeState(t);
  const toggle = () => setThemeState((t) => (t === 'light' ? 'dark' : 'light'));

  return <ThemeCtx.Provider value={{ theme, setTheme, toggle }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);
