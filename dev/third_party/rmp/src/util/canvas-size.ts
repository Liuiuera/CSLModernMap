import { createContext, useContext } from 'react';
import { getCanvasSize } from './helpers';
import { useWindowSize } from './hooks';

// Embedded workspaces measure the area below the Ribbon; upstream retains its window layout.
export const CanvasSizeContext = createContext<{ width: number; height: number } | undefined>(undefined);
export const useCanvasSize = () => {
    const size = useWindowSize();
    return useContext(CanvasSizeContext) ?? getCanvasSize(size);
};
