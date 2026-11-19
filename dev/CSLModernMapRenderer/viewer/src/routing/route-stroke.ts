import {cumulative, distance, geo, slice} from './geometry';
import type {XYZ} from './types';

export type RouteStroke = {points: XYZ[]; properties: Record<string, unknown>};
const roadModes = new Set(['GROUND', 'ELEVATED', 'TUNNEL']);
const width = (part: RouteStroke): number => Number(part.properties.surfaceWidth || part.properties.physicalWidth || 0);

export function renderRouteFeatures(features: GeoJSON.Feature[], zoom: number): GeoJSON.Feature[] {
  // 本地地图位于赤道附近；经纬度距离对应屏幕距离。只简化中间点，不能丢弃短线段。
  const toleranceSquared = (.375 * 360 / (512 * 2**Math.floor(zoom)))**2;
  return features.map(feature => {
    if (feature.geometry.type !== 'LineString' || !roadModes.has(String(feature.properties?.mode))) return feature;
    const points = feature.geometry.coordinates, last = points.length-1;
    if (last < 2) return feature;
    const keep = new Set([0, last]), pending = [[0, last]];
    while (pending.length) {
      const [start, end] = pending.pop()!, a = points[start], b = points[end];
      const dx = b[0]-a[0], dy = b[1]-a[1], span = dx*dx+dy*dy;
      let maximum = toleranceSquared, furthest = -1;
      for (let i = start+1; i < end; i++) {
        const p = points[i], t = span ? Math.max(0, Math.min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/span)) : 0;
        const distanceSquared = (p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2;
        if (distanceSquared > maximum) { maximum = distanceSquared; furthest = i; }
      }
      if (furthest >= 0) { keep.add(furthest); pending.push([start, furthest], [furthest, end]); }
    }
    return {...feature, geometry: {...feature.geometry, coordinates: points.filter((_, i) => keep.has(i))}};
  });
}

export function routeStrokeFeatures(parts: RouteStroke[], maxLength = Infinity): GeoJSON.Feature[] {
  let offset = 0;
  return parts.flatMap((part, index) => {
    const lengths = cumulative(part.points), total = lengths.at(-1)!, base = width(part);
    if (index) offset += distance(parts[index-1].points.at(-1)!, part.points[0]);
    const routeStart = offset; offset += total;
    const neighborAt = (neighbor: RouteStroke | undefined, atStart: boolean): RouteStroke | undefined => {
      if (!neighbor || !roadModes.has(String(neighbor.properties.mode))) return;
      const a = atStart ? part.points[0] : part.points.at(-1)!;
      const b = atStart ? neighbor.points.at(-1)! : neighbor.points[0];
      return distance(a, b) < 1e-6 ? neighbor : undefined;
    };
    const previous = neighborAt(parts[index-1], true), next = neighborAt(parts[index+1], false);
    const before = Math.max(base, previous ? width(previous) : base), after = Math.max(base, next ? width(next) : base);
    const congestion = part.properties.congestion ?? -1;
    const feature = (points: XYZ[], scale = 1, trafficNeighbor: unknown = congestion, trafficBlend = 0,
      start = 0, end = total): GeoJSON.Feature => ({type: 'Feature',
      properties: {...part.properties, physicalWidth: Number(part.properties.physicalWidth ?? 0)*scale,
        surfaceWidth: Number(part.properties.surfaceWidth ?? 0)*scale, trafficNeighbor, trafficBlend,
        routeStart: routeStart+start, routeEnd: routeStart+end},
      geometry: {type: 'LineString', coordinates: points.map(geo)}});
    if (!roadModes.has(String(part.properties.mode)) || total <= 0)
      return [feature(part.points)];
    // 沿用道路预览的短渐宽方式：只加宽窄侧，最多占本段的 45%，分为八片。
    const span = (target: number) => base > 0 && target > base ? Math.min(Math.max(12, Math.min(30, 1.5*target)), total*.45) : 0;
    const head = span(before), tail = span(after), cuts = [0, total];
    for (let at = maxLength; at < total; at += maxLength) cuts.push(at);
    if (head) for (let i = 1; i <= 8; i++) cuts.push(head*i/8);
    if (tail) for (let i = 0; i < 8; i++) cuts.push(total-tail+tail*i/8);
    // 两侧各自从原色混到交界的等比例混色；短路段保留中间的原色。
    const colorSpan = Math.min(30, total*.45);
    const colorHead = previous && previous.properties.congestion !== congestion ? colorSpan : 0;
    const colorTail = next && next.properties.congestion !== congestion ? colorSpan : 0;
    if (colorHead) for (let i = 1; i <= 16; i++) cuts.push(colorHead*i/16);
    if (colorTail) for (let i = 0; i < 16; i++) cuts.push(total-colorTail+colorTail*i/16);
    const sorted = [...new Set(cuts)].sort((a, b) => a-b);
    return sorted.slice(1).map((end, i) => {
      const start = sorted[i], middle = (start+end)/2;
      const w = middle < head ? before+(base-before)*middle/head
        : middle > total-tail ? base+(after-base)*(middle-(total-tail))/tail : base;
      const neighbor = middle < colorHead ? previous : middle > total-colorTail ? next : undefined;
      const blend = middle < colorHead ? .5*(1-middle/colorHead)
        : middle > total-colorTail ? .5*(1-(total-middle)/colorTail) : 0;
      return feature(slice(part.points, lengths, start/total, end/total), base > 0 ? w/base : 1,
        neighbor?.properties.congestion ?? congestion, blend, start, end);
    });
  });
}
