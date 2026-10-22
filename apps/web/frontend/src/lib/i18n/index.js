// UI language (en | zh). Strings stay in Chinese at the call site and are looked
// up here by their original text, so untranslated strings simply fall back to
// Chinese. Switching language reloads the page, which keeps `t()` a plain
// function usable in module-level config (nav tables etc.).
import shell from './en.shell';
import data from './en.data';
import factor from './en.factor';
import observ from './en.observ';

const KEY = 'zjq_locale';
const EN = { ...shell, ...data, ...factor, ...observ };

function readLocale() {
  try { return localStorage.getItem(KEY) || 'en'; } catch { return 'en'; }
}

export const locale = readLocale();

export function setLocale(next) {
  try { localStorage.setItem(KEY, next); } catch { /* storage unavailable */ }
  window.location.reload();
}

// t('{n} 行', { n: 5 }) → '5 rows' (en) / '5 行' (zh)
export function t(zh, vars) {
  let s = locale === 'en' ? (EN[zh] ?? zh) : zh;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
}

if (typeof document !== 'undefined') document.documentElement.lang = locale === 'en' ? 'en' : 'zh-CN';
