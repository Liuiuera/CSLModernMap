import createGraph from 'ngraph.graph';
import {aStar} from 'ngraph.path';
import Flatbush from 'flatbush';
import {isBoarding, itinerary} from './itinerary';
import {transitCandidates} from './candidates';
import {cumulative, distance, planarDistance, pointAt, slice} from './geometry';
import {RoutingError} from './types';
import type {Edge, Maneuver, NavigationPackage, RouteResult, RoutingProfile, Snap, TransitCandidate, TransitCost, TransitPreference, XYZ} from './types';

type State = {position: XYZ};
type Cost = {cost: number};
type Segment = {ref: string; index: number};
const START = '@origin', GOAL = '@destination';
const DURATION_WAIT_FACTOR = 1.3;
const finitePoint = (p: unknown): p is XYZ => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite);

export class Router {
  readonly graph = createGraph<State, Cost>();
  readonly transitionCount: number;
  private readonly edges = new Map<string, Edge>();
  private readonly byGeometry = new Map<string, Edge[]>();
  private readonly incoming = new Map<string, Edge[]>();
  private readonly outgoing = new Map<string, Edge[]>();
  private readonly junctions = new Set<string>();
  private readonly lengths = new Map<string, number[]>();
  private readonly segments: Segment[] = [];
  private readonly index: Flatbush | null;
  private readonly exactNodeGeometry: boolean;
  private profile: RoutingProfile = 'car-shortest';
  private minimumCostPerMeter = 1;
  private transitRouter?: Router;
  private transitCost!: TransitCost;
  private costConfigId?: TransitPreference;

  private setTransitCost(config: TransitPreference = 'balanced'): void {
    const model = this.data.costModel!;
    if (model.id === 'transit-roads-v1') {
      if (config !== 'balanced') throw new RoutingError('INVALID_QUERY', 'Unsupported legacy transit preference');
      this.transitCost = {walkFactor: model.walkFactor!, boardingCost: model.boardingCost!};
      this.costConfigId = undefined;
    } else {
      if (!Object.hasOwn(model.preferences!, config)) throw new RoutingError('INVALID_QUERY', 'Unknown transit preference');
      this.transitCost = model.preferences![config]; this.costConfigId = config;
    }
  }

  private boardingCost(from: Edge | undefined, to: Edge): number {
    return this.transitNetwork && isBoarding(from, to) ? this.transitCost.boardingCost : 0;
  }

  private edgeCost(edge: Edge): number {
    return edge.lengthM * (this.transitNetwork ? edge.mode === 'RIDE' ? 1 : this.transitCost.walkFactor
      : this.profile === 'car-balanced' ? edge.recommendedCostPerMeter ?? 1 : 1);
  }

