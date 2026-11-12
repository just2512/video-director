#!/usr/bin/env bash
# Provision the hosted media runtime without Ubuntu archive mirrors.
# FFmpeg is supplied by the hash-pinned imageio-ffmpeg wheel already used by
# the application; WenQuanYi Micro Hei is fetched from an immutable Git commit.
set -euo pipefail

font_commit="cd82defe33ec0e86e628329f1b63049ef562c8e5"
font_blob="2c9bc2d4e02213d46c1d6b20837ffd54480ea256"
apache_blob="79fd69c9da127a6ab0d96c0f2f543def955345fd"
gpl_blob="94a9ed024d3859793618152ea559a168bbcbb5e2"
font_tmp="$RUNNER_TEMP/wqy-microhei.ttc"
font_dir="/usr/share/fonts/truetype/wqy"
license_dir="/usr/share/doc/fonts-wqy-microhei"

fetch_verified_git_blob() {
  local path="$1"
  local expected="$2"
  local output="$3"
  local url="https://raw.githubusercontent.com/anthonyfok/fonts-wqy-microhei/$font_commit/$path"
  curl --fail --location --silent --show-error \
    --retry 3 --retry-delay 2 --connect-timeout 20 --max-time 120 \
    "$url" -o "$output"
  local actual
  actual="$(git hash-object "$output")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Git blob verification failed for $path: expected $expected, got $actual" >&2
    exit 1
  fi
}

# Pin and verify the Linux wheel; no Ubuntu archive mirror is required.
ffmpeg_requirements="$RUNNER_TEMP/imageio-ffmpeg.requirements.txt"
printf '%s\n' \
  'imageio-ffmpeg==0.8.1 --hash=sha256:050d6850601c3b3890bd2b28306f3b842b3daea390a561101174375a1c460bda' \
  > "$ffmpeg_requirements"
python -m pip install --disable-pip-version-check --no-deps --only-binary=:all: \
  --require-hashes -r "$ffmpeg_requirements"

ffmpeg_bin="$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
if [[ ! -x "$ffmpeg_bin" ]]; then
  echo "Pinned imageio-ffmpeg binary is missing or not executable: $ffmpeg_bin" >&2
  exit 1
fi
"$ffmpeg_bin" -hide_banner -version
encoders="$("$ffmpeg_bin" -hide_banner -encoders 2>&1)"
if ! grep -q 'libx264' <<< "$encoders"; then
  echo "Pinned FFmpeg build does not expose libx264; refusing to run render tests." >&2
  exit 1
fi
filters="$("$ffmpeg_bin" -hide_banner -filters 2>&1)"
if ! grep -q 'drawtext' <<< "$filters"; then
  echo "Pinned FFmpeg build does not expose drawtext; refusing to run CJK render tests." >&2
  exit 1
fi

fetch_verified_git_blob "wqy-microhei.ttc" "$font_blob" "$font_tmp"
fetch_verified_git_blob "LICENSE_Apache2.txt" "$apache_blob" "$RUNNER_TEMP/wqy-LICENSE_Apache2.txt"
fetch_verified_git_blob "LICENSE_GPLv3.txt" "$gpl_blob" "$RUNNER_TEMP/wqy-LICENSE_GPLv3.txt"

sudo install -d -m 0755 "$font_dir" "$license_dir"
sudo install -m 0644 "$font_tmp" "$font_dir/wqy-microhei.ttc"
sudo install -m 0644 "$RUNNER_TEMP/wqy-LICENSE_Apache2.txt" "$license_dir/LICENSE_Apache2.txt"
sudo install -m 0644 "$RUNNER_TEMP/wqy-LICENSE_GPLv3.txt" "$license_dir/LICENSE_GPLv3.txt"
if command -v fc-cache >/dev/null 2>&1; then
  fc-cache -f "$font_dir"
fi

font_path="$(python -c 'from video_director.graphics.font_resolver import find_local_font; print(find_local_font() or "")')"
if [[ "$font_path" != "$font_dir/wqy-microhei.ttc" ]]; then
  echo "Font resolver did not select the verified WQY font: $font_path" >&2
  exit 1
fi

ffmpeg_bin_dir="$RUNNER_TEMP/ffmpeg-bin"
mkdir -p "$ffmpeg_bin_dir"
# FFmpeg and FFprobe are separate executables. Use the upstream static
# FFprobe Linux x64 asset, SHA-256 pinned, to avoid runner-specific shared libs.
ffprobe_url="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffprobe-linux-x64.gz"
ffprobe_sha256="25d9b6ccb05e3d9de9e04e31e2506d8dd7f9f0418981965ac6df12e8d3afd067"
ffprobe_download="$RUNNER_TEMP/ffprobe-linux-x64.gz"
curl --fail --location --silent --show-error \
  --retry 3 --retry-delay 2 --connect-timeout 20 --max-time 180 \
  "$ffprobe_url" -o "$ffprobe_download"
echo "$ffprobe_sha256  $ffprobe_download" | sha256sum --check --status
ffprobe_bin="$ffmpeg_bin_dir/ffprobe"
gzip -dc "$ffprobe_download" > "$ffprobe_bin"
chmod 0755 "$ffprobe_bin"
"$ffprobe_bin" -hide_banner -version

# Verify that the independent FFprobe can read AAC metadata produced by the
# exact pinned FFmpeg executable before renderer acceptance starts.
probe_smoke="$RUNNER_TEMP/ffprobe-validation.mp4"
"$ffmpeg_bin" -y -hide_banner -loglevel error \
  -f lavfi -i "color=c=black:s=32x32:d=0.5" \
  -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=0.5" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$probe_smoke"
probe_metadata="$("$ffprobe_bin" -v error -select_streams a:0 \
  -show_entries stream=codec_name,sample_rate,channels \
  -of default=noprint_wrappers=1 "$probe_smoke")"
printf '%s\n' "$probe_metadata"
if [[ "$probe_metadata" != *"codec_name=aac"* || "$probe_metadata" != *"sample_rate=48000"* ]]; then
  echo "FFPROBE_SMOKE=FAIL metadata did not contain AAC/48000Hz fields" >&2
  exit 1
fi
echo "FFPROBE_SMOKE=PASS"
ln -sfn "$ffmpeg_bin" "$ffmpeg_bin_dir/ffmpeg"
echo "$ffmpeg_bin_dir" >> "$GITHUB_PATH"
echo "IMAGEIO_FFMPEG_EXE=$ffmpeg_bin" >> "$GITHUB_ENV"

echo "MEDIA_RUNTIME=PASS"
echo "FFMPEG=$ffmpeg_bin"
echo "CJK_FONT=$font_path"
echo "WQY_FONT_GIT_BLOB=$font_blob"
