#!/usr/bin/env python3
"""Cut prewedding videos (offline). ffmpeg is bundled through imageio-ffmpeg,
so nothing else has to be installed on the computer.

Usage examples:
  # one part, 0:30 -> 2:15
  python cut_video.py "D:\\Videos\\pw01.mp4" --start 0:30 --end 2:15

  # several parts joined into one highlight
  python cut_video.py pw01.mp4 --segments "0:30-0:45, 1:10-1:25, 3:00-3:20"

  # vertical for TikTok / Reels, 1080x1920, with fade in/out
  python cut_video.py pw01.mp4 --start 1:00 --end 1:30 --ratio 9:16 --fade 0.5

  # very fast cut without re-encoding (cut points snap to keyframes)
  python cut_video.py pw01.mp4 --start 0:30 --end 2:15 --fast
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

try:
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # fall back to ffmpeg on PATH
    FFMPEG = "ffmpeg"


def parse_time(t: str) -> float:
    """'90', '1:30', '01:01:30', '1:30.5' -> seconds."""
    parts = t.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad time: {t}")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def probe(path: Path) -> tuple[float | None, bool]:
    """Return (duration_seconds, has_audio) by reading ffmpeg's stderr."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    return dur, "Audio:" in r.stderr


def build_segments(args, duration: float | None) -> list[tuple[float, float]]:
    if args.segments:
        segs = []
        for part in args.segments.split(","):
            a, b = part.strip().split("-")
            segs.append((parse_time(a), parse_time(b)))
    else:
        start = parse_time(args.start) if args.start else 0.0
        end = parse_time(args.end) if args.end else duration
        if end is None:
            raise ValueError("cannot read video length; please give --end")
        segs = [(start, end)]
    for a, b in segs:
        if duration and a >= duration:
            raise ValueError(f"start {a:g}s is after the end of the video ({duration:.1f}s)")
        if b <= a:
            raise ValueError(f"end must be after start: {a:g}s-{b:g}s")
    return segs


def video_filter(args) -> str:
    f = []
    if args.ratio:
        rw, rh = (int(v) for v in args.ratio.split(":"))
        # centre crop to the ratio, then scale to the target height
        f.append(f"crop='min(iw,ih*{rw}/{rh})':'min(ih,iw*{rh}/{rw})'")
    if args.height:
        f.append(f"scale=-2:{args.height}")
    f.append("setsar=1")
    return ",".join(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Cut prewedding videos")
    ap.add_argument("input", help="video file")
    ap.add_argument("-o", "--output", help="output file (default: <name>_cut.mp4)")
    ap.add_argument("--start", help="start time, e.g. 0:30")
    ap.add_argument("--end", help="end time, e.g. 2:15")
    ap.add_argument("--segments", help='several parts to join, e.g. "0:30-0:45, 1:10-1:25"')
    ap.add_argument("--ratio", help="crop to 16:9, 9:16, 1:1 or 4:5")
    ap.add_argument("--height", type=int, help="output height, e.g. 1080 or 1920")
    ap.add_argument("--fade", type=float, default=0, help="fade in/out seconds (default 0)")
    ap.add_argument("--mute", action="store_true", help="remove the audio")
    ap.add_argument("--crf", type=int, default=18, help="quality 15 (best) - 28 (small); default 18")
    ap.add_argument("--fast", action="store_true", help="no re-encode (one part only, keyframe accurate)")
    args = ap.parse_args()

    src = Path(args.input.strip('"'))
    if not src.is_file():
        print(f"[ERROR] not found: {src}")
        return 1
    dst = Path(args.output) if args.output else src.with_name(src.stem + "_cut.mp4")
    duration, has_audio = probe(src)
    has_audio = has_audio and not args.mute
    try:
        segs = build_segments(args, duration)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    if args.fast:
        if len(segs) > 1 or args.ratio or args.height or args.fade:
            print("[ERROR] --fast works only for one part without --ratio/--height/--fade")
            return 1
        a, b = segs[0]
        cmd = [FFMPEG, "-y", "-hide_banner", "-ss", str(a), "-to", str(b), "-i", str(src),
               "-c", "copy", "-avoid_negative_ts", "make_zero"]
        if not has_audio:
            cmd.append("-an")
        cmd.append(str(dst))
    else:
        vf = video_filter(args)
        parts, labels = [], []
        for i, (a, b) in enumerate(segs):
            parts.append(f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS,{vf}[v{i}]")
            labels.append(f"[v{i}]")
            if has_audio:
                parts.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]")
                labels.append(f"[a{i}]")
        n = len(segs)
        parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}"
                     + ("[vc][ac]" if has_audio else "[vc]"))
        total = sum(b - a for a, b in segs)
        vout, aout = "[vc]", "[ac]"
        if args.fade > 0:
            st = max(0.0, total - args.fade)
            parts.append(f"[vc]fade=t=in:st=0:d={args.fade},fade=t=out:st={st}:d={args.fade}[vf]")
            vout = "[vf]"
            if has_audio:
                parts.append(f"[ac]afade=t=in:st=0:d={args.fade},afade=t=out:st={st}:d={args.fade}[af]")
                aout = "[af]"
        cmd = [FFMPEG, "-y", "-hide_banner", "-i", str(src), "-filter_complex", ";".join(parts),
               "-map", vout]
        if has_audio:
            cmd += ["-map", aout, "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]

    print("Cutting:", ", ".join(f"{a:g}s-{b:g}s" for a, b in segs))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print("[ERROR] ffmpeg failed")
        return 1
    print(f"\nDone. Saved to: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
