"""Tests for the pre-wedding cut standard (cut_standard.py + cut_video.py).

Every test video is made here from scratch: a textured "location", a bright
"couple" that can stand still or walk, and a camera that can stay steady,
shake, pan away or zoom out. Nothing is downloaded.

Run from the prewedding-toolkit folder:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

TOOLKIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLKIT))

import cut_standard as cs  # noqa: E402
import cut_video  # noqa: E402

FFMPEG = cs.FFMPEG
W, H, FPS = 640, 360, 25


# --------------------------------------------------------------------------
# synthetic clips


def _world(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    world = cv2.GaussianBlur(rng.integers(40, 200, (1400, 2200)).astype(np.uint8), (0, 0), 2.5)
    for _ in range(400):
        x, y = int(rng.integers(0, 2150)), int(rng.integers(0, 1350))
        c = int(rng.integers(20, 235))
        if rng.random() < 0.5:
            cv2.rectangle(world, (x, y), (x + int(rng.integers(8, 60)), y + int(rng.integers(8, 60))),
                          c, -1)
        else:
            cv2.circle(world, (x, y), int(rng.integers(4, 30)), c, -1)
    return world


def steady(t):
    return 0.0, 0.0, 1.0, 0.0


def wobble(amp_px, t, seed=1):
    """Hand-held shake: a few mixed frequencies, as a camera operator would make."""
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 2 * math.pi, 6)
    x = amp_px * (0.6 * math.sin(2 * math.pi * 2.7 * t + ph[0])
                  + 0.4 * math.sin(2 * math.pi * 4.9 * t + ph[1])
                  + 0.3 * math.sin(2 * math.pi * 7.3 * t + ph[2]))
    y = amp_px * (0.6 * math.sin(2 * math.pi * 3.3 * t + ph[3])
                  + 0.4 * math.sin(2 * math.pi * 5.6 * t + ph[4]))
    rot = 0.3 * amp_px / 10 * math.sin(2 * math.pi * 3.9 * t + ph[5])
    return x, y, rot


def make_clip(path: Path, seconds: float, camera=steady, subject=None, seed: int = 0,
              brightness: float = 1.0, audio: bool = True, fps: int = FPS,
              bokeh: bool = False) -> Path:
    """camera(t) -> (dx px, dy px, zoom, roll degrees); subject(t) -> (x, y) or None.
    bokeh=True: soft, out-of-focus background behind a sharp couple."""
    world = _world(seed)
    if bokeh:
        world = cv2.GaussianBlur(world, (0, 0), 12)
    couple = np.random.default_rng(seed + 7).integers(0, 2, (12, 23)).astype(np.uint8) * 150 + 90
    couple = cv2.resize(couple, (92, 120), interpolation=cv2.INTER_NEAREST)  # clothes pattern
    rng = np.random.default_rng(seed + 100)
    wc = np.array([world.shape[1] / 2, world.shape[0] / 2])
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for k in range(int(round(seconds * fps))):
        t = k / fps
        scene = world
        if subject is not None and subject(t) is not None:
            sx, sy = subject(t)
            scene = world.copy()
            x0, y0 = int(wc[0] + sx), int(wc[1] + sy)
            scene[y0:y0 + 120, x0:x0 + 92] = couple
        dx, dy, zoom, roll = camera(t)
        m = cv2.getRotationMatrix2D((W / 2, H / 2), roll, 1.0 / zoom)
        m[0, 2] += wc[0] + dx - W / 2
        m[1, 2] += wc[1] + dy - H / 2
        view = cv2.warpAffine(scene, m, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        view = view.astype(np.float32) * brightness + rng.normal(0, 2.0, view.shape)
        proc.stdin.write(np.clip(view, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0, "ffmpeg could not write the test clip"
    return path


def walk_from(t0, speed=110.0, bob=0.0):
    """Couple stands still, then walks to the right from t0 (px per second),
    bobbing up and down `bob` px with each step."""
    def pos(t):
        if t < t0:
            return -120.0, -40.0
        return -120 + speed * (t - t0), -40 + bob * math.sin(2 * math.pi * 2.0 * (t - t0))
    return pos


def length_of(path: Path) -> float:
    dur, _ = cs.probe_video(path)
    return dur


# --------------------------------------------------------------------------


class StandardOnClips(unittest.TestCase):
    """The standard applied to synthetic clips (measure + choose)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cutstd_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def clip(self, name, *args, **kwargs) -> Path:
        return make_clip(self.tmp / name, *args, **kwargs)

    def test_starts_on_action_and_ends_before_pan_out(self):
        def cam(t):  # steady, then swings away fast at 4.5 s
            x = 0.0 if t < 4.5 else 600.0 * (t - 4.5)
            return x, 0.0, 1.0, 0.0
        p = cs.pick_cut(self.clip("action_pan.mp4", 6.5, camera=cam, subject=walk_from(2.0)))
        self.assertTrue(p.kept, p.summary())
        self.assertGreaterEqual(p.start, 1.7, p.summary())
        self.assertLessEqual(p.start, 2.2, p.summary())
        self.assertGreaterEqual(p.end, 3.9, p.summary())
        self.assertLessEqual(p.end, 4.55, p.summary())
        self.assertIn("pan-out", p.why_end)
        self.assertIn("action", p.why_start)

    def test_ends_before_shake(self):
        def cam(t):
            if t < 3.5:
                return 0.0, 0.0, 1.0, 0.0
            x, y, r = wobble(14.0, t)
            return x, y, 1.0, r
        p = cs.pick_cut(self.clip("shake_end.mp4", 6.0, camera=cam, subject=walk_from(0.0)))
        self.assertTrue(p.kept, p.summary())
        self.assertAlmostEqual(p.start, 0.5, delta=0.05, msg=p.summary())
        self.assertGreaterEqual(p.end, 2.8, p.summary())
        self.assertLessEqual(p.end, 3.55, p.summary())
        self.assertIn("shake", p.why_end)

    def test_record_bump_and_first_half_second_dropped(self):
        def cam(t):  # the bump from pressing record, gone after 0.35 s
            if t < 0.35:
                x, y, r = wobble(25.0, t, seed=3)
                return x, y, 1.0, r
            return 0.0, 0.0, 1.0, 0.0
        p = cs.pick_cut(self.clip("bump.mp4", 5.0, camera=cam, subject=walk_from(0.0)))
        self.assertTrue(p.kept, p.summary())
        self.assertGreaterEqual(p.start, 0.5, p.summary())
        self.assertLessEqual(p.start, 1.0, p.summary())
        self.assertAlmostEqual(p.end, 5.0, delta=0.1, msg=p.summary())

    def test_couple_running_on_soft_background_is_not_shake_or_pan(self):
        # tripod, sharp couple running and bobbing across an out-of-focus
        # background: most corner points are on the couple, but the camera is still
        p = cs.pick_cut(self.clip("bokeh_run.mp4", 5.0, bokeh=True,
                                  subject=walk_from(1.5, speed=250.0, bob=6.0)))
        self.assertTrue(p.kept, p.summary())
        self.assertGreaterEqual(p.start, 1.2, p.summary())
        self.assertLessEqual(p.start, 1.7, p.summary())
        self.assertAlmostEqual(p.end, 5.0, delta=0.1, msg=p.summary())

    def test_shaky_clip_on_soft_background_is_skipped(self):
        def cam(t):
            x, y, r = wobble(14.0, t, seed=4)
            return x, y, 1.0, r
        p = cs.pick_cut(self.clip("bokeh_shaky.mp4", 4.0, camera=cam, bokeh=True,
                                  subject=walk_from(1.0, bob=3.0)))
        self.assertFalse(p.kept)
        self.assertIn("shaky", p.skip)

    def test_shaky_clip_is_skipped(self):
        def cam(t):
            x, y, r = wobble(14.0, t, seed=5)
            return x, y, 1.0, r
        p = cs.pick_cut(self.clip("handheld.mp4", 5.0, camera=cam, subject=walk_from(1.0)))
        self.assertFalse(p.kept)
        self.assertIn("shaky", p.skip)

    def test_gentle_handheld_is_kept(self):
        def cam(t):  # small, slow sway that a good operator or a gimbal leaves
            return 2.0 * math.sin(2 * math.pi * 1.2 * t), 1.5 * math.sin(2 * math.pi * 0.9 * t), 1.0, 0.0
        p = cs.pick_cut(self.clip("gimbal.mp4", 5.0, camera=cam, subject=walk_from(1.5)))
        self.assertTrue(p.kept, p.summary())
        self.assertGreaterEqual(p.start, 1.2, p.summary())
        self.assertLessEqual(p.start, 1.7, p.summary())

    def test_slow_cinematic_pan_is_kept(self):
        def cam(t):  # 40 px/s on a 640 px picture: a slow reveal, not a pan-out
            return 40.0 * t, 0.0, 1.0, 0.0
        p = cs.pick_cut(self.clip("slow_pan.mp4", 5.0, camera=cam))
        self.assertTrue(p.kept, p.summary())
        self.assertAlmostEqual(p.start, 0.5, delta=0.05, msg=p.summary())
        self.assertAlmostEqual(p.end, 5.0, delta=0.1, msg=p.summary())

    def test_ends_before_zoom_out(self):
        def cam(t):  # zooms out 40 % a second from 3.5 s
            return 0.0, 0.0, 1.0 if t < 3.5 else math.exp(-0.4 * (t - 3.5)), 0.0
        p = cs.pick_cut(self.clip("zoom_out.mp4", 5.5, camera=cam, subject=walk_from(1.0)))
        self.assertTrue(p.kept, p.summary())
        self.assertLessEqual(p.end, 3.55, p.summary())
        self.assertGreaterEqual(p.end, 2.8, p.summary())
        self.assertIn("pan-out", p.why_end)

    def test_still_detail_shot_is_kept_from_half_second(self):
        p = cs.pick_cut(self.clip("rings.mp4", 4.0))
        self.assertTrue(p.kept, p.summary())
        self.assertAlmostEqual(p.start, 0.5, delta=0.01)
        self.assertAlmostEqual(p.end, 4.0, delta=0.1)
        self.assertIn("no action", p.why_start)

    def test_too_short_clip_is_skipped(self):
        p = cs.pick_cut(self.clip("oops.mp4", 1.2))
        self.assertFalse(p.kept)
        self.assertIn("too short", p.skip)

    def test_dark_clip_is_skipped(self):
        p = cs.pick_cut(self.clip("lens_cap.mp4", 3.0, brightness=0.05))
        self.assertFalse(p.kept)
        self.assertIn("dark", p.skip)

    def test_bts_file_name_is_skipped(self):
        path = self.tmp / "BTS_0007.mp4"
        p = cs.pick_cut(path)   # skipped by name, never opened
        self.assertFalse(p.kept)
        self.assertIn("behind-the-scenes", p.skip)
        self.assertFalse(cs.is_bts_name(Path("A001_bride_walk.mp4")))
        self.assertFalse(cs.is_bts_name(Path("ambush.mp4")))
        self.assertTrue(cs.is_bts_name(Path("c0012 behind scenes.MP4")))

    def test_no_skip_keeps_shaky_clip(self):
        def cam(t):
            x, y, r = wobble(14.0, t, seed=6)
            return x, y, 1.0, r
        path = self.clip("handheld_keep.mp4", 4.0, camera=cam)
        p = cs.pick_cut(path, cs.Standard(skip_bad=False))
        self.assertTrue(p.kept, p.summary())
        self.assertGreaterEqual(p.start, 0.5)

    def test_50fps_clip_measured_in_seconds(self):
        def cam(t):
            x = 0.0 if t < 3.0 else 600.0 * (t - 3.0)
            return x, 0.0, 1.0, 0.0
        p = cs.pick_cut(self.clip("a50.mp4", 4.5, camera=cam, subject=walk_from(1.0), fps=50))
        self.assertTrue(p.kept, p.summary())
        self.assertGreaterEqual(p.start, 0.7, p.summary())
        self.assertLessEqual(p.start, 1.2, p.summary())
        self.assertLessEqual(p.end, 3.05, p.summary())
        self.assertGreaterEqual(p.end, 2.4, p.summary())


