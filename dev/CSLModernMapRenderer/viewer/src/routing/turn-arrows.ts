import {cumulative, distance, geo, pointAt, slice} from './geometry';
import type {Edge, NavigationPackage, RouteResult, XYZ} from './types';
import type {ExpressionSpecification} from 'maplibre-gl';
import polygonClipping from 'polygon-clipping';

const metersPerPixelAtZero = 78271.51696402048*111195/(2*Math.PI*6378137/360);
export const arrowOutlineWidth: ExpressionSpecification = ['interpolate', ['exponential', 2], ['zoom'],
  ...Array.from({length: 25}, (_, z) => [z, ['max', 2.5, ['*', ['get', 'arrowWidth'], .28*2**z/metersPerPixelAtZero]]]).flat()] as ExpressionSpecification;

export type RouteTurn = {meters: number; edgeIndex: number; kind: 'left' | 'right' | 'continue'};
type Point = [number, number];
const gap = (a: Point, b: Point) => Math.hypot(a[0]-b[0], a[1]-b[1]);
const mix = (a: Point, b: Point, t: number): Point => [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t];

export function routeTurns(data: NavigationPackage, route: RouteResult): RouteTurn[] {
  if (route.costProfile === 'transit') return [];
  const byId = new Map(data.edges.map(edge => [edge.id, edge]));
  const arms = new Map<string, Set<string>>(), outgoing = new Map<string, Edge[]>();
  for (const edge of data.edges) {
    for (const node of [edge.fromNode, edge.toNode]) {
      if (!arms.has(node)) arms.set(node, new Set());
      arms.get(node)!.add(edge.geometryRef);
    }
    if (!outgoing.has(edge.fromNode)) outgoing.set(edge.fromNode, []);
    outgoing.get(edge.fromNode)!.push(edge);
  }
  const turns: RouteTurn[] = [];
  let previous: Edge | undefined, previousPoints: XYZ[] = [], meters = 0;
  for (const [edgeIndex, traversal] of route.traversals.entries()) {
    const edge = byId.get(traversal.edgeId)!, geometry = data.geometries[edge.geometryRef];
    const points = slice(geometry, cumulative(geometry), edge.reverse ? 1-traversal.fromFraction : traversal.fromFraction,
      edge.reverse ? 1-traversal.toFraction : traversal.toFraction);
    if (previous) {
      meters += distance(previousPoints.at(-1)!, points[0]);
      const beforeLengths = cumulative(previousPoints), afterLengths = cumulative(points);
      const before = pointAt(previousPoints, beforeLengths, Math.max(0, 1-15/Math.max(15, beforeLengths.at(-1)!)));
      const at = previousPoints.at(-1)!;
      const angleTo = (next: XYZ) => Math.atan2((at[0]-before[0])*(next[2]-at[2])-(at[2]-before[2])*(next[0]-at[0]),
        (at[0]-before[0])*(next[0]-at[0])+(at[2]-before[2])*(next[2]-at[2]))*180/Math.PI;
      const angle = angleTo(pointAt(points, afterLengths, Math.min(1, 15/Math.max(15, afterLengths.at(-1)!))));
      const junction = arms.get(edge.fromNode)!.size >= 3;
      const renamed = !!previous.name.trim() && !!edge.name.trim() && previous.name.trim() !== edge.name.trim();
      // 小角度岔出需要存在更接近直行的出口，不能把无名路的分段当成换路。
      const fork = junction && Math.abs(angle) >= 15 && (outgoing.get(edge.fromNode) ?? []).some(other => {
        if (other.geometryRef === edge.geometryRef || other.sourceSegmentId === previous!.sourceSegmentId) return false;
        const line = data.geometries[other.geometryRef], oriented = other.reverse ? [...line].reverse() : line;
        return Math.abs(angleTo(pointAt(oriented, cumulative(oriented), Math.min(1, 15/Math.max(15, other.lengthM)))))+10 < Math.abs(angle);
      });
      if (renamed || junction && Math.abs(angle) >= 30 || fork)
        turns.push({meters, edgeIndex, kind: Math.abs(angle) >= 15 ? angle > 0 ? 'left' : 'right' : 'continue'});
    }
    meters += cumulative(points).at(-1)!;
    previous = edge; previousPoints = points;
  }
  return turns;
}

