import qs from 'qs';

export function getProtooUrl(params) {
	//let protooPort = 5213; // yun: HA Proxy's Port
	let protooPort = 4443; // yun: HA Proxy's Port
	if (window.location.hostname === 'test.mediasoup.org') {
		protooPort = 4444;
	}

	const hostname = window.location.hostname;
	// 10.20.13.197를 haporxy 서버로 고정
	//const hostname = '10.20.13.197'; // yun: HA Proxy's IP addr
	const protocol = 'wss';
	const query = qs.stringify(params);
	
	return `${protocol}://${hostname}:${protooPort}/?${query}`;
}
