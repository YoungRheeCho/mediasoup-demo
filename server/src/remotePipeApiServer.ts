// // remotePipeApiServer.ts
// import express from 'express';
// import type * as mediasoup from 'mediasoup';
// import type { Room, RoomId } from './room'; // 경로는 당신 프로젝트 구조에 맞게 조정

// type TransportStore = Map<string, mediasoup.types.PipeTransport>;
// type ProducerStore = Map<string, mediasoup.types.Producer>;

// type RouterRole = 'producer' | 'consumer';

// export function createRemotePipeApiServer(params: {
//   /**
//    * roomId로 Room을 찾아오거나(없으면 생성) 반환하는 함수.
//    * => 이걸 server.ts(또는 RoomManager) 쪽의 getOrCreateRoom에 연결하면 됨.
//    */
//   getOrCreateRoom: (opts: {
//     roomId: RoomId;
//     consumerReplicas?: number;
//     usePipeTransports?: boolean;
//     useRemotePipe?: boolean;
//   }) => Promise<Room>;

//   pipeTransports: TransportStore; // transportId -> PipeTransport (요청 간 상태 유지)
//   producers: ProducerStore;       // producerId -> Producer (pause/resume/close용)

//   /**
//    * 이 노드(원격 SFU)가 PipeTransport를 바인드할 listenInfo를 제공.
//    * (요청자가 준 ip를 신뢰하지 않기 위해 server가 결정)
//    */
//   bindListenInfoForPipe: () => {
//     protocol: 'udp' | 'tcp';
//     ip: string;
//     announcedAddress?: string;
//   };
// }) {
//   const { getOrCreateRoom, pipeTransports, producers, bindListenInfoForPipe } = params;

//   const app = express();
//   app.use(express.json());

//   /**
//    * ✅ roomId 기반 PipeTransport 생성
//    * 요청: { roomId, routerRole?, enableSctp?, numSctpStreams?, enableRtx?, enableSrtp? }
//    * 응답: { id, tuple, srtpParameters, routerId? }
//    */
//   app.post('/pipe/createPipeTransport', async (req, res) => {
//     try {
//       const {
//         roomId,
//         routerRole = 'consumer', // 기본값은 consumer 추천(뷰어가 붙는 쪽일 가능성이 높아서)
//         consumerReplicas,
//         usePipeTransports,
//         useRemotePipe,
//         enableSctp,
//         numSctpStreams,
//         enableRtx,
//         enableSrtp,
//       } = req.body as {
//         roomId: RoomId;
//         routerRole?: RouterRole;
//         consumerReplicas?: number;
//         usePipeTransports?: boolean;
//         useRemotePipe?: boolean;
//         enableSctp?: boolean;
//         numSctpStreams?: { OS: number; MIS: number };
//         enableRtx?: boolean;
//         enableSrtp?: boolean;
//       };

//       if (!roomId) return res.status(400).send('missing roomId');

//       // roomId로 Room을 가져오거나 생성
//       const room = await getOrCreateRoom({
//         roomId,
//         consumerReplicas,
//         usePipeTransports,
//         useRemotePipe,
//       });

//       // role에 맞는 Router 선택
//       const router = room.getRouter(routerRole);

//       // remote는 자신의 listenInfo를 사용 (caller가 준 ip 신뢰 X)
//       const listenInfo = bindListenInfoForPipe();

//       const transport = await router.createPipeTransport({
//         listenInfo,
//         enableSctp,
//         numSctpStreams,
//         enableRtx,
//         enableSrtp,
//       });

//       pipeTransports.set(transport.id, transport);

//       // transport close 시 정리
//       transport.observer.on('close', () => {
//         pipeTransports.delete(transport.id);
//       });

//       res.json({
//         id: transport.id,
//         tuple: transport.tuple,
//         srtpParameters: transport.srtpParameters,
//         routerId: router.id, // 디버깅용 (필요없으면 제거 가능)
//       });
//     } catch (e: any) {
//       res.status(500).send(e?.stack ?? String(e));
//     }
//   });

//   /**
//    * PipeTransport connect
//    * 요청: { transportId, ip, port, srtpParameters? }
//    */
//   app.post('/pipe/connectPipeTransport', async (req, res) => {
//     try {
//       const { transportId, ip, port, srtpParameters } = req.body as {
//         transportId: string;
//         ip: string;
//         port: number;
//         srtpParameters?: any;
//       };

//       const transport = pipeTransports.get(transportId);
//       if (!transport) return res.status(404).send(`PipeTransport not found: ${transportId}`);

//       await transport.connect({ ip, port, srtpParameters });
//       res.json({ ok: true });
//     } catch (e: any) {
//       res.status(500).send(e?.stack ?? String(e));
//     }
//   });

//   /**
//    * PipeProducer 생성 (원격 router의 PipeTransport 위에서)
//    * 요청: { transportId, id?, kind, rtpParameters, paused?, appData? }
//    * 응답: { id }
//    */
//   app.post('/pipe/produce', async (req, res) => {
//     try {
//       const { transportId, id, kind, rtpParameters, paused, appData } = req.body as {
//         transportId: string;
//         id?: string;
//         kind: mediasoup.types.MediaKind;
//         rtpParameters: mediasoup.types.RtpParameters;
//         paused?: boolean;
//         appData?: any;
//       };

//       const transport = pipeTransports.get(transportId);
//       if (!transport) return res.status(404).send(`PipeTransport not found: ${transportId}`);

//       const producer = await transport.produce({
//         id,
//         kind,
//         rtpParameters,
//         paused,
//         appData,
//       });

//       producers.set(producer.id, producer);

//       producer.observer.on('close', () => {
//         producers.delete(producer.id);
//       });

//       res.json({ id: producer.id });
//     } catch (e: any) {
//       res.status(500).send(e?.stack ?? String(e));
//     }
//   });

//   app.post('/pipe/closeProducer', async (req, res) => {
//     const { producerId } = req.body as { producerId: string };
//     const p = producers.get(producerId);
//     if (p && !p.closed) p.close();
//     producers.delete(producerId);
//     res.json({ ok: true });
//   });

//   app.post('/pipe/pauseProducer', async (req, res) => {
//     const { producerId } = req.body as { producerId: string };
//     const p = producers.get(producerId);
//     if (p && !p.paused) await p.pause();
//     res.json({ ok: true });
//   });

//   app.post('/pipe/resumeProducer', async (req, res) => {
//     const { producerId } = req.body as { producerId: string };
//     const p = producers.get(producerId);
//     if (p && p.paused) await p.resume();
//     res.json({ ok: true });
//   });

//   return app;
// }