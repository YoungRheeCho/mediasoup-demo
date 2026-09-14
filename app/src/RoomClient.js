import RoomClientOrigin from './RoomClient.origin';
import RoomClientEdge from './RoomClient.edge';

const mode = window.__CFG__?.mode ?? 'origin';

export default (mode === 'edge') ? RoomClientEdge : RoomClientOrigin;
