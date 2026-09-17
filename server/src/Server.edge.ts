import process from 'node:process';
import * as https from 'node:https';
import * as http from 'node:http';
import * as net from 'node:net';
import * as fs from 'node:fs';
import * as mediasoup from 'mediasoup';
import type * as mediasoupTypes from 'mediasoup/types';
import { AwaitQueue } from 'awaitqueue';
import * as throttle from '@sitespeed.io/throttle';
import type * as throttleTypes from '@sitespeed.io/throttle';

import { Logger } from './Logger';
import { EnhancedEventEmitter } from './enhancedEvents';
import { WsServer } from './WsServer';
import { ApiServer } from './ApiServer';
import { Room } from './Room';
import { InvalidStateError, ForbiddenError, RoomNotFound } from './errors';
import { clone } from './utils';
import type {
	ServerConfig,
	RoomId,
	SerializedServer,
	WorkerAppData,
	ProducerAppData, // yeon
} from './types';

const logger = new Logger('Server');

// yeon
export const EXTERNAL_PEER_ID = '__external__';
export const EXTERNAL_PEER_DISPLAY_NAME = 'External Producer';

export type ServerCreateOptions = {
	config: ServerConfig;
	networkThrottleSecret?: string;
};

type ServerConstructorOptions = {
	config: ServerConfig;
	workersAndWebRtcServers: WorkersAndWebRtcServers;
	httpServer: https.Server | http.Server;
	wsServer: WsServer;
	apiServer: ApiServer;
	networkThrottleSecret?: string;
};

type WorkersAndWebRtcServers = Map<
	number,
	{
		worker: mediasoupTypes.Worker<WorkerAppData>;
		webRtcServer: mediasoupTypes.WebRtcServer;
	}
>;

type RoomData = {
	queue: AwaitQueue;
	room?: Room;
};

export type ServerObserverEvents = {
	/**
	 * Emitted when a new Server is created.
	 */
	'new-server': [Server];
};

export type ServerEvents = {
	/**
	 * Emitted when the Server is closed no matter how.
	 */
	closed: [];
	/**
	 * Emitted when Server dies.
	 */
	died: [];
	/**
	 * Emitted when a new Room is created.
	 */
	'new-room': [Room];
};

export class Server extends EnhancedEventEmitter<ServerEvents> {
	public static readonly observer: EnhancedEventEmitter<ServerObserverEvents> =
		new EnhancedEventEmitter();

	readonly #config: ServerConfig;
	readonly #httpServer: https.Server | http.Server;
	readonly #httpConnections: Set<net.Socket> = new Set();
	readonly #wsServer: WsServer;
	readonly #apiServer: ApiServer;
	readonly #workersAndWebRtcServers: WorkersAndWebRtcServers = new Map();
	#nextWorkerIdx: number = 0;
	readonly #roomQueues: Map<RoomId, RoomData> = new Map();
	readonly #networkThrottleSecret?: string;
	#networkThrottleEnabled: boolean = false;
	#networkThrottleEnabledByRoomId?: RoomId;
	readonly #networkThrottleAwaitQueue: AwaitQueue = new AwaitQueue();
	readonly #createdAt: Date;
	#closed: boolean = false;
	
	// yeon
	// Server 클래스 내부
	#remotePipeHttpServer?: http.Server;
	//#remotePipeApp?: import('express').Express;

	// yeon 
	#remotePipeTransports = new Map<string, mediasoup.types.PipeTransport>();
	#remotePipeProducers  = new Map<string, mediasoup.types.Producer>();

	// yeon
	// edge
	static async create({
		config,
		networkThrottleSecret,
	}: ServerCreateOptions): Promise<Server> {
		logger.debug('create()');

		const httpOriginHeader = Server.computeHttpOriginHeader(config);
		const workersAndWebRtcServers =
			await Server.createWorkersAndWebRtcServers(config);
		const httpServer = await Server.createHttpServer(config);
		const wsServer = WsServer.create({ httpServer, httpOriginHeader });
		const apiServer = ApiServer.create({ httpOriginHeader });
		const server = new Server({
			config,
			workersAndWebRtcServers,
			httpServer,
			wsServer,
			apiServer,
			networkThrottleSecret,
		});
		
		// yeon
		// edge
		server.startRemotePipeApi(); //여기서 Expipe를 위한 서버를 구축하는 함수 호출

		Server.observer.emit('new-server', server);

		return server;
	}