  constructor(readonly data: NavigationPackage, private readonly transitNetwork = false) {
    if (transitNetwork) {
      const model = data.costModel;
      const valid = (cost: TransitCost | undefined, legacy = false) => cost
        && Number.isFinite(cost.walkFactor) && cost.walkFactor >= 1
        && Number.isFinite(cost.boardingCost) && (legacy ? cost.boardingCost >= 0 : cost.boardingCost > 0);
      if (!model || (model.id === 'transit-roads-v1'
        ? !valid(model as TransitCost, true)
        : model.id !== 'transit-roads-v2' || !model.preferences
          || Object.keys(model.preferences).length !== 3
          || !(['balanced', 'less-walking', 'fewer-transfers'] as const).every(key => valid(model.preferences![key]))))
        throw new RoutingError('DATA_INCOMPLETE', 'Invalid transit cost model');
      this.setTransitCost();
    }
    if (data.routingSchemaVersion !== 1 || !data.snapshotId || !data.graphRevision
        || !data.costRevision || !data.nodes || !data.geometries || !Array.isArray(data.edges)
        || data.durationModel !== undefined && !['car-speed-limit-v1', 'car-speed-traffic-v2'].includes(data.durationModel))
      throw new RoutingError('DATA_INCOMPLETE');
    for (const [ref, points] of Object.entries(data.geometries)) {
      if (!Array.isArray(points) || points.length < 2 || !points.every(finitePoint))
        throw new RoutingError('DATA_INCOMPLETE', `Invalid geometry: ${ref}`);
      const lengths = cumulative(points);
      if (!transitNetwork && lengths.at(-1)! <= 0) throw new RoutingError('DATA_INCOMPLETE', `Zero length: ${ref}`);
      this.lengths.set(ref, lengths);
    }
    this.graph.beginUpdate();
    let exactNodeGeometry = true;
    for (const edge of data.edges) {
      const from = data.nodes[edge.fromNode], to = data.nodes[edge.toNode];
      const points = data.geometries[edge.geometryRef];
      if (typeof edge.id !== 'string' || (!transitNetwork && !edge.id.startsWith('road:')) || this.edges.has(edge.id)
          || typeof edge.sourceSegmentId !== 'string' || typeof edge.reverse !== 'boolean'
          || !finitePoint(from) || !finitePoint(to)
          || !points || edge.modeAccess !== 'allowed' || !edge.directionEvidence
          || (transitNetwork && !['WALK', 'ACCESS', 'RIDE'].includes(edge.mode))
          || (transitNetwork && edge.mode === 'RIDE' && (
            !edge.fromNode.startsWith('stop:') || !edge.toNode.startsWith('stop:')
            || !edge.lineId || !edge.routeId || typeof edge.transitKind !== 'string'
            || !Number.isInteger(edge.fromStopIndex) || edge.fromStopIndex! < 0
            || !Number.isInteger(edge.toStopIndex) || edge.toStopIndex! < 0
            || typeof edge.fromStop !== 'string' || typeof edge.toStop !== 'string'
            || typeof edge.color !== 'string' || typeof edge.schematic !== 'boolean'))
          || !Number.isFinite(edge.lengthM) || edge.lengthM < 0 || (!transitNetwork && edge.lengthM === 0)
          || (edge.widthM !== undefined && (!Number.isFinite(edge.widthM) || edge.widthM < 0))
          || (edge.recommendedCostPerMeter !== undefined && (!Number.isFinite(edge.recommendedCostPerMeter) || edge.recommendedCostPerMeter <= 0))
          || (data.costModel?.id === 'car-balanced-v1' && edge.recommendedCostPerMeter === undefined)
          || (edge.congestion != null && (!Number.isFinite(edge.congestion) || edge.congestion < 0 || edge.congestion > 1))
          || (edge.speedLimitMps != null && (!Number.isFinite(edge.speedLimitMps) || edge.speedLimitMps <= 0))
          || Math.abs(edge.lengthM-this.lengths.get(edge.geometryRef)!.at(-1)!) > .01
          || distance(from, points[edge.reverse ? points.length-1 : 0]) > 1
          || distance(to, points[edge.reverse ? 0 : points.length-1]) > 1)
        throw new RoutingError('DATA_INCOMPLETE', `Invalid edge: ${edge.id}`);
      this.edges.set(edge.id, edge);
      this.minimumCostPerMeter = Math.min(this.minimumCostPerMeter, edge.recommendedCostPerMeter ?? 1);
      exactNodeGeometry &&= distance(from, points[edge.reverse ? points.length-1 : 0]) < 1e-8
        && distance(to, points[edge.reverse ? 0 : points.length-1]) < 1e-8;
      for (const [directory, key] of [[this.incoming, edge.toNode], [this.outgoing, edge.fromNode],
        [this.byGeometry, edge.geometryRef]] as const) {
        const values = directory.get(key) ?? [];
        values.push(edge); directory.set(key, values);
      }
      this.graph.addNode(edge.id, {position: to});
    }
    let transitionCount = 0;
    for (const edge of data.edges) {
      for (const next of this.outgoing.get(edge.toNode) ?? []) {
        if (this.turnAllowed(edge, next)) transitionCount++;
      }
    }
    this.transitionCount = transitionCount;
    const visitVirtualLinks = this.graph.forEachLinkedNode;
    // A* 只枚举当前到达方向的转移，避免常驻每个路口的入度×出度对象。
    this.graph.forEachLinkedNode = (id, visit, oriented) => {
      const edge = this.edges.get(String(id));
      if (edge) for (const next of this.outgoing.get(edge.toNode) ?? []) {
        if (this.turnAllowed(edge, next)) visit(this.graph.getNode(next.id)!, {
          id: `${edge.id}>${next.id}`, fromId: edge.id, toId: next.id, data: {cost: this.edgeCost(next)+this.boardingCost(edge, next)},
        });
      }
      visitVirtualLinks(id, visit, oriented);
    };
    this.graph.endUpdate();
    this.exactNodeGeometry = exactNodeGeometry;
    const arms = new Map<string, number>();
    // 双向边只算一条物理道路；自环在同一节点有两个路口分支。
    for (const [edge] of this.byGeometry.values()) {
      for (const node of [edge.fromNode, edge.toNode]) arms.set(node, (arms.get(node) ?? 0) + 1);
    }
    for (const [node, count] of arms) if (count >= 3) this.junctions.add(node);
    for (const [ref] of this.byGeometry) {
      if (this.lengths.get(ref)!.at(-1) === 0 || transitNetwork && this.byGeometry.get(ref)![0].mode !== 'WALK') continue;
      for (let i = 1; i < data.geometries[ref].length; i++) this.segments.push({ref, index: i});
    }
    this.index = this.segments.length ? new Flatbush(this.segments.length) : null;
    for (const {ref, index} of this.segments) {
      const [a, b] = [data.geometries[ref][index-1], data.geometries[ref][index]];
      this.index!.add(Math.min(a[0], b[0]), Math.min(a[2], b[2]), Math.max(a[0], b[0]), Math.max(a[2], b[2]));
    }
    this.index?.finish();
  }

