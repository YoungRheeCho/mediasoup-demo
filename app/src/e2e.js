/**
 * Insertable streams.
 *
 * https://github.com/webrtc/samples/blob/gh-pages/src/content/insertable-streams/endtoend-encryption/js/main.js
 */

import Logger from './Logger';

const logger = new Logger('e2e');

let e2eSupported = undefined;
let worker = undefined;

export function isSupported() {
	if (e2eSupported === undefined) {
		if (RTCRtpSender.prototype.createEncodedStreams) {
			try {
				const stream = new ReadableStream();

				window.postMessage(stream, '*', [stream]);
				worker = new Worker('/js/e2e-worker.js', { name: 'e2e worker' });

				logger.debug('isSupported() | supported');

				e2eSupported = true;
			} catch (error) {
				logger.debug(`isSupported() | not supported: ${error}`);

				e2eSupported = false;
			}
		} else {
			logger.debug('isSupported() | not supported');

			e2eSupported = false;
		}
	}

	return e2eSupported;
}

export function setCryptoKey(operation, key, useCryptoOffset) {
	logger.debug(
		'setCryptoKey() [operation:%o, useCryptoOffset:%o]',
		operation,
		useCryptoOffset
	);

	assertSupported();

	worker.postMessage({
		operation: operation,
		currentCryptoKey: key,
		useCryptoOffset: useCryptoOffset,
	});
}

export function setupSenderTransform(sender) {
	logger.debug('setupSenderTransform()');

	assertSupported();

	const senderStreams = sender.createEncodedStreams();
	const readableStream = senderStreams.readable || senderStreams.readableStream;
	const writableStream = senderStreams.writable || senderStreams.writableStream;

	worker.postMessage(
		{
			operation: 'encode',
			readableStream,
			writableStream,
		},
		[readableStream, writableStream]
	);
}

// yun: for sending deadline(latest_decode_time)
//export function setupReceiverTransform(receiver) {
export function setupReceiverTransform(receiver, { protoo } = {}) {
	logger.debug('setupReceiverTransform()');
	assertSupported();

	const receiverStreams = receiver.createEncodedStreams();
	const readableStream =
		receiverStreams.readable || receiverStreams.readableStream;
	const writableStream =
		receiverStreams.writable || receiverStreams.writableStream;

	worker.addEventListener('message', async ({ data }) => {
		if (!data)
			return;
		if (data.type === 'rtp-timestamp') {
			const { consumerId, rtpTimestamp } = data;

			try {
				const result = await window.__printDeadline?.(consumerId, rtpTimestamp);
				if (!result)
					return;

				console.log('[deadline-debug] auto matched', {
					consumerId,
					rtpTimestamp,
					latestDecodeTimeNtp: String(result.latestDecodeTimeNtp)
				});

				// if (protoo && !protoo.closed) {
				// 	protoo.notify('recv-deadline', {
				// 		consumerId,
				// 		rtpTimestamp: result.rtpTimestamp,
				// 		latestDecodeTimeNtp: String(result.latestDecodeTimeNtp)
				// 	});
				// }
			} catch (error) {
				console.error('[deadline-debug] auto match failed', {
					consumerId,
					rtpTimestamp,
					error
				});
			}

			return;
		}

		if (data.type === 'deadline-worker-error') {
			console.error('[deadline-debug] worker error', data);
		}
	});
	
	worker.postMessage(
		{
			operation: 'decode',
			readableStream,
			writableStream,
		},
		[readableStream, writableStream]
	);
}

function assertSupported() {
	if (e2eSupported === false) throw new Error('e2e not supported');
	else if (e2eSupported === undefined)
		throw new Error('e2e not initialized, must call isSupported() first');
}
