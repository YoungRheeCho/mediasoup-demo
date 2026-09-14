#!/bin/bash

http-server dist -S -C ../server/certs/cert.pem -K ../server/certs/key.pem -p 5555
