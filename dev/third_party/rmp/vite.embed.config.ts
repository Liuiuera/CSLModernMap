import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import svgr from 'vite-plugin-svgr';
import { fileURLToPath, URL } from 'node:url';
import { cpSync, copyFileSync, mkdirSync } from 'node:fs';

export default defineConfig({
    base: './',
    publicDir: false,
    plugins: [react(), svgr(), { name: 'embedded-resources', closeBundle() {
        mkdirSync('dist-embed/images', { recursive: true });
        cpSync('public/images/facilities', 'dist-embed/images/facilities', { recursive: true });
        copyFileSync('public/logo.svg', 'dist-embed/logo.svg');
        mkdirSync('dist-embed/licenses', { recursive: true });
        copyFileSync('LICENCE', 'dist-embed/licenses/RMP-LICENSE.txt');
        copyFileSync('RMP-IMAGE-TERMS.txt', 'dist-embed/licenses/RMP-IMAGE-TERMS.txt');
        copyFileSync('node_modules/@fontsource/noto-sans-sc/LICENSE', 'dist-embed/licenses/Noto-Sans-SC-LICENSE.txt');
    } }],
    resolve: { alias: [{ find: /^@railmapgen\/rmg-runtime$/, replacement: fileURLToPath(new URL('./src/embed/runtime.ts', import.meta.url)) }] },
    build: { outDir: 'dist-embed', rollupOptions: { input: 'embed.html' } },
});
