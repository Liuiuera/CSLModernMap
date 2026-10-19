import { MultiDirectedGraph } from 'graphology';
import { nanoid } from 'nanoid';
import { EdgeAttributes, Id, NodeAttributes, NodeId, NodeType } from '../constants/constants';
import { LINE_WIDTH, LineStyleType } from '../constants/lines';
import { MiscNodeType } from '../constants/nodes';
import { StationType } from '../constants/stations';
import stations from '../components/svgs/stations/stations';
import miscNodes from '../components/svgs/nodes/misc-nodes';
import { linePaths, lineStyles } from '../components/svgs/lines/lines';
import { FacilitiesType, getFacilityOptions } from '../components/svgs/nodes/facilities';
import { findShortestClosedPath } from '../util/graph-find-shortest-closed-path';
import { transformedBoundingBox } from '../util/helpers';
import { TextLanguage } from '../util/fonts';
import i18n from '../i18n/config';

export type InsertRequest = { kind: 'title' | 'legend' | 'facility'; title?: string; facility?: FacilitiesType };
type LegendEdge = EdgeAttributes & { cmmSourceLineId?: number; cmmSourceLineName?: string; cmmDecoration?: string };
type LegendNode = NodeAttributes & { cmmDecoration?: string };
type Row = { nodes: string[]; samples: string[]; label?: string; stroke: number };
export type Insertion = { graph: MultiDirectedGraph<LegendNode, LegendEdge>; rows: Row[]; focusText: boolean };

export function validateInsertion(value: unknown): InsertRequest {
    const request = value as InsertRequest | undefined;
    if (
        !request ||
        !['title', 'legend', 'facility'].includes(request.kind) ||
        (request.title !== undefined && typeof request.title !== 'string') ||
        (request.kind === 'facility' && !Object.values(FacilitiesType).includes(request.facility!))
    )
        throw new Error('Invalid insertion tool');
    return { kind: request.kind, ...(request.kind === 'title' && request.title !== undefined ? { title: request.title } : {}),
        ...(request.kind === 'facility' ? { facility: request.facility } : {}) };
}

const signature = (value: unknown): string => {
    if (Array.isArray(value)) return '[' + value.map(signature).join(',') + ']';
    if (value && typeof value === 'object')
        return JSON.stringify(
            Object.entries(value)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([k, v]) => [k, signature(v)])
        );
    return JSON.stringify(value) ?? 'null';
};

