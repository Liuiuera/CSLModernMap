import type {Edge, NavigationPackage, RouteResult, TransitLeg, TransitStop} from './types';

export function isBoarding(from: Edge | undefined, to: Edge): boolean {
  return to.mode === 'RIDE' && !(from?.mode === 'RIDE' && from.lineId === to.lineId
    && from.routeId === to.routeId && from.toStopIndex === to.fromStopIndex);
}

export function itinerary(data: NavigationPackage, traversals: RouteResult['traversals']) {
  const edges = new Map(data.edges.map(edge => [edge.id, edge]));
  const legs: TransitLeg[] = [];
  let previous: Edge | undefined, boardings = 0, walkingDistanceM = 0;
  const stop = (edge: Edge, side: 'from' | 'to'): TransitStop => {
    const node = side === 'from' ? edge.fromNode : edge.toNode;
    return {stopId: node.slice('stop:'.length), stopIndex: edge[`${side}StopIndex`]!,
      name: edge[`${side}Stop`]!, position: data.nodes[node]};
  };
  traversals.forEach((part, index) => {
    const edge = edges.get(part.edgeId)!, boarding = isBoarding(previous, edge);
    if (boarding) boardings++;
    const mode = edge.mode === 'RIDE' ? 'ride' : 'walk';
    const length = (part.toFraction-part.fromFraction)*edge.lengthM;
    if (mode === 'walk') walkingDistanceM += length;
    let leg = legs.at(-1);
    if (leg && leg.mode === mode && !boarding) {
      leg.distanceM += length; leg.traversalEnd = index+1;
    } else {
      leg = {mode, name: mode === 'ride' ? edge.name : '', stops: 0, distanceM: length,
        traversalStart: index, traversalEnd: index+1};
      if (mode === 'ride') Object.assign(leg, {lineId: edge.lineId, routeId: edge.routeId,
        transitKind: edge.transitKind, fromStop: edge.fromStop, color: edge.color,
        schematic: false, stopSequence: [stop(edge, 'from')]});
      legs.push(leg);
    }
    if (mode === 'ride') {
      leg.toStop = edge.toStop; leg.stops++; leg.schematic ||= edge.schematic;
      leg.stopSequence!.push(stop(edge, 'to'));
    }
    previous = edge;
  });
  legs.forEach((leg, index) => {
    if (leg.mode !== 'walk') return;
    const before = legs[index-1], after = legs[index+1];
    leg.walkKind = before && after ? 'transfer' : before ? 'egress' : after ? 'access' : 'walking-only';
    if (before) leg.fromStopRef = before.stopSequence!.at(-1);
    if (after) leg.toStopRef = after.stopSequence![0];
  });
  return {itineraryVersion: 1 as const, legs, boardings, walkingDistanceM};
}
