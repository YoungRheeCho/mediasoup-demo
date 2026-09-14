import fs from 'node:fs';
import path from 'node:path';

type Mode = 'origin' | 'edge';

const CONFIG_PATH = path.resolve(__dirname, '../mode_config.json');

function loadMode(): Mode {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, 'utf8');
    const cfg = JSON.parse(raw);
    const mode = (cfg?.mode ?? 'origin') as Mode;

    if (mode !== 'origin' && mode !== 'edge') {
      throw new Error(`Invalid mode "${cfg?.mode}"`);
    }
    return mode;
  } catch (e) {
    return 'origin';
  }
}

const mode = loadMode();

let ServerImpl: any;
if (mode === 'edge') {
  ServerImpl = require('./Server.edge').Server;
} else {
  ServerImpl = require('./Server.origin').Server;
}

export const Server = ServerImpl;
export type Server = InstanceType<typeof ServerImpl>;
