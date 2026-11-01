export type XYZ = [number, number, number];
export type RoutingProfile = 'car-shortest' | 'car-balanced' | 'transit';
export type TransitPreference = 'balanced' | 'less-walking' | 'fewer-transfers';
export type TransitCost = {walkFactor: number; boardingCost: number};
export type Edge = {
  id: string; sourceSegmentId: string; fromNode: string; toNode: string;
  geometryRef: string; reverse: boolean; lengthM: number; modeAccess: 'allowed';
  directionEvidence: string; name: string; mode: string; level: string;
  congestion?: number | null; speedLimitMps?: number | null; speedSource?: string | null;
  recommendedCostPerMeter?: number;
  widthM?: number;
  roadMode?: string; lineId?: string; routeId?: string; fromStopIndex?: number; toStopIndex?: number;
  fromStop?: string; toStop?: string; transitKind?: string; color?: string; schematic?: boolean;
};
export type NavigationPackage = {
  routingSchemaVersion: number; cityId: string; snapshotId: string;
  graphRevision: string; costRevision: string; capturedAt: string;
  costModel?: {id: string; walkFactor?: number; boardingCost?: number; preferences?: Record<TransitPreference, TransitCost>};
  durationModel?: string;
  transit?: Pick<NavigationPackage, 'nodes' | 'geometries' | 'edges' | 'costModel'>;
  nodes: Record<string, XYZ>; geometries: Record<string, XYZ[]>; edges: Edge[];
  components: Record<string, string>; capabilities: Record<string, string>;
  diagnostics: { roadCount: number; nodeCount: number; edgeCount: number;
    weakComponents: number | null; excluded: Record<string, number>; ruleScope: string;
    trafficDirections?: number; speedLimitDirections?: number; legacySpeedDirections?: number };
  rangeLimited?: boolean; exportRegion?: [number, number, number, number];
};
export type Snap = {
  geometryRef: string; fraction: number; position: XYZ; distanceM: number;
  name: string; mode: string; direction: 'forward' | 'backward' | 'both';
};
export type Endpoint = { position: XYZ; snap: Snap; place?: {name: string; geometry: GeoJSON.Geometry; key?: string; kind?: string; context?: string} };
export type TransitStop = {stopId: string; stopIndex: number; name: string; position: XYZ};
export type TransitLeg = {mode: 'walk' | 'ride'; name: string; fromStop?: string; toStop?: string;
  stops: number; distanceM: number; color?: string; schematic?: boolean;
  traversalStart: number; traversalEnd: number; lineId?: string; routeId?: string; transitKind?: string;
  stopSequence?: TransitStop[]; walkKind?: 'access' | 'transfer' | 'egress' | 'walking-only';
  fromStopRef?: TransitStop; toStopRef?: TransitStop};
export type Maneuver = { kind: 'depart' | 'continue' | 'left' | 'right' | 'arrive';
  roadName: string; distanceM: number };
export type RouteResult = {
  status: 'ok'; edgeIds: string[]; roadNames: string[]; geometry: XYZ[]; distanceM: number;
  durationSeconds: number | null; durationModel: string | null;
  costProfile: RoutingProfile; cost: number; costUnit: 'meter' | 'preference-meter';
  costConfigId?: TransitPreference;
  traversals: {edgeId: string; fromFraction: number; toFraction: number}[];
  quality: Record<string, string>; warningCodes: string[]; maneuvers: Maneuver[];
  itineraryVersion?: 1; legs?: TransitLeg[]; boardings?: number; walkingDistanceM?: number;
};
export type TransitCandidate = RouteResult & {
  candidateId: string; generatedBy: TransitPreference[]; balancedCost: number;
};
export type Identity = { requestId: number; documentEpoch: number; snapshotId: string;
  graphRevision: string; costRevision: string };
export class RoutingError extends Error {
  constructor(public readonly code: string, detail = '') { super(detail || code); }
}