class ChooseRules(unittest.TestCase):
    """The decision rules on hand-made measurements (no video needed)."""

    def analysis(self, seconds=6.0, shake=None, pan=None, action=None, zoom=None):
        t = np.arange(0, seconds, 0.04)
        z = np.zeros_like(t)
        return cs.Analysis(Path("x.mp4"), seconds, t,
                           shake=z + 0.02 if shake is None else shake(t),
                           pan=z if pan is None else pan(t),
                           zoom=z if zoom is None else zoom(t),
                           action=z if action is None else action(t),
                           brightness=z + 120)

    def test_short_shake_blip_is_ignored(self):
        a = self.analysis(shake=lambda t: np.where(np.abs(t - 3.0) < 0.05, 0.5, 0.02))
        p = cs.choose(a, cs.Standard())
        self.assertTrue(p.kept)
        self.assertAlmostEqual(p.end, 6.0)

    def test_longest_steady_part_with_action_wins(self):
        # shaky 2.0-2.6 s; action only in the second part
        a = self.analysis(shake=lambda t: np.where((t > 2.0) & (t < 2.6), 0.5, 0.02),
                          action=lambda t: np.where(t > 3.5, 0.02, 0.0))
        p = cs.choose(a, cs.Standard())
        self.assertTrue(p.kept)
        self.assertAlmostEqual(p.start, 3.5 - 0.15, delta=0.05)
        self.assertAlmostEqual(p.end, 6.0)

    def test_late_action_keeps_minimum_length(self):
        a = self.analysis(seconds=4.0, action=lambda t: np.where(t > 3.6, 0.02, 0.0))
        p = cs.choose(a, cs.Standard())
        self.assertTrue(p.kept)
        self.assertAlmostEqual(p.end - p.start, 1.5, delta=0.01)

    def test_head_trim_setting(self):
        a = self.analysis(action=lambda t: t * 0 + 0.02)
        self.assertAlmostEqual(cs.choose(a, cs.Standard(head_trim=1.0)).start, 1.0, delta=0.04)


