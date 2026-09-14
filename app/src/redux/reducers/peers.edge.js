import { debug } from "node:console";
import Logger from "../../Logger";

const initialState = {};
const logger = new Logger('peers');

const EXTERNAL_PEER_ID = '__external__';

function ensurePeer(state, peerId) {

	if (!peerId) return state;
	if (state[peerId]) {
		return state;
	}

	const isExternal = peerId === EXTERNAL_PEER_ID;

	return {
		...state,
		[peerId]: {
			id: peerId,
			displayName: isExternal ? 'Origin' : `External(${String(peerId).slice(0, 6)})`,
			device: { flag: isExternal ? 'external' : 'unknown', name: isExternal ? 'pipe' : 'external' },
			consumers: [],
			dataConsumers: [],
		},
	};
}

const peers = (state = initialState, action) => {
	switch (action.type) {
		case 'SET_ROOM_STATE': {
			const roomState = action.payload.state;

			if (roomState === 'closed') return {};
			else return state;
		}

		case 'ADD_PEER': {
			const { peer } = action.payload;

			return { ...state, [peer.id]: peer };
		}

		case 'REMOVE_PEER': {
			const { peerId } = action.payload;
			const newState = { ...state };

			delete newState[peerId];

			return newState;
		}

		case 'SET_PEER_DISPLAY_NAME': {
			const { displayName, peerId } = action.payload;
			const peer = state[peerId];

			if (!peer) throw new Error('no Peer found');

			const newPeer = { ...peer, displayName };

			return { ...state, [newPeer.id]: newPeer };
		}

		//어떤 피어에게 새로운 consumer가 생겼으니
		//그 peer의 consumers 배열에 consumer.id를 추가해서 Redux state를 갱신
		// edge
		case 'ADD_CONSUMER': {
			const { consumer } = action.payload;
			let pid = action.payload.peerId;

			// peerId 자체가 없으면 fallback 하나만 사용
			if (!pid) pid = EXTERNAL_PEER_ID;

			// peer가 없으면 생성 (immutable)
			const nextState = ensurePeer(state, pid);

			const peer = nextState[pid];

			// yeon
			if (!peer) {
				logger._debug("[DEBUG] no peer for peerid");
				return nextState;
			}
	
			return {
				...nextState,
				[pid]: {
					...peer,
					consumers: [...peer.consumers, consumer.id],
				},
			};
		}
		case 'REMOVE_CONSUMER': {
			const { consumerId, peerId } = action.payload;
			const peer = state[peerId];

			// NOTE: This means that the Peer was closed before, so it's ok.
			if (!peer) return state;

			const idx = peer.consumers.indexOf(consumerId);

			if (idx === -1) throw new Error('Consumer not found');

			const newConsumers = peer.consumers.slice();

			newConsumers.splice(idx, 1);

			const newPeer = { ...peer, consumers: newConsumers };

			return { ...state, [newPeer.id]: newPeer };
		}

		case 'ADD_DATA_CONSUMER': {
			const { dataConsumer, peerId } = action.payload;

			// special case for bot DataConsumer.
			if (!peerId) return state;

			const peer = state[peerId];

			if (!peer) throw new Error('no Peer found for new DataConsumer');

			const newDataConsumers = [...peer.dataConsumers, dataConsumer.id];
			const newPeer = { ...peer, dataConsumers: newDataConsumers };

			return { ...state, [newPeer.id]: newPeer };
		}

		case 'REMOVE_DATA_CONSUMER': {
			const { dataConsumerId, peerId } = action.payload;

			// special case for bot DataConsumer.
			if (!peerId) return state;

			const peer = state[peerId];

			// NOTE: This means that the Peer was closed before, so it's ok.
			if (!peer) return state;

			const idx = peer.dataConsumers.indexOf(dataConsumerId);

			if (idx === -1) throw new Error('DataConsumer not found');

			const newDataConsumers = peer.dataConsumers.slice();

			newDataConsumers.splice(idx, 1);

			const newPeer = { ...peer, dataConsumers: newDataConsumers };

			return { ...state, [newPeer.id]: newPeer };
		}

		default: {
			return state;
		}
	}
};

export default peers;
