#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf build/
mkdir -p build/certs
pip install -r requirements.txt --target build/ --no-compile --no-user
curl -sSf -o build/certs/global-bundle.pem \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
cp -r src build/
find build -name __pycache__ -type d -prune -exec rm -rf {} +