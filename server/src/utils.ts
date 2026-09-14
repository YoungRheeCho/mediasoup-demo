/**
 * Clones the given value.
 */
export function clone<T>(value: T): T {
	if (value === undefined) {
		return undefined as unknown as T;
	} else if (Number.isNaN(value)) {
		return NaN as unknown as T;
	} else if (typeof structuredClone === 'function') {
		// Available in Node >= 18.
		return structuredClone(value);
	} else {
		return JSON.parse(JSON.stringify(value));
	}
}

export function assertUnreachable(key: string, value: never): never {
	throw new TypeError(`invalid ${key}: ${value}`);
}

/**
 * Whether two URLs have same HTTP scheme and hostname. Port is ignored.
 */
export function areSameHttpOrigins(urlA?: string, urlB?: string): boolean {
	if (!urlA || !urlB) {
		return false;
	}

	try {
		const a = new URL(urlA);
		const b = new URL(urlB);

		return (
			(a.protocol === 'http:' || a.protocol === 'https:') &&
			a.protocol === b.protocol &&
			a.hostname === b.hostname
		);
	} catch {
		return false;
	}
}
