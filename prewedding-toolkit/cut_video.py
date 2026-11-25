#!/usr/bin/env python3
"""Cut prewedding videos (offline). ffmpeg is bundled through imageio-ffmpeg,
so nothing else has to be installed on the computer.

The cut standard is ON by default (see cut_standard.py). With no times given:
  * the first 0.5 s of every clip is dropped,
  * the cut starts when the motion / action starts,
  * the cut ends before camera shake or a pan-out,
  * shaky and behind-the-scenes (BTS) clips are skipped.
Give --start/--end/--segments to choose the cut yourself (used exactly).

Usage examples:
  # automatic: cut one clip by the standard -> pw01_cut.mp4
  python cut_video.py "D:\\Videos\\pw01.mp4"

  # automatic: every clip in a folder -> D:\\Shoot\\_cut\\*_cut.mp4 + cut_report.txt
  python cut_video.py "D:\\Shoot"

  # a folder straight to one Premiere Pro timeline, no video render
  python cut_video.py "D:\\Shoot" --xml-only

  # only show what the standard would cut
  python cut_video.py "D:\\Shoot" --dry-run

  # one part, 0:30 -> 2:15
  python cut_video.py "D:\\Videos\\pw01.mp4" --start 0:30 --end 2:15

  # several parts joined into one highlight
  python cut_video.py pw01.mp4 --segments "0:30-0:45, 1:10-1:25, 3:00-3:20"

  # vertical for TikTok / Reels, 1080x1920, with fade in/out
  python cut_video.py pw01.mp4 --start 1:00 --end 1:30 --ratio 9:16 --fade 0.5

  # very fast cut without re-encoding (cut points snap to keyframes)
  python cut_video.py pw01.mp4 --start 0:30 --end 2:15 --fast

  # also write pw01_cut.xml to open the cuts in Premiere Pro (25 fps sequence)
  python cut_video.py pw01.mp4 --segments "0:30-0:45, 1:10-1:25" --xml

  # only the Premiere XML, no video render; 29.97 fps sequence
  python cut_video.py pw01.mp4 --segments "0:30-0:45, 1:10-1:25" --xml-only --xml-fps 29.97
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

SKIPPED = 3   # exit code when the standard skips the clip (shaky / BTS)


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


def write_premiere_xml(args, src: Path, dst: Path, segs, has_audio: bool) -> int:
    """Premiere Pro timeline of the cuts, pointing at the ORIGINAL video."""
    import premiere_xml

    xml_path = dst.with_suffix(".xml")
    try:
        placements = premiere_xml.export_cut_list(
            src, segs, xml_path, sequence_fps=args.xml_fps, ffmpeg=FFMPEG, audio=has_audio)
    except ValueError as e:
        print(f"[ERROR] Premiere XML: {e}")
        return 1
    print("Premiere XML:")
    print(premiere_xml.describe(placements, args.xml_fps))
    if args.ratio or args.height or args.fade:
        print("  (--ratio/--height/--fade are not in the XML; set them in Premiere)")
    print(f"Saved to: {xml_path}  (Premiere Pro: File > Import)\n")
    return 0


def standard_from_args(args):
    import cut_standard

    return cut_standard.Standard(head_trim=args.head_trim, shake_limit=args.shake,
                                 pan_limit=args.pan, min_keep=args.min_length,
                                 skip_bad=not args.no_skip)


def write_folder_timeline(args, picks, xml_path: Path) -> int:
    """One Premiere Pro timeline with the kept part of every clip, in name order."""
    from dataclasses import replace

    import premiere_xml

    cuts = []
    try:
        for p in picks:
            if p.kept:
                media = premiere_xml.probe_media(p.path, FFMPEG)
                if args.mute:
                    media = replace(media, audio_channels=0)
                cuts.append(premiere_xml.Cut(media, p.start, p.end))
        if not cuts:
            print("No clip was kept, so no Premiere XML was written.")
            return 0
        placements = premiere_xml.write_xmeml(xml_path, cuts, args.xml_fps)
    except ValueError as e:
        print(f"[ERROR] Premiere XML: {e}")
        return 1
    print("\nPremiere XML:")
    print(premiere_xml.describe(placements, args.xml_fps))
    print(f"Saved to: {xml_path}  (Premiere Pro: File > Import)")
    return 0


def cut_folder(folder: Path, args) -> int:
    """Apply the cut standard to every clip in a folder."""
    import cut_standard

    if args.start or args.end or args.segments or args.no_auto:
        print("[ERROR] a folder is always cut by the standard; "
              "--start/--end/--segments/--no-auto work with one video only")
        return 1
    clips = cut_standard.list_clips(folder)
    if not clips:
        print(f"[ERROR] no videos found in: {folder}")
        return 1
    out_dir = Path(args.output) if args.output else folder / "_cut"
    std = standard_from_args(args)
    print(f"{len(clips)} clips in {folder}")
    picks, failed = [], []
    for i, clip in enumerate(clips, 1):
        print(f"\n[{i}/{len(clips)}] {clip.name}: checking shake, pans and action ...")
        try:
            pick = cut_standard.pick_cut(clip, std, FFMPEG)
        except Exception as e:  # unreadable file: note it and go on with the others
            print(f"[ERROR] {clip.name}: {e}")
            failed.append(f"FAIL  {clip.name}: {e}")
            continue
        print(pick.summary())
        picks.append(pick)
        if not pick.kept or args.dry_run or args.xml_only:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        if cut_one(args, clip, out_dir / f"{clip.stem}_cut.mp4", pick=pick, xml=False) != 0:
            failed.append(f"FAIL  {clip.name}: ffmpeg could not cut it")

    kept = [p for p in picks if p.kept]
    total = f"{len(kept)} kept, {len(picks) - len(kept)} skipped" + \
        (f", {len(failed)} failed" if failed else "")
    print(f"\n{total}")
    if args.dry_run:
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "cut_report.txt"
    lines = ["Pre-wedding cut standard",
             f"Folder: {folder}",
             f"Rules: drop the first {std.head_trim:g}s, start when the action starts, "
             "end before camera shake or a pan-out, skip shaky and behind-the-scenes clips.",
             "", *[p.summary() for p in picks], *failed, "", total, ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {report}")
    code = 1 if failed else 0
    if args.xml or args.xml_only:
        if write_folder_timeline(args, picks, out_dir / f"{folder.name}_timeline.xml") != 0:
            code = 1
    if not args.xml_only and kept:
        print(f"Cut videos saved in: {out_dir}")
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description="Cut prewedding videos")
    ap.add_argument("input", help="video file, or a folder of clips")
    ap.add_argument("-o", "--output",
                    help="output file (default: <name>_cut.mp4); for a folder: output folder "
                         "(default: <folder>\\_cut)")
    ap.add_argument("--start", help="start time, e.g. 0:30")
    ap.add_argument("--end", help="end time, e.g. 2:15")
    ap.add_argument("--segments", help='several parts to join, e.g. "0:30-0:45, 1:10-1:25"')
    ap.add_argument("--ratio", help="crop to 16:9, 9:16, 1:1 or 4:5")
    ap.add_argument("--height", type=int, help="output height, e.g. 1080 or 1920")
    ap.add_argument("--fade", type=float, default=0, help="fade in/out seconds (default 0)")
    ap.add_argument("--mute", action="store_true", help="remove the audio")
    ap.add_argument("--crf", type=int, default=18, help="quality 15 (best) - 28 (small); default 18")
    ap.add_argument("--fast", action="store_true", help="no re-encode (one part only, keyframe accurate)")
    ap.add_argument("--xml", action="store_true",
                    help="also write a Premiere Pro XML timeline of the cuts (<output name>.xml)")
    ap.add_argument("--xml-only", action="store_true",
                    help="write only the Premiere Pro XML, do not render the video")
    ap.add_argument("--xml-fps", default="25",
                    help="Premiere sequence frame rate for --xml: 25, 29.97, 50, 59.94 ... (default 25)")
    std = ap.add_argument_group(
        "cut standard", "used when no --start/--end/--segments are given, and for folders")
    std.add_argument("--no-auto", action="store_true",
                     help="turn the standard off: cut the whole clip or the times you give")
    std.add_argument("--no-skip", action="store_true",
                     help="never skip shaky or behind-the-scenes clips")
    std.add_argument("--head-trim", type=float, default=0.5,
                     help="seconds dropped at the start of every clip (default 0.5)")
    std.add_argument("--shake", type=float, default=0.15,
                     help="shake limit; higher allows more shake (default 0.15)")
    std.add_argument("--pan", type=float, default=0.35,
                     help="pan-out speed limit in picture widths per second (default 0.35)")
    std.add_argument("--min-length", type=float, default=1.5,
                     help="shortest cut to keep, in seconds (default 1.5)")
    std.add_argument("--dry-run", action="store_true",
                     help="only show what would be cut; make no files")
    args = ap.parse_args()

    src = Path(args.input.strip().strip('"'))
    if src.is_dir():
        return cut_folder(src, args)
    if not src.is_file():
        print(f"[ERROR] not found: {src}")
        return 1
    dst = Path(args.output) if args.output else src.with_name(src.stem + "_cut.mp4")
    return cut_one(args, src, dst)


def cut_one(args, src: Path, dst: Path, pick=None, xml: bool = True) -> int:
    """Cut one clip. `pick` is the standard's decision when it is already known."""
    duration, has_audio = probe(src)
    has_audio = has_audio and not args.mute
    if not (args.start or args.end or args.segments or args.no_auto):
        if pick is None:
            import cut_standard

            print(f"{src.name}: checking shake, pans and action ...")
            try:
                pick = cut_standard.pick_cut(src, standard_from_args(args), FFMPEG)
            except Exception as e:
                print(f"[ERROR] {e}")
                return 1
            print(pick.summary())
        if not pick.kept:
            print("Skipped by the cut standard (use --no-skip to cut it anyway).")
            return SKIPPED
        segs = [(pick.start, pick.end)]
    else:
        try:
            segs = build_segments(args, duration)
        except ValueError as e:
            print(f"[ERROR] {e}")
            return 1
    if args.dry_run:
        print("Would cut:", ", ".join(f"{a:g}s-{b:g}s" for a, b in segs))
        return 0

    if xml and (args.xml or args.xml_only):
        if write_premiere_xml(args, src, dst, segs, has_audio) != 0:
            return 1
        if args.xml_only:
            return 0

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
