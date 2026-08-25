import * as grpc from '@grpc/grpc-js';
import * as protoLoader from '@grpc/proto-loader';
import * as path from 'path';

// metrics agent 주소.
// 실제 배포 환경에서는 반드시 환경변수로 지정.
const METRICS_AGENT_ADDR = process.env['METRICS_AGENT_ADDR'];

// sysinfo.proto 위치.
const SYSINFO_PROTO_PATH =
	process.env['SYSINFO_PROTO_PATH'] ??
	path.resolve(process.cwd(), 'sysinfo.proto');

let client: any | undefined;

/**
 * metrics agent용 gRPC client를 최초 1회 생성하고 재사용.
 */
function getClient(): any
{
	if (client)
	{
		return client;
	}

	if (!METRICS_AGENT_ADDR)
	{
		throw new Error(
			'METRICS_AGENT_ADDR environment variable is not set'
		);
	}

	const packageDefinition = protoLoader.loadSync(
		SYSINFO_PROTO_PATH,
		{
			keepCase: true,
			longs: String,
			enums: String,
			defaults: true,
			oneofs: true
		}
	);

	const grpcObject =
		grpc.loadPackageDefinition(packageDefinition) as any;

	client = new grpcObject.sysinfo.SysInfoService(
		METRICS_AGENT_ADDR,
		grpc.credentials.createInsecure()
	);

	return client;
}


/**
 * 실제 unary RPC 한 번 수행.
 */
function sendViewerCount(viewerCount: number): Promise<void>
{
	return new Promise<void>((resolve, reject) =>
	{
		const grpcClient = getClient();

		grpcClient.updateViewerCount(
			{
				viewer_count: viewerCount
			},
			(error: grpc.ServiceError | null) =>
			{
				if (error)
				{
					reject(error);

					return;
				}

				resolve();
			}
		);
	});
}


/*
 * join/leave가 짧은 시간에 연속 발생해도
 * viewer count 전송 순서가 뒤집히지 않도록 순차적으로 전송.
 */
let sendChain: Promise<void> = Promise.resolve();

export function reportViewerCount(
	viewerCount: number
): Promise<void>
{
	sendChain = sendChain
		.catch(() =>
		{
			// 이전 요청 실패가 다음 요청까지 막지 않도록 함.
		})
		.then(() => sendViewerCount(viewerCount));

	return sendChain;
}