class CutVideoCommand(unittest.TestCase):
    """cut_video.py end to end: same entry point, standard on by default."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cutcli_"))

        def pan_cam(t):
            return (0.0 if t < 4.0 else 600.0 * (t - 4.0)), 0.0, 1.0, 0.0

        def shaky_cam(t):
            x, y, r = wobble(14.0, t, seed=9)
            return x, y, 1.0, r

        cls.good = make_clip(cls.tmp / "A001.mp4", 5.5, camera=pan_cam, subject=walk_from(1.5))
        cls.folder = cls.tmp / "shoot"
        cls.folder.mkdir()
        make_clip(cls.folder / "A001.mp4", 5.5, camera=pan_cam, subject=walk_from(1.5))
        make_clip(cls.folder / "A002.mp4", 4.0, camera=shaky_cam, subject=walk_from(1.0))
        make_clip(cls.folder / "A003_BTS.mp4", 2.5)
        make_clip(cls.folder / "A004.mp4", 4.0, subject=walk_from(0.0), fps=50)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["cut_video.py", *map(str, argv)]), redirect_stdout(out):
            code = cut_video.main()
        return code, out.getvalue()

    def test_default_cut_follows_the_standard(self):
        dst = self.tmp / "auto_cut.mp4"
        code, out = self.run_cli(self.good, "-o", dst)
        self.assertEqual(code, 0, out)
        pick = cs.pick_cut(self.good)
        self.assertAlmostEqual(length_of(dst), pick.length, delta=0.15)
        self.assertGreaterEqual(pick.start, 1.2)
        self.assertLessEqual(pick.end, 4.05)
        self.assertIn("KEEP", out)

    def test_shaky_single_clip_is_skipped_without_output(self):
        dst = self.tmp / "shaky_cut.mp4"
        code, out = self.run_cli(self.folder / "A002.mp4", "-o", dst)
        self.assertEqual(code, cut_video.SKIPPED, out)
        self.assertFalse(dst.exists())
        self.assertIn("SKIP", out)

    def test_manual_times_are_used_exactly(self):
        dst = self.tmp / "manual_cut.mp4"
        code, out = self.run_cli(self.good, "--start", "0", "--end", "2", "-o", dst)
        self.assertEqual(code, 0, out)
        self.assertAlmostEqual(length_of(dst), 2.0, delta=0.1)

    def test_no_auto_cuts_whole_clip(self):
        dst = self.tmp / "whole_cut.mp4"
        code, out = self.run_cli(self.good, "--no-auto", "-o", dst)
        self.assertEqual(code, 0, out)
        self.assertAlmostEqual(length_of(dst), 5.5, delta=0.1)

    def test_dry_run_makes_no_file(self):
        dst = self.tmp / "dry_cut.mp4"
        code, out = self.run_cli(self.good, "--dry-run", "-o", dst)
        self.assertEqual(code, 0, out)
        self.assertFalse(dst.exists())
        self.assertIn("KEEP", out)

    def test_folder_cuts_good_clips_and_skips_bad_ones(self):
        out_dir = self.tmp / "folder_out"
        code, out = self.run_cli(self.folder, "-o", out_dir)
        self.assertEqual(code, 0, out)
        made = sorted(p.name for p in out_dir.glob("*.mp4"))
        self.assertEqual(made, ["A001_cut.mp4", "A004_cut.mp4"], out)
        report = (out_dir / "cut_report.txt").read_text(encoding="utf-8")
        self.assertRegex(report, r"KEEP\s+A001\.mp4")
        self.assertRegex(report, r"SKIP\s+A002\.mp4.*shaky")
        self.assertRegex(report, r"SKIP\s+A003_BTS\.mp4.*behind-the-scenes")
        self.assertRegex(report, r"KEEP\s+A004\.mp4")
        self.assertIn("2 kept, 2 skipped", out)

    def test_folder_premiere_timeline_has_only_kept_clips(self):
        out_dir = self.tmp / "xml_out"
        code, out = self.run_cli(self.folder, "-o", out_dir, "--xml-only")
        self.assertEqual(code, 0, out)
        self.assertEqual(list(out_dir.glob("*.mp4")), [])   # --xml-only renders nothing
        xml_path = out_dir / "shoot_timeline.xml"
        self.assertTrue(xml_path.is_file(), out)
        root = ET.parse(xml_path).getroot()
        items = root.findall("./sequence/media/video/track/clipitem")
        self.assertEqual([i.findtext("name") for i in items], ["A001.mp4", "A004.mp4"])
        # A004 is 50 fps on a 25 fps sequence: in point is the standard's 0.5 s = 12-13 frames
        a004_in = int(items[1].findtext("in"))
        self.assertIn(a004_in, (12, 13))


if __name__ == "__main__":
    unittest.main()
