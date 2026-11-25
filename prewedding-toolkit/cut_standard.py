#!/usr/bin/env python3
"""The pre-wedding cut standard: find the usable part of a raw clip.

cut_video.py uses this automatically when no --start/--end/--segments are
given. The rules:

  1. Drop the first 0.5 s of every clip (the bump from pressing record).
  2. Start the cut when the motion / action starts.
  3. End the cut before camera shake or a pan-out (the camera swinging or
     zooming away at the end of the shot).
  4. Skip shaky and behind-the-scenes (BTS) clips: shaky most of the time,
     no steady part long enough to use, too dark (lens cap), too short, or
     "BTS" / "behind" in the file name.

How the clip is measured (OpenCV, offline):
  * ffmpeg decodes a small grey copy (long side 320 px, about 25 frames/s).
  * Camera motion between two frames: corner points are tracked
    (Lucas-Kanade) and one move (shift + rotation + zoom) is fitted with
    RANSAC, so people walking in the shot do not count as camera motion.
  * Shake = the fast back-and-forth part of the camera motion (the motion
    minus its 0.5 s average).
  * Pan-out = a fast camera move or zoom-out that lasts.
  * Action = picture that changed once the camera motion is removed (people
    moving), or a smooth camera move (slider / gimbal shot).

Speeds are in picture sizes per second: 0.35 means the picture moves 35 % of
its width (long side) in one second.

Check one clip without cutting anything:
  python cut_standard.py "D:\\Videos\\A001.mp4"
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

try:
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # fall back to ffmpeg on PATH
    FFMPEG = "ffmpeg"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".mxf", ".wmv", ".3gp"}
BTS_WORDS = ("bts", "behind", "behindthescenes")
ANALYSIS_SIZE = 320      # long side of the grey copy, in pixels
ANALYSIS_FPS = 25.0      # about this many frames per second are measured


@dataclass
class Standard:
    """The cut standard. Every number can be changed from cut_video.py."""
    head_trim: float = 0.5        # rule 1: seconds dropped at the start of every clip
    lead: float = 0.15            # start this much before the action is detected
    action_level: float = 0.004   # share of the picture that must change to count as action
    action_hold: float = 0.25     # ... for at least this many seconds
    camera_move: float = 0.03     # a smooth camera move at least this fast is action too
    shake_limit: float = 0.15     # shake score above this is shaky
    pan_limit: float = 0.35       # camera faster than this is a pan-out
    zoom_limit: float = 0.25      # zooming out faster than 25 % a second is a pan-out
    min_bad: float = 0.2          # shake / pan shorter than this is ignored
    end_margin: float = 0.1       # stop this much before the shake / pan-out
    min_keep: float = 1.5         # a usable part shorter than this is not kept
    max_shaky: float = 0.5        # shaky for more than this share of the clip -> skip
    dark_level: float = 16.0      # brightness 0-255; darker than this -> skip
    skip_bad: bool = True         # rule 4 on / off


@dataclass
class Analysis:
    """Per-frame measurements of one clip (all arrays have the same length)."""
    path: Path
    duration: float
    times: np.ndarray         # seconds
    shake: np.ndarray         # shake score
    pan: np.ndarray           # smoothed camera speed, picture sizes / s
    zoom: np.ndarray          # smoothed zoom rate per s (negative = zooming out)
    action: np.ndarray        # share of the picture that changed, camera motion removed
    brightness: np.ndarray    # 0-255

    @property
    def dt(self) -> float:
        return float(np.median(np.diff(self.times))) if len(self.times) > 1 else 1 / ANALYSIS_FPS


@dataclass
class Pick:
    """What the standard decided for one clip."""
    path: Path
    duration: float = 0.0
    start: float | None = None
    end: float | None = None
    skip: str | None = None       # reason, when the clip is skipped
    why_start: str = ""
    why_end: str = ""
    shaky_share: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def kept(self) -> bool:
        return self.skip is None and self.start is not None and self.end is not None

    @property
    def length(self) -> float:
        return (self.end - self.start) if self.kept else 0.0

    def summary(self) -> str:
        name = self.path.name
        if not self.kept:
            return f"SKIP  {name}: {self.skip}"
        return (f"KEEP  {name}: {self.start:.2f}s-{self.end:.2f}s ({self.length:.1f}s)"
                f"  start: {self.why_start}; end: {self.why_end}"
                + "".join(f"; {n}" for n in self.notes))


# --------------------------------------------------------------------------
# reading the clip


def probe_video(path: Path, ffmpeg: str = FFMPEG) -> tuple[float | None, float | None]:
    """(duration seconds, frames per second) from ffmpeg's stderr."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    fps = None
    for line in r.stderr.splitlines():
        if "Video:" in line:
            f = re.search(r"([\d.]+)\s*fps", line) or re.search(r"([\d.]+)\s*tbr", line)
            if f:
                fps = float(f[1])
                break
    return dur, fps


