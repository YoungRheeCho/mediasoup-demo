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

let RoomImpl: any;

if (mode === 'edge') {
	RoomImpl = require("./Room.edge").Room;
} else {
	RoomImpl = require("./Room.origin").Room;
}

export const Room = RoomImpl;
export type Room = InstanceType<typeof RoomImpl>;