// 对原中心线做等距偏移，拐点使用偏移线交点；只圆化轮廓，不平滑或截弯路线。
export function arrowPolygons(input: Point[], width: number): Point[][][] {
  const points = input.filter((p, i) => !i || gap(p, input[i-1]) > 1e-6);
  if (points.length < 2) return [];
  const lengths = [0];
  for (let i = 1; i < points.length; i++) lengths.push(lengths[i-1]+gap(points[i-1], points[i]));
  const total = lengths.at(-1)!, neckDistance = total-width*1.25;
  if (total < width*3) return [];
  let neckIndex = 1;
  while (lengths[neckIndex] < neckDistance) neckIndex++;
  const neck = mix(points[neckIndex-1], points[neckIndex],
    (neckDistance-lengths[neckIndex-1])/(lengths[neckIndex]-lengths[neckIndex-1]));
  const shaft = [...points.slice(0, neckIndex), neck];
  const normals: Point[] = shaft.slice(1).map((p, i) => {
    const a = shaft[i], span = gap(a, p);
    return [-(p[1]-a[1])/span, (p[0]-a[0])/span];
  });
  const left: Point[] = [], right: Point[] = [];
  for (let i = 0; i < shaft.length; i++) {
    const p = shaft[i], a = normals[Math.max(0, i-1)], b = normals[Math.min(i, normals.length-1)];
    const denominator = 1+a[0]*b[0]+a[1]*b[1];
    // 回折空间不足时不绘制自交的轮廓。
    if (denominator < .08) return [];
    const offset: Point = [(a[0]+b[0])*width/2/denominator, (a[1]+b[1])*width/2/denominator];
    left.push([p[0]+offset[0], p[1]+offset[1]]);
    right.push([p[0]-offset[0], p[1]-offset[1]]);
  }
  const tip = points.at(-1)!, beforeTip = points.at(-2)!, finalSpan = gap(beforeTip, tip);
  const forward: Point = [(tip[0]-beforeTip[0])/finalSpan, (tip[1]-beforeTip[1])/finalSpan];
  const normal: Point = [-forward[1], forward[0]];
  const headBase: Point = [tip[0]-forward[0]*width*1.25, tip[1]-forward[1]*width*1.25];
  left.push([headBase[0]+normal[0]*width/2, headBase[1]+normal[1]*width/2]);
  right.push([headBase[0]-normal[0]*width/2, headBase[1]-normal[1]*width/2]);
  const vertices: Point[] = [...left,
    [headBase[0]+normal[0]*width*.75, headBase[1]+normal[1]*width*.75], tip,
    [headBase[0]-normal[0]*width*.75, headBase[1]-normal[1]*width*.75], ...right.reverse()];
  const ring: Point[] = [];
  const outline = vertices.filter((p, i) => !i || gap(p, vertices[i-1]) > 1e-6);
  for (let i = 0; i < outline.length; i++) {
    const b = outline[i], a = outline[(i+outline.length-1)%outline.length], c = outline[(i+1)%outline.length];
    const radius = Math.min(width*.125, gap(a, b)/2, gap(b, c)/2);
    const entry = mix(b, a, radius/gap(a, b)), exit = mix(b, c, radius/gap(b, c));
    ring.push(entry);
    for (let j = 1; j <= 4; j++) ring.push(mix(mix(entry, b, j/4), mix(b, exit, j/4), j/4));
  }
  ring.push(ring[0]);
  // 密集短段可能令偏移边缘相交；按非零填充规则清理后再交给原生填充图层。
  return polygonClipping.union([ring]);
}

export function turnArrowFeatures(data: NavigationPackage, route: RouteResult,
  widthMeters: (edge: Edge) => number): GeoJSON.Feature[] {
  const turns = routeTurns(data, route), byId = new Map(data.edges.map(edge => [edge.id, edge]));
  const lengths = cumulative(route.geometry), total = lengths.at(-1)!;
  return turns.flatMap((turn, i) => {
    const incoming = byId.get(route.traversals[turn.edgeIndex-1].edgeId)!, outgoing = byId.get(route.traversals[turn.edgeIndex].edgeId)!;
    const width = Math.max(widthMeters(incoming), widthMeters(outgoing))*1.35;
    const start = Math.max(turn.meters-width*4, i ? (turns[i-1].meters+turn.meters)/2 : 0);
    const end = Math.min(turn.meters+width*2.75, i+1<turns.length ? (turn.meters+turns[i+1].meters)/2 : total);
    if (turn.meters-start < width || end-turn.meters < width*1.6) return [];
    const polygons = arrowPolygons(slice(route.geometry, lengths, start/total, end/total).map(p => [p[0], p[2]]), width);
    if (!polygons.length) return [];
    // 小比例尺下让出路网；放大后由原生投影恢复，不在相机事件中写源。
    const minZoom = Math.max(15, Math.ceil(Math.log2(8*metersPerPixelAtZero/width)));
    return [{type: 'Feature' as const, id: `turn-${i}`, properties: {mode: 'TURN_ARROW', passedAt: turn.meters+width*.5, minZoom, arrowWidth: width},
      geometry: {type: 'MultiPolygon' as const, coordinates: polygons.map(polygon => polygon.map(outline =>
        outline.map(p => geo([p[0], 0, p[1]]))))}}];
  });
}
