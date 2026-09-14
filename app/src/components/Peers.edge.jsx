import React from 'react';
import { connect } from 'react-redux';
import PropTypes from 'prop-types';
import classnames from 'classnames';
import * as appPropTypes from './appPropTypes';
import { Appear } from './transitions';
import Peer from './Peer';

const role = new URLSearchParams(window.location.search).get('role') || 'viewer'; // yun

// edge
const Peers = ({ peers, activeSpeakerId, speakingPeerIds }) => { // yun

	// yeon: 외부/송출자 피어 우선 선택
	// edge
	// 이전 두번째 피어들은 영상이 보이지 않았던 원인
	const externalPeer =
		peers.find(p => p?.device?.flag === 'external') ||
		peers.find(p => (p?.displayName || '').startsWith('External'));
	const visiblePeers =  role === 'viewer' ? (externalPeer ? [externalPeer] : peers.slice(0, 1)) : peers;

	return (
		<div data-component="Peers">
			{visiblePeers.map(peer => (
				<Appear key={peer.id} duration={300}>
					<div
						className={classnames('peer-container', {
							'active-speaker': peer.id === activeSpeakerId,
							speaking: speakingPeerIds.includes(peer.id),
						})}
					>
						<Peer id={peer.id} />
					</div>
				</Appear>
			))}
		</div>
	);
};

//const Peers = ({ peers, activeSpeakerId, speakingPeerIds }) => {
//	return (
//		<div data-component="Peers">
//			{peers.map(peer => {
//				return (
//					<Appear key={peer.id} duration={1000}>
//						<div
//							className={classnames('peer-container', {
//								'active-speaker': peer.id === activeSpeakerId,
//								speaking: speakingPeerIds.includes(peer.id),
//							})}
//						>
//							<Peer id={peer.id} />
//						</div>
//					</Appear>
//				);
//			})}
//		</div>
//	);
//};

Peers.propTypes = {
	peers: PropTypes.arrayOf(appPropTypes.Peer).isRequired,
	activeSpeakerId: PropTypes.string,
	speakingPeerIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

const mapStateToProps = state => {
	const peersArray = Object.values(state.peers);

	return {
		peers: peersArray,
		activeSpeakerId: state.room.activeSpeakerId,
		speakingPeerIds: state.room.speakingPeerIds,
	};
};

const PeersContainer = connect(mapStateToProps, null, null, {
	areStatesEqual: (next, prev) => {
		return (
			prev.peers === next.peers &&
			prev.room.activeSpeakerId === next.room.activeSpeakerId &&
			prev.room.speakingPeerIds.length === next.room.speakingPeerIds.length
		);
	},
})(Peers);

export default PeersContainer;