	private static computeHttpOriginHeader(config: ServerConfig): string {
		const schema = config.http.tls ? 'https' : 'http';
		const domain = config.domain;
		const port = config.http.listenPort;
		const httpOriginHeader = `${schema}://${domain}:${port}`;

		logger.info(
			'computeHttpOriginHeader() | computed HTTP Origin header: %o',
			httpOriginHeader
		);

		return httpOriginHeader;
	}

	private async readJsonBody(req: http.IncomingMessage): Promise<any> {
		const chunks: Buffer[] = [];
		for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
		if (chunks.length === 0) return {};
		const raw = Buffer.concat(chunks).toString('utf8');
		try {
			return JSON.parse(raw);
		} catch {
			throw new Error('Invalid JSON body');
		}
	}
	
	// yeon
	// edge
	private sendJson(res: http.ServerResponse, status: number, obj: any) {
		const data = Buffer.from(JSON.stringify(obj));
		res.writeHead(status, {
			'content-type': 'application/json',
			'content-length': data.length,
		});
		res.end(data);
	}

	// yeon
	// edge
	private startRemotePipeApi(): void {
		const remotePipeApi = (this.#config as any).remotePipeApi ?? {};
		const port: number = remotePipeApi.port ?? 4445;
		const bindIp: string = remotePipeApi.bindIp ?? '0.0.0.0';
		//const pipeBindIp: string = String(process.env['SERVER_IP']); // 
		const pipeBindIp: string = String(process.env['POD_IP']); // 
		this.#remotePipeHttpServer = http.createServer(async (req, res) => {
			try {
				if (!req.url) return this.sendJson(res, 404, { error: 'No url' });
				if (req.method !== 'POST') return this.sendJson(res, 405, { error: 'POST only' });
	
				const body = await this.readJsonBody(req);
	
				// roomId 기반으로 Room 조회한다. 상대방은 body.roomId를 필수로 보내야 함
				const roomId = body.roomId as string | undefined;
				if (!roomId) return this.sendJson(res, 400, { error: 'missing roomId' });
	
				const room = await this.getOrCreateRoom({
					roomId,
					consumerReplicas: 0,
					usePipeTransports: false,
					useRemotePipe: true,
				});
	
				// "producerRouter"를 사용, 만약 usePipe 옵션인 경우 둘다 해줘야 함
				const router: any = (room as any).producerRouter ?? (room as any).router ?? (room as any).getRouter?.();
				if (!router) return this.sendJson(res, 500, { error: 'Room has no router reference' });
	
				if (req.url === '/pipe/createPipeTransport') {
					const { enableSctp, numSctpStreams, enableRtx, enableSrtp } = body;
	
					const transport = await router.createPipeTransport({
						listenInfo: { 
							protocol: 'udp',
							ip: process.env['MEDIASOUP_LISTEN_IP'] ?? '0.0.0.0',
							announcedAddress: pipeBindIp,
							portRange:{
								min: Number(process.env['MEDIASOUP_MIN_PORT'] ?? 40000),
								max: Number(process.env['MEDIASOUP_MAX_PORT'] ?? 40999),
							}
						 },
						enableSctp: Boolean(enableSctp),
						numSctpStreams: numSctpStreams ?? { OS: 1024, MIS: 1024 },
						enableRtx: Boolean(enableRtx),
						enableSrtp: Boolean(enableSrtp),
					});
	
					this.#remotePipeTransports.set(transport.id, transport);
					
					logger.debug('createPipeTransport and send json');
					return this.sendJson(res, 200, {
						id: transport.id,
						tuple: transport.tuple,
						srtpParameters: transport.srtpParameters,
					});
				}
	
				if (req.url === '/pipe/connectPipeTransport') {
					const { TransportId, ip, port, srtpParameters } = body;
					const transport = this.#remotePipeTransports.get(TransportId);
					if (!transport) return this.sendJson(res, 404, { error: `PipeTransport not found: ${TransportId}` });
	
					await transport.connect({ ip, port, srtpParameters });
					logger.debug('connect and send json');
					return this.sendJson(res, 200, { ok: true });
				}
	
				if (req.url === '/pipe/produce') {
					const { TransportId, id, kind, rtpParameters, paused, appData } = body;
					const transport = this.#remotePipeTransports.get(TransportId);
					if (!transport) return this.sendJson(res, 404, { error: `PipeTransport not found: ${TransportId}` });

					const producer = await transport.produce({ id, kind, rtpParameters, paused, 
						appData: {
							
							peerId: EXTERNAL_PEER_ID, // hardcoding
							source: kind === 'video' ? 'video' : 'audio',
							// 필요하면 추가 필드도 여기에
							...(appData ?? {})
						} as ProducerAppData
					});
					//서버 객체에 producer를 저장 => 이후 이어지는 요청에서 producer를 꺼내 쓰기 위함 
					this.#remotePipeProducers.set(producer.id, producer);
				
					// url에 입력한 roomid, origin에서의 roomid와 동일한 값을 받아왔기 때문에 기존에 있던 룸을 반환
					const room = await this.getOrCreateRoom({ roomId, consumerReplicas: 0, usePipeTransports: false, useRemotePipe: true });

					// 여기서 producer를 받아와서 이미 들어와 있는 시청자들에게 consume 트리거
					// 그러나, 이후 들어오는 시청자들에게도 consume하기 위한 과정 필요하기 때문에 ExternalProducers 맵에 추가
					// 근데 익스터널은 모두 __external__이걸로 취급하기 때문에 추후 중복 우려 가능 이후에 수정해야 함
					room.addExternalProducer(producer);
					await (room as any).onExternalProducer?.(producer);

					logger.debug('produce and send json');
					return this.sendJson(res, 200, { id: producer.id });
				}
	
				if (req.url === '/pipe/closeProducer') {
					const { producerId } = body;
					const p = this.#remotePipeProducers.get(producerId);
					if (p && !p.closed) p.close();
					this.#remotePipeProducers.delete(producerId);
					return this.sendJson(res, 200, { ok: true });
				}
	
				if (req.url === '/pipe/pauseProducer') {
					const { producerId } = body;
					const p = this.#remotePipeProducers.get(producerId);
					if (p && !p.paused) await p.pause();
					return this.sendJson(res, 200, { ok: true });
				}
	
				if (req.url === '/pipe/resumeProducer') {
					const { producerId } = body;
					const p = this.#remotePipeProducers.get(producerId);
					if (p && p.paused) await p.resume();
					return this.sendJson(res, 200, { ok: true });
				}
				
				logger.debug('Unknown endpoint');
				return this.sendJson(res, 404, { error: 'Unknown endpoint' });
			} catch (e: any) {
				logger.debug('startRemotePipeApi error');
				return this.sendJson(res, 500, { error: e?.message ?? String(e) });
			}
		});
	