def _read_pgm_stream(stream):
    """Yield grey frames from a stream of binary PGM images."""
    while True:
        magic = stream.readline()
        if not magic:
            return
        if magic.strip() != b"P5":
            raise RuntimeError("unexpected data from ffmpeg")
        dims = stream.readline().split()
        while len(dims) < 2:  # width and height may be on separate lines
            dims += stream.readline().split()
        w, h = int(dims[0]), int(dims[1])
        stream.readline()  # max value (255)
        buf = stream.read(w * h)
        if len(buf) < w * h:
            return
        yield np.frombuffer(buf, np.uint8).reshape(h, w)


def read_small_frames(path: Path, fps: float | None, ffmpeg: str = FFMPEG):
    """Yield (time_seconds, grey frame) for about 25 frames per second.

    Frames are decimated by a whole number (50 fps -> every 2nd frame) so the
    time between measured frames stays even.
    """
    step = max(1, int((fps or ANALYSIS_FPS) // (ANALYSIS_FPS - 0.5)))
    rate = (fps or ANALYSIS_FPS) / step
    s = ANALYSIS_SIZE
    vf = []
    if step > 1:
        vf.append(f"select=not(mod(n\\,{step}))")
    vf.append(f"scale='if(gte(iw,ih),{s},-2)':'if(gte(iw,ih),-2,{s})':flags=area,format=gray")
    for sync in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-an", "-sn", "-dn",
               "-vf", ",".join(vf), *sync, "-f", "image2pipe", "-c:v", "pgm", "-"]
        # stderr goes to a file: a damaged clip can print many errors, and a
        # full pipe would stop ffmpeg
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log)
            n = 0
            try:
                for frame in _read_pgm_stream(proc.stdout):
                    yield n / rate, frame
                    n += 1
            finally:
                proc.stdout.close()
                proc.wait()
                log.seek(0)
                err = log.read().decode("utf-8", "replace")
        if n or "Unrecognized option" not in err:
            if n == 0:
                lines = [re.sub(r"^\[[^\]]*\]\s*", "", x).strip() for x in err.splitlines()]
                why = "; ".join(dict.fromkeys(x for x in lines if x))   # one line, no repeats
                raise RuntimeError(f"could not read the video ({why[:160] or 'no frames'})")
            return


# --------------------------------------------------------------------------
# measuring


