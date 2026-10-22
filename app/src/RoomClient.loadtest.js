import protooClient from 'protoo-client';
import * as mediasoupClient from 'mediasoup-client';
import { AwaitQueue } from 'awaitqueue';

import Logger from './Logger';
import { getProtooUrl } from './urlFactory';

const logger = new Logger('RoomClientLoadTest');


export default class RoomClientLoadTest {
  constructor({
    roomId,
    peerId,
    displayName,
    device,
    handlerName,
    forceTcp = false,
    preferLocalCodecsOrder = false,
    consumeAudio = false,
    mediaSinkRoot = null
  }) {
    this._roomId = roomId;
    this._peerId = peerId;
    this._displayName = displayName;

    this._device =
      device || {
        flag: 'loadtest',
        name: 'loadtest',
        version: '1'
      };

    this._handlerName = handlerName;
    this._forceTcp = Boolean(forceTcp);

    this._preferLocalCodecsOrder =
      Boolean(preferLocalCodecsOrder);

    this._consumeAudio =
      Boolean(consumeAudio);

    this._closed = false;

    this._protooUrl =
      getProtooUrl({
        roomId,
        peerId,
        consumerReplicas: undefined,
        usePipeTransports: false
      });

    this._protoo = null;
    this._mediasoupDevice = null;
    this._recvTransport = null;

    this._consumers =
      new Map();

    this._videoElements =
      new Map();

    this._consumingAwaitQueue =
      new AwaitQueue();

    this._mediaSinkRoot =
      mediaSinkRoot ||
      document.getElementById('media-sink') ||
      document.body;

    this._joinPromise = null;

    /*
     * Playwright debug에서 그대로 볼 수 있는 state.
     */
    this.state = {
      peerId,

      joined: false,
      closed: false,

      videoConsumers: 0,

      transportState: 'new',

      lastError: null
    };
  }


  /*
   * ============================================================
   * Public
   * ============================================================
   */

  async join() {
    if (this._joinPromise) {
      return this._joinPromise;
    }

    this._joinPromise =
      new Promise(
        (resolve, reject) => {
          let settled = false;

          const fail = error => {
            this.state.lastError =
              error?.message ||
              String(error);

            if (settled)
              return;

            settled = true;

            reject(error);
          };


          const protooTransport =
            new protooClient.WebSocketTransport(
              this._protooUrl
            );


          this._protoo =
            new protooClient.Peer(
              protooTransport
            );


          /*
           * Server request handler는 join 전에 먼저 설치.
           *
           * join 과정에서 server가 newConsumer request를
           * 보낼 수 있으므로 중요.
           */
          this._setupProtooHandlers();


          this._protoo.on(
            'open',
            async () => {
              try {
                await this._joinRoom();

                if (!settled) {
                  settled = true;
                  resolve();
                }
              }
              catch (error) {
                fail(error);
              }
            }
          );


          this._protoo.on(
            'failed',
            () => {
              fail(
                new Error(
                  `protoo WebSocket failed` +
                  ` peerId=${this._peerId}`
                )
              );
            }
          );


          this._protoo.on(
            'disconnected',
            () => {
              if (
                this.state.transportState !==
                'closed'
              ) {
                this.state.transportState =
                  'disconnected';
              }

              if (!this.state.joined) {
                fail(
                  new Error(
                    `protoo disconnected before join` +
                    ` peerId=${this._peerId}`
                  )
                );
              }
            }
          );


          this._protoo.on(
            'close',
            () => {
              if (this._closed)
                return;

              this.state.closed = true;

              if (!this.state.joined) {
                fail(
                  new Error(
                    `protoo closed before join` +
                    ` peerId=${this._peerId}`
                  )
                );
              }
            }
          );
        }
      );


    return this._joinPromise;
  }


  async getRtpSnapshot() {
    let packetsReceived = 0;
    let bytesReceived = 0;

    let videoPacketsReceived = 0;
    let videoBytesReceived = 0;

    let audioPacketsReceived = 0;
    let audioBytesReceived = 0;

    let packetsLost = 0;

    let framesReceived = 0;
    let framesDecoded = 0;

    let inboundVideoStreams = 0;


    for (
      const consumer
      of this._consumers.values()
    ) {
      if (!consumer.rtpReceiver)
        continue;

      try {
        const report =
          await consumer.rtpReceiver.getStats();


        report.forEach(stat => {
          if (
            stat.type !== 'inbound-rtp' ||
            stat.isRemote
          ) {
            return;
          }


          const kind =
            stat.kind ||
            stat.mediaType;


          const packets =
            stat.packetsReceived ?? 0;

          const bytes =
            stat.bytesReceived ?? 0;


          packetsReceived +=
            packets;

          bytesReceived +=
            bytes;

          packetsLost +=
            stat.packetsLost ?? 0;


          if (kind === 'video') {
            inboundVideoStreams++;

            videoPacketsReceived +=
              packets;

            videoBytesReceived +=
              bytes;

            framesReceived +=
              stat.framesReceived ?? 0;

            framesDecoded +=
              stat.framesDecoded ?? 0;
          }
          else if (kind === 'audio') {
            audioPacketsReceived +=
              packets;

            audioBytesReceived +=
              bytes;
          }
        });
      }
      catch (error) {
        logger.warn(
          'getRtpSnapshot() failed:%o',
          error
        );
      }
    }


    return {
      peerId:
        this._peerId,

      joined:
        this.state.joined,

      transportState:
        this.state.transportState,

      videoConsumers:
        this.state.videoConsumers,

      inboundVideoStreams,

      packetsReceived,
      bytesReceived,

      videoPacketsReceived,
      videoBytesReceived,

      audioPacketsReceived,
      audioBytesReceived,

      packetsLost,

      framesReceived,
      framesDecoded
    };
  }


