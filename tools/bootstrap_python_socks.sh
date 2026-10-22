#!/usr/bin/env bash
set -euo pipefail

python_bin="${PY311_BIN:-/home/actions/miniforge3/envs/py311/bin}/python3.11"
tmp_base="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
metadata="$tmp_base/pysocks-1.7.1.json"

if "$python_bin" -c "import socks" >/dev/null 2>&1; then
  echo "Python SOCKS support is already available."
  exit 0
fi

# curl can use the runner's SOCKS proxy before Python's pip has PySocks installed.
curl --fail --silent --show-error --location \
  --retry 2 --retry-all-errors --connect-timeout 15 --max-time 120 \
  "https://pypi.org/pypi/PySocks/1.7.1/json" -o "$metadata"

wheel_url="$("$python_bin" -c 'import json,sys; print(next(item["url"] for item in json.load(open(sys.argv[1], encoding="utf-8"))["urls"] if item["filename"].endswith("py3-none-any.whl")))' "$metadata")"
wheel="$tmp_base/$(basename "$wheel_url")"
curl --fail --silent --show-error --location \
  --retry 2 --retry-all-errors --connect-timeout 15 --max-time 120 \
  "$wheel_url" -o "$wheel"

env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy \
  -u HTTP_PROXY -u http_proxy \
  "$python_bin" -m pip install --no-index "$wheel"
"$python_bin" -c "import socks; print('Python SOCKS support ready:', socks.__file__)"
