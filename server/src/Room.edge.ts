// yeon: room 객체에 externalproducer를 정의하기 위해 필요
import * as mediasoup from 'mediasoup';
//
import type * as mediasoupTypes from 'mediasoup/types';
import * as protoo from 'protoo-server';
import type * as protooTypes from 'protoo-server';
import type * as throttleTypes from '@sitespeed.io/throttle';

import { Logger } from './Logger';
import { EnhancedEventEmitter } from './enhancedEvents';
import { Bot } from './Bot';
import { Peer } from './Peer';
import { BroadcasterPeer } from './BroadcasterPeer';
import {
	RequestNameForRoom,
	RequestApiMethod,
	RequestApiPath,
	RequestData,
	RequestInternalData,
	RequestResponseData,
	TypedApiRequest,
} from './signaling/apiMessages';
import { clone, assertUnreachable } from './utils';
import type {
	ServerConfig,
	RoomId,
	PeerId,
	SerializedRoom,
	PeerProducersInfo,
	WebRtcTransportAppData,
	PlainTransportAppData,
	ProducerAppData,
	SerializedPeer,
} from './types';

// yeon: viewer count -> metrics agent
import { reportViewerCount } from './ViewerMetricsClient';

const staticLogger = new Logger('Room');
// yeon
const EXTERNAL_PEER_ID = '__external__';

export type RoomCreateOptions = {
	roomId: RoomId;
	consumerReplicas: number;
	usePipeTransports: boolean;
	config: ServerConfig;
	producerRouter: mediasoupTypes.Router;
	consumerRouter: mediasoupTypes.Router;
	producerWebRtcServer: mediasoupTypes.WebRtcServer;
	consumerWebRtcServer: mediasoupTypes.WebRtcServer;
};

type RoomConstructorOptions = {
	logger: Logger;
	roomId: RoomId;
	consumerReplicas: number;
	usePipeTransports: boolean;
	config: ServerConfig;
	producerRouter: mediasoupTypes.Router;
	consumerRouter: mediasoupTypes.Router;
	producerWebRtcServer: mediasoupTypes.WebRtcServer;
	consumerWebRtcServer: mediasoupTypes.WebRtcServer;
	audioLevelObserver: mediasoupTypes.AudioLevelObserver;
	activeSpeakerObserver: mediasoupTypes.ActiveSpeakerObserver;
	protooRoom: protooTypes.Room;
	bot: Bot;
};

export type RoomEvents = {
	/**
	 * Emitted when the Room is closed no matter how.
	 */
	closed: [];
	/**
	 * Emitted to apply network throttle.
	 */
	'apply-network-throttle': [
		{
			secret: string;
			options: throttleTypes.ThrottleStartOptions;
		},
		resolve: () => void,
		reject: (error: Error) => void,
	];
	/**
	 * Emitted to stop network throttle.
	 */
	'stop-network-throttle': [
		{
			secret: string;
		},
		resolve: () => void,
		reject: (error: Error) => void,
	];
};

function dumpProducersBrief(
	producers: Array<{ id: string; kind?: any; appData?: any }>
) {
	return producers.map(p => ({
		id: p.id,
		kind: (p as any).kind,
		appData: (p as any).appData,
	}));
}

export class Room extends EnhancedEventEmitter<RoomEvents> {
	readonly #logger: Logger;
	readonly #roomId: RoomId;
	readonly #consumerReplicas: number;
	readonly #usePipeTransports: boolean;
	readonly #config: ServerConfig;
	readonly #producerRouter: mediasoupTypes.Router;
	readonly #consumerRouter: mediasoupTypes.Router;
	readonly #producerWebRtcServer: mediasoupTypes.WebRtcServer;
	readonly #consumerWebRtcServer: mediasoupTypes.WebRtcServer;
	readonly #audioLevelObserver: mediasoupTypes.AudioLevelObserver;
	readonly #activeSpeakerObserver: mediasoupTypes.ActiveSpeakerObserver;
	readonly #observedProducers: Map<
		string,
		mediasoupTypes.Producer<ProducerAppData>
	> = new Map();
	readonly #protooRoom: protooTypes.Room;
	readonly #bot: Bot;
	readonly #joiningPeers: Map<string, Peer> = new Map();
	readonly #peers: Map<string, Peer> = new Map();
	readonly #joiningBroadcasterPeers: Map<string, BroadcasterPeer> = new Map();
	readonly #broadcasterPeers: Map<string, BroadcasterPeer> = new Map();
	readonly #createdAt: Date;
	#closed: boolean = false;

