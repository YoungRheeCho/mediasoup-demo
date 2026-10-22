import RoomClientLoadTest from './RoomClient.loadtest.js';


/*
 * ============================================================
 * URL
 * ============================================================
 */

const params =
  new URLSearchParams(
    window.location.search
  );


const ROOM_ID =
  params.get('roomId');


if (!ROOM_ID) {
  throw new Error(
    'missing roomId'
  );
}


/*
 * Page 하나 자체가 viewer 하나이므로
 * page마다 unique peerId를 생성한다.
 */
const PEER_ID =
  params.get('peerId') ||
  `loadtest-${crypto.randomUUID()}`;


const DISPLAY_NAME =
  params.get('displayName') ||
  `viewer-${PEER_ID.slice(0, 8)}`;


/*
 * ============================================================
 * Create exactly ONE viewer
 * ============================================================
 */

const client =
  new RoomClientLoadTest({
    roomId:
      ROOM_ID,

    peerId:
      PEER_ID,

    displayName:
      DISPLAY_NAME,

    device: {
      flag: 'loadtest',
      name: 'loadtest',
      version: '1'
    },

    forceTcp:
      false,

    consumeAudio:
      false,

    mediaSinkRoot:
      document.getElementById(
        'media-sink'
      )
  });


/*
 * test_viewer_flood.mjs debug에서 사용.
 */
window.CLIENT =
  client;


/*
 * RoomClient의 state object를 그대로 노출.
 */
window.__LOADTEST_STATE =
  client.state;


/*
 * Playwright가 필요할 때만 getStats().
 *
 * setInterval로 매초 getStats()하지 않는다.
 */
window.__getLoadTestRtpSnapshot =
  async () =>
    client.getRtpSnapshot();


console.log(
  '[loadtest] starting',
  {
    roomId:
      ROOM_ID,

    peerId:
      PEER_ID
  }
);


/*
 * ============================================================
 * Join
 * ============================================================
 *
 * IMPORTANT:
 *
 * page가 load되면 viewer 하나를 즉시 join.
 *
 * test_viewer_flood.mjs는 별도로
 *
 *   WAIT_PC_CREATE
 *   WAIT_PC_CONNECTED
 *
 * 를 확인한다.
 *
 * top-level await를 사용하지 않기 위해
 * async function 내부에서 join한다.
 */

async function start()
{
  try
  {
    await client.join();

    console.log(
      '[loadtest] joined',
      {
        roomId:
          ROOM_ID,

        peerId:
          PEER_ID
      }
    );
  }
  catch (error)
  {
    client.state.lastError =
      error?.message ||
      String(error);

    console.error(
      '[loadtest] join failed:',
      error
    );
  }
}


/*
 * Promise를 await하지 않는다.
 *
 * start() 내부에서 client.join()을 await하므로
 * 기존 동작 순서는 그대로 유지된다.
 */
start();
