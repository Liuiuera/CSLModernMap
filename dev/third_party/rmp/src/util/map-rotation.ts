import { MultiDirectedGraph } from 'graphology';
import { EdgeAttributes, GraphAttributes, NodeAttributes } from '../constants/constants';

export interface MapRotation {
    angle: 90 | 180 | 270;
    cx: number;
    cy: number;
}

type Point = { x: number; y: number };

export const rotateVector = ({ x, y }: Point, angle: number): Point => {
    switch ((angle + 360) % 360) {
        case 90: return { x: -y, y: x };
        case 180: return { x: -x, y: -y };
        case 270: return { x: y, y: -x };
        default: return { x, y };
    }
};

export const rotatePoint = (point: Point, rotation?: MapRotation, inverse = false): Point => {
    if (!rotation) return point;
    const { cx, cy, angle } = rotation;
    const rotated = rotateVector({ x: point.x - cx, y: point.y - cy }, inverse ? -angle : angle);
    return { x: rotated.x + cx, y: rotated.y + cy };
};

export const currentMapRotation = (): MapRotation | undefined => window.graph?.getAttribute('cmmRotation');

export const toMapPoint = (point: Point): Point => rotatePoint(point, currentMapRotation(), true);
export const toMapVector = (point: Point): Point => rotateVector(point, -(currentMapRotation()?.angle ?? 0));

export const validateMapRotation = (rotation: unknown): void => {
    if (rotation === undefined) return;
    const value = rotation as MapRotation | null;
    if (!value || ![90, 180, 270].includes(value.angle) || !Number.isFinite(value.cx) || !Number.isFinite(value.cy))
        throw new Error('Invalid map rotation');
};

/** Keep native route geometry intact, including direction-dependent parallel offsets. */
export const rotateMap = (
    graph: MultiDirectedGraph<NodeAttributes, EdgeAttributes, GraphAttributes>,
    delta: number
): boolean => {
    if (delta !== 90 && delta !== -90) throw new Error('Map rotation requires +90 or -90 degrees');
    if (!graph.order) return false;
    const previous = graph.getAttribute('cmmRotation');
    const angle = ((previous?.angle ?? 0) + delta + 360) % 360;
    if (!angle) {
        graph.removeAttribute('cmmRotation');
        return true;
    }
    let xMin = Infinity, yMin = Infinity, xMax = -Infinity, yMax = -Infinity;
    graph.forEachNode((_, { x, y }) => {
        xMin = Math.min(xMin, x); yMin = Math.min(yMin, y);
        xMax = Math.max(xMax, x); yMax = Math.max(yMax, y);
    });
    graph.setAttribute('cmmRotation', {
        angle: angle as MapRotation['angle'],
        cx: previous?.cx ?? (xMin + xMax) / 2,
        cy: previous?.cy ?? (yMin + yMax) / 2,
    });
    return true;
};
