import sourceCss from '@fontsource/noto-sans-sc/400.css?raw';
const files = import.meta.glob('../../node_modules/@fontsource/noto-sans-sc/files/*400-normal.woff2', { query: '?url', import: 'default', eager: true }) as Record<string, string>;
// WebView2 and WKWebView support WOFF2; omit Fontsource's legacy WOFF alternative.
const css = sourceCss.replace(/, url\([^)]+\) format\('woff'\)/g, '').replace(/url\(([^)]+)\)/g, (match, raw: string) => {
    const filename = raw.replace(/['"]/g, '').split('/').pop();
    const entry = Object.entries(files).find(([key]) => key.endsWith('/' + filename));
    if (!entry) throw new Error(`Missing local font: ${filename}`);
    return `url("${entry[1]}")`;
});
const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);
export const embeddedFontStyle = async () => {
    const encoded = new Map<string, string>();
    for (const url of [...css.matchAll(/url\("([^"]+)"\)/g)].map(match => match[1])) {
        // Vite embeds small font subsets directly in the bundle.
        if (url.startsWith('data:')) { encoded.set(url, url); continue; }
        const response = await fetch(url); if (!response.ok) throw new Error(`Local font unavailable: ${url}`);
        const data = await response.arrayBuffer();
        const bytes = new Uint8Array(data); let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        encoded.set(url, `data:font/woff2;base64,${btoa(binary)}`);
    }
    const result = document.createElement('style');
    result.textContent = css.replace(/url\("([^"]+)"\)/g, (_, url: string) => `url("${encoded.get(url)}")`);
    return result;
};
