// Tiny client-side download helpers shared by chart export buttons.
// Everything happens in-browser; no backend round-trip.

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // give the browser a tick to start the download before revoking
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadDataURL(dataUrl, filename) {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// rows: array of arrays (first row is header). Values are CSV-escaped.
export function downloadCSV(rows, filename) {
  const esc = (c) => {
    if (c === null || c === undefined) return '';
    const s = String(c);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = rows.map((r) => r.map(esc).join(',')).join('\n');
  // explicit UTF-8 BOM so Excel reads the encoding correctly
  downloadBlob(new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' }), filename);
}

// Stitch the per-pane <canvas> elements KLineChart renders into one tall PNG.
export function stitchCanvasesToPng(root, filename) {
  if (!root) return false;
  const canvases = Array.from(root.querySelectorAll('canvas')).filter((c) => c.width && c.height);
  if (!canvases.length) return false;
  const width = Math.max(...canvases.map((c) => c.width));
  const totalH = canvases.reduce((a, c) => a + c.height, 0);
  const out = document.createElement('canvas');
  out.width = width;
  out.height = totalH;
  const ctx = out.getContext('2d');
  // KLineChart canvases are transparent; paint a background so the PNG isn't see-through
  ctx.fillStyle = getComputedStyle(root).backgroundColor || '#ffffff';
  ctx.fillRect(0, 0, width, totalH);
  let y = 0;
  for (const c of canvases) {
    ctx.drawImage(c, 0, y);
    y += c.height;
  }
  downloadDataURL(out.toDataURL('image/png'), filename);
  return true;
}