  private turnAllowed(from: Edge, to: Edge): boolean {
    return from.toNode === to.fromNode
      && (this.transitNetwork || from.sourceSegmentId !== to.sourceSegmentId || from.reverse === to.reverse);
  }

  private transit(): Router {
    if (!this.data.transit) throw new RoutingError('TRANSIT_UNAVAILABLE');
    return this.transitRouter ??= new Router({...this.data, ...this.data.transit, durationModel: undefined, transit: undefined}, true);
  }

  async candidates(origin: Snap, destination: Snap): Promise<TransitCandidate[]> {
    if (!this.transitNetwork) return this.transit().candidates(origin, destination);
    const preferences: TransitPreference[] = this.data.costModel!.id === 'transit-roads-v2'
      ? ['balanced', 'less-walking', 'fewer-transfers'] : ['balanced'];
    const results = preferences.map(preference => ({preference, route: this.route(origin, destination, false, 'transit', preference)}));
    this.setTransitCost('balanced');
    return transitCandidates(results, this.data.graphRevision, this.edges, this.transitCost);
  }

  snap(position: XYZ, profile: RoutingProfile = 'car-shortest'): Snap[] {
    if (profile === 'transit' && !this.transitNetwork) return this.transit().snap(position);
    if (!finitePoint(position)) throw new RoutingError('INVALID_ENDPOINT');
    if (!this.index) throw new RoutingError('DATA_INCOMPLETE');
    const candidates = new Map<string, Snap>();
    for (const radius of [30, 100, 300]) {
      for (const id of this.index.search(position[0]-radius, position[2]-radius, position[0]+radius, position[2]+radius)) {
        const {ref, index} = this.segments[id];
        const points = this.data.geometries[ref], a = points[index-1], b = points[index];
        const dx = b[0]-a[0], dz = b[2]-a[2], squared = dx*dx+dz*dz;
        const t = squared > 0 ? Math.max(0, Math.min(1, ((position[0]-a[0])*dx+(position[2]-a[2])*dz)/squared)) : 0;
        const projected = a.map((v, k) => v + t*(b[k]-v)) as XYZ;
        const distanceM = planarDistance(position, projected);
        if (distanceM > radius || (candidates.get(ref)?.distanceM ?? Infinity) <= distanceM) continue;
        const edges = this.byGeometry.get(ref)!;
        const lengths = this.lengths.get(ref)!;
        candidates.set(ref, {geometryRef: ref,
          fraction: (lengths[index-1]+t*(lengths[index]-lengths[index-1]))/lengths.at(-1)!,
          position: projected, distanceM, name: edges[0].name, mode: edges[0].roadMode ?? edges[0].mode,
          direction: edges.length > 1 ? 'both' : edges[0].reverse ? 'backward' : 'forward'});
      }
      if (candidates.size) break;
    }
    if (!candidates.size) throw new RoutingError('SNAP_NOT_FOUND');
    return [...candidates.values()].sort((a, b) => a.distanceM-b.distanceM || a.geometryRef.localeCompare(b.geometryRef)).slice(0, 8);
  }

