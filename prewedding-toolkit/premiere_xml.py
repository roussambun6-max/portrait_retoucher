#!/usr/bin/env python3
"""Make a Premiere Pro timeline (Final Cut Pro 7 XML, xmeml version 4) from
video cuts, so the edit can be opened and finished in Premiere Pro.

Import in Premiere Pro with File > Import and pick the .xml file.

Usage examples:
  # parts of two clips plus one whole clip on a 25 fps sequence
  python premiere_xml.py --fps 25 -o teaser.xml "pw01.mp4@0:30-0:45" "pw02.mp4@0:10-0:14, 0:20-0:23" phone.mp4

  # from cut_video.py (writes pw01_cut.xml next to the video)
  python cut_video.py pw01.mp4 --segments "0:30-0:45, 1:10-1:25" --xml

Frame rates (why this file is careful):
  Premiere reads <start>, <end>, <in>, <out> and <duration> of every clipitem
  as frames of the SEQUENCE frame rate. A 50 fps clip cut at 0:30 on a 25 fps
  sequence therefore needs in=750, not in=1500. An earlier export wrote the
  clip's own frame numbers, so Premiere started each clip twice as far in and
  ran past the end of the media.

  This module:
    * gives every clipitem the sequence <rate>, and writes only sequence frames
      in it,
    * gives each <file> the media's own <rate> and its <duration> in its own
      frames,
    * adds <pproTicksIn>/<pproTicksOut> (exact Premiere time) for the same
      in/out points,
    * keeps every out point on or before the last whole sequence frame of the
      media.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote

TICKS_PER_SECOND = 254_016_000_000  # Premiere Pro's internal time unit

# Rates cameras and phones record at. The NTSC rates are exact fractions.
STANDARD_RATES = [
    Fraction(24000, 1001), Fraction(24), Fraction(25),
    Fraction(30000, 1001), Fraction(30), Fraction(48), Fraction(50),
    Fraction(60000, 1001), Fraction(60), Fraction(100),
    Fraction(120000, 1001), Fraction(120),
]


# --------------------------------------------------------------------------
# frame rates and time


def parse_rate(value) -> Fraction:
    """'25', '29.97', '59.94', '23.976', '30000/1001', 50 -> exact frame rate.

    A value within 1% of a standard rate snaps to it, so 29.97 becomes
    30000/1001 and a phone's average of 29.92 fps becomes 29.97.
    """
    if isinstance(value, Fraction):
        fps = value
    else:
        text = str(value).strip().lower()
        if text.endswith("fps"):
            text = text[:-3].strip()
        try:
            fps = Fraction(text)
        except (ValueError, ZeroDivisionError):
            raise ValueError(f"bad frame rate: {value}") from None
    if fps <= 0:
        raise ValueError(f"bad frame rate: {value}")
    best = min(STANDARD_RATES, key=lambda r: abs(r - fps))
    return best if abs(best - fps) <= best / 100 else fps


def timebase(fps: Fraction) -> tuple[int, bool]:
    """Frame rate -> (<timebase>, <ntsc>) as written in xmeml."""
    if fps.denominator == 1001:
        return round(fps * Fraction(1001, 1000)), True
    return round(fps), False


def format_rate(fps: Fraction) -> str:
    return f"{float(fps):.3f}".rstrip("0").rstrip(".")


def _seconds(value) -> Fraction:
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def _round(x: Fraction) -> int:
    """Round half up (Python's round() is half-to-even)."""
    return math.floor(x + Fraction(1, 2))


def ticks(frames: int, fps: Fraction) -> int:
    """Frames at `fps` -> Premiere ticks (exact for all standard rates)."""
    return _round(Fraction(frames) * TICKS_PER_SECOND / fps)


def parse_time(t: str) -> float:
    """'90', '1:30', '01:01:30', '1:30.5' -> seconds (same as cut_video.py)."""
    parts = t.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad time: {t}")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def parse_segments(text: str) -> list[tuple[float, float]]:
    """'0:30-0:45, 1:10-1:25' -> [(30.0, 45.0), (70.0, 85.0)]"""
    segs = []
    for part in text.split(","):
        a, b = part.strip().split("-")
        segs.append((parse_time(a), parse_time(b)))
    return segs


# --------------------------------------------------------------------------
# media and cuts


@dataclass(frozen=True)
class Media:
    path: str                  # full path on the editing computer
    fps: Fraction              # the clip's own frame rate
    duration: float            # seconds
    width: int = 1920
    height: int = 1080
    audio_channels: int = 2    # 0 = no audio
    sample_rate: int = 48000

    @property
    def frames(self) -> int:
        """Whole frames in the media, counted at the media's own rate."""
        return math.floor(_seconds(self.duration) * self.fps)

    @property
    def name(self) -> str:
        return _path_obj(self.path).name


@dataclass(frozen=True)
class Cut:
    media: Media
    start: float               # seconds into the media
    end: float


@dataclass(frozen=True)
class Placement:
    """Where one cut lands. Every number is in frames of the SEQUENCE rate."""
    cut: Cut
    start: int                 # timeline in
    end: int                   # timeline out
    in_: int                   # media in
    out: int                   # media out
    duration: int              # whole media length
    clamped: bool              # out was pulled back to the end of the media


def plan_timeline(cuts: list[Cut], sequence_fps) -> list[Placement]:
    """Lay the cuts end to end and convert all times to sequence frames."""
    seq = parse_rate(sequence_fps)
    placements: list[Placement] = []
    pos = 0
    for cut in cuts:
        media = cut.media
        # Last whole sequence frame that is still inside the media.
        length = math.floor(Fraction(media.frames) * seq / media.fps)
        fin = _round(_seconds(cut.start) * seq)
        fout = _round(_seconds(cut.end) * seq)
        if fin < 0 or fout <= fin:
            raise ValueError(f"{media.name}: bad cut {cut.start:g}s-{cut.end:g}s")
        clamped = fout > length
        fout = min(fout, length)
        if fout <= fin:
            raise ValueError(
                f"{media.name}: {cut.start:g}s-{cut.end:g}s is after the end of the clip "
                f"({float(media.frames / media.fps):.2f}s)")
        placements.append(Placement(cut, pos, pos + fout - fin, fin, fout, length, clamped))
        pos += fout - fin
    return placements


# --------------------------------------------------------------------------
# reading clips with ffmpeg


def parse_ffmpeg_info(text: str, path) -> Media:
    """Build a Media from the stderr of `ffmpeg -hide_banner -i <path>`."""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        raise ValueError(f"cannot read the length of {path}")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])

    video = next((ln for ln in text.splitlines()
                  if "Video:" in ln and "attached pic" not in ln), None)
    if video is None:
        raise ValueError(f"no video stream in {path}")
    fm = (re.search(r"(\d+(?:\.\d+)?)\s*fps", video)
          or re.search(r"(\d+(?:\.\d+)?)(k?)\s*tbr", video))
    if not fm:
        raise ValueError(f"cannot read the frame rate of {path}")
    fps_text = fm[1]
    if fm.lastindex and fm.lastindex >= 2 and fm[2] == "k":
        fps_text = str(float(fps_text) * 1000)
    fps = parse_rate(fps_text)

    width, height = 1920, 1080
    sm = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video)
    if sm:
        width, height = int(sm[1]), int(sm[2])
    rm = (re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", text)
          or re.search(r"rotate\s*:\s*(-?\d+)", text))
    if rm and round(abs(float(rm[1]))) % 180 == 90:
        width, height = height, width

    channels, sample_rate = 0, 48000
    audio = next((ln for ln in text.splitlines() if "Audio:" in ln), None)
    if audio:
        am = re.search(r"(\d+)\s*Hz,\s*([^,]+)", audio)
        if am:
            sample_rate = int(am[1])
            layout = am[2].strip().lower()
            channels = 1 if layout in ("mono", "1 channels") else 2
        else:
            channels = 2

    return Media(path=str(path), fps=fps, duration=duration, width=width,
                 height=height, audio_channels=channels, sample_rate=sample_rate)


