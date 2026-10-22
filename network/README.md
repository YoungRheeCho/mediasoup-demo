this directory covers mediasoup with k8s setting

mediasoup is consist of mediasoup and mediasoup-demo

mediasoup demo has 2 parts, mediasoup-server and mediasoup-app mediasoup-server: WebRTC SFU mediasoup-app: client web server to connect SFU

In mediasoup-server and app, there is a configuration file that sets the mode of the SFU you want to operate. server: mediasoup-demo/server/mode_config.json app: mediasoup-demo/app/public/config.js

The mode has origin and edge, and the description of this mode will be added later.

You can set the "mode" item in each file to origin or edge to operate it.

You can then run it locally, or you can push the build and image with Docker to use it as a Kubernetes container image.

docker compose build docker compose push

You can use this push image for yaml's container image.

We will add more information about Kubernetes setting later.
