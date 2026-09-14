import PeersOrigin from './Peers.origin';
import PeersEdge from './Peers.edge';

const mode = window.__CFG__?.mode ?? 'origin';

export default (mode === 'edge') ? PeersEdge : PeersOrigin;
