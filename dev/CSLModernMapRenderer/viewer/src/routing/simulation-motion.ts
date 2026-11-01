import {cumulative, pointAt} from './geometry';
import type {RouteResult, XYZ} from './types';

export const angularDelta = (from: number, to: number): number => ((to-from+540)%360+360)%360-180;
export function turnToward(from: number, to: number, seconds: number, tau = .28, maxRate = 100): number {
  const delta = angularDelta(from, to)*(1-Math.exp(-seconds/tau));
  return from+Math.max(-maxRate*seconds, Math.min(maxRate*seconds, delta));
}
export const advance = (meters: number, kmh: number, seconds: number, total: number): number =>
  Math.min(total, meters+kmh/3.6*seconds);

export function approachZoom(base: number, turnDistance: number): number {
  const t = Math.max(0, Math.min(1, (turnDistance-120)/480));
  return Math.max(14, base-1.2*t*t*(3-2*t));
}

export function routeTrack(points: XYZ[]) {
  const lengths = cumulative(points), total = lengths.at(-1)!;
  const at = (meters: number): XYZ => pointAt(points, lengths, total > 0 ? meters/total : 0);
  const heading = (meters: number, window = 18): number => {
    const a = at(Math.max(0, meters-window/2)), b = at(Math.min(total, meters+window/2));
    // 与 geo() 一致：游戏 +x 为北、-z 为东。
    return Math.atan2(-(b[2]-a[2]), b[0]-a[0])*180/Math.PI;
  };
  return {total, at, heading, straightAhead(meters: number): number {
    const ahead = Math.min(600, total-meters), initial = heading(meters);
    for (let d = 30; d < ahead; d += 30)
      if (Math.abs(angularDelta(initial, heading(meters+d))) >= 25) return d;
    return ahead;
  }};
}

export function nextManeuver(route: RouteResult, meters: number) {
  let boundary = 0;
  for (let i = 0; i < route.maneuvers.length-1; i++) {
    boundary += route.maneuvers[i].distanceM;
    const next = route.maneuvers[i+1];
    if (boundary > meters+.2 && ['left', 'right', 'arrive'].includes(next.kind))
      return {...next, remainingM: boundary-meters};
  }
  return {kind: 'arrive', roadName: '', remainingM: Math.max(0, route.distanceM-meters)};
}
