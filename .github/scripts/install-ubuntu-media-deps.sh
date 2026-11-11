#!/usr/bin/env bash
# Install Ubuntu media/font packages without allowing a degraded Azure mirror
# to hold a GitHub-hosted job until its overall timeout.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 package [package ...]" >&2
  exit 2
fi

apt_options=(
  -o Acquire::Retries=1
  -o Acquire::http::Timeout=10
  -o Acquire::https::Timeout=10
)

remove_azure_mirror() {
  local mirror_file="/etc/apt/apt-mirrors.txt"
  if [[ -f "$mirror_file" ]] && grep -q 'azure\.archive\.ubuntu\.com' "$mirror_file"; then
    echo "::warning::Azure Ubuntu mirror stalled; retrying against fallback mirrors."
    sudo sed -i '/azure\.archive\.ubuntu\.com/d' "$mirror_file"
    cat "$mirror_file"
  fi
}

apt_update() {
  if timeout 120 sudo apt-get "${apt_options[@]}" update; then
    return 0
  fi
  remove_azure_mirror
  timeout 180 sudo apt-get "${apt_options[@]}" update
}

apt_download_packages() {
  if timeout 180 sudo apt-get "${apt_options[@]}" install -y --download-only --no-install-recommends "$@"; then
    return 0
  fi
  remove_azure_mirror
  timeout 180 sudo apt-get "${apt_options[@]}" install -y --download-only --no-install-recommends "$@"
}

apt_update
apt_download_packages "$@"
# All package archives are now local; this final step does not need a mirror.
sudo apt-get install -y --no-install-recommends "$@"
