import i18n from '../i18n/config';
import stations from '../components/svgs/stations/stations';
import miscNodes from '../components/svgs/nodes/misc-nodes';
import { linePaths, lineStyles } from '../components/svgs/lines/lines';
import { MiscNodeType } from '../constants/nodes';
import { RuntimeMode } from '../constants/constants';
import { getFacilityOptions } from '../components/svgs/nodes/facilities';

const elements = [MiscNodeType.Virtual, MiscNodeType.Fill, MiscNodeType.Facilities, MiscNodeType.Text];
export const toolCatalog = () => ({
    stations: Object.entries(stations).map(([id, item]) => ({
        id: `station-${id}`,
        label: i18n.t(item.metadata.displayName),
    })),
    paths: Object.entries(linePaths).map(([id, item]) => ({ id, label: i18n.t(item.metadata.displayName) })),
    styles: Object.entries(lineStyles).map(([id, item]) => ({
        id,
        label: i18n.t(item.metadata.displayName),
        paths: item.metadata.supportLinePathType,
    })),
    elements: elements.map(id => ({ id: `misc-node-${id}`, label: i18n.t(miscNodes[id].metadata.displayName) })),
    facilities: Object.entries(getFacilityOptions(key => i18n.t(key))).map(([id, label]) => ({ id, label })),
});

export function validateMode(value: unknown): RuntimeMode {
    const catalog = toolCatalog();
    if (
        value === 'select' ||
        catalog.stations.some(item => item.id === value) ||
        catalog.elements.some(item => item.id === value)
    )
        return value as RuntimeMode;
    if (typeof value === 'string' && value.startsWith('line-')) {
        const [path, style] = value.slice(5).split('/');
        if (
            value === `line-${path}/${style}` &&
            catalog.paths.some(item => item.id === path) &&
            catalog.styles.some(item => item.id === style && item.paths.includes(path as never))
        )
            return value as RuntimeMode;
    }
    throw new Error(`Unavailable RMP tool: ${String(value)}`);
}