  route(origin: Snap, destination: Snap, knownRulesOnly = false, profile: RoutingProfile = 'car-shortest', costConfigId?: TransitPreference): RouteResult {
    if (profile === 'transit' && !this.transitNetwork) return this.transit().route(origin, destination, knownRulesOnly, profile, costConfigId);
    if (this.transitNetwork) this.setTransitCost(costConfigId);
    else if (costConfigId !== undefined) throw new RoutingError('INVALID_QUERY');
    if (knownRulesOnly) throw new RoutingError('RULE_COVERAGE_INCOMPLETE');
    if (profile !== 'car-shortest' && !(profile === 'transit' && this.transitNetwork)
        && (profile !== 'car-balanced' || this.data.costModel?.id !== 'car-balanced-v1'))
      throw new RoutingError('INVALID_QUERY');
    this.profile = profile;
    for (const snap of [origin, destination]) {
      if (!this.byGeometry.has(snap.geometryRef) || !Number.isFinite(snap.fraction) || snap.fraction < 0 || snap.fraction > 1)
        throw new RoutingError('INVALID_ENDPOINT');
      if (this.transitNetwork && this.byGeometry.get(snap.geometryRef)![0].mode !== 'WALK') throw new RoutingError('INVALID_ENDPOINT');
    }
    const startEdges = this.byGeometry.get(origin.geometryRef)!;
    const endEdges = this.byGeometry.get(destination.geometryRef)!;
    const startPosition = pointAt(this.data.geometries[origin.geometryRef], this.lengths.get(origin.geometryRef)!, origin.fraction);
    const endPosition = pointAt(this.data.geometries[destination.geometryRef], this.lengths.get(destination.geometryRef)!, destination.fraction);
    const endpointNode = (snap: Snap, edge: Edge): string | undefined => snap.fraction === 0
      ? edge.reverse ? edge.toNode : edge.fromNode : snap.fraction === 1
      ? edge.reverse ? edge.fromNode : edge.toNode : undefined;
    const startNode = endpointNode(origin, startEdges[0]), endNode = endpointNode(destination, endEdges[0]);
    if (origin.geometryRef === destination.geometryRef && Math.abs(origin.fraction-destination.fraction) < 1e-10
        || startNode !== undefined && startNode === endNode)
      return this.result([], [startPosition, endPosition]);
    this.graph.addNode(START, {position: startPosition});
    this.graph.addNode(GOAL, {position: endPosition});
    const initial = new Map<string, number>();
    const final = new Map<string, number>();
    try {
      for (const edge of startEdges) {
        const fraction = edge.reverse ? 1-origin.fraction : origin.fraction;
        this.graph.addLink(START, edge.id, {cost: (1-fraction)*this.edgeCost(edge)});
        initial.set(edge.id, fraction);
      }
      for (const edge of endEdges) {
        const fraction = edge.reverse ? 1-destination.fraction : destination.fraction;
        const terminal = `@terminal:${edge.id}`;
        this.graph.addNode(terminal, {position: endPosition});
        this.graph.addLink(terminal, GOAL, {cost: 0});
        final.set(terminal, fraction);
        for (const incoming of this.incoming.get(edge.fromNode) ?? []) {
          if (this.turnAllowed(incoming, edge)) this.graph.addLink(incoming.id, terminal, {cost: fraction*this.edgeCost(edge)});
        }
        if (fraction === 0) {
          for (const incoming of this.incoming.get(edge.fromNode) ?? [])
            this.graph.addLink(incoming.id, terminal, {cost: 0});
        }
        if (origin.geometryRef === destination.geometryRef) {
          const from = edge.reverse ? 1-origin.fraction : origin.fraction;
          if (fraction >= from) this.graph.addLink(START, terminal, {cost: (fraction-from)*this.edgeCost(edge)});
        }
        for (const startEdge of startEdges) {
          const from = initial.get(startEdge.id)!;
          if (from === 0 && startEdge.fromNode === edge.fromNode)
            this.graph.addLink(START, terminal, {cost: fraction*this.edgeCost(edge)});
        }
      }
      // 恰在路口的起点不携带虚构的入路方向。
      for (const edge of startEdges) {
        if (initial.get(edge.id) !== 0) continue;
        for (const outgoing of this.outgoing.get(edge.fromNode) ?? []) {
          this.graph.addLink(START, outgoing.id, {cost: this.edgeCost(outgoing)+this.boardingCost(undefined, outgoing)});
          initial.set(outgoing.id, 0);
        }
      }
      const lowerBound = this.profile === 'car-balanced' ? this.minimumCostPerMeter : 1;
      const finder = aStar(this.graph, {oriented: true, distance: (_a, _b, link) => link.data.cost,
        heuristic: (a, b) => this.exactNodeGeometry ? planarDistance(a.data.position, b.data.position)*lowerBound : 0});
      const path = finder.find(START, GOAL).reverse();
      if (!path.length) throw new RoutingError('NO_PATH');
      const traversals: RouteResult['traversals'] = [];
      for (let index = 1; index < path.length-1; index++) {
        const id = String(path[index].id), terminal = id.startsWith('@terminal:');
        const edgeId = terminal ? id.slice('@terminal:'.length) : id;
        const from = index === 1 ? initial.get(edgeId) ?? 0 : 0;
        const to = terminal ? final.get(id)! : 1;
        if (to-from > 1e-10) traversals.push({edgeId, fromFraction: from, toFraction: to});
      }
      return this.result(traversals, [startPosition, endPosition]);
    } finally {
      this.graph.removeNode(START); this.graph.removeNode(GOAL);
      for (const id of final.keys()) this.graph.removeNode(id);
    }
  }

