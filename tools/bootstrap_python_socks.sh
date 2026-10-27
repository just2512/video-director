#!/usr/bin/env bash
set -euo pipefail

python_bin="${PY311_BIN:-/home/actions/miniforge3/envs/py311/bin}/python3.11"
tmp_base="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
mkdir -p "$tmp_base"

download_and_install_wheel() {
  local package="$1"
  local version="$2"
  local slug="${package,,}"
  local metadata="$tmp_base/${slug}-${version}.json"

  # curl can use the runner's SOCKS proxy before Python's HTTP clients have
  # all optional SOCKS transports installed.
  curl --fail --silent --show-error --location \
    --retry 2 --retry-all-errors --connect-timeout 15 --max-time 120 \
    "https://pypi.org/pypi/${package}/${version}/json" -o "$metadata"

  local wheel_url
  wheel_url="$("$python_bin" - "$metadata" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    urls = json.load(stream)["urls"]
wheel = next((item for item in urls if item["filename"].endswith("py3-none-any.whl")), None)
if wheel is None:
    raise SystemExit("No universal Python wheel found for requested SOCKS support package")
print(wheel["url"])
PY
)"
  local wheel="$tmp_base/$(basename "${wheel_url%%\?*}")"
  curl --fail --silent --show-error --location \
    --retry 2 --retry-all-errors --connect-timeout 15 --max-time 120 \
    "$wheel_url" -o "$wheel"

  # The wheel is local; unset proxy variables so pip does not require the
  # very SOCKS dependency being bootstrapped in order to install it.
  env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy \
    -u HTTP_PROXY -u http_proxy \
    "$python_bin" -m pip install --no-index --no-deps "$wheel"
}

if ! "$python_bin" -c "import socks" >/dev/null 2>&1; then
  download_and_install_wheel "PySocks" "1.7.1"
fi

# PySocks (import socks) is for Requests/pip; HTTPX has a separate optional
# dependency named socksio. Both must be present for OpenAI-compatible calls
# when the self-hosted runner exports an ALL_PROXY/SOCKS URL.
if ! "$python_bin" -c "import socksio" >/dev/null 2>&1; then
  download_and_install_wheel "socksio" "1.0.0"
fi

"$python_bin" - <<'PY'
import socks
import socksio
print("Python SOCKS transports ready:", socks.__file__, socksio.__file__)
PY