  close() {
    if (this._closed)
      return;

    this._closed = true;

    this.state.closed = true;


    for (
      const consumerId
      of Array.from(
        this._consumers.keys()
      )
    ) {
      this._removeConsumer(
        consumerId
      );
    }


    if (this._recvTransport) {
      try {
        this._recvTransport.close();
      }
      catch {}

      this._recvTransport = null;
    }


    if (this._protoo) {
      try {
        this._protoo.close();
      }
      catch {}

      this._protoo = null;
    }


    try {
      this._consumingAwaitQueue.close();
    }
    catch {}


    this.state.joined = false;
    this.state.transportState = 'closed';
  }


  /*
   * ============================================================
   * protoo handlers
   * ============================================================
   */

  _setupProtooHandlers() {
    this._protoo.on(
      'request',
      async (
        request,
        accept,
        reject
      ) => {
        switch (request.method) {
          case 'newConsumer': {
            await this._consumingAwaitQueue.push(
              async () => {
                try {
                  await this._handleNewConsumer(
                    request,
                    accept,
                    reject
                  );
                }
                catch (error) {
                  logger.error(
                    '[%s] newConsumer failed:%o',
                    this._peerId,
                    error
                  );

                  try {
                    reject(
                      500,
                      error.message
                    );
                  }
                  catch {}
                }
              }
            );

            break;
          }


          case 'newDataConsumer': {
            /*
             * Viewer load-test에는 incoming
             * DataChannel 불필요.
             */
            reject(
              403,
              'load-test viewer does not consume DataChannels'
            );

            break;
          }


          default: {
            reject(
              404,
              `unsupported request method ${request.method}`
            );

            break;
          }
        }
      }
    );


    this._protoo.on(
      'notification',
      notification => {
        switch (
          notification.method
        ) {
          case 'consumerClosed': {
            const {
              consumerId
            } = notification.data;

            this._removeConsumer(
              consumerId
            );

            break;
          }


          case 'consumerPaused': {
            const {
              consumerId
            } = notification.data;

            const consumer =
              this._consumers.get(
                consumerId
              );

            if (
              consumer &&
              !consumer.paused
            ) {
              consumer.pause();
            }

            break;
          }


          case 'consumerResumed': {
            const {
              consumerId
            } = notification.data;

            const consumer =
              this._consumers.get(
                consumerId
              );

            if (
              consumer &&
              consumer.paused
            ) {
              consumer.resume();
            }

            break;
          }


          default:
            break;
        }
      }
    );
  }


  /*
   * ============================================================
   * Join
   * ============================================================
   */

  async _joinRoom() {
    this._mediasoupDevice =
      await mediasoupClient.Device.factory({
        handlerName:
          this._handlerName
      });


    const {
      routerRtpCapabilities
    } =
      await this._protoo.request(
        'getRouterRtpCapabilities'
      );


    await this._mediasoupDevice.load({
      routerRtpCapabilities,

      preferLocalCodecsOrder:
        this._preferLocalCodecsOrder
    });


    /*
     * Receive-only load-test client.
     */
    await this._createRecvTransport();


    /*
     * No incoming DataChannel.
     */
    await this._protoo.request(
      'join',
      {
        displayName:
          this._displayName,

        device:
          this._device,

        rtpCapabilities:
          this._mediasoupDevice
            .rtpCapabilities,

        sctpCapabilities:
          undefined
      }
    );


    this.state.joined = true;
  }