  private result(traversals: RouteResult['traversals'], emptyGeometry: XYZ[] = []): RouteResult {
    const geometry: XYZ[] = [], maneuvers: Maneuver[] = [];
    const roadNames = new Set<string>();
    let previousPoints: XYZ[] = [];
    let previousEdge: Edge | undefined;
    const durationModel = this.transitNetwork ? null : this.data.durationModel ?? 'car-speed-traffic-v2';
    const trafficDuration = durationModel === 'car-speed-traffic-v2';
    let durationSeconds: number | null = durationModel ? 0 : null;
    let distanceM = 0, cost = 0, missingTraffic = 0, missingSpeed = 0, legacySpeed = 0;
    const trip = this.transitNetwork ? itinerary(this.data, traversals) : undefined;
    for (const traversal of traversals) {
      const edge = this.edges.get(traversal.edgeId)!;
      if (edge.name) roadNames.add(edge.name);
      const from = edge.reverse ? 1-traversal.fromFraction : traversal.fromFraction;
      const to = edge.reverse ? 1-traversal.toFraction : traversal.toFraction;
      const points = slice(this.data.geometries[edge.geometryRef], this.lengths.get(edge.geometryRef)!, from, to);
      const length = (traversal.toFraction-traversal.fromFraction)*edge.lengthM;
      const junction = previousEdge !== undefined && this.junctions.has(edge.fromNode);
      let kind: Maneuver['kind'] = maneuvers.length ? 'continue' : 'depart';
      if (junction && previousPoints.length > 1) {
        const previousLengths = cumulative(previousPoints), previousLength = previousLengths.at(-1)!;
        const a = pointAt(previousPoints, previousLengths, Math.max(0, 1-15/Math.max(previousLength, 15)));
        const b = previousPoints.at(-1)!, c = pointAt(points, cumulative(points), Math.min(1, 15/Math.max(length, 15)));
        const cross = (b[0]-a[0])*(c[2]-b[2])-(b[2]-a[2])*(c[0]-b[0]);
        const dot = (b[0]-a[0])*(c[0]-b[0])+(b[2]-a[2])*(c[2]-b[2]);
        const angle = Math.atan2(cross, dot)*180/Math.PI;
        if (Math.abs(angle) >= 30) kind = angle > 0 ? 'left' : 'right';
      }
      const last = maneuvers.at(-1);
      if (last && !junction) last.distanceM += length;
      else maneuvers.push({kind, roadName: edge.name, distanceM: length});
      for (const point of points) if (!geometry.length || distance(geometry.at(-1)!, point) > 1e-8) geometry.push(point);
      distanceM += length;
      cost += (traversal.toFraction-traversal.fromFraction)*this.edgeCost(edge);
      if (this.transitNetwork) {
        const boarding = this.boardingCost(previousEdge, edge);
        cost += boarding;
      }
      const limit = edge.speedLimitMps ?? 60/3.6;
      const speed = trafficDuration
        ? Math.min(limit, edge.congestion == null ? 60/3.6 : Math.max(5/3.6, limit*(1-edge.congestion))) : limit;
      if (durationSeconds !== null) durationSeconds += length/speed;
      if (edge.congestion == null) missingTraffic++;
      if (edge.speedLimitMps == null) missingSpeed++;
      if (edge.speedSource === 'cs1-prefab-legacy') legacySpeed++;
      previousPoints = points;
      previousEdge = edge;
    }
    if (durationSeconds !== null && trafficDuration) durationSeconds *= DURATION_WAIT_FACTOR;
    maneuvers.push({kind: 'arrive', roadName: '', distanceM: 0});
    if (geometry.length === 1) geometry.push(geometry[0]);
    return {status: 'ok', costProfile: this.profile, cost, durationSeconds, durationModel, roadNames: [...roadNames],
      ...trip,
      ...(this.costConfigId ? {costConfigId: this.costConfigId} : {}),
      costUnit: this.profile === 'car-shortest' ? 'meter' : 'preference-meter',
      edgeIds: traversals.map(t => t.edgeId), traversals, geometry: geometry.length ? geometry : emptyGeometry,
      distanceM, quality: this.transitNetwork ? {topology: 'native-road-nodes-and-exported-stops', direction: 'exported-stop-order',
        access: 'bidirectional-road-walking', endpointAccess: 'nearest-road', duration: 'unavailable'}
        : {topology: 'native-node-id', direction: 'exported', access: 'native-car-lanes',
        turns: 'unverified', speed: missingSpeed ? 'partial-or-missing' : legacySpeed ? 'prefab-limit' : 'effective-limit',
        traffic: missingTraffic ? 'partial-or-missing' : 'snapshot', endpointAccess: 'nearest-road',
        duration: trafficDuration ? 'speed-and-traffic-with-wait-factor' : 'speed-limit-without-delays'},
      warningCodes: this.transitNetwork ? ['WALK_ALONG_ROADS', 'TIMETABLE_UNAVAILABLE',
        ...(trip!.legs.some(leg => leg.schematic) ? ['TRANSIT_SCHEMATIC'] : []), ...(this.data.rangeLimited ? ['RANGE_LIMITED'] : [])]
        : ['TURN_RULES_UNVERIFIED', 'THIRD_PARTY_RULES_UNVERIFIED',
        ...(missingTraffic ? ['TRAFFIC_MISSING'] : []), ...(missingSpeed ? ['SPEED_LIMIT_MISSING'] : []),
        ...(legacySpeed ? ['PREFAB_SPEED_LIMIT'] : []), ...(this.data.rangeLimited ? ['RANGE_LIMITED'] : [])], maneuvers};
  }
}
