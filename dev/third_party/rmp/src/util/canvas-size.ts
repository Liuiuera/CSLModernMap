import { createContext, useContext } from 'react';
import { getCanvasSize } from './helpers';
import { useWindowSize } from './hooks';

// Embedded workspaces measure the area below the Ribbon; upstream retains its window layout.
export const CanvasSizeContext = createContext<{ width: number; height: number; visibleWidth?: number } | undefined>(
    undefined
);
export const useCanvasSize = (): { width: number; height: number; visibleWidth?: number } => {
    const size = useWindowSize();
    return useContext(CanvasSizeContext) ?? getCanvasSize(size);
};

export const embeddedWorkspaceSize = (width: number, height: number, detailsVisible: boolean) => {
    const panelWidth = detailsVisible ? Math.min(320, Math.max(0, width - 48)) : 0;
    return {
        width: Math.max(1, width - (width >= 900 ? panelWidth : 0)),
        height,
        visibleWidth: Math.max(1, width - panelWidth),
    };
};

// The SVG stays full-width under an overlay panel, but fitting and visibility use its unobscured area.
export const visibleCanvasBounds = () => {
    const canvas = document.getElementById('canvas')!;
    const rect = canvas.getBoundingClientRect();
    const workspace = canvas.closest<HTMLElement>('[data-cmm-visible-width]')!;
    const width = Number(workspace.dataset.cmmVisibleWidth);
    return {
        left: rect.left,
        top: rect.top,
        right: rect.left + width,
        bottom: rect.bottom,
        width,
        height: rect.height,
    };
};
