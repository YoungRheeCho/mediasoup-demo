import peersOrigin from './peers.origin';
import peersEdge from './peers.edge';

const mode = window.__CFG__?.mode ?? 'origin';
console.log('[peers reducer] mode =', mode, 'CFG =', window.__CFG__);

export default (mode === 'edge') ? peersEdge : peersOrigin;
