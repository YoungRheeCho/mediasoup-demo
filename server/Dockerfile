FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /work

# 0) 필수 패키지 설치 
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils \
    python3 python3-dev python3-pip \
    build-essential g++ gcc make \
  && rm -rf /var/lib/apt/lists/*

# 1) Node.js v22.22.0 설치 (npm까지)
ENV NODE_VERSION=22.22.0
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz \
  && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
  && rm -f /tmp/node.tar.xz \
  && node -v && npm -v

# 버전 확인 로그: Python/gcc
RUN python3 --version && gcc --version && g++ --version

# 2) mediasoup build 
# 명심: 여기서 서버의 경로는 dockerfile 현재 경로 기준이 아닌,
# docker compose 파일에서 설정된 context 경로 기준이다.

# 여기서 필요없는 파일들은 싹 다 제외해야 함
COPY mediasoup ./mediasoup
WORKDIR /work/mediasoup

# 에러:
# COPY mediasoup ./mediasoup로 복사한 소스 안에 내용물이
#worker/out/.../build 같은 "이미 configure 되어있는 빌드 산출물"이 같이 들어왔고, 
# 그 안의 설정 파일이 "호스트 경로(/home/n2sl/...)"를 기억하고 있어서 
# 컨테이너에서는 존재하지 않아 FileNotFoundError
RUN npm ci && npm run typescript:build && npm run worker:build

# 3) mediasoup-demo server 설치 + build
WORKDIR /work/mediasoup-demo/server
COPY mediasoup-demo/server .
RUN npm ci
RUN npm run typescript:build

ENV NODE_ENV=production

# 쓰는 포트 번호 명시
EXPOSE 4443

CMD ["npm", "start"]