	// yeon: ProducerAppData왜 이걸로 강제해야 하는지는 잘 모르겠음
	// 정확히는 Producer<ProducerAppData> 구조 자체를 모르겠음 => 이후 확인 필요
	#externalProducers = new Map<string, mediasoupTypes.Producer<ProducerAppData>>();
	#remotePipeEnabled = true;
	/**
	 * roomId별 Edge 타겟 목록.
	 * - key: roomId
	 * - value: 원격 SFU의 base URL (예: "http://10.20.13.200:4443")
	 *
	 * roomId를 못 박고 싶지 않으면 '*' 를 default로 사용.
	 */
	// yeon
	#remotePipeTargetsByRoomId: Record<string, Array<{ url: string }>> = {
		// 기본값(전체 룸 공통)
		'*': [
			{ url: 'http://10.20.13.157:4445' }, //hardcoding
			{ url: 'http://10.20.13.190:4445' }, //hardcoding
			//{ url: 'http://10.20.13.186:4445' }, //hardcoding
			//{ url: 'http://10.1.2.3:4445' }, //hardcoding
		],

		// 특정 roomId에만 다르게 적용하고 싶으면:
		// 'live1': [{ url: 'http://10.0.0.12:4443' }],
	};

	// yeon: 외부에서 room의 external producer를 접근하고 값을 채우기 위해 room에서 제공하는 메서드
	// edge
	public addExternalProducer(producer: mediasoupTypes.Producer<ProducerAppData>): void {
		this.#logger.debug("addExternalProducer()");

		this.#externalProducers.set(producer.id, producer);
		// 정리:중복 등록 방지/메모리 누수 방지
		producer.observer.once('close', () => {
			this.#externalProducers.delete(producer.id);
		});
	}
	public getExternalProducers(): mediasoupTypes.Producer<ProducerAppData>[] {
		return Array.from(this.#externalProducers.values());
	}

	// yeon
	// edge
	public async onExternalProducer(producer: mediasoupTypes.Producer<ProducerAppData>): Promise<void> {
		
		this.#observedProducers.set(producer.id, producer);

		producer.observer.on('close', () => {
			this.#observedProducers.delete(producer.id);
		});

		// 2) usePipeTransports=true 인 구조라면, 시청자들이 붙는 consumerRouter에서 consume 해야 함
		//    producerRouter -> consumerRouter 
		if (this.#usePipeTransports) {
			await (this.#producerRouter as any).pipeToRouter({
				producerId: producer.id,
				router: this.#consumerRouter,
			});
		}

		// 3) 이미 들어와 있는 시청자들에게 consume 트리거
		const peers = Array.from(this.#peers.values());
		await Promise.allSettled(
			peers.map(p => p.consume({ producer, consumerReplicas: this.#consumerReplicas }))
		);

		// 오디오면 observer에도 추가
		if (producer.kind === 'audio') {
			this.#audioLevelObserver.addProducer({ producerId: producer.id }).catch(() => { });
			this.#activeSpeakerObserver.addProducer({ producerId: producer.id }).catch(() => { });
		}

		// peers.forEach(p => p.notify('newProducer', { producerId: producer.id, kind: producer.kind }));
	}

	static async create({
		roomId,
		consumerReplicas,
		usePipeTransports,
		config,
		producerRouter,
		consumerRouter,
		producerWebRtcServer,
		consumerWebRtcServer,
	}: RoomCreateOptions): Promise<Room> {
		staticLogger.debug(
			'create() [roomId:%o, usePipeTransports:%o]',
			roomId,
			usePipeTransports
		);

		const logger = new Logger(`[roomId:${roomId}]`, staticLogger);

		const audioLevelObserver = await producerRouter.createAudioLevelObserver({
			maxEntries: 10,
			threshold: -80,
			interval: 800,
		});

		const activeSpeakerObserver =
			await producerRouter.createActiveSpeakerObserver();

		const protooRoom = new protoo.Room();

		const bot = await Bot.create({
			usePipeTransports,
			producerRouter,
			consumerRouter,
		});

		const room = new Room({
			logger,
			roomId,
			consumerReplicas,
			usePipeTransports,
			config,
			producerRouter,
			consumerRouter,
			producerWebRtcServer,
			consumerWebRtcServer,
			audioLevelObserver,
			activeSpeakerObserver,
			protooRoom,
			bot,
		});

		return room;
	}

	get roomId(): RoomId {
		return this.#roomId;
	}

	get usePipeTransports(): boolean {
		return this.#usePipeTransports;
	}

	// yeon
	getRouter(role: 'producer' | 'consumer' = 'producer'): mediasoupTypes.Router {
		return role === 'consumer' ? this.#consumerRouter : this.#producerRouter;
	}

	// yeon
	getRouterId(role: 'producer' | 'consumer' = 'producer'): string {
		return this.getRouter(role).id;
	}

	private constructor({
		logger,
		roomId,
		consumerReplicas,
		usePipeTransports,
		config,
		producerRouter,
		consumerRouter,
		producerWebRtcServer,
		consumerWebRtcServer,
		audioLevelObserver,
		activeSpeakerObserver,
		protooRoom,
		bot,
	}: RoomConstructorOptions) {
		super();

		this.#logger = logger;

		this.#logger.debug('constructor()');

		this.#roomId = roomId;
		this.#consumerReplicas = consumerReplicas;
		this.#usePipeTransports = usePipeTransports;
		this.#config = config;
		this.#producerRouter = producerRouter;
		this.#consumerRouter = consumerRouter;
		this.#producerWebRtcServer = producerWebRtcServer;
		this.#consumerWebRtcServer = consumerWebRtcServer;
		this.#audioLevelObserver = audioLevelObserver;
		this.#activeSpeakerObserver = activeSpeakerObserver;
		this.#protooRoom = protooRoom;
		this.#bot = bot;
		this.#createdAt = new Date();

		this.handleProducerRouter();
		this.handleConsumerRouter();
		this.handleProducerWebRtcServer();
		this.handleConsumerWebRtcServer();
		this.handleAudioLevelObserver();
		this.handleActiveSpeakerObserver();
	}

	get id(): RoomId {
		return this.#roomId;
	}

	close(): void {
		this.#logger.debug('close()');

		if (this.#closed) {
			return;
		}

		this.#closed = true;

		for (const peer of this.#joiningPeers.values()) {
			peer.close();
		}

		for (const peer of this.#peers.values()) {
			peer.close();
		}

		for (const broadcasterPeer of this.#joiningBroadcasterPeers.values()) {
			broadcasterPeer.close();
		}

		for (const broadcasterPeer of this.#broadcasterPeers.values()) {
			broadcasterPeer.close();
		}

		this.#protooRoom.close();

		this.#producerRouter.close();

		this.#consumerRouter.close();

		this.emit('closed');
	}

	serialize(): SerializedRoom {
		return {
			roomId: this.#roomId,
			createdAt: this.#createdAt,
			numPeers: this.#peers.size,
			numJoiningPeers: this.#joiningPeers.size,
			peers: this.getAllPeers().map(peer => peer.serialize()),
			numBroadcasterPeers: this.#broadcasterPeers.size,
			numJoiningBroadcasterPeers: this.#joiningBroadcasterPeers.size,
			broadcasterPeers: this.getAllBroadcasterPeers().map(broadcasterPeer =>
				broadcasterPeer.serialize()
			),
		};
	}

	getBroadcasterPeer(peerId: PeerId): BroadcasterPeer | undefined {
		return (
			this.#broadcasterPeers.get(peerId) ??
			this.#joiningBroadcasterPeers.get(peerId)
		);
	}

	processWsConnection({
		peerId,
		protooTransport,
		remoteAddress,
	}: {
		peerId: PeerId;
		protooTransport: protooTypes.WebSocketTransport;
		remoteAddress: string;
	}): void {
		this.#logger.debug('processWsConnection() [peerId:%o]', peerId);

		this.mayCloseExistingPeer(peerId);

		this.#logger.debug(
			'processWsConnection() | creating a new Peer [peerId:%o]',
			peerId
		);

		const protooPeer = this.#protooRoom.createPeer(peerId, protooTransport);
		const peer = Peer.create({ peerId, protooPeer, remoteAddress });

		// NOTE: The Peer is not yet joined. It will once it sends 'join' request.
		this.#joiningPeers.set(peer.id, peer);

		this.handlePeer(peer);
	}

	async processApiRequest<Name extends RequestNameForRoom>({
		name,
		method,
		path,
		data,
		internalData,
	}: RequestData<Name> extends undefined
		? RequestInternalData<Name> extends undefined
		? {
			name: Name;
			method: RequestApiMethod<Name>;
			path: RequestApiPath<Name>;
			data?: undefined;
			internalData?: undefined;
		}
		: {
			name: Name;
			method: RequestApiMethod<Name>;
			path: RequestApiPath<Name>;
			data?: undefined;
			internalData: RequestInternalData<Name>;
		}
		: RequestInternalData<Name> extends undefined
		? {
			name: Name;
			method: RequestApiMethod<Name>;
			path: RequestApiPath<Name>;
			data: RequestData<Name>;
			internalData?: undefined;
		}
		: {
			name: Name;
			method: RequestApiMethod<Name>;
			path: RequestApiPath<Name>;
			data: RequestData<Name>;
			internalData: RequestInternalData<Name>;
		}): Promise<RequestResponseData<Name>> {
		return new Promise((resolve, reject) => {
			this.handleApiRequest({
				name,
				method,
				path,
				data,
				internalData,
				accept: resolve,
			} as TypedApiRequest<RequestNameForRoom>).catch(error => {
				this.#logger.warn(
					`API request processing failed [name:%o]: ${error}`,
					name
				);

				reject(error as Error);
			});
		});
	}

	private mayClose(): void {
		// If this is the latest Peer in the Room, close the Room.
		// NOTE: Run it in next loop iteration to avoid the case in which there is
		// only a Peer in the Room and it reconnects without closing its previous
		// connection.
		//
		// NOTE: We do not take into account BroadcasterPeers.
		setImmediate(() => {
			if (
				!this.#closed &&
				this.#peers.size === 0 &&
				this.#joiningPeers.size === 0
			) {
				this.#logger.info('last Peer in the Room left, closing the Room');

				this.close();
			}
		});
	}

	private getAllPeers(): Peer[] {
		return Array.from(this.#peers.values());
	}

	private getOtherPeers(excludedPeer: Peer): Peer[] {
		return Array.from(this.#peers.values()).filter(
			peer => peer !== excludedPeer
		);
	}

	private getAllBroadcasterPeers(): BroadcasterPeer[] {
		return Array.from(this.#broadcasterPeers.values());
	}

	private getOtherBroadcasterPeers(
		excludedBroadcasterPeer: BroadcasterPeer
	): BroadcasterPeer[] {
		return Array.from(this.#broadcasterPeers.values()).filter(
			broadcasterPeer => broadcasterPeer !== excludedBroadcasterPeer
		);
	}

	private mayCloseExistingPeer(peerId: PeerId): void {
		const existingPeer = this.#peers.get(peerId);

		if (existingPeer) {
			this.#logger.warn(
				'mayCloseExistingPeer() | there is already a Peer with same peerId, closing it [peerId:%o]',
				peerId
			);

			existingPeer.close();
		}

		const existingJoiningPeer = this.#joiningPeers.get(peerId);

		if (existingJoiningPeer) {
			this.#logger.warn(
				'mayCloseExistingPeer() | there is already a joining Peer with same peerId, closing it [peerId:%o]',
				peerId
			);

			existingJoiningPeer.close();
		}

		const existingBroadcasterPeer = this.#broadcasterPeers.get(peerId);

		if (existingBroadcasterPeer) {
			this.#logger.warn(
				'mayCloseExistingPeer() | there is already a BroadcasterPeer with same peerId, closing it [peerId:%o]',
				peerId
			);

			existingBroadcasterPeer.close();
		}

		const existingJoiningBroadcasterPeer =
			this.#joiningBroadcasterPeers.get(peerId);

		if (existingJoiningBroadcasterPeer) {
			this.#logger.warn(
				'mayCloseExistingPeer() | there is already a joining BroadcasterPeer with same peerId, closing it [peerId:%o]',
				peerId
			);

			existingJoiningBroadcasterPeer.close();
		}
	}

	// yeon
	// edge
	private handlePeer(peer: Peer): void {
		peer.on('closed', () => {
			this.#joiningPeers.delete(peer.id);
			this.#peers.delete(peer.id);

			// yeon: viewer count
			const viewerCount = this.#peers.size;
			// viewer count 변경 전달
			this.#logger.warn(
                '[VIEWER-COUNT] closed peerId=%s viewerCount=%d',
                peer.id,
                viewerCount
            );

			// yeon: metrics agent로 현재 viewer count 전달.
			void reportViewerCount(viewerCount)
				.catch(error => {
					this.#logger.warn(
						'[VIEWER-COUNT] failed to report to metrics agent ' +
						'[viewerCount:%d]: %o',
						viewerCount,
						error
					);
				});
			this.mayClose();
		});

		peer.on('joined', callback => {
			this.#logger.debug('handlePeer |  new peer joined the room');
			this.#joiningPeers.delete(peer.id);
			this.#peers.set(peer.id, peer);

			// yeon: viewer count
			const viewerCount = this.#peers.size;
			
			this.#logger.warn(
                '[VIEWER-COUNT] closed peerId=%s viewerCount=%d',
                peer.id,
                viewerCount
            );

			// yeon: metrics agent로 현재 viewer count 전달.
			void reportViewerCount(viewerCount)
				.catch(error => {
					this.#logger.warn(
						'[VIEWER-COUNT] failed to report to metrics agent ' +
						'[viewerCount:%d]: %o',
						viewerCount,
						error
					);
				});
			const otherPeers = this.getOtherPeers(peer);
			const broadcasterPeers = this.getAllBroadcasterPeers();

			callback([
				...otherPeers.map(otherPeer => otherPeer.serialize()),
				...broadcasterPeers.map(broadcasterPeer => broadcasterPeer.serialize()),
			]);

			// 다른 모든 피어들 불러와서 그 피어들의 프로듀서들을 불러와서 consume 시키기
			for (const otherPeer of otherPeers) {
				otherPeer.notify('newPeer', { peer: peer.serialize() });

				for (const producer of otherPeer.getProducers()) {
					void peer.consume({
						producer,
						consumerReplicas: this.#consumerReplicas,
					});
				}

				for (const chatDataProducer of otherPeer.getChatDataProducers()) {
					void peer.consumeData({ dataProducer: chatDataProducer });
				}
			}

			for (const broadcasterPeer of broadcasterPeers) {
				for (const producer of broadcasterPeer.getProducers()) {
					void peer.consume({
						producer,
						consumerReplicas: this.#consumerReplicas,
					});
				}
			}

			// yeon
			// edge
			// 룸에서 externalProducers를 따로 관리한다.
			// 외부 producer들을 불러와서 새로운 피어에게 consume
			const externalList = this.getExternalProducers(); // 배열이면 그대로
			this.#logger.warn(
				'[JOIN] external producers count=%d list=%o',
				externalList.length,
				dumpProducersBrief(externalList as any)
			);

			this.#logger.warn('[DEBUG][JOIN] peerId=%s', peer.id);

			for (const producer of externalList) {
				this.#logger.warn(
					'[DEBUG][JOIN] consuming external producer - producerId=%s', producer.id,);

				void peer.consume({ producer, consumerReplicas: this.#consumerReplicas })
					.then(() => {
						this.#logger.warn(
							'[DEBUG][JOIN] consume external producer success -> peerId=%s producerId=%s',
							peer.id,
							producer.id
						);
					})
					.catch((e: any) => {
						this.#logger.error(
							'[DEBUG][JOIN] consume external producer failed -> peerId=%s producerId=%s err=%o',
							peer.id,
							producer.id,
							e
						);
					});
			}
			
			this.#logger.warn('[JOIN] finished external producer loop for peerId=%s', peer.id);

			void peer.consumeData({ dataProducer: this.#bot.getDataProducer() });
		});

		peer.on('disconnected', () => {
			const otherPeers = this.getOtherPeers(peer);

			for (const otherPeer of otherPeers) {
				otherPeer.notify('peerClosed', { peerId: peer.id });
			}
		});

		peer.on('get-router-rtp-capabilities', callback => {
			callback(this.#consumerRouter.rtpCapabilities);
		});

		peer.on(
			'create-webrtc-transport',
			// eslint-disable-next-line @typescript-eslint/no-misused-promises
			async ({ direction, sctpCapabilities, forceTcp }, resolve, reject) => {
				try {
					let mediasoupRouter: mediasoupTypes.Router;
					let mediasoupWebRtcServer: mediasoupTypes.WebRtcServer;

					switch (direction) {
						case 'producer': {
							mediasoupRouter = this.#producerRouter;
							mediasoupWebRtcServer = this.#producerWebRtcServer;

							break;
						}

						case 'consumer': {
							mediasoupRouter = this.#consumerRouter;
							mediasoupWebRtcServer = this.#consumerWebRtcServer;

							break;
						}

						default: {
							assertUnreachable('invalid transport direction', direction);
						}
					}

					const transport =
						await mediasoupRouter.createWebRtcTransport<WebRtcTransportAppData>(
							{
								...clone(this.#config.mediasoup.webRtcTransportOptions),
								enableUdp: !forceTcp,
								enableTcp: true,
								webRtcServer: mediasoupWebRtcServer,
								iceConsentTimeout: 20,
								enableSctp: Boolean(sctpCapabilities),
								numSctpStreams: sctpCapabilities?.numStreams,
								appData: { direction },
							}
						);

					const { maxIncomingBitrate } =
						this.#config.mediasoup.additionalWebRtcTransportOptions ?? {};

					if (maxIncomingBitrate) {
						transport.setMaxIncomingBitrate(maxIncomingBitrate).catch(error => {
							this.#logger.warn(
								`transport.setMaxIncomingBitrate() failed: ${error}`
							);
						});
					}

					resolve(transport);
				} catch (error) {
					reject(error as Error);
				}
			}
		);

		// eslint-disable-next-line @typescript-eslint/no-misused-promises
		peer.on('new-producer', async ({ producer }) => {
			if (this.#usePipeTransports) {
				await this.#producerRouter.pipeToRouter({
					producerId: producer.id,
					router: this.#consumerRouter,
				});
			}

			// yeon
			//Origin->Edge remote pipe (방송/송출 시 자동 복제)
			// await this.pipeProducerToEdges(producer as mediasoupTypes.Producer<ProducerAppData>);

			const otherPeers = this.getOtherPeers(peer);
			for (const otherPeer of otherPeers) {
				void otherPeer.consume({
					producer,
					consumerReplicas: this.#consumerReplicas,
				});
			}

			if (producer.kind === 'audio') {
				this.#audioLevelObserver
					.addProducer({ producerId: producer.id })
					.catch(() => { });

				this.#activeSpeakerObserver
					.addProducer({ producerId: producer.id })
					.catch(() => { });
			}
		});

		// eslint-disable-next-line @typescript-eslint/no-misused-promises
		peer.on('new-data-producer', async ({ dataProducer }) => {
			const { channel } = dataProducer.appData;

			switch (channel) {
				case 'chat': {
					if (this.#usePipeTransports) {
						await this.#producerRouter.pipeToRouter({
							dataProducerId: dataProducer.id,
							router: this.#consumerRouter,
						});
					}

					const otherPeers = this.getOtherPeers(peer);

					for (const otherPeer of otherPeers) {
						void otherPeer.consumeData({
							dataProducer,
						});
					}

					break;
				}

				case 'bot': {
					void this.#bot.consumeData({ dataProducer, peer });

					break;
				}
			}
		});

		peer.on('get-can-consume', ({ producerId, rtpCapabilities }, callback) => {
			if (rtpCapabilities) {
				callback(
					this.#consumerRouter.canConsume({
						producerId,
						rtpCapabilities,
					})
				);
			} else {
				callback(false);
			}
		});

		peer.on('display-name-changed', ({ displayName, oldDisplayName }) => {
			const otherPeers = this.getOtherPeers(peer);

			for (const otherPeer of otherPeers) {
				otherPeer.notify('peerDisplayNameChanged', {
					peerId: peer.id,
					displayName,
					oldDisplayName,
				});
			}
		});

		peer.on(
			'apply-network-throttle',
			({ secret, options }, resolve, reject) => {
				this.emit(
					'apply-network-throttle',
					{ secret, options },
					resolve,
					reject
				);
			}
		);

		peer.on('stop-network-throttle', ({ secret }, resolve, reject) => {
			this.emit('stop-network-throttle', { secret }, resolve, reject);
		});
	}

	private handleBroadcasterPeer(broadcasterPeer: BroadcasterPeer): void {
		broadcasterPeer.on('closed', () => {
			this.#joiningBroadcasterPeers.delete(broadcasterPeer.id);
			this.#broadcasterPeers.delete(broadcasterPeer.id);
		});

		broadcasterPeer.on('joined', () => {
			this.#joiningBroadcasterPeers.delete(broadcasterPeer.id);
			this.#broadcasterPeers.set(broadcasterPeer.id, broadcasterPeer);


			const peers = this.getAllPeers();


			for (const peer of peers) {
				peer.notify('newPeer', { peer: broadcasterPeer.serialize() });
			}
		});

		broadcasterPeer.on('disconnected', () => {
			const peers = this.getAllPeers();

			for (const peer of peers) {
				peer.notify('peerClosed', { peerId: broadcasterPeer.id });
			}
		});

		broadcasterPeer.on('get-router-rtp-capabilities', callback => {
			callback(this.#consumerRouter.rtpCapabilities);
		});

		broadcasterPeer.on(
			'create-plain-transport',
			// eslint-disable-next-line @typescript-eslint/no-misused-promises
			async ({ direction, comedia, rtcpMux }, resolve, reject) => {
				try {
					let mediasoupRouter: mediasoupTypes.Router;

					switch (direction) {
						case 'producer': {
							mediasoupRouter = this.#producerRouter;

							break;
						}

						case 'consumer': {
							mediasoupRouter = this.#consumerRouter;

							break;
						}

						default: {
							assertUnreachable('invalid transport direction', direction);
						}
					}

					const transport =
						await mediasoupRouter.createPlainTransport<PlainTransportAppData>({
							...clone(this.#config.mediasoup.plainTransportOptions),
							comedia,
							rtcpMux,
							appData: { direction },
						});

					resolve(transport);
				} catch (error) {
					reject(error as Error);
				}
			}
		);

		// eslint-disable-next-line @typescript-eslint/no-misused-promises
		broadcasterPeer.on('new-producer', async ({ producer }) => {
			if (this.#usePipeTransports) {
				await this.#producerRouter.pipeToRouter({
					producerId: producer.id,
					router: this.#consumerRouter,
				});
			}

			// yeon
			// ✅ Origin->Edge remote pipe
			// await this.pipeProducerToEdges(producer as mediasoupTypes.Producer<ProducerAppData>);

			const peers = this.getAllPeers();

			for (const peer of peers) {
				void peer.consume({
					producer,
					consumerReplicas: this.#consumerReplicas,
				});
			}

			if (producer.kind === 'audio') {
				this.#audioLevelObserver
					.addProducer({ producerId: producer.id })
					.catch(() => { });

				this.#activeSpeakerObserver
					.addProducer({ producerId: producer.id })
					.catch(() => { });
			}
		});

		broadcasterPeer.on(
			'get-can-consume',
			({ producerId, rtpCapabilities }, callback) => {
				if (rtpCapabilities) {
					callback(
						this.#consumerRouter.canConsume({
							producerId,
							rtpCapabilities,
						})
					);
				} else {
					callback(false);
				}
			}
		);

		broadcasterPeer.on('get-peer-producers-infos', callback => {
			const peerProducersMap: Map<
				PeerId,
				mediasoupTypes.Producer<ProducerAppData>[]
			> = new Map();

			for (const producer of this.#observedProducers.values()) {
				const { peerId } = producer.appData;
				const producers = peerProducersMap.get(peerId);

				if (producers) {
					producers.push(producer);
				} else {
					peerProducersMap.set(peerId, [producer]);
				}
			}

			const peerProducersInfos: PeerProducersInfo[] = [];

			for (const [peerId, producers] of peerProducersMap) {
				peerProducersInfos.push({
					peerId,
					producers: producers.map(producer => {
						return {
							producerId: producer.id,
							kind: producer.kind,
							source: producer.appData.source,
							// NOTE: Remove rtcpFeedback from codecs.
							// NOTE: Remove RTX codecs.
							consumableCodecs: producer.consumableRtpParameters.codecs
								.filter(
									codec =>
										codec.mimeType.toLowerCase() !== 'audio/rtx' &&
										codec.mimeType.toLowerCase() !== 'video/rtx'
								)
								.map(codec => {
									return {
										...codec,
										rtcpFeedback: undefined,
									};
								}),
						};
					}),
				});
			}

			callback(peerProducersInfos);
		});

		broadcasterPeer.on('get-producer', ({ producerId }, callback) => {
			const producer = this.#observedProducers.get(producerId);

			callback(producer);
		});
	}

	private handleProducerRouter(): void {
		this.#producerRouter.observer.on('close', () => {
			this.close();
		});

		this.#producerRouter.observer.on('newtransport', transport => {
			transport.observer.on('newproducer', producer => {
				this.#observedProducers.set(
					producer.id,
					producer as mediasoupTypes.Producer<ProducerAppData>
				);

				producer.observer.on('close', () => {
					this.#observedProducers.delete(producer.id);
				});
			});
		});
	}

	private handleConsumerRouter(): void {
		this.#consumerRouter.observer.on('close', () => {
			this.close();
		});
	}

	private handleProducerWebRtcServer(): void {
		this.#producerWebRtcServer.observer.on('close', () => {
			this.close();
		});
	}

	private handleConsumerWebRtcServer(): void {
		this.#producerWebRtcServer.observer.on('close', () => {
			this.close();
		});
	}

	private handleAudioLevelObserver(): void {
		this.#audioLevelObserver.on('volumes', volumes => {
			const allPeers = this.getAllPeers();
			const peerVolumes = volumes.map(({ producer, volume }) => {
				const { peerId } = producer.appData as ProducerAppData;

				return {
					peerId,
					volume,
				};
			});

			for (const peer of allPeers) {
				peer.notify('speakingPeers', { peerVolumes });
			}
		});

		this.#audioLevelObserver.on('silence', () => {
			const allPeers = this.getAllPeers();

			for (const peer of allPeers) {
				peer.notify('speakingPeers', { peerVolumes: [] });
				peer.notify('activeSpeaker', { peerId: undefined });
			}
		});
	}

	private handleActiveSpeakerObserver(): void {
		this.#activeSpeakerObserver.on('dominantspeaker', ({ producer }) => {
			const { peerId } = producer.appData as ProducerAppData;
			const allPeers = this.getAllPeers();

			for (const peer of allPeers) {
				peer.notify('activeSpeaker', { peerId });
			}
		});
	}

	// eslint-disable-next-line @typescript-eslint/require-await
	private async handleApiRequest(
		request: TypedApiRequest<RequestNameForRoom>
	): Promise<void> {
		const { name, data, internalData, accept } = request;

		switch (name) {
			case 'getRouterRtpCapabilities': {
				accept({
					routerRtpCapabilities: this.#consumerRouter.rtpCapabilities,
				});

				break;
			}

			case 'createBroadcasterPeer': {
				const { peerId, displayName, device } = data;
				const { remoteAddress } = internalData;

				this.mayCloseExistingPeer(peerId);

				this.#logger.debug(
					'handleApiRequest() | creating a new BroadcasterPeer [peerId:%o]',
					peerId
				);

				const broadcasterPeer = BroadcasterPeer.create({
					peerId,
					remoteAddress,
					displayName,
					device,
				});

				// NOTE: The BroadcasterPeer is not yet joined. It will once it sends
				// 'join' request.
				this.#joiningBroadcasterPeers.set(broadcasterPeer.id, broadcasterPeer);

				this.handleBroadcasterPeer(broadcasterPeer);

				accept();

				break;
			}

			default: {
				assertUnreachable('request name', name);
			}
		}
	}
}