		this.#remotePipeHttpServer.listen(port, bindIp, () => {
			logger.info(`RemotePipe API listening on http://${bindIp}:${port}`);
		});
	}

	private static async createWorkersAndWebRtcServers(
		config: ServerConfig
	): Promise<WorkersAndWebRtcServers> {
		logger.debug('createWorkersAndWebRtcServers()');

		try {
			const workersAndWebRtcServers: WorkersAndWebRtcServers = new Map();
			const { numWorkers, workerSettings, webRtcServerOptions } =
				config.mediasoup;

			logger.info(
				`createWorkersAndWebRtcServers() | launching ${numWorkers} mediasoup ${numWorkers === 1 ? 'Worker' : 'Workers'}...`
			);

			for (let idx = 0; idx < numWorkers; ++idx) {
				const worker = await mediasoup.createWorker<WorkerAppData>({
					dtlsCertificateFile: workerSettings.dtlsCertificateFile,
					dtlsPrivateKeyFile: workerSettings.dtlsPrivateKeyFile,
					logLevel: workerSettings.logLevel,
					logTags: workerSettings.logTags,
					disableLiburing: workerSettings.disableLiburing,
					appData: {
						idx,
					},
				});

				// Create a WebRtcServer in this Worker.
				// Each mediasoup Worker will run its own WebRtcServer, so those cannot
				// share the same listening port. Hence we increase the port for each
				// Worker.
				const clonnedWebRtcServerOptions = clone(webRtcServerOptions);
				//const portIncrement = workersAndWebRtcServers.size - 1;
				const portIncrement = idx;
				for (const listenInfo of clonnedWebRtcServerOptions.listenInfos) {
					listenInfo.port! += portIncrement;
				}

				const webRtcServer = await worker.createWebRtcServer(
					clonnedWebRtcServerOptions
				);

				workersAndWebRtcServers.set(idx, { worker, webRtcServer });
			}

			return workersAndWebRtcServers;
		} catch (error) {
			logger.error(`createWorkersAndWebRtcServers() | failed: ${error}`);

			throw error;
		}
	}

	private static async createHttpServer(
		config: ServerConfig
	): Promise<https.Server | http.Server> {
		logger.debug('createHttpServer()');

		try {
			const tls = config.http.tls
				? {
						cert: fs.readFileSync(config.http.tls.cert),
						key: fs.readFileSync(config.http.tls.key),
					}
				: undefined;

			if (!tls) {
				logger.debug(
					'createHttpServer() | no TLS provided in the configuration, fallback to HTTP server'
				);
			}

			const httpServer = tls ? https.createServer(tls) : http.createServer();

			await new Promise<void>((resolve, reject) => {
				httpServer.listen(
					{ port: config.http.listenPort, host: config.http.listenIp },
					resolve
				);

				httpServer.on('error', error => {
					reject(error);
				});
			});

			return httpServer;
		} catch (error) {
			logger.error(`createHttpServer() | failed: ${error}`);

			throw error;
		}
	}

	private constructor({
		config,
		workersAndWebRtcServers,
		httpServer,
		wsServer,
		apiServer,
		networkThrottleSecret,
	}: ServerConstructorOptions) {
		super();

		logger.debug('constructor()');

		this.#config = config;
		this.#workersAndWebRtcServers = workersAndWebRtcServers;
		this.#httpServer = httpServer;
		this.#wsServer = wsServer;
		this.#apiServer = apiServer;
		this.#networkThrottleSecret = networkThrottleSecret;
		this.#createdAt = new Date();

		// We need to verify that all mediasoup Workers are alive at this point
		// (just in case they died for whatever reason before reaching this
		// constructor).
		for (const { worker } of this.#workersAndWebRtcServers.values()) {
			if (worker.closed) {
				throw new InvalidStateError(
					`mediasoup Worker is closed [pid:${worker.pid}, died:${worker.died}]`
				);
			}

			this.handleWorker(worker);
		}

		this.handleHttpServer();
		this.handleWsServer();
		this.handleApiServer();

		if (this.#networkThrottleSecret) {
			process.env['LOG_THROTTLE'] = 'true';
		}
	}

	close(): void {
		logger.debug('close()');

		if (this.#closed) {
			return;
		}

		this.#closed = true;

		for (const { queue, room } of this.#roomQueues.values()) {
			queue.stop();
			room?.close();
		}

		for (const { worker } of this.#workersAndWebRtcServers.values()) {
			worker.close();
		}

		// Stop listening for HTTP/WS connections.
		this.#httpServer.close();
		// yeon
		this.#remotePipeHttpServer?.close();
		
		this.#httpServer.closeAllConnections();

		// Close all existing HTTP/WS connections.
		for (const httpConnection of this.#httpConnections) {
			httpConnection.destroy();
		}

		if (this.#networkThrottleEnabled) {
			this.stopNetworkThrottleInternal().catch(() => {});
		}

		// NOTE: We don't stop this.#networkThrottleAwaitQueue on purpose.

		this.emit('closed');
	}

	serialize(): SerializedServer {
		return {
			createdAt: this.#createdAt,
			numWorkers: this.#workersAndWebRtcServers.size,
			networkThrottleEnabled: this.#networkThrottleEnabled,
			numRooms: this.#roomQueues.size,
			rooms: Array.from(this.#roomQueues.values())
				.filter(({ room }) => room !== undefined)
				// NOTE: TypeScript is not smart to know that `room` here is guaranteed
				// to exist.
				.map(({ room }) => room!.serialize()),
		};
	}

	isNetworkThrottleEnabled(): boolean {
		return this.#networkThrottleEnabled;
	}

	/**
	 * Get a Room instance (or create one if it does not exist).
	 */
	private async getOrCreateRoom({
		roomId,
		consumerReplicas = 0,
		usePipeTransports = false,
		useRemotePipe = true,
	}: {
		roomId: RoomId;
		consumerReplicas?: number;
		usePipeTransports?: boolean;
		useRemotePipe?: boolean;
	}): Promise<Room> {
		if (usePipeTransports && this.#config.mediasoup.numWorkers < 2) {
			throw new InvalidStateError(
				'at least 2 mediasoup Workers are needed to create a Room with usePipeTransports option'
			);
		}

		let roomData = this.#roomQueues.get(roomId);

		if (!roomData) {
			roomData = {
				queue: new AwaitQueue(),
				room: undefined,
			};

			this.#roomQueues.set(roomId, roomData);
		}

		// Enqueue it to avoid race conditions when multiple users join at the same
		// time requesting the same `roomId`.
		return roomData.queue.push<Room>(async () => {
			// Verify that the room was not removed just before this task execution
			// begins.
			if (!this.#roomQueues.get(roomId)) {
				throw new InvalidStateError(`Room with id '${roomId}' has been closed`);
			}

			if (roomData.room) {
				return roomData.room;
			}

			logger.info(
				'getOrCreateRoom() | creating a new Room [roomId:%o, usePipeTransports:%o]',
				roomId,
				usePipeTransports
			);

			const { worker: producerWorker, webRtcServer: producerWebRtcServer } =
				this.getNextWorkerAndWebRtcServer();

			const { worker: consumerWorker, webRtcServer: consumerWebRtcServer } =
				usePipeTransports
					? this.getNextWorkerAndWebRtcServer()
					: {
							worker: producerWorker,
							webRtcServer: producerWebRtcServer,
						};

			const { mediaCodecs } = this.#config.mediasoup.routerOptions;

			const producerRouter = await producerWorker.createRouter({
				mediaCodecs,
			});

			const consumerRouter = usePipeTransports
				? await consumerWorker.createRouter({
						mediaCodecs,
					})
				: producerRouter;

			const room = await Room.create({
				roomId,
				consumerReplicas,
				usePipeTransports,
				config: this.#config,
				producerRouter,
				consumerRouter,
				producerWebRtcServer,
				consumerWebRtcServer,
			});

			roomData.room = room;
			this.handleRoom(room);

			this.emit('new-room', room);

			//--------------------------------------------------------------------------------------------
			// youngrhee
			// 새로 만들어진 room이니, origin에게 지금 활성화된 producer들을
			// 다시 파이프해달라고 요청 (viewer 응답을 막지 않도록 기다리지 않음)
			void this.requestResyncFromOrigin(roomId).catch(error => {
				logger.warn(
					'getOrCreateRoom() | requestResyncFromOrigin() failed [roomId:%o]: %o',
					roomId,
					error
				);
			});
			//--------------------------------------------------------------------------------------------

			return room;
		}, 'getOrCreateRoom()');
	}

	private getNextWorkerAndWebRtcServer(): {
		worker: mediasoupTypes.Worker<WorkerAppData>;
		webRtcServer: mediasoupTypes.WebRtcServer;
	} {
		const { worker, webRtcServer } = this.#workersAndWebRtcServers.get(
			this.#nextWorkerIdx
		)!;

		if (++this.#nextWorkerIdx === this.#workersAndWebRtcServers.size) {
			this.#nextWorkerIdx = 0;
		}

		return { worker, webRtcServer };
	}

	//----------------------------------------------------------------------------
	//youngrhee
	// edge: origin에게 "나 새로 열렸어, 지금 활성 producer들 다시 파이프해줘"라고 요청
	private async requestResyncFromOrigin(roomId: string): Promise<void> {
		const originIp = process.env['PUBLIC_IP'];

		if (!originIp) {
			logger.warn(
				'requestResyncFromOrigin() | ORIGIN_API_URL not configured, skipping resync [roomId:%o]',
				roomId
			);

			return;
		}

		const originHttpPort = process.env['ORIGIN_HTTP_LISTEN_PORT'] ?? '4443';
    	const originBaseUrl = `https://${originIp}:${originHttpPort}`;

		// 이 edge 서버가 origin에게 "나한테 파이프할 땐 이 주소로 해줘"라고 알려줄 자기 자신의 주소
		const remotePipeApi = (this.#config as any).remotePipeApi ?? {};
    	const myPort: number = remotePipeApi.port ?? 4445;
    	const myIp: string = process.env['POD_IP'] ?? '0.0.0.0';
    	const myUrl = `http://${myIp}:${myPort}`;
		const bodyStr = JSON.stringify({ edgeUrl: myUrl });
		
		try {
			const caCert = fs.readFileSync('./certs/cert.pem');

			const result = await new Promise<{ status: number; body: string }>((resolve, reject) => {
            const req = https.request(
                {
                    hostname: originIp,
                    port: originHttpPort,
                    path: `/rooms/${roomId}/resync`,
                    method: 'POST',
                    ca: caCert,
					checkServerIdentity: () => undefined,
                    headers: {
                        'content-type': 'application/json',
                        'content-length': Buffer.byteLength(bodyStr),
                        origin: originBaseUrl,
                    },
                },
                res => {
                    let data = '';
                    res.on('data', chunk => { data += chunk; });
                    res.on('end', () => {
                        resolve({ status: res.statusCode ?? 0, body: data });
                    });
                }
            );

            	req.on('error', reject);
            	req.write(bodyStr);
            	req.end();
			});

			if (result.status < 200 || result.status >= 300) {
				logger.warn(
					'requestResyncFromOrigin() | origin responded with non-OK status [roomId:%o, status:%o, body:%o]',
					roomId,
					result.status,
					result.body
				);
				return;
			}

       		 const parsed = JSON.parse(result.body) as { producerCount: number };

			logger.info(
				'requestResyncFromOrigin() | succeeded [roomId:%o, producerCount:%o]',
				roomId,
				parsed.producerCount
			);
		} catch (error) {
			logger.warn(
				'requestResyncFromOrigin() | failed [roomId:%o]: %o',
				roomId,
				error
			);
		}
	}

	// edge: 이 room이 닫혔다는 걸 origin에게 알려서, 그 파이프를 닫아달라고 요청
	private async notifyOriginRoomClosed(roomId: string): Promise<void> {
		const originIp = process.env['PUBLIC_IP'];
		if (!originIp) return;

		const originHttpPort = Number(process.env['HTTP_LISTEN_PORT'] ?? '4443');

		const remotePipeApi = (this.#config as any).remotePipeApi ?? {};
		const myPort: number = remotePipeApi.port ?? 4445;
		const myIp: string = process.env['POD_IP'] ?? '0.0.0.0';
		const myUrl = `http://${myIp}:${myPort}`;

		const originBaseUrlForHeader = `https://${originIp}:${originHttpPort}`;
		const bodyStr = JSON.stringify({ edgeUrl: myUrl });

		try {
			const caCert = fs.readFileSync('./certs/cert.pem');

			const result = await new Promise<{ status: number; body: string }>((resolve, reject) => {
				const req = https.request(
					{
						hostname: originIp,
						port: originHttpPort,
						path: `/rooms/${roomId}/edge-closed`,
						method: 'POST',
						ca: caCert,
						checkServerIdentity: () => undefined,
						headers: {
							'content-type': 'application/json',
							'content-length': Buffer.byteLength(bodyStr),
							origin: originBaseUrlForHeader,
						},
					},
					res => {
						let data = '';
						res.on('data', chunk => { data += chunk; });
						res.on('end', () => resolve({ status: res.statusCode ?? 0, body: data }));
					}
				);

				req.on('error', reject);
				req.write(bodyStr);
				req.end();
			});

			if (result.status < 200 || result.status >= 300) {
				logger.warn(
					'notifyOriginRoomClosed() | origin responded with non-OK status [roomId:%o, status:%o, body:%o]',
					roomId, result.status, result.body
				);
				return;
			}

			const parsed = JSON.parse(result.body) as { closed: boolean; reason?: string };

			logger.info(
				'notifyOriginRoomClosed() | succeeded [roomId:%o, closed:%o, reason:%o]',
				roomId,
				parsed.closed,
				parsed.reason
			);
		} catch (error) {
			logger.warn('notifyOriginRoomClosed() | failed [roomId:%o]: %o', roomId, error);
		}
	}
	//----------------------------------------------------------------------------
	private async applyNetworkThrottle({
		secret,
		options,
	}: {
		secret: string;
		options: throttleTypes.ThrottleStartOptions;
	}): Promise<void> {
		logger.debug('applyNetworkThrottle() [options:%o]', options);

		if (
			!this.#networkThrottleSecret ||
			!secret ||
			secret !== this.#networkThrottleSecret
		) {
			throw new ForbiddenError('GO TO HELL 🖕🏼');
		}

		await this.applyNetworkThrottleInternal(options);
	}

	private async stopNetworkThrottle({
		secret,
	}: {
		secret: string;
	}): Promise<void> {
		logger.debug('stopNetworkThrottle()');

		if (
			!this.#networkThrottleSecret ||
			!secret ||
			secret !== this.#networkThrottleSecret
		) {
			throw new ForbiddenError('GO TO HELL 🖕🏼');
		}

		await this.stopNetworkThrottleInternal();
	}

	private async applyNetworkThrottleInternal(
		options: throttleTypes.ThrottleStartOptions
	): Promise<void> {
		// Enqueue it to avoid race conditions when calling multiple times to
		// throttle API.
		return this.#networkThrottleAwaitQueue.push(async () => {
			logger.debug('applyNetworkThrottleInternal() [options:%o]', options);

			if (this.#networkThrottleEnabled) {
				await this.stopNetworkThrottleInternal();
			}

			try {
				await throttle.start(options);
			} catch (error) {
				logger.error(
					'applyNetworkThrottleInternal() | throttle.start() failed [options:%o]:',
					options,
					error
				);

				throw error;
			}

			logger.info(
				'applyNetworkThrottleInternal() | network throttle applied [options:%o]',
				options
			);

			this.#networkThrottleEnabled = true;
		}, 'applyNetworkThrottleInternal()');
	}

	private async stopNetworkThrottleInternal(): Promise<void> {
		// Enqueue it to avoid race conditions when calling multiple times to
		// throttle API.
		return this.#networkThrottleAwaitQueue.push(async () => {
			logger.debug('stopNetworkThrottleInternal()');

			// Let's be optimistic.
			const savedNetworkThrottleEnabled = this.#networkThrottleEnabled;
			const savedNetworkThrottleEnabledByRoomId =
				this.#networkThrottleEnabledByRoomId;

			this.#networkThrottleEnabled = false;
			this.#networkThrottleEnabledByRoomId = undefined;

			let stopError: Error | undefined = undefined;

			try {
				await throttle.stop();
			} catch (error) {
				logger.error(
					'stopNetworkThrottleInternal() | throttle.stop() failed:',
					error
				);

				stopError = error as Error;
			}

			try {
				await throttle.stop({ localhost: true });
			} catch (error) {
				logger.error(
					'stopNetworkThrottleInternal() | throttle.stop({ localhost: true }) failed:',
					error
				);

				stopError = error as Error;
			}

			if (stopError) {
				this.#networkThrottleEnabled = savedNetworkThrottleEnabled;
				this.#networkThrottleEnabledByRoomId =
					savedNetworkThrottleEnabledByRoomId;

				throw stopError;
			}

			logger.info('stopNetworkThrottleInternal() | network throttle stopped');
		}, 'stopNetworkThrottleInternal()');
	}

	private handleWorker(worker: mediasoupTypes.Worker<WorkerAppData>): void {
		worker.on('died', () => {
			logger.error('mediasoup Worker died [pid:%o]', worker.pid);

			this.close();
			this.emit('died');
		});

		worker.observer.on('close', () => {
			this.#workersAndWebRtcServers.delete(worker.appData.idx);

			// Ignore if Server is closed or if the Worker died since then its 'died'
			// event fired already.
			if (this.#closed || worker.died) {
				return;
			}

			logger.error('mediasoup Worker unexpectedly closed [pid:%o]', worker.pid);

			this.close();
			this.emit('died');
		});
	}

	private handleHttpServer(): void {
		// Let's keep a list with the HTTP connections (including WebSocket
		// upgrades) to later be able to close them all.
		this.#httpServer.on('connection', (httpConnection: net.Socket) => {
			this.#httpConnections.add(httpConnection);

			httpConnection.on('close', () => {
				this.#httpConnections.delete(httpConnection);
			});
		});

		this.#httpServer.on('request', this.#apiServer.getApp());
	}

	private handleWsServer(): void {
		this.#wsServer.on(
			'get-or-create-room',
			({ roomId, consumerReplicas, usePipeTransports }, resolve, reject) => {
				this.getOrCreateRoom({ roomId, consumerReplicas, usePipeTransports })
					.then(resolve)
					.catch(reject);
			}
		);
	}

	private handleApiServer(): void {
		this.#apiServer.on('get-room', ({ roomId }, callback, errback) => {
			const room = this.#roomQueues.get(roomId)?.room;

			if (room) {
				callback(room);
			} else {
				errback(new RoomNotFound(`Room '${roomId}' doesn't exist`));
			}
		});
	}

	private handleRoom(room: Room): void {
		room.on('closed', () => {
			const roomData = this.#roomQueues.get(room.id);

			if (roomData) {
				roomData.room = undefined;
			}

			this.#roomQueues.delete(room.id);

			if (room.id === this.#networkThrottleEnabledByRoomId) {
				logger.info(
					'the Room that applied network throttle closed, stopping network throttle...'
				);

				this.stopNetworkThrottleInternal().catch(() => {});
			}

			this.#networkThrottleEnabledByRoomId = undefined;
			//--------------------------------------------------------------------------------
			//youngrhee: 이 방이 닫혔으니, origin에게도 이 edge로 가는 파이프를 닫아달라고 알림
			void this.notifyOriginRoomClosed(room.id).catch(error => {
				logger.warn(
					'handleRoom() | notifyOriginRoomClosed() failed [roomId:%o]: %o',
					room.id,
					error
				);
			});
			//--------------------------------------------------------------------------------
		});

		room.on(
			'apply-network-throttle',
			({ secret, options }: { secret: any; options: any }, resolve: any, reject: any) => {
				this.applyNetworkThrottle({ secret, options })
					.then(() => {
						this.#networkThrottleEnabledByRoomId = room.id;

						resolve();
					})
					.catch(reject);
			}
		);

		room.on('stop-network-throttle', ({ secret }: { secret: any }, resolve: any, reject: any) => {
			this.stopNetworkThrottle({ secret }).then(resolve).catch(reject);
		});
	}
}