  async _createRecvTransport() {
    const transportInfo =
      await this._protoo.request(
        'createWebRtcTransport',
        {
          sctpCapabilities:
            undefined,

          forceTcp:
            this._forceTcp,

          appData: {
            direction: 'consumer',
            loadTest: true
          }
        }
      );


    const {
      transportId,
      iceParameters,
      iceCandidates,
      dtlsParameters,
      sctpParameters
    } = transportInfo;


    this._recvTransport =
      this._mediasoupDevice
        .createRecvTransport({
          id:
            transportId,

          iceParameters,
          iceCandidates,

          dtlsParameters: {
            ...dtlsParameters,
            role: 'auto'
          },

          sctpParameters,

          iceServers: []
        });


    this._recvTransport.on(
      'connect',
      (
        {
          dtlsParameters:
            localDtlsParameters
        },
        callback,
        errback
      ) => {
        this._protoo
          .request(
            'connectWebRtcTransport',
            {
              transportId:
                this._recvTransport.id,

              dtlsParameters:
                localDtlsParameters
            }
          )
          .then(callback)
          .catch(errback);
      }
    );


    this._recvTransport.on(
      'connectionstatechange',
      connectionState => {
        this.state.transportState =
          connectionState;
      }
    );
  }


  /*
   * ============================================================
   * Consumer
   * ============================================================
   */

  async _handleNewConsumer(
    request,
    accept,
    reject
  ) {
    const {
      peerId,
      consumerId,
      producerId,
      kind,
      rtpParameters,
      producerPaused,
      appData = {}
    } = request.data;


    /*
     * 현재 viewer benchmark는 video만 필요.
     */
    if (
      kind !== 'video' &&
      !this._consumeAudio
    ) {
      reject(
        403,
        'load-test viewer consumes video only'
      );

      return;
    }


    if (!this._recvTransport) {
      reject(
        503,
        'recv transport not ready'
      );

      return;
    }


    const consumer =
      await this._recvTransport.consume({
        id:
          consumerId,

        producerId,
        kind,
        rtpParameters,

        streamId:
          `${peerId}-${
            appData.source ===
            'screensharing'
              ? 'screensharing'
              : 'audio-video'
          }`,

        appData: {
          ...appData,
          peerId
        }
      });


    this._consumers.set(
      consumer.id,
      consumer
    );


    consumer.on(
      'transportclose',
      () => {
        this._removeConsumer(
          consumer.id,
          false
        );
      }
    );


    consumer.on(
      'trackended',
      () => {
        this._removeVideoElement(
          consumer.id
        );
      }
    );


    /*
     * 실제 decode/render 결과가 필요 없더라도
     * remote video track에 active sink를 유지한다.
     */
    if (
      consumer.kind ===
      'video'
    ) {
      this._createVideoSink(
        consumer
      );
    }


    this._updateVideoConsumerCount();


    /*
     * server가 paused Consumer를 resume할 수 있도록
     * request에 응답.
     */
    accept();


    logger.debug(
      '[%s] consumer created' +
      ' [id:%s, producerId:%s, kind:%s, producerPaused:%s]',
      this._peerId,
      consumer.id,
      producerId,
      kind,
      producerPaused
    );
  }


  /*
   * ============================================================
   * Video sink
   * ============================================================
   */

  _createVideoSink(
    consumer
  ) {
    const video =
      document.createElement(
        'video'
      );


    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;

    video.setAttribute(
      'playsinline',
      ''
    );


    /*
     * display:none / visibility:hidden 사용하지 않음.
     *
     * video pipeline sink는 유지하면서
     * visual footprint만 1x1로 제한.
     */
    video.style.width =
      '1px';

    video.style.height =
      '1px';

    video.style.minWidth =
      '1px';

    video.style.minHeight =
      '1px';

    video.style.margin =
      '0';

    video.style.padding =
      '0';

    video.style.border =
      '0';

    video.style.pointerEvents =
      'none';


    video.dataset.peerId =
      this._peerId;

    video.dataset.consumerId =
      consumer.id;


    video.srcObject =
      new MediaStream([
        consumer.track
      ]);


    this._mediaSinkRoot
      .appendChild(
        video
      );


    this._videoElements.set(
      consumer.id,
      video
    );


    void video
      .play()
      .catch(error => {
        logger.debug(
          '[%s] video.play() failed:%o',
          this._peerId,
          error
        );
      });
  }


  _removeVideoElement(
    consumerId
  ) {
    const video =
      this._videoElements.get(
        consumerId
      );


    if (!video)
      return;


    try {
      video.pause();
      video.srcObject = null;
      video.remove();
    }
    catch {}


    this._videoElements.delete(
      consumerId
    );
  }


  _removeConsumer(
    consumerId,
    closeConsumer = true
  ) {
    const consumer =
      this._consumers.get(
        consumerId
      );


    if (!consumer)
      return;


    this._removeVideoElement(
      consumerId
    );


    if (
      closeConsumer &&
      !consumer.closed
    ) {
      try {
        consumer.close();
      }
      catch {}
    }


    this._consumers.delete(
      consumerId
    );


    this._updateVideoConsumerCount();
  }


  _updateVideoConsumerCount() {
    let count = 0;

    for (
      const consumer
      of this._consumers.values()
    ) {
      if (
        consumer.kind ===
        'video'
      ) {
        count++;
      }
    }

    this.state.videoConsumers =
      count;
  }
}
