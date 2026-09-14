import qs from 'qs';

// yeon: 
//오리진의 경우 viewer를 사용하지 않으므로 굳이 haproxy로 연결할 필요는 없음
//추후 haproxy 연결 필요에 따라 수정할 가능성은 있음
//현재로서는 동일한 서버의 sfu에게 시그널링 통신하도록 설정만 하면 됨
export function getProtooUrl(params) {
	let protooPort = 4443;

	if (window.location.hostname === 'test.mediasoup.org') {
		protooPort = 4444;
	}

	const hostname = window.location.hostname;
	const protocol = 'wss';

	const query = qs.stringify(params);

	return `${protocol}://${hostname}:${protooPort}/?${query}`;
}
