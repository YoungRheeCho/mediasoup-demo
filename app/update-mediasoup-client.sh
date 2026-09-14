#!/usr/bin/env bash

#
# This script updates mediasoup-client in package-lock.json to its latest commit
# in v3 branch.
#

npm install github:versatica/mediasoup-client#v3 --legacy-peer-deps
