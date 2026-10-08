// NIQE (Mittal et al. 2013) of one received video frame, computed off the main thread.
// Port of the reference MATLAB code (computequality.m, computefeature.m, estimateaggdparam.m).
//
// in : { frame: VideoFrame (transferred), at: epoch ms }
// out: { at, niqe: number|null, width, height, computeMs } or { at, niqe: null, error }
import params from './niqe_params.json'; // pristine model { mu: [36], cov: [36][36] }

const BLOCK = 96;

// log-gamma (Lanczos), accurate enough for the arguments used here (0.1 .. 15)
const LG = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
function lgamma(x) {
	if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
	x -= 1;
	let a = LG[0];
	const t = x + 7.5;
	for (let i = 1; i < 9; i++) a += LG[i] / (x + i);
	return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}
const gammaFn = (x) => Math.exp(lgamma(x));

// gam = 0.2:0.001:10 and r_gam of estimateaggdparam.m (r_gam is increasing)
const GAM = new Float64Array(9801);
for (let i = 0; i < GAM.length; i++) GAM[i] = 0.2 + i * 0.001;
const R_GAM = GAM.map((g) => gammaFn(2 / g) ** 2 / (gammaFn(1 / g) * gammaFn(3 / g)));

// same result as [~, i] = min((r_gam - x).^2), by binary search
function lookupAlpha(x) {
	if (!(x === x)) return GAM[0]; // NaN: MATLAB min() returns the first index
	let lo = 0, hi = R_GAM.length - 1;
	if (x <= R_GAM[lo]) return GAM[lo];
	if (x >= R_GAM[hi]) return GAM[hi];
	while (hi - lo > 1) {
		const m = (lo + hi) >> 1;
		if (R_GAM[m] < x) lo = m; else hi = m;
	}
	return GAM[Math.abs(R_GAM[lo] - x) <= Math.abs(R_GAM[hi] - x) ? lo : hi];
}

// estimateaggdparam.m -> [alpha, betal, betar]
function aggd(v) {
	let ln = 0, ls = 0, rn = 0, rs = 0, sAbs = 0, sSq = 0;
	for (let i = 0; i < v.length; i++) {
		const p = v[i];
		if (p < 0) { ln++; ls += p * p; } else if (p > 0) { rn++; rs += p * p; }
		sAbs += Math.abs(p);
		sSq += p * p;
	}
	const lstd = Math.sqrt(ls / ln), rstd = Math.sqrt(rs / rn); // NaN without samples, as in MATLAB
	const g = lstd / rstd, n = v.length;
	const rhat = (sAbs / n) ** 2 / (sSq / n);
	const alpha = lookupAlpha(rhat * (g ** 3 + 1) * (g + 1) / (g * g + 1) ** 2);
	const k = Math.sqrt(gammaFn(1 / alpha) / gammaFn(3 / alpha));
	return [alpha, lstd * k, rstd * k];
}

// fspecial('gaussian', 7, 7/6) applied separably, like imfilter(..., 'replicate')
const WIN = (() => {
	const s = 7 / 6, w = [];
	for (let i = -3; i <= 3; i++) w.push(Math.exp(-(i * i) / (2 * s * s)));
	const t = w.reduce((a, b) => a + b, 0);
	return w.map((v) => v / t);
})();

function blur(src, w, h) {
	const tmp = new Float64Array(w * h), out = new Float64Array(w * h);
	for (let y = 0; y < h; y++) {
		for (let x = 0; x < w; x++) {
			let a = 0;
			for (let k = -3; k <= 3; k++) a += WIN[k + 3] * src[y * w + Math.min(w - 1, Math.max(0, x + k))];
			tmp[y * w + x] = a;
		}
	}
	for (let y = 0; y < h; y++) {
		for (let x = 0; x < w; x++) {
			let a = 0;
			for (let k = -3; k <= 3; k++) a += WIN[k + 3] * tmp[Math.min(h - 1, Math.max(0, y + k)) * w + x];
			out[y * w + x] = a;
		}
	}
	return out;
}

function mscn(im, w, h) {
	const sq = new Float64Array(w * h);
	for (let i = 0; i < sq.length; i++) sq[i] = im[i] * im[i];
	const mu = blur(im, w, h), mu2 = blur(sq, w, h), out = new Float64Array(w * h);
	for (let i = 0; i < out.length; i++) out[i] = (im[i] - mu[i]) / (Math.sqrt(Math.abs(mu2[i] - mu[i] * mu[i])) + 1);
	return out;
}

