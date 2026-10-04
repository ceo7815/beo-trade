#!/usr/bin/env bash
# Install Docker Engine and the Compose plugin on Ubuntu or Debian.
# Existing Docker configuration is left unchanged.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root on the XCloud server."
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Cannot detect the OS. Install Docker Engine and the Compose plugin, then run scripts/deploy.sh."
  exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *)
    echo "Unsupported distro: ${ID:-unknown}."
    echo "Install Docker Engine and the Docker Compose plugin manually, then run scripts/deploy.sh."
    exit 1
    ;;
esac

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Docker and Compose are already installed. Docker configuration was not changed."
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
    arch="$(dpkg --print-architecture)"
    echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
  fi
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! command -v git >/dev/null 2>&1; then
  apt-get install -y git
fi

echo "Bootstrap finished. Docker configuration files that already existed were not replaced."
if ! command -v java >/dev/null 2>&1 || ! java -version 2>&1 | grep -q 'version "2[1-9]'; then
  apt-get install -y openjdk-21-jre-headless || echo "Host Java 21 was not installed. The Theta container image includes Java 21."
fi
docker --version
docker compose version
