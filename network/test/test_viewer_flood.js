import { chromium } from 'playwright';

const roomArg = process.argv.find(arg => arg.startsWith('--roomId='));
const ROOM_ID = roomArg?.split('=')[1];

if (!ROOM_ID) {
  console.error(
    'input ROOM_ID. e.g., node viewer_flood.js --roomId=test-room'
  );
  process.exit(1);
}

const CUSTOM_CHROMIUM_PATH = '/mnt/old/home/n2sl/chromium/src/out/Default/chrome';
const BASE_URL = 'https://10.20.13.186:5555';
const VIEWER_COUNT = 10000;

/*
 * 한 batch에서 동시에 접속할 viewer 수.
 */
const BATCH_SIZE = 15;

/*
 * 한 batch 완료 후 다음 batch까지 대기하는 시간.
 *
 * 빠르게 접속시키려면:
 *   0
 * 또는
 *   1000
 *
 * 기존 설정을 유지하려면:
 *   120_000
 */
const BATCH_DELAY_MS = 1000;

const sleep = ms =>
  new Promise(resolve => setTimeout(resolve, ms));

async function getInboundStats(page) {
  return page.evaluate(async () => {
    const pcs = window.__peerConnections ?? [];

    const result = {
      peerConnectionCount: pcs.length,
      connectedCount: 0,
      connectionStates: [],
      packetsReceived: 0,
      bytesReceived: 0,
      videoPacketsReceived: 0,
      videoBytesReceived: 0,
      audioPacketsReceived: 0,
      audioBytesReceived: 0
    };

    for (const pc of pcs) {
      result.connectionStates.push(pc.connectionState);

      if (pc.connectionState === 'connected') {
        result.connectedCount++;
      }

      const stats = await pc.getStats();

      stats.forEach(report => {
        if (
          report.type !== 'inbound-rtp' ||
          report.isRemote
        ) {
          return;
        }

        const packets = report.packetsReceived ?? 0;
        const bytes = report.bytesReceived ?? 0;
        const kind = report.kind ?? report.mediaType;

        result.packetsReceived += packets;
        result.bytesReceived += bytes;

        if (kind === 'video') {
          result.videoPacketsReceived += packets;
          result.videoBytesReceived += bytes;
        } else if (kind === 'audio') {
          result.audioPacketsReceived += packets;
          result.audioBytesReceived += bytes;
        }
      });
    }

    return result;
  });
}

