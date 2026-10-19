import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import checker from 'vite-plugin-checker';
import svgr from 'vite-plugin-svgr';
import { fileURLToPath, URL } from 'node:url';

// https://vitejs.dev/config
export default defineConfig({
    base: './',
    resolve: { alias: [{ find: /^@railmapgen\/rmg-runtime$/, replacement: fileURLToPath(new URL('./src/embed/runtime.ts', import.meta.url)) }] },
    plugins: [
        react(),
        svgr(),
        checker({ typescript: true, eslint: { lintCommand: 'eslint ./src', useFlatConfig: true } }),
    ],
    build: {
        rollupOptions: {
            output: {
                manualChunks: {
                    react: [
                        'react',
                        'react-dom',
                        '@reduxjs/toolkit',
                        'react-redux',
                        'react-i18next',
                    ],
                    chakra: ['@chakra-ui/react', '@emotion/react', '@emotion/styled', 'framer-motion', 'react-icons'],
                },
            },
        },
    },
});
