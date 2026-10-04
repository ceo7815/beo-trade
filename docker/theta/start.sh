#!/bin/sh
set -eu
cd /opt/theta
echo "Theta Terminal runtime:"
java -version
if [ -n "${THETADATA_API_KEY:-}" ]; then
  echo "THETADATA_API_KEY is present"
else
  echo "THETADATA_API_KEY is empty. The terminal stays up and MDDS stays disconnected until the key is added and this container is recreated."
fi
exec java -jar /opt/theta/ThetaTerminalv3.jar --config /opt/theta/config.toml
