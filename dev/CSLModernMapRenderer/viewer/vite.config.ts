import { build, defineConfig } from 'vite';
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const workerSource = resolve(
  'node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs');
const workerTarget = resolve('dist/assets/maplibre-gl-csp-worker.js');

export default defineConfig({
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
  },
  plugins: [{
    name: 'copy-maplibre-csp-worker',
    async closeBundle() {
      mkdirSync(dirname(workerTarget), { recursive: true });
      // 单文件 Worker 同时供本地预览和离线 HTML 的 Blob 使用。
      await build({
        configFile: false,
        build: {
          outDir: dirname(workerTarget),
          emptyOutDir: false,
          target: 'es2022',
          lib: { entry: workerSource, formats: ['iife'], fileName: () => 'maplibre-gl-csp-worker.js', name: 'MapLibreWorker' },
        },
      });
      const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'));
      const notices: string[] = [];
      for (const [path, entry] of Object.entries(lock.packages) as [string, { dev?: boolean; version?: string; license?: string }][]) {
        if (!path || entry.dev) continue;
        notices.push(`${path} ${entry.version ?? ''} (${entry.license ?? 'see package notices'})`);
        for (const name of readdirSync(path)) {
          if (/^(licen[cs]e|notice|copying)(\.|$)/i.test(name)) {
            notices.push(readFileSync(resolve(path, name), 'utf8'));
          }
        }
      }
      writeFileSync('dist/THIRD_PARTY_LICENSES.txt', notices.join('\n\n'));
    },
  }],
});
