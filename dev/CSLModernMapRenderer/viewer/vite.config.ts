import { defineConfig } from 'vite';
import { copyFileSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';

const workerSource = resolve(
  'node_modules/maplibre-gl/dist/maplibre-gl-csp-worker.js');
const workerTarget = resolve('dist/assets/maplibre-gl-csp-worker.js');

export default defineConfig({
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    target: process.env.CSLMM_MACOS_BUILD ? 'safari16' : 'es2022',
  },
  plugins: [{
    name: 'check-transit-badge-layout',
    buildStart() {
      const source = readFileSync(resolve('../csmodernmap/transit_badge_layout.py'), 'utf8').replace(/\r\n/g, '\n');
      const hash = createHash('sha256').update(source).digest('hex');
      if (!readFileSync(resolve('src/transit-badge-layout.js'), 'utf8').split(/\r?\n/, 1)[0].endsWith(hash)) {
        throw new Error('Regenerate the shared layout with python ../scripts/generate_transit_badge_layout.py');
      }
    },
  }, {
    name: 'copy-maplibre-csp-worker',
    closeBundle() {
      mkdirSync(dirname(workerTarget), { recursive: true });
      copyFileSync(workerSource, workerTarget);
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
      writeFileSync(resolve('dist/NAVIGATION_LICENSES.txt'), notices.join('\n\n')
        + '\n\nPScript 0.8.1\n' + readFileSync(resolve('PScript-LICENSE.txt'), 'utf8')
        + '\n\nTabler Icons 2.47.0\n' + readFileSync(resolve('../resources/fonts/Tabler-LICENSE.txt'), 'utf8'));
    },
  }],
});
