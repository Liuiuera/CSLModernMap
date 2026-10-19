import { MultiDirectedGraph } from 'graphology';
import { MonoColour } from '@railmapgen/rmg-palette-resources';
import { EdgeAttributes, NodeAttributes, NodeType, Theme } from '../constants/constants';
import { LinePathType, LineStyleType } from '../constants/lines';
import { MiscNodeType } from '../constants/nodes';
import { StationType } from '../constants/stations';
import stations from '../components/svgs/stations/stations';
import miscNodes from '../components/svgs/nodes/misc-nodes';
import { linePaths, lineStyles } from '../components/svgs/lines/lines';
import { FacilitiesType } from '../components/svgs/nodes/facilities';
import { TextLanguage } from '../util/fonts';
import { RMPSave } from '../util/save';

interface GeneratedDraft {
    version: number;
    stations: { id: string; x: number; y: number; name: string; transfer: boolean }[];
    lines: { source_line_id: number; name?: string; chains: string[][]; color: string | number[] | null }[];
    water: [number, number][][];
    facilities: { kind: 'airport' | 'rail' | 'port'; x: number; y: number; name: string }[];
}

const facilityTypes = {
    airport: FacilitiesType.Airport,
    rail: FacilitiesType.Railway,
    port: FacilitiesType.CruiseCentre,
};

// Only new drafts use these defaults; saved projects retain every edited attribute.
export function materializeDraft(project: RMPSave & { cmmDraft?: GeneratedDraft }): RMPSave {
    if (!project.cmmDraft) return project;
    const draft = project.cmmDraft;
    if (draft.version !== 1) throw new Error('Unsupported generated draft version');
    const graph = new MultiDirectedGraph<
        NodeAttributes,
        EdgeAttributes & { cmmSourceLineId?: number; cmmSourceLineName?: string }
    >();
    const node = (id: string, x: number, y: number, type: NodeType, data: object, zIndex: number) => {
        const defaults =
            type in stations
                ? stations[type as StationType].defaultAttrs
                : miscNodes[type as MiscNodeType].defaultAttrs;
        graph.addNode(id, { x, y, type, visible: true, zIndex, [type]: { ...structuredClone(defaults), ...data } });
    };
    draft.stations.forEach(row => {
        const type = row.transfer ? StationType.ShmetroInt : StationType.ShmetroBasic;
        const names = row.name.split('\n', 2);
        node(`stn_cmm_${row.id}`, row.x, row.y, type, { names: [names[0], names[1] ?? ''] }, 5);
    });
    const parallel = new Map<string, number>();
    draft.lines.forEach((line, number) => {
        const style = structuredClone(lineStyles[LineStyleType.Generic].defaultAttrs);
        if (line.color !== null) {
            const color =
                typeof line.color === 'string'
                    ? line.color
                    : '#' +
                      line.color
                          .slice(0, 3)
                          .map(v => v.toString(16).padStart(2, '0'))
                          .join('');
            if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error('Invalid source line colour');
            style.layers[0].color = ['other', 'other', color, MonoColour.white] as Theme;
        }
        const seen = new Set<string>();
        line.chains.forEach((chain, chainNumber) => {
            const keys = chain.map(id => `stn_cmm_${id}`);
            if (keys.some(id => !graph.hasNode(id))) throw new Error('Generated line references a missing station');
            keys.slice(1).forEach((right, index) => {
                const left = keys[index];
                const pair = JSON.stringify([left, right].sort());
                if (left === right || seen.has(pair)) return;
                seen.add(pair);
                const parallelIndex = parallel.get(pair) ?? 0;
                parallel.set(pair, parallelIndex + 1);
                graph.addDirectedEdgeWithKey(`line_route_${number}_${chainNumber}_${index}`, left, right, {
                    visible: true,
                    zIndex: 0,
                    type: LinePathType.Diagonal,
                    style: LineStyleType.Generic,
                    diagonal: {
                        ...structuredClone(linePaths[LinePathType.Diagonal].defaultAttrs),
                        startFrom: left < right ? 'from' : 'to',
                    },
                    generic: structuredClone(style),
                    reconcileId: '',
                    parallelIndex,
                    cmmSourceLineId: line.source_line_id,
                    cmmSourceLineName: line.name,
                });
            });
        });
    });
    draft.water.forEach((points, number) => {
        if (points.length < 3 || points.length > 80) throw new Error('Invalid generated water contour');
        // Opaque faces share RMP's river colour so partition seams do not darken the water.
        const color = structuredClone(lineStyles[LineStyleType.River].defaultAttrs.color);
        const keys = points.map(([x, y], index) => {
            const id = `misc_node_water_${number}_${index}`;
            node(
                id,
                x,
                y,
                index === 0 ? MiscNodeType.Fill : MiscNodeType.Virtual,
                index === 0 ? { color, opacity: 1, selectedPatterns: [] } : {},
                -10
            );
            return id;
        });
        keys.forEach((id, index) =>
            graph.addDirectedEdgeWithKey(`line_water_${number}_${index}`, id, keys[(index + 1) % keys.length], {
                visible: false,
                zIndex: -10,
                type: LinePathType.Simple,
                style: LineStyleType.SingleColor,
                simple: structuredClone(linePaths[LinePathType.Simple].defaultAttrs),
                'single-color': structuredClone(lineStyles[LineStyleType.SingleColor].defaultAttrs),
                reconcileId: '',
                parallelIndex: -1,
            })
        );
    });
    draft.facilities.forEach((row, number) => {
        if (!(row.kind in facilityTypes)) throw new Error('Invalid generated facility');
        node(
            `misc_node_facility_${number}`,
            row.x,
            row.y,
            MiscNodeType.Facilities,
            { type: facilityTypes[row.kind] },
            6
        );
        if (row.name)
            node(
                `misc_node_facility_name_${number}`,
                row.x + 15,
                row.y,
                MiscNodeType.Text,
                { content: row.name, language: TextLanguage.zh, textAnchor: 'start' },
                6
            );
    });
    return {
        version: 77,
        svgViewBoxZoom: project.svgViewBoxZoom,
        svgViewBoxMin: project.svgViewBoxMin,
        graph: graph.export(),
    };
}