def probe_media(path, ffmpeg: str = "ffmpeg") -> Media:
    path = Path(path).resolve()
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return parse_ffmpeg_info(r.stderr, path)


# --------------------------------------------------------------------------
# writing xmeml


def _is_windows_path(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith("\\\\")


def _path_obj(path: str):
    return PureWindowsPath(path) if _is_windows_path(path) else PurePosixPath(path)


def path_to_url(path: str) -> str:
    r"""Full path -> <pathurl>, e.g. D:\Videos\a b.mp4 -> file://localhost/D%3A/Videos/a%20b.mp4"""
    path = str(path)
    if _is_windows_path(path):
        p = PureWindowsPath(path)
        if p.drive.startswith("\\\\"):  # \\server\share\dir\a.mp4
            server, share = p.drive.lstrip("\\").split("\\", 1)
            rest = "/".join(p.parts[1:])
            return f"file://{quote(server)}/{quote(share)}/{quote(rest, safe='/')}"
        return "file://localhost/" + quote(p.as_posix(), safe="/")
    return "file://localhost" + quote(PurePosixPath(path).as_posix(), safe="/")


def _sub(parent, tag: str, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent, fps: Fraction):
    tb, ntsc = timebase(fps)
    r = _sub(parent, "rate")
    _sub(r, "timebase", tb)
    _sub(r, "ntsc", "TRUE" if ntsc else "FALSE")


def _video_characteristics(parent, fps: Fraction, width: int, height: int):
    sc = _sub(parent, "samplecharacteristics")
    _rate(sc, fps)
    _sub(sc, "width", width)
    _sub(sc, "height", height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")


def _file(parent, media: Media, file_id: str, full: bool):
    """Full <file> the first time a clip appears, a reference after that."""
    f = _sub(parent, "file", id=file_id)
    if not full:
        return
    _sub(f, "name", media.name)
    _sub(f, "pathurl", path_to_url(media.path))
    _rate(f, media.fps)                          # the clip's OWN rate
    _sub(f, "duration", media.frames)            # in the clip's own frames
    m = _sub(f, "media")
    _video_characteristics(_sub(m, "video"), media.fps, media.width, media.height)
    if media.audio_channels:
        a = _sub(m, "audio")
        sc = _sub(a, "samplecharacteristics")
        _sub(sc, "depth", 16)
        _sub(sc, "samplerate", media.sample_rate)
        _sub(a, "channelcount", min(media.audio_channels, 2))


def build_xmeml(cuts: list[Cut], sequence_fps="25", name: str = "Sequence",
                width: int | None = None, height: int | None = None) -> str:
    """Return the xmeml v4 document for `cuts` laid end to end."""
    if not cuts:
        raise ValueError("no cuts to export")
    seq = parse_rate(sequence_fps)
    if seq.denominator not in (1, 1001):
        raise ValueError(f"Premiere sequence rate must be a whole number or an NTSC rate "
                         f"(23.976, 29.97, 59.94), not {format_rate(seq)}")
    placements = plan_timeline(cuts, seq)
    width = width or cuts[0].media.width
    height = height or cuts[0].media.height
    n_audio = max(min(c.media.audio_channels, 2) for c in cuts)

    # ids: one file + master clip per media path, one clipitem per track slot
    file_ids: dict[str, str] = {}
    master_ids: dict[str, str] = {}
    for c in cuts:
        if c.media.path not in file_ids:
            n = len(file_ids) + 1
            file_ids[c.media.path] = f"file-{n}"
            master_ids[c.media.path] = f"masterclip-{n}"
    next_id = iter(range(1, 10 ** 9))
    video_ids = [f"clipitem-{next(next_id)}" for _ in cuts]
    audio_ids: list[dict[int, str]] = [
        {ch: f"clipitem-{next(next_id)}" for ch in range(1, min(c.media.audio_channels, 2) + 1)}
        for c in cuts]

    # 1-based position of each cut on each track, for <link><clipindex>
    links: list[list[tuple[str, str, int, int]]] = []
    count = {("video", 1): 0, ("audio", 1): 0, ("audio", 2): 0}
    for i in range(len(cuts)):
        count[("video", 1)] += 1
        group = [(video_ids[i], "video", 1, count[("video", 1)])]
        for ch, cid in audio_ids[i].items():
            count[("audio", ch)] += 1
            group.append((cid, "audio", ch, count[("audio", ch)]))
        links.append(group)

    written: set[str] = set()

    def clipitem(track, cid: str, i: int, audio_track: int | None):
        p = placements[i]
        media = p.cut.media
        ci = _sub(track, "clipitem", id=cid)
        _sub(ci, "masterclipid", master_ids[media.path])
        _sub(ci, "name", media.name)
        _sub(ci, "enabled", "TRUE")
        # Everything from here to <file> is in SEQUENCE frames.
        _sub(ci, "duration", p.duration)
        _rate(ci, seq)
        _sub(ci, "start", p.start)
        _sub(ci, "end", p.end)
        _sub(ci, "in", p.in_)
        _sub(ci, "out", p.out)
        _sub(ci, "pproTicksIn", ticks(p.in_, seq))
        _sub(ci, "pproTicksOut", ticks(p.out, seq))
        if audio_track is None:
            _sub(ci, "alphatype", "none")
            _sub(ci, "pixelaspectratio", "square")
            _sub(ci, "anamorphic", "FALSE")
        fid = file_ids[media.path]
        _file(ci, media, fid, full=fid not in written)
        written.add(fid)
        if audio_track is not None:
            st = _sub(ci, "sourcetrack")
            _sub(st, "mediatype", "audio")
            _sub(st, "trackindex", audio_track)
        if len(links[i]) > 1:
            for ref, mediatype, trackindex, clipindex in links[i]:
                ln = _sub(ci, "link")
                _sub(ln, "linkclipref", ref)
                _sub(ln, "mediatype", mediatype)
                _sub(ln, "trackindex", trackindex)
                _sub(ln, "clipindex", clipindex)
                if mediatype == "audio":
                    _sub(ln, "groupindex", 1)

    root = ET.Element("xmeml", version="4")
    sequence = _sub(root, "sequence", id="sequence-1")
    _sub(sequence, "name", name)
    _sub(sequence, "duration", placements[-1].end)
    _rate(sequence, seq)
    tc = _sub(sequence, "timecode")
    _rate(tc, seq)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media_el = _sub(sequence, "media")

    video = _sub(media_el, "video")
    _video_characteristics(_sub(video, "format"), seq, width, height)
    track = _sub(video, "track")
    for i in range(len(cuts)):
        clipitem(track, video_ids[i], i, None)
    _sub(track, "enabled", "TRUE")
    _sub(track, "locked", "FALSE")

    if n_audio:
        audio = _sub(media_el, "audio")
        _sub(audio, "numOutputChannels", 2)
        fmt = _sub(audio, "format")
        sc = _sub(fmt, "samplecharacteristics")
        _sub(sc, "depth", 16)
        _sub(sc, "samplerate", 48000)
        # A stereo clip comes in as an FCP7 pair: left channel on A1, right on A2.
        for ch in range(1, n_audio + 1):
            track = _sub(audio, "track")
            for i in range(len(cuts)):
                if ch in audio_ids[i]:
                    clipitem(track, audio_ids[i][ch], i, ch)
            _sub(track, "enabled", "TRUE")
            _sub(track, "locked", "FALSE")

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n"


def write_xmeml(xml_path, cuts: list[Cut], sequence_fps="25", name: str | None = None,
                **kwargs) -> list[Placement]:
    """Write the XML file and return where each cut landed."""
    xml_path = Path(xml_path)
    xml = build_xmeml(cuts, sequence_fps, name=name or xml_path.stem, **kwargs)
    xml_path.write_bytes(xml.encode("utf-8"))
    return plan_timeline(cuts, sequence_fps)


def export_cut_list(src, segments, xml_path, sequence_fps="25", ffmpeg: str = "ffmpeg",
                    audio: bool = True, name: str | None = None) -> list[Placement]:
    """cut_video.py: one source video, several (start, end) parts in seconds."""
    media = probe_media(src, ffmpeg)
    if not audio:
        media = replace(media, audio_channels=0)
    cuts = [Cut(media, a, b) for a, b in segments]
    return write_xmeml(xml_path, cuts, sequence_fps, name=name)


def describe(placements: list[Placement], sequence_fps) -> str:
    seq = parse_rate(sequence_fps)
    lines = []
    for p in placements:
        m = p.cut.media
        line = (f"  {m.name} ({format_rate(m.fps)} fps): "
                f"{p.cut.start:g}s-{p.cut.end:g}s -> frames {p.in_}-{p.out}, "
                f"timeline {p.start}-{p.end} @ {format_rate(seq)} fps")
        if p.clamped:
            line += "  [end moved back to the end of the clip]"
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# command line


def _find_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # fall back to ffmpeg on PATH
        return "ffmpeg"


_SEGMENTS_RE = re.compile(r"\s*[\d:.]+\s*-\s*[\d:.]+\s*(,\s*[\d:.]+\s*-\s*[\d:.]+\s*)*")


def parse_clip_arg(arg: str) -> tuple[str, list[tuple[float, float]] | None]:
    """'D:\\v\\a.mp4@0:30-0:45, 1:10-1:25' -> (path, [(30, 45), (70, 85)])"""
    path, sep, segs = arg.rpartition("@")
    if sep and _SEGMENTS_RE.fullmatch(segs):
        return path.strip().strip('"'), parse_segments(segs)
    return arg.strip().strip('"'), None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Make a Premiere Pro timeline (FCP7 XML) from video clips. "
                    "Clips with different frame rates can share one sequence.")
    ap.add_argument("clips", nargs="+",
                    help='video file (whole clip) or "file@0:30-0:45, 1:10-1:25" (parts of it)')
    ap.add_argument("-o", "--output", help="xml file (default: <first clip>_timeline.xml)")
    ap.add_argument("--fps", default="25",
                    help="sequence frame rate: 23.976, 24, 25, 29.97, 30, 50, 59.94, 60 (default 25)")
    ap.add_argument("--name", help="sequence name (default: xml file name)")
    ap.add_argument("--no-audio", action="store_true", help="video only")
    args = ap.parse_args()

    ffmpeg = _find_ffmpeg()
    probed: dict[Path, Media] = {}
    cuts: list[Cut] = []
    try:
        seq = parse_rate(args.fps)
        for arg in args.clips:
            path, segs = parse_clip_arg(arg)
            src = Path(path)
            if not src.is_file():
                print(f"[ERROR] not found: {src}")
                return 1
            if src not in probed:
                media = probe_media(src, ffmpeg)
                if args.no_audio:
                    media = replace(media, audio_channels=0)
                probed[src] = media
            media = probed[src]
            for a, b in segs or [(0.0, float(media.frames / media.fps))]:
                cuts.append(Cut(media, a, b))
        first = Path(parse_clip_arg(args.clips[0])[0])
        out = Path(args.output) if args.output else first.with_name(first.stem + "_timeline.xml")
        placements = write_xmeml(out, cuts, seq, name=args.name)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1
    print(describe(placements, seq))
    print(f"\nDone. Premiere XML saved to: {out}")
    print("In Premiere Pro: File > Import, then pick this .xml file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
