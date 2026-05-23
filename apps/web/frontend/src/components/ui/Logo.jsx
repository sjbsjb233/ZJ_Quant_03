// ZJ Quant wordmark logo — 方案 09, ported from the design handoff "ZJ Logo Wordmark.html".
// A single `size` knob drives every internal dimension, exactly like the source's --zj-size.
//   · mark "ZJ" : Geist 700, letter-spacing -0.04em
//   · dot       : warm-terracotta accent (kept terracotta on dark — never pure white)
//   · tag "QUANT": JetBrains Mono, tracked out 0.32em
import React from 'react';

export function ZjLogo({
  size = 56,
  inverse = false,    // light text + lighter dot, for dark backgrounds
  horizontal = false, // mark + tag on one row, divided by a hairline
  markOnly = false,   // hide the QUANT tag (compact contexts)
  style,
  ...rest
}) {
  // Two colour sets mirror the spec verbatim. The inverse set is fixed hex (not theme
  // tokens) so the logo reads correctly on any dark surface regardless of page theme.
  const textColor = inverse ? '#f5f2ec' : 'var(--fg)';
  const dotColor = inverse ? '#d4a974' : 'var(--accent)';
  const tagColor = inverse ? '#8e8878' : 'var(--fg-3)';

  const container = {
    display: 'inline-flex',
    flexDirection: horizontal ? 'row' : 'column',
    alignItems: 'center',
    gap: horizontal ? size * 0.32 : size * 0.14,
    color: textColor,
    userSelect: 'none',
    lineHeight: 1,
    ...style,
  };
  const markStyle = {
    fontFamily: 'var(--font-sans)',
    fontWeight: 700,
    fontSize: size,
    letterSpacing: '-0.04em',
    lineHeight: 1,
    display: 'inline-flex',
    alignItems: 'baseline',
  };
  const dotStyle = {
    width: size * 0.125,
    height: size * 0.125,
    background: dotColor,
    borderRadius: '50%',
    margin: `0 0 ${size * 0.11}px ${size * 0.07}px`,
    display: 'inline-block',
    flexShrink: 0,
  };
  const tagStyle = horizontal
    ? {
        fontFamily: 'var(--font-mono)',
        fontWeight: 500,
        fontSize: size * 0.22,
        textTransform: 'uppercase',
        letterSpacing: '0.32em',
        color: tagColor,
        borderLeft: '1px solid currentColor',
        paddingLeft: size * 0.28,
        opacity: 0.6,
      }
    : {
        fontFamily: 'var(--font-mono)',
        fontWeight: 500,
        fontSize: size * 0.19,
        textTransform: 'uppercase',
        letterSpacing: '0.32em',
        color: tagColor,
        paddingLeft: '0.32em', // optical re-centre against the trailing letter-spacing
      };

  return (
    <span style={container} aria-label="ZJ Quant" {...rest}>
      <span style={markStyle}>
        ZJ<span style={dotStyle} />
      </span>
      {!markOnly && <span style={tagStyle}>Q U A N T</span>}
    </span>
  );
}