export function buildInsertion(request: InsertRequest): Insertion {
    const graph = new MultiDirectedGraph<LegendNode, LegendEdge>();
    const rows: Row[] = [];
    const decoration = request.kind === 'legend' ? { cmmDecoration: 'legend' } : {};
    const node = (type: NodeType, data: object, x = 0, y = 0) => {
        const id = `${type in stations ? 'stn' : 'misc_node'}_${nanoid(10)}`;
        const defaults =
            type in stations
                ? stations[type as StationType].defaultAttrs
                : miscNodes[type as MiscNodeType].defaultAttrs;
        graph.addNode(id, {
            type,
            x,
            y,
            visible: true,
            zIndex: 6,
            [type]: { ...structuredClone(defaults), ...data },
            ...decoration,
        });
        return id;
    };
    const text = (content: string, extra: object = {}, x = 0) =>
        node(
            MiscNodeType.Text,
            {
                content,
                language: TextLanguage.zh,
                textAnchor: 'start',
                dominantBaseline: 'middle',
                ...extra,
            },
            x
        );
    if (request.kind === 'title') {
        const size = miscNodes[MiscNodeType.Text].defaultAttrs.fontSize * 2;
        text(request.title ?? i18n.t('embed.defaultTitle'), { fontSize: size, lineHeight: size, bold: 'bold' });
        return { graph, rows, focusText: true };
    }
    if (request.kind === 'facility') {
        node(MiscNodeType.Facilities, { type: request.facility });
        return { graph, rows, focusText: false };
    }
    const source = window.graph;
    const excluded = new Set<string>();
    source.forEachNode((id, attrs) => {
        if (attrs.type === MiscNodeType.Fill)
            findShortestClosedPath(source, id as NodeId)?.edges.forEach(edge => excluded.add(edge));
    });
    const seen = new Set<string>();
    source.forEachEdge((id, raw, from, to) => {
        const attrs = raw as LegendEdge;
        if (
            !attrs.visible ||
            attrs.cmmDecoration ||
            id.startsWith('line_water_') ||
            excluded.has(id) ||
            attrs.style === LineStyleType.River ||
            !source.getNodeAttribute(from, 'visible') ||
            !source.getNodeAttribute(to, 'visible')
        )
            return;
        const styleAttrs = structuredClone(attrs[attrs.style] ?? lineStyles[attrs.style].defaultAttrs);
        const comparable =
            attrs.style === LineStyleType.Generic
                ? {
                      ...styleAttrs,
                      layers: (styleAttrs as NonNullable<EdgeAttributes['generic']>).layers.map(
                          ({ id: _id, ...layer }) => layer
                      ),
                  }
                : styleAttrs;
        const key = signature([attrs.cmmSourceLineId ?? null, attrs.style, comparable]);
        if (seen.has(key)) return;
        seen.add(key);
        const left = node(MiscNodeType.Virtual, {});
        const right = node(MiscNodeType.Virtual, {}, 80);
        const edge = `line_${nanoid(10)}`;
        graph.addDirectedEdgeWithKey(edge, left, right, {
            type: attrs.type,
            [attrs.type]: structuredClone(linePaths[attrs.type].defaultAttrs),
            style: attrs.style,
            [attrs.style]: styleAttrs,
            visible: true,
            zIndex: 6,
            reconcileId: '',
            parallelIndex: -1,
            ...decoration,
        });
        const label = text(attrs.cmmSourceLineName?.trim() || i18n.t('embed.unnamedLine'), {}, 100);
        const stroke =
            attrs.style === LineStyleType.Generic
                ? Math.max(...(styleAttrs as NonNullable<EdgeAttributes['generic']>).layers.map(layer => layer.width))
                : LINE_WIDTH;
        rows.push({ nodes: [left, right, label], samples: [edge], label, stroke });
    });
    const collator = new Intl.Collator(i18n.language, { numeric: true, sensitivity: 'base' });
    rows.sort((a, b) => collator.compare(
        graph.getNodeAttribute(a.label!, MiscNodeType.Text)!.content,
        graph.getNodeAttribute(b.label!, MiscNodeType.Text)!.content));
    const facilityNames = getFacilityOptions(key => i18n.t(key));
    source.forEachNode((_id, raw) => {
        const attrs = raw as LegendNode;
        if (!attrs.visible || attrs.cmmDecoration) return;
        const type = attrs.type;
        if (!(type in stations) && type !== MiscNodeType.Facilities) return;
        const key = type === MiscNodeType.Facilities ? `facility:${attrs.facilities!.type}` : type;
        if (seen.has(key)) return;
        seen.add(key);
        const data =
            type in stations
                ? { ...structuredClone(attrs[type as StationType]), names: ['', ''] }
                : structuredClone(attrs.facilities!);
        const sample = node(type, data);
        const label = text(
            type in stations
                ? i18n.t(stations[type as StationType].metadata.displayName)
                : facilityNames[attrs.facilities!.type],
            {},
            100
        );
        rows.push({ nodes: [sample, label], samples: [sample], label, stroke: 0 });
    });
    if (!rows.length) throw new Error(i18n.t('embed.emptyLegend'));
    const heading = text(i18n.t('embed.legend'), { bold: 'bold' });
    rows.unshift({ nodes: [heading], samples: [heading], stroke: 0 });
    return { graph, rows, focusText: false };
}

// Measure the rendered native symbols before the single history commit; labels never overlap wide or tall samples.
export function positionInsertion(insertion: Insertion, x: number, y: number) {
    const graph = window.graph;
    if (!insertion.rows.length) {
        insertion.graph.forEachNode(id => graph.mergeNodeAttributes(id, { x, y }));
        return;
    }
    const bounds = (ids: string[], stroke = 0) => {
        const elements = ids.flatMap(id =>
            [id, id + '.pre', id + '.post']
                .map(key => document.getElementById(key))
                .filter((element): element is HTMLElement => element !== null)
        );
        const boxes = elements.map(element => transformedBoundingBox(element as unknown as SVGSVGElement));
        return {
            left: Math.min(...boxes.map(b => b.x)) - stroke / 2,
            right: Math.max(...boxes.map(b => b.x + b.width)) + stroke / 2,
            top: Math.min(...boxes.map(b => b.y)) - stroke / 2,
            bottom: Math.max(...boxes.map(b => b.y + b.height)) + stroke / 2,
        };
    };
    const measurements = insertion.rows.map(row => bounds(row.samples, row.stroke));
    const sampleWidth = Math.max(80, ...measurements.slice(1).map(b => b.right - b.left));
    let top = y;
    insertion.rows.forEach((row, index) => {
        const box = measurements[index];
        const label = row.label ? bounds([row.label]) : undefined;
        const height = Math.max(32, box.bottom - box.top, label ? label.bottom - label.top : 0);
        const dx = x - box.left + (row.label ? (sampleWidth - (box.right - box.left)) / 2 : 0);
        const dy = top + height / 2 - (box.top + box.bottom) / 2;
        row.nodes.forEach(id => {
            const attrs = graph.getNodeAttributes(id);
            graph.mergeNodeAttributes(id, { x: attrs.x + dx, y: attrs.y + dy });
        });
        if (row.label && label)
            graph.mergeNodeAttributes(row.label, {
                x: graph.getNodeAttribute(row.label, 'x') + x + sampleWidth + 20 - (label.left + dx),
                y: graph.getNodeAttribute(row.label, 'y') + top + height / 2 - ((label.top + label.bottom) / 2 + dy),
            });
        top += height + 12;
    });
}

export const insertedSelection = (insertion: Insertion) =>
    new Set([...insertion.graph.nodes(), ...insertion.graph.edges()] as Id[]);