_LK = dict(winSize=(21, 21), maxLevel=3,
           criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
_OPEN = np.ones((3, 3), np.uint8)


def _corners(img: np.ndarray) -> np.ndarray | None:
    """Corner points spread over a 4 x 3 grid, so a sharp couple in front of a
    soft (bokeh) background does not take all the points."""
    h, w = img.shape
    found = []
    for r in range(3):
        for c in range(4):
            y0, x0 = r * h // 3, c * w // 4
            cell = img[y0:(r + 1) * h // 3, x0:(c + 1) * w // 4]
            p = cv2.goodFeaturesToTrack(cell, maxCorners=25, qualityLevel=0.05, minDistance=6,
                                        blockSize=7)
            if p is not None:
                found.append(p + np.float32([x0, y0]))
    return np.concatenate(found) if found else None


def camera_motion(prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """2x3 matrix that moves `prev` onto `cur` (shift + rotation + zoom)."""
    pts = _corners(prev)
    if pts is not None and len(pts) >= 10:
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, **_LK)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, **_LK)
        ok = (st.reshape(-1) == 1) & (st2.reshape(-1) == 1) & \
            (np.linalg.norm((back - pts).reshape(-1, 2), axis=1) < 0.5)   # tracked both ways
        if ok.sum() >= 8:
            p0, p1 = pts[ok], nxt[ok]
            still = np.linalg.norm((p1 - p0).reshape(-1, 2), axis=1) < 0.3
            m, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC,
                                                 ransacReprojThreshold=1.0, maxIters=1000,
                                                 confidence=0.995)
            # Camera on a tripod while the couple moves: the background points
            # stay still. Prefer "no camera motion" when it explains as many points.
            if m is None or inl is None or still.sum() >= 0.8 * inl.sum():
                if still.sum() >= 6:
                    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            if m is not None and inl is not None and inl.sum() >= 6:
                return m
    # few corners (sky, plain wall, dark): shift only, by phase correlation
    (dx, dy), _ = cv2.phaseCorrelate(prev.astype(np.float32), cur.astype(np.float32))
    return np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]])


def changed_share(prev: np.ndarray, cur: np.ndarray, m: np.ndarray) -> float:
    """Share of the picture that changed once the camera motion `m` is removed."""
    h, w = cur.shape
    warped = cv2.warpAffine(prev, m, (w, h), flags=cv2.INTER_LINEAR)
    valid = cv2.warpAffine(np.full_like(prev, 255), m, (w, h), flags=cv2.INTER_NEAREST)
    valid = cv2.erode(valid, _OPEN, iterations=2) > 0
    if valid.sum() < 0.2 * w * h:
        return 0.0
    diff = cur.astype(np.int16) - warped.astype(np.int16)
    diff -= np.int16(np.median(diff[valid]))       # ignore exposure changes
    moved = ((np.abs(diff) > 20) & valid).astype(np.uint8)
    moved = cv2.morphologyEx(moved, cv2.MORPH_OPEN, _OPEN)   # drop single-pixel noise
    return float(moved.sum()) / float(valid.sum())


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    """Centred moving average over n samples (edges use the nearest values)."""
    n = max(1, int(n))
    if n == 1 or len(x) == 0:
        return x.astype(float)
    pad = n // 2
    xp = np.pad(x.astype(float), (pad, n - 1 - pad), mode="edge")
    return np.convolve(xp, np.ones(n) / n, mode="valid")


