import {Router} from './router';
import {RoutingError} from './types';
import type {Identity, NavigationPackage, RoutingProfile, Snap, TransitPreference, XYZ} from './types';

let router: Router | null = null;
let epoch = -1;
self.addEventListener('message', async (event: MessageEvent) => {
  const message = event.data as Identity & {type: string; data?: NavigationPackage;
    position?: XYZ; origin?: Snap; destination?: Snap; knownRulesOnly?: boolean; profile?: RoutingProfile; costConfigId?: TransitPreference};
  const identity: Identity = {requestId: message.requestId, documentEpoch: message.documentEpoch,
    snapshotId: message.snapshotId, graphRevision: message.graphRevision, costRevision: message.costRevision};
  try {
    let result: unknown;
    if (message.type === 'routingInit') {
      if (!message.data) throw new RoutingError('DATA_INCOMPLETE');
      router = new Router(message.data); epoch = message.documentEpoch;
      result = {nodes: router.graph.getNodesCount(), transitions: router.transitionCount};
    } else {
      if (!router || epoch !== message.documentEpoch || router.data.snapshotId !== message.snapshotId
          || router.data.graphRevision !== message.graphRevision || router.data.costRevision !== message.costRevision)
        throw new RoutingError('STALE_QUERY');
      if (message.type === 'snapQuery' && message.position) result = router.snap(message.position, message.profile);
      else if (message.type === 'routeQuery' && message.origin && message.destination)
        result = router.route(message.origin, message.destination, message.knownRulesOnly, message.profile, message.costConfigId);
      else if (message.type === 'transitCandidatesQuery' && message.origin && message.destination)
        result = await router.candidates(message.origin, message.destination);
      else throw new RoutingError('INVALID_QUERY');
    }
    self.postMessage({...identity, status: 'ok', result});
  } catch (error) {
    self.postMessage({...identity, status: 'error',
      code: error instanceof RoutingError ? error.code : 'WORKER_FAILED',
      detail: error instanceof Error ? error.message : String(error)});
  }
});