(async () => {
  const browser = await chromium.launch({
	executablePath: CUSTOM_CHROMIUM_PATH,
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--no-sandbox'
    ]
  });

  /*
   * 성공한 viewer의 context를 보관한다.
   * context를 닫지 않아야 viewer 연결이 유지된다.
   */
  const contexts = [];

  let attemptedCount = 0;
  let httpSuccessCount = 0;
  let websocketCount = 0;
  let peerConnectedCount = 0;
  let mediaReceivingCount = 0;
  let failedCount = 0;

  async function connectViewer(viewerNumber) {
    let context = null;

    try {
      context = await browser.newContext({
        ignoreHTTPSErrors: true
      });

      /*
       * 웹 애플리케이션이 생성하는 RTCPeerConnection을 수집한다.
       */
      await context.addInitScript(() => {
        const NativeRTCPeerConnection =
          window.RTCPeerConnection;

        window.__peerConnections = [];

        window.RTCPeerConnection = new Proxy(
          NativeRTCPeerConnection,
          {
            construct(target, args) {
              const pc = new target(...args);

              window.__peerConnections.push(pc);

              return pc;
            }
          }
        );
      });

      const page = await context.newPage();

      let viewerWebsocketCount = 0;
      let receivedWebsocketFrames = 0;

      /*
       * 로그가 너무 많으면 warning 출력을 제거하고
       * error만 출력하도록 바꿔도 된다.
       */
      page.on('console', message => {
        const type = message.type();

        if (type === 'error' || type === 'warning') {
          console.log(
            `[viewer ${viewerNumber}] browser ${type}: ` +
            message.text()
          );
        }
      });

      page.on('pageerror', error => {
        console.error(
          `[viewer ${viewerNumber}] page error:`,
          error.message
        );
      });

      page.on('requestfailed', request => {
        console.error(
          `[viewer ${viewerNumber}] request failed:`,
          request.url(),
          request.failure()?.errorText
        );
      });

      page.on('websocket', ws => {
        viewerWebsocketCount++;
        websocketCount++;

        console.log(
          `[viewer ${viewerNumber}] WebSocket created: ` +
          ws.url()
        );

        ws.on('framereceived', () => {
          receivedWebsocketFrames++;
        });

        ws.on('close', () => {
          console.log(
            `[viewer ${viewerNumber}] WebSocket closed: ` +
            ws.url()
          );
        });

        ws.on('socketerror', error => {
          console.error(
            `[viewer ${viewerNumber}] WebSocket error:`,
            error
          );
        });
      });

      const url =
        `${BASE_URL}/?roomId=${encodeURIComponent(ROOM_ID)}`;

      console.log(
        `[viewer ${viewerNumber}] connecting to ${url}`
      );

      const response = await page.goto(url, {
        waitUntil: 'domcontentloaded',
        timeout: 30_000
      });

      if (!response) {
        throw new Error(
          'page.goto() returned no HTTP response'
        );
      }

      if (!response.ok()) {
        throw new Error(
          `HTTP load failed: ` +
          `${response.status()} ${response.statusText()}`
        );
      }

      httpSuccessCount++;

      /*
       * 최소 하나의 RTCPeerConnection이 만들어질 때까지 대기한다.
       */
      await page.waitForFunction(
        () =>
          (window.__peerConnections?.length ?? 0) > 0,
        undefined,
        {
          timeout: 30_000
        }
      );

      /*
       * ICE/DTLS 연결이 완료된 PeerConnection이
       * 하나 이상 생길 때까지 대기한다.
       */
      await page.waitForFunction(
        () => {
          return window.__peerConnections?.some(
            pc => pc.connectionState === 'connected'
          );
        },
        undefined,
        {
          timeout: 30_000
        }
      );

      peerConnectedCount++;

      /*
       * RTP 수신 여부를 확인한다.
       *
       * batch 내부의 viewer들은 이 5초를 동시에 기다린다.
       */
      const before = await getInboundStats(page);

      await page.waitForTimeout(5000);

      const after = await getInboundStats(page);

      const packetsDelta =
        after.packetsReceived -
        before.packetsReceived;

      const bytesDelta =
        after.bytesReceived -
        before.bytesReceived;

      const receivingMedia =
        packetsDelta > 0 &&
        bytesDelta > 0;

      if (receivingMedia) {
        mediaReceivingCount++;

        console.log(
          `[viewer ${viewerNumber}] CONNECTED AND RECEIVING` +
          ` pc=${after.peerConnectionCount}` +
          ` state=${after.connectionStates.join(',')}` +
          ` websocket=${viewerWebsocketCount}` +
          ` wsFrames=${receivedWebsocketFrames}` +
          ` packetsDelta=${packetsDelta}` +
          ` bytesDelta=${bytesDelta}` +
          ` videoPackets=${after.videoPacketsReceived}` +
          ` videoBytes=${after.videoBytesReceived}` +
          ` audioPackets=${after.audioPacketsReceived}` +
          ` audioBytes=${after.audioBytesReceived}`
        );
      } else {
        console.error(
          `[viewer ${viewerNumber}] connected but no inbound RTP` +
          ` state=${after.connectionStates.join(',')}` +
          ` websocket=${viewerWebsocketCount}` +
          ` wsFrames=${receivedWebsocketFrames}` +
          ` packetsDelta=${packetsDelta}` +
          ` bytesDelta=${bytesDelta}`
        );
      }

      /*
       * 성공한 viewer는 연결 유지를 위해 context를 보관한다.
       */
      contexts.push(context);

      return {
        viewerNumber,
        success: true,
        receivingMedia
      };
    } catch (error) {
      failedCount++;

      console.error(
        `[viewer ${viewerNumber}] FAILED:`,
        error.message
      );

      /*
       * 실패한 viewer의 리소스는 정리한다.
       */
      if (context) {
        try {
          await context.close();
        } catch (closeError) {
          console.error(
            `[viewer ${viewerNumber}] context close failed:`,
            closeError.message
          );
        }
      }

      return {
        viewerNumber,
        success: false,
        receivingMedia: false,
        error: error.message
      };
    }
  }

  /*
   * BATCH_SIZE만큼 동시에 연결한다.
   */
  for (
    let batchStart = 0;
    batchStart < VIEWER_COUNT;
    batchStart += BATCH_SIZE
  ) {
    const batchEnd = Math.min(
      batchStart + BATCH_SIZE,
      VIEWER_COUNT
    );

    const batchNumber =
      Math.floor(batchStart / BATCH_SIZE) + 1;

    const viewerNumbers = [];

    for (
      let viewerNumber = batchStart + 1;
      viewerNumber <= batchEnd;
      viewerNumber++
    ) {
      viewerNumbers.push(viewerNumber);
    }

    console.log('');
    console.log('========================================');
    console.log(
      `[batch ${batchNumber}] starting viewers ` +
      `${batchStart + 1}-${batchEnd}`
    );
    console.log('========================================');

    const batchStartTime = Date.now();

    /*
     * 이 batch의 viewer들을 동시에 실행한다.
     */
    const results = await Promise.allSettled(
      viewerNumbers.map(viewerNumber =>
        connectViewer(viewerNumber)
      )
    );

    attemptedCount += viewerNumbers.length;

    /*
     * connectViewer 내부에서 예외를 처리하지만,
     * 예상치 못한 Promise rejection도 확인한다.
     */
    for (const result of results) {
      if (result.status === 'rejected') {
        failedCount++;

        console.error(
          '[batch] unexpected rejected promise:',
          result.reason
        );
      }
    }

    const batchElapsedMs =
      Date.now() - batchStartTime;

    const batchSuccessCount = results.filter(
      result =>
        result.status === 'fulfilled' &&
        result.value.success
    ).length;

    const batchReceivingCount = results.filter(
      result =>
        result.status === 'fulfilled' &&
        result.value.receivingMedia
    ).length;

    console.log('');
    console.log(
      `[batch ${batchNumber} result]` +
      ` attempted=${viewerNumbers.length}` +
      ` connected=${batchSuccessCount}` +
      ` receiving=${batchReceivingCount}` +
      ` elapsed=${(batchElapsedMs / 1000).toFixed(2)}s`
    );

    console.log(
      `[total summary]` +
      ` attempted=${attemptedCount}` +
      ` http=${httpSuccessCount}` +
      ` websocket=${websocketCount}` +
      ` peerConnected=${peerConnectedCount}` +
      ` receivingMedia=${mediaReceivingCount}` +
      ` failed=${failedCount}`
    );

    /*
     * 마지막 batch가 아니면 설정된 시간만큼 대기한다.
     */
    if (
      batchEnd < VIEWER_COUNT &&
      BATCH_DELAY_MS > 0
    ) {
      console.log(
        `[batch ${batchNumber}] waiting ` +
        `${BATCH_DELAY_MS / 1000} seconds...`
      );

      await sleep(BATCH_DELAY_MS);
    }
  }

  console.log('');
  console.log('========== FINAL RESULT ==========');
  console.log(`roomId             : ${ROOM_ID}`);
  console.log(`attempted          : ${attemptedCount}`);
  console.log(`HTTP loaded        : ${httpSuccessCount}`);
  console.log(`WebSockets created : ${websocketCount}`);
  console.log(`Peer connected     : ${peerConnectedCount}`);
  console.log(`Receiving RTP      : ${mediaReceivingCount}`);
  console.log(`Failed             : ${failedCount}`);
  console.log(`Active contexts    : ${contexts.length}`);

  /*
   * browser.close()를 호출하지 않기 때문에
   * 성공한 viewer들의 연결이 유지된다.
   *
   * Ctrl+C로 종료하면 browser를 정리한다.
   */
  const shutdown = async signal => {
    console.log('');
    console.log(`${signal} received. Closing browser...`);

    try {
      await browser.close();
    } finally {
      process.exit(0);
    }
  };

  process.once('SIGINT', () => {
    void shutdown('SIGINT');
  });

  process.once('SIGTERM', () => {
    void shutdown('SIGTERM');
  });
})().catch(error => {
  console.error('fatal error:', error);
  process.exit(1);
});
