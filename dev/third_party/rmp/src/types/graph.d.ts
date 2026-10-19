import { MultiDirectedGraph } from 'graphology';
import { EdgeAttributes, GraphAttributes, NodeAttributes } from '../constants/constants';

declare global {
    interface Window { graph: MultiDirectedGraph<NodeAttributes, EdgeAttributes, GraphAttributes>; }
}
