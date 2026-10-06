#!/bin/sh
set -eu
mkdir -p /opt/theta/lib /opt/theta/logs
chown -R theta:theta /opt/theta/lib /opt/theta/logs
cd /opt/theta
echo "Theta Terminal runtime:"
java -version
if [ -n "${THETADATA_API_KEY:-}" ]; then
  echo "THETADATA_API_KEY is present"
else
  echo "THETADATA_API_KEY is empty. The terminal stays up and MDDS stays disconnected until the key is added and this container is recreated."
fi
runuser --preserve-environment -u theta -- java -jar /opt/theta/ThetaTerminalv3.jar --bootstrap-config /opt/theta/bootstrap.toml &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null || true; wait "$pid" || true; exit 0' TERM INT

# A terminal whose MDDS session was taken over answers 478 until it signs in again.
# Exiting lets Docker (restart: unless-stopped) start a fresh session.
sleep "${THETA_WATCH_GRACE_SECONDS:-120}" &
wait $!
strikes=0
while kill -0 "$pid" 2>/dev/null; do
  status=$(curl -sS --max-time 5 http://127.0.0.1:25503/v3/terminal/mdds/status 2>/dev/null || echo ERROR)
  case "$status" in
    *UNVERIFIED*) strikes=$((strikes + 1)) ;;
    *) strikes=0 ;;
  esac
  if [ "$strikes" -ge 3 ]; then
    echo "MDDS session is UNVERIFIED on 3 checks. Restarting the terminal to sign in again."
    kill -TERM "$pid" 2>/dev/null || true
    wait "$pid" || true
    exit 1
  fi
  sleep 30 &
  wait $!
done
wait "$pid"
