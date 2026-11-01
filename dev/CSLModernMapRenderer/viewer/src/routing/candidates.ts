import {isBoarding} from './itinerary';
import type {Edge, RouteResult, TransitCandidate, TransitCost, TransitPreference} from './types';

// IEEE-754 位序列避免 JS 与 Python 对小数指数和尾零的 JSON 表达不同。
function fractionKey(value: number): string {
  const bytes = new Uint8Array(8);
  new DataView(bytes.buffer).setFloat64(0, value === 0 ? 0 : value);
  return [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export function trajectorySignature(route: RouteResult): string {
  return JSON.stringify(route.traversals.map(part => [part.edgeId, fractionKey(part.fromFraction), fractionKey(part.toFraction)]));
}

export async function candidateId(graphRevision: string, signature: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${graphRevision}\n${signature}`));
  return 'transit:' + [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function itinerarySignature(route: RouteResult): string {
  if (!route.boardings) return 'walking-only';
  return JSON.stringify(route.legs!.map(leg => leg.mode === 'ride'
    ? ['ride', leg.lineId, leg.routeId, leg.stopSequence!.map(stop => [stop.stopId, stop.stopIndex])]
    : ['walk', leg.walkKind, leg.fromStopRef ? [leg.fromStopRef.stopId, leg.fromStopRef.stopIndex] : null,
      leg.toStopRef ? [leg.toStopRef.stopId, leg.toStopRef.stopIndex] : null]));
}

export function transitScore(route: RouteResult, edges: Map<string, Edge>, cost: TransitCost): number {
  let total = 0, previous: Edge | undefined;
  for (const part of route.traversals) {
    const edge = edges.get(part.edgeId)!;
    total += (part.toFraction-part.fromFraction)*edge.lengthM*(edge.mode === 'RIDE' ? 1 : cost.walkFactor)
      + (isBoarding(previous, edge) ? cost.boardingCost : 0);
    previous = edge;
  }
  return total;
}

export function compareCandidates(a: TransitCandidate, b: TransitCandidate, preference: TransitPreference = 'balanced'): number {
  const first = preference === 'less-walking' ? a.walkingDistanceM!-b.walkingDistanceM!
    : preference === 'fewer-transfers' ? a.boardings!-b.boardings! : 0;
  if (first) return first;
  const metrics = a.balancedCost-b.balancedCost || a.walkingDistanceM!-b.walkingDistanceM!
    || a.boardings!-b.boardings! || a.distanceM-b.distanceM;
  if (metrics) return metrics;
  const left = trajectorySignature(a), right = trajectorySignature(b);
  return left < right ? -1 : left > right ? 1 : 0;
}

export async function transitCandidates(results: Array<{route: RouteResult; preference: TransitPreference}>,
  graphRevision: string, edges: Map<string, Edge>, balanced: TransitCost): Promise<TransitCandidate[]> {
  const exact = new Map<string, TransitCandidate>();
  for (const {route, preference} of results) {
    const signature = trajectorySignature(route), previous = exact.get(signature);
    if (previous) { previous.generatedBy.push(preference); continue; }
    exact.set(signature, {...route, candidateId: await candidateId(graphRevision, signature),
      generatedBy: [preference], balancedCost: transitScore(route, edges, balanced)});
  }
  const itineraries = new Set<string>();
  return [...exact.values()].sort(compareCandidates).filter(route => {
    const signature = itinerarySignature(route);
    if (itineraries.has(signature)) return false;
    itineraries.add(signature); return true;
  });
}