// computefeature.m for every b x b block (circshift wraps inside the block)
const SHIFTS = [[0, 1], [1, 0], [1, 1], [1, -1]];
function blockFeatures(d, w, h, b) {
	const rows = [], blk = new Float64Array(b * b), pair = new Float64Array(b * b);
	for (let by = 0; by < Math.floor(h / b); by++) {
		for (let bx = 0; bx < Math.floor(w / b); bx++) {
			for (let y = 0; y < b; y++)
				for (let x = 0; x < b; x++) blk[y * b + x] = d[(by * b + y) * w + bx * b + x];
			const [a0, l0, r0] = aggd(blk);
			const f = [a0, (l0 + r0) / 2];
			for (const [dy, dx] of SHIFTS) {
				for (let y = 0; y < b; y++) {
					const sy = (y - dy + b) % b;
					for (let x = 0; x < b; x++) pair[y * b + x] = blk[y * b + x] * blk[sy * b + (x - dx + b) % b];
				}
				const [a, l, r] = aggd(pair);
				f.push(a, (r - l) * (gammaFn(2 / a) / gammaFn(1 / a)), l, r);
			}
			rows.push(f);
		}
	}
	return rows;
}

// imresize(im, 0.5): bicubic with antialiasing, symmetric borders
const DOWN = [-0.0234375, -0.0703125, 0.2265625, 0.8671875, 0.8671875, 0.2265625, -0.0703125, -0.0234375].map((v) => v / 2);
const reflect = (j, n) => (j < 0 ? -j - 1 : j >= n ? 2 * n - 1 - j : j);
function half(im, w, h) {
	const W = w >> 1, H = h >> 1, tmp = new Float64Array(H * w), out = new Float64Array(W * H);
	for (let Y = 0; Y < H; Y++) {
		for (let x = 0; x < w; x++) {
			let a = 0;
			for (let i = 0; i < 8; i++) a += DOWN[i] * im[reflect(2 * Y - 3 + i, h) * w + x];
			tmp[Y * w + x] = a;
		}
	}
	for (let Y = 0; Y < H; Y++) {
		for (let X = 0; X < W; X++) {
			let a = 0;
			for (let i = 0; i < 8; i++) a += DOWN[i] * tmp[Y * w + reflect(2 * X - 3 + i, w)];
			out[Y * W + X] = a;
		}
	}
	return [out, W, H];
}

// pinv of a symmetric matrix (Jacobi eigendecomposition)
function pinvSym(A) {
	const n = A.length, a = A.map((r) => Float64Array.from(r));
	const V = Array.from({ length: n }, (_, i) => { const r = new Float64Array(n); r[i] = 1; return r; });
	for (let sweep = 0; sweep < 100; sweep++) {
		let off = 0;
		for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++) off += a[p][q] * a[p][q];
		if (off < 1e-30) break;
		for (let p = 0; p < n - 1; p++) {
			for (let q = p + 1; q < n; q++) {
				if (a[p][q] === 0) continue;
				const th = (a[q][q] - a[p][p]) / (2 * a[p][q]);
				const t = (th >= 0 ? 1 : -1) / (Math.abs(th) + Math.sqrt(th * th + 1));
				const c = 1 / Math.sqrt(t * t + 1), s = t * c;
				for (let k = 0; k < n; k++) { const x = a[k][p], y = a[k][q]; a[k][p] = c * x - s * y; a[k][q] = s * x + c * y; }
				for (let k = 0; k < n; k++) { const x = a[p][k], y = a[q][k]; a[p][k] = c * x - s * y; a[q][k] = s * x + c * y; }
				for (let k = 0; k < n; k++) { const x = V[k][p], y = V[k][q]; V[k][p] = c * x - s * y; V[k][q] = s * x + c * y; }
			}
		}
	}
	const ev = a.map((r, i) => r[i]);
	const tol = n * Math.max(...ev.map(Math.abs)) * 2.220446049250313e-16;
	const P = Array.from({ length: n }, () => new Float64Array(n));
	for (let k = 0; k < n; k++) {
		if (Math.abs(ev[k]) <= tol) continue;
		for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) P[i][j] += V[i][k] * V[j][k] / ev[k];
	}
	return P;
}

