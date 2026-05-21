"""Symbol normalisation (doc test T-BD-012).

Standard form is ``<code>.<EXCHANGE>`` e.g. '600519.SH'. A bare 6-digit
A-share code is normalised by inferring the exchange from its prefix; anything
already carrying a suffix, or a code we can't confidently classify (HK 5-digit,
US tickers), is returned unchanged.
"""
from __future__ import annotations


def normalize_symbol(code: str) -> str:
    s = str(code).strip().upper()
    if "." in s:  # already qualified, e.g. 600519.SH / 00700.HK
        return s
    if len(s) == 6 and s.isdigit():
        if s[0] == "6":  # Shanghai main board / STAR (688)
            return f"{s}.SH"
        if s[0] in ("0", "3"):  # Shenzhen main / ChiNext (300)
            return f"{s}.SZ"
        if s[0] in ("4", "8") or s.startswith("92"):  # Beijing Stock Exchange
            return f"{s}.BJ"
    return s  # can't infer (HK/US/...) -> leave as-is


def is_normalized(code: str) -> bool:
    return "." in str(code)
