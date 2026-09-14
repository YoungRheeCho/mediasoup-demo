FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /work/mediasoup-demo/app

# ---- 기본 도구 + Node.js v22.22.0 설치 ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils \
  && rm -rf /var/lib/apt/lists/*

ENV NODE_VERSION=22.22.0
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz \
  && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
  && rm -f /tmp/node.tar.xz \
  && node -v && npm -v

# ---- 소스 복사 ----
# Dockerfile이 있는 디렉토리의 내용을 컨테이너로 복사
# 명심: 여기서 서버의 경로는 dockerfile 현재 경로 기준이 아닌,
# docker compose 파일에서 설정된 context 경로 기준이다.
# 즉 현재 기준 경로는 network/ 임 
COPY mediasoup-demo/app .

WORKDIR /work/mediasoup-demo/server
COPY mediasoup-demo/server/config.mjs ./config.mjs
WORKDIR /work/mediasoup-demo/app

# 의존성 설치 + 빌드
# npm install 할 때, 해당 경로에 package.json을 보장해야 한다.
RUN npm install --legacy-peer-deps 
RUN npm install -g http-server
RUN npm run build

EXPOSE 5555

# cert/key는 docker-compose에서 볼륨 마운트를 ㅠ사용
#   - app 컨테이너 기준 경로: /work/server/certs/cert.pem, /work/server/certs/key.pem

CMD ["sh", "-lc", "http-server dist -S -C ../server/certs/cert.pem -K ../server/certs/key.pem -p 5555"]