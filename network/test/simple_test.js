import { chromium } from 'playwright';

/*
const roomArg = process.argv.find(arg => arg.startsWith('--roomId='));
const ROOM_ID = roomArg?.split('=')[1];
if (!ROOM_ID) {
  console.error('input ROOM_ID. e.g., node viewer.js --roomId=<room_id>');
  process.exit(1);
}
*/

const BASE_URL = 'https://10.20.13.186:5555';
const VIEWER_COUNT = 10000;

const BATCH_SIZE = 15;
const BATCH_DELAY_MS = 120_000;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--no-sandbox'
    ]
  });

  const contexts = [];

  for (let i = 0; i < VIEWER_COUNT; i++) {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true
    });
    contexts.push(context);

    const page = await context.newPage();
//	const url = `${BASE_URL}/?roomId=${ROOM_ID}`;
    const url = `${BASE_URL}/?roomId=test-room`;

    console.log(`viewer ${i + 1} connecting...`);
    await page.goto(url, { waitUntil: 'networkidle' });

{/*
    // 15명마다 n초 대기
    if ((i + 1) % BATCH_SIZE === 0 && i + 1 < VIEWER_COUNT) {
      console.log(`${i + 1} viewers connected. Waiting ${BATCH_DELAY_MS / 1000} seconds...`);
      await new Promise(resolve => setTimeout(resolve, BATCH_DELAY_MS));
    }
*/}
  }

  console.log(`${VIEWER_COUNT} viewers connected`);
})();