def _median(x: np.ndarray, n: int) -> np.ndarray:
    """Centred running median over n samples. Unlike an average it keeps a
    sudden start of a pan sharp, so the steady part before it is not lost."""
    n = max(1, int(n)) | 1     # odd
    if n == 1 or len(x) == 0:
        return x.astype(float)
    xp = np.pad(x.astype(float), (n // 2, n // 2), mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(xp, n), axis=1)


def _fill_gaps(x: np.ndarray) -> np.ndarray:
    """Replace NaN (repeated frames) by straight lines between their neighbours."""
    bad = np.isnan(x)
    if not bad.any():
        return x
    if bad.all():
        return np.zeros_like(x)
    idx = np.arange(len(x))
    x = x.copy()
    x[bad] = np.interp(idx[bad], idx[~bad], x[~bad])
    return x


def measure(path: Path, ffmpeg: str = FFMPEG) -> Analysis:
    """Measure camera shake, pans, zoom and action through the whole clip."""
    duration, fps = probe_video(path, ffmpeg)
    times, vx, vy, vr, vz, act, bright = [], [], [], [], [], [], []
    prev = None
    for t, frame in read_small_frames(path, fps, ffmpeg):
        cur = cv2.GaussianBlur(frame, (5, 5), 0)
        times.append(t)
        bright.append(float(frame.mean()))
        if prev is None or cv2.absdiff(prev, cur).max() == 0:
            # first frame, or a repeated frame (variable frame rate): no measurement
            for arr in (vx, vy, vr, vz, act):
                arr.append(np.nan)
        else:
            m = camera_motion(prev, cur)
            h, w = cur.shape
            cx, cy = w / 2.0, h / 2.0
            nx = m[0, 0] * cx + m[0, 1] * cy + m[0, 2]
            ny = m[1, 0] * cx + m[1, 1] * cy + m[1, 2]
            unit = float(max(w, h))
            vx.append((nx - cx) / unit)
            vy.append((ny - cy) / unit)
            vr.append(math.atan2(m[1, 0], m[0, 0]) * 0.5)
            vz.append(math.log(max(1e-6, math.hypot(m[0, 0], m[1, 0]))))
            act.append(changed_share(prev, cur, m))
        prev = cur
    if not times:
        raise RuntimeError(f"no frames in {path}")

    t = np.asarray(times)
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1 / ANALYSIS_FPS
    rate = 1.0 / dt
    vx, vy, vr, vz = (_fill_gaps(np.asarray(a, float)) * rate for a in (vx, vy, vr, vz))
    act = _fill_gaps(np.asarray(act, float))
    n_half = round(0.5 * rate)
    # steady part of the camera motion; what is left over is shake
    sx, sy, sr = _median(vx, n_half), _median(vy, n_half), _median(vr, n_half)
    jitter = (vx - sx) ** 2 + (vy - sy) ** 2 + (vr - sr) ** 2
    return Analysis(
        path=path,
        duration=float(duration) if duration else float(t[-1] + dt),
        times=t,
        shake=np.sqrt(_smooth(jitter, round(0.4 * rate))),
        pan=np.hypot(sx, sy),
        zoom=_median(vz, n_half),
        action=_smooth(act, round(0.2 * rate)),
        brightness=np.asarray(bright),
    )


# --------------------------------------------------------------------------
# deciding


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(first, last + 1), ...] for every run of True values."""
    m = np.concatenate([[False], mask.astype(bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _drop_short(mask: np.ndarray, n: int) -> np.ndarray:
    out = mask.copy()
    for a, b in _runs(mask):
        if b - a < n:
            out[a:b] = False
    return out


def is_bts_name(path: Path) -> bool:
    words = re.split(r"[^a-z0-9]+", path.stem.lower())
    return any(w in BTS_WORDS for w in words)


def choose(a: Analysis, std: Standard) -> Pick:
    """Apply the cut standard to a measured clip."""
    pick = Pick(a.path, duration=a.duration)
    t, dt = a.times, a.dt
    end_of_clip = a.duration

    def skip(reason: str) -> Pick:
        pick.skip = reason
        return pick

    if std.skip_bad and end_of_clip < std.head_trim + std.min_keep:
        return skip(f"too short ({end_of_clip:.1f}s)")
    if std.skip_bad and float(np.median(a.brightness)) < std.dark_level:
        return skip("too dark (lens cap or camera pointing down?)")

    after_head = t >= std.head_trim
    shaky = a.shake > std.shake_limit
    pan_out = (a.pan > std.pan_limit) | (a.zoom < -std.zoom_limit)
    dark = a.brightness < std.dark_level
    n_bad = max(1, round(std.min_bad / dt))
    shaky = _drop_short(shaky, n_bad)
    pan_out = _drop_short(pan_out, n_bad)
    bad = _drop_short(shaky | pan_out | dark, n_bad)
    pick.shaky_share = float(shaky[after_head].mean()) if after_head.any() else 0.0
    if std.skip_bad and pick.shaky_share > std.max_shaky:
        return skip(f"shaky ({pick.shaky_share:.0%} of the clip)")

    # action = people moving, or a smooth camera move (slider / gimbal), not shake
    smooth_move = (a.pan > std.camera_move) & (a.shake < std.shake_limit / 2)
    active = (a.action > std.action_level) | (smooth_move & ~bad)
    n_hold = max(1, round(std.action_hold / dt))

    # candidate steady parts: (start, end, action samples, i0, i1)
    candidates = []
    for i0, i1 in _runs(~bad & after_head):
        # a part that starts right after the first 0.5 s starts exactly at 0.5 s
        start = std.head_trim if i0 == 0 or not after_head[i0 - 1] else float(t[i0])
        if i1 < len(t):
            end = float(t[i1]) - std.end_margin
            why_end = ("pan-out" if pan_out[i1] else "shake" if shaky[i1] else "dark") \
                + f" at {t[i1]:.2f}s"
        else:
            end, why_end = end_of_clip, "end of the clip"
        end = min(end, end_of_clip)
        if end - start > 0:
            candidates.append((start, end, int(active[i0:i1].sum()), i0, i1, why_end))

    long_enough = [c for c in candidates if c[1] - c[0] >= std.min_keep]
    if not long_enough:
        if std.skip_bad:
            best = max((c[1] - c[0] for c in candidates), default=0.0)
            return skip(f"no steady part long enough (longest {best:.1f}s)")
        if not candidates:   # --no-skip: keep the clip anyway, minus the first 0.5 s
            if end_of_clip - std.head_trim < 0.2:
                return skip(f"too short ({end_of_clip:.1f}s)")
            pick.start, pick.end = std.head_trim, end_of_clip
            pick.why_start = f"first {std.head_trim:g}s dropped"
            pick.why_end = "end of the clip (shaky)"
            return pick
        long_enough = candidates

    start, end, _, i0, i1, why_end = max(long_enough, key=lambda c: (c[2], c[1] - c[0]))
    pick.why_end = why_end

    onset = None
    for i in range(i0, i1):
        if active[i:min(i + n_hold, i1)].all():
            onset = i
            break
    if onset is not None and onset > i0:
        pick.why_start = f"action starts at {t[onset]:.2f}s"
        start = max(start, float(t[onset]) - std.lead)
    elif onset is not None:
        pick.why_start = (f"already moving, first {std.head_trim:g}s dropped"
                          if start <= std.head_trim + 1e-6
                          else f"steady from {start:.2f}s, already moving")
    else:
        pick.why_start = (f"no action found, first {std.head_trim:g}s dropped"
                          if start <= std.head_trim + 1e-6
                          else f"no action found, steady from {start:.2f}s")
    if end - start < std.min_keep:   # action starts late: keep at least min_keep
        first = std.head_trim if i0 == 0 or not after_head[i0 - 1] else float(t[i0])
        start = max(first, end - std.min_keep)
        pick.notes.append(f"action starts late, cut starts earlier to keep {std.min_keep:g}s")
    pick.start, pick.end = round(start, 3), round(end, 3)
    return pick


def pick_cut(path: Path, std: Standard | None = None, ffmpeg: str = FFMPEG) -> Pick:
    """Decide the cut for one clip: name check, measure, then apply the rules."""
    std = std or Standard()
    path = Path(path)
    if std.skip_bad and is_bts_name(path):
        return Pick(path, skip="behind-the-scenes (BTS in the file name)")
    return choose(measure(path, ffmpeg), std)


def list_clips(folder: Path) -> list[Path]:
    """Video files directly inside `folder`, in name order (earlier cuts left out)."""
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXTS
                   and not p.stem.endswith("_cut") and not p.name.startswith(".")),
                  key=lambda p: p.name.lower())


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python cut_standard.py <video or folder> ...")
        return 1
    for arg in sys.argv[1:]:
        p = Path(arg.strip('"'))
        for clip in (list_clips(p) if p.is_dir() else [p]):
            print(pick_cut(clip).summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
