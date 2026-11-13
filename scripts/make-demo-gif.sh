#!/usr/bin/env bash
# Turn the recording from ui/scripts/record-demo.mjs into docs/demo.gif (~900 px wide, 10 fps, <= 5 MB) and
# copy its screenshots to docs/screenshots/. The quality-setup wait is shortened to its first seconds and
# its last half second, so the GIF shows the progress bar without 20 s of waiting.
#
#   scripts/make-demo-gif.sh ui/demo-recording
set -euo pipefail

rec=${1:-ui/demo-recording}
out=${2:-docs/demo.gif}
python=${PYTHON:-python}
ffmpeg=${FFMPEG:-ffmpeg}

# Times in seconds from the start of the video; the cut is relative to the trimmed start.
read -r start end cut_from cut_to < <("$python" -c "
import json, sys
m = json.load(open(sys.argv[1]))
start = max(0.0, m['start'] - 0.3)
print(round(start, 2), round(m['end'] + 0.5, 2),
      round(m['quality_wait_start'] + 3.0 - start, 2), round(m['quality_wait_end'] - 0.5 - start, 2))
" "$rec/marks.json")

filters="trim=start=$start:end=$end,setpts=PTS-STARTPTS"
filters="$filters,select='not(between(t,$cut_from,$cut_to))',setpts=N/FRAME_RATE/TB,fps=10,scale=900:-1:flags=lanczos"

"$ffmpeg" -y -loglevel error -i "$rec/demo.webm" \
  -vf "$filters,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  "$out"

mkdir -p docs/screenshots
cp "$rec"/screenshots/*.png docs/screenshots/
size=$(wc -c < "$out")
echo "wrote $out ($((size / 1024)) KB) and docs/screenshots/"
[ "$size" -le $((5 * 1024 * 1024)) ] || { echo "the GIF is over 5 MB" >&2; exit 1; }
