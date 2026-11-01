import { transformedBoundingBox } from './helpers';
import { NodeAttributes } from '../constants/constants';
import { StationAttributes, StationType } from '../constants/stations';

export function positionStationName(id: string, node: NodeAttributes, dx: number, dy: number): void {
    const attrs = node[node.type as StationType]! as StationAttributes & { nameOffsetX?: string; nameOffsetY?: string };
    if ('nameOffsetX' in attrs && 'nameOffsetY' in attrs) {
        attrs.nameOffsetX = dx < 0 ? 'left' : dx > 0 ? 'right' : 'middle';
        attrs.nameOffsetY = dy < 0 ? 'top' : dy > 0 ? 'bottom' : 'middle';
        delete attrs.preciseNameOffsets;
        return;
    }
    const label = document.querySelector<SVGGraphicsElement>(`[data-cmm-name="${CSS.escape(id)}"]`);
    const station = document.getElementById(id) as unknown as SVGGraphicsElement | null;
    const core = document.getElementById(`stn_core_${id}`) as unknown as SVGGraphicsElement | null;
    if (!label || !station || !core) throw new Error('Station name layout is unavailable');
    const bounds = (element: SVGGraphicsElement) => {
        const box = transformedBoundingBox(element, station);
        return { left: box.x, right: box.x + box.width, top: box.y, bottom: box.y + box.height };
    };
    const text = bounds(label),
        icon = bounds(core);
    const x =
        dx < 0
            ? icon.left - 8 - text.right
            : dx > 0
              ? icon.right + 8 - text.left
              : (icon.left + icon.right - text.left - text.right) / 2;
    const y =
        dy < 0
            ? icon.top - 8 - text.bottom
            : dy > 0
              ? icon.bottom + 8 - text.top
              : (icon.top + icon.bottom - text.top - text.bottom) / 2;
    attrs.preciseNameOffsets = {
        x: Number(label.dataset.cmmNameX) + x,
        y: Number(label.dataset.cmmNameY) + y,
        anchor: label.dataset.cmmNameAnchor as 'start' | 'middle' | 'end',
    };
}
