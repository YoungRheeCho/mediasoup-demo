import Logger from './Logger';

const logger = new Logger('niqeSampler');

// Sends one decoded frame every intervalMs to niqeWorker.js.
// Frames come from the decoder, not from a <video> element, so this keeps working
// in a hidden tab. Chrome only (MediaStreamTrackProcessor); returns null elsewhere.
//
//   const sampler = startNiqeSampler(consumer.track);
//   sampler.take(); // NIQE results since the previous call: [{ at, niqe, width, height, computeMs }]
//   sampler.stop();
export function startNiqeSampler(track, { intervalMs = 1000 } = {}) {
	if (typeof MediaStreamTrackProcessor === 'undefined') {
		logger.warn('startNiqeSampler() | MediaStreamTrackProcessor not supported');
		return null;
	}

	const clone = track.clone(); // stopping the clone does not affect playback
	const reader = new MediaStreamTrackProcessor({ track: clone }).readable.getReader();
	const worker = new Worker(new URL('./niqeWorker.js', import.meta.url), { type: 'module' });
	const samples = [];
	let busy = false;
	let stopped = false;
	// random phase so the viewers on one PC do not all compute at the same moment
	let nextAt = performance.now() + Math.random() * intervalMs;

	worker.onmessage = ({ data }) => {
		busy = false;
		samples.push(data);
	};
	worker.onerror = (event) => {
		busy = false;
		logger.warn('startNiqeSampler() | worker error:%o', event.message);
	};

	(async () => {
		while (!stopped) {
			let frame;
			try {
				const { value, done } = await reader.read();
				if (done) break;
				frame = value;
			}
			catch (error) {
				break;
			}

			const now = performance.now();

			if (!stopped && !busy && now >= nextAt) {
				busy = true;
				// keep a fixed schedule (phase, phase + interval, ...) so the delay until the
				// next frame does not add up; slots missed while busy are skipped
				while (nextAt <= now) nextAt += intervalMs;
				// ownership moves to the worker, which closes the frame.
				// Date.now(): same clock as the stats row timestamp
				worker.postMessage({ frame, at: Date.now() }, [frame]);
			}
			else {
				// close every other frame right away, or the decoder runs out of buffers and the video freezes
				frame.close();
			}
		}
	})();

	return {
		take: () => samples.splice(0),
		stop: () => {
			if (stopped) return;
			stopped = true;
			reader.cancel().catch(() => {});
			clone.stop();
			worker.terminate();
		},
	};
}