// computequality.m: gray is w0 x h0 luminance in 0..255. null when it cannot be computed
export function niqe(gray, w0, h0, { mu: muP, cov: covP } = params) {
	const w = Math.floor(w0 / BLOCK) * BLOCK, h = Math.floor(h0 / BLOCK) * BLOCK;
	if (w === 0 || h === 0) return null;
	const im = new Float64Array(w * h);
	for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) im[y * w + x] = gray[y * w0 + x];
	const f1 = blockFeatures(mscn(im, w, h), w, h, BLOCK);
	const [im2, w2, h2] = half(im, w, h);
	const f2 = blockFeatures(mscn(im2, w2, h2), w2, h2, BLOCK / 2);
	const F = f1.map((r, i) => r.concat(f2[i])), D = 36;

	// nanmean, and the covariance over blocks without NaN (nancov)
	const mu = new Float64Array(D);
	for (let j = 0; j < D; j++) {
		let s = 0, c = 0;
		for (const r of F) if (r[j] === r[j]) { s += r[j]; c++; }
		mu[j] = c ? s / c : NaN;
	}
	const ok = F.filter((r) => r.every((v) => v === v));
	if (ok.length < 2 || mu.some((v) => v !== v)) return null; // e.g. a flat black frame
	const m = new Float64Array(D);
	for (const r of ok) for (let j = 0; j < D; j++) m[j] += r[j] / ok.length;
	const C = Array.from({ length: D }, (_, i) => Array.from({ length: D }, (_, j) => covP[i][j] / 2));
	for (const r of ok)
		for (let i = 0; i < D; i++)
			for (let j = 0; j < D; j++) C[i][j] += (r[i] - m[i]) * (r[j] - m[j]) / (ok.length - 1) / 2;
	const P = pinvSym(C);
	let q = 0;
	for (let i = 0; i < D; i++) for (let j = 0; j < D; j++) q += (muP[i] - mu[i]) * P[i][j] * (muP[j] - mu[j]);
	return Math.sqrt(q);
}

// MATLAB rgb2gray weights
const R_W = 0.298936021293775, G_W = 0.587043074451121, B_W = 0.114020904255103;

// VideoFrame -> luminance 0..255
async function lumaOf(frame) {
	const w = frame.visibleRect.width, h = frame.visibleRect.height;
	const gray = new Uint8Array(w * h);
	if (['I420', 'I420A', 'I422', 'I444', 'NV12'].includes(frame.format)) {
		const buf = new Uint8Array(frame.allocationSize());
		const [y] = await frame.copyTo(buf); // plane 0 = Y
		if (frame.colorSpace?.fullRange === true) {
			// full range: Y is already 0..255
			for (let r = 0; r < h; r++)
				for (let c = 0; c < w; c++)
					gray[r * w + c] = buf[y.offset + r * y.stride + c];
		}
		else {
			// limited range 16..235 (WebRTC default, also when the range is unknown):
			// stretch to full range (close to rgb2gray after a YUV->RGB conversion)
			for (let r = 0; r < h; r++)
				for (let c = 0; c < w; c++)
					gray[r * w + c] = Math.round(Math.min(255, Math.max(0, (buf[y.offset + r * y.stride + c] - 16) * 255 / 219)));
		}
	}
	else {
		// RGB or GPU-backed frame (format null): draw it, then rgb2gray
		const ctx = new OffscreenCanvas(w, h).getContext('2d', { willReadFrequently: true });
		ctx.drawImage(frame, 0, 0);
		const px = ctx.getImageData(0, 0, w, h).data;
		for (let i = 0; i < w * h; i++) gray[i] = Math.round(R_W * px[4 * i] + G_W * px[4 * i + 1] + B_W * px[4 * i + 2]);
	}
	return { gray, w, h };
}

self.onmessage = async ({ data: { frame, at } }) => {
	const started = performance.now();
	let w = null, h = null;
	try {
		let luma;
		try {
			luma = await lumaOf(frame);
		}
		finally {
			frame.close(); // give the decoder buffer back before the long computation
		}
		({ w, h } = luma);
		const value = niqe(luma.gray, w, h);
		self.postMessage({ at, niqe: value, width: w, height: h, computeMs: performance.now() - started });
	}
	catch (error) {
		self.postMessage({ at, niqe: null, width: w, height: h, error: String(error) });
	}
};
