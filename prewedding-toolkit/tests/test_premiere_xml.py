"""Tests for premiere_xml.py: timecodes for clips at 25, 29.97, 50 and 59.94 fps.

Run from the prewedding-toolkit folder:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote

TOOLKIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLKIT))

import premiere_xml as px  # noqa: E402

R25 = Fraction(25)
R2997 = Fraction(30000, 1001)
R50 = Fraction(50)
R5994 = Fraction(60000, 1001)
RATES = {"25": R25, "29.97": R2997, "50": R50, "59.94": R5994}


def media(fps, seconds=60.0, name=None, channels=2):
    label = px.format_rate(fps).replace(".", "_")
    return px.Media(path=name or rf"D:\Prewedding\Teaser\A001_{label}fps.mp4", fps=fps,
                    duration=seconds, width=1920, height=1080, audio_channels=channels)


def rate_of(el) -> Fraction:
    r = el.find("rate")
    tb = int(r.findtext("timebase"))
    return Fraction(tb * 1000, 1001) if r.findtext("ntsc") == "TRUE" else Fraction(tb)


def parse(xml: str):
    """-> (sequence element, video clipitems, audio tracks, {file id: full <file>})"""
    root = ET.fromstring(xml.split("<!DOCTYPE xmeml>", 1)[1])
    seq = root.find("sequence")
    files = {f.get("id"): f for f in root.iter("file") if f.find("pathurl") is not None}
    video = seq.findall("media/video/track/clipitem")
    audio = seq.findall("media/audio/track")
    return seq, video, audio, files


def frames(ci, tag) -> int:
    return int(ci.findtext(tag))


class RateTests(unittest.TestCase):
    def test_parse_rate_names(self):
        self.assertEqual(px.parse_rate("25"), R25)
        self.assertEqual(px.parse_rate("29.97"), R2997)
        self.assertEqual(px.parse_rate("50"), R50)
        self.assertEqual(px.parse_rate("59.94"), R5994)
        self.assertEqual(px.parse_rate("23.976"), Fraction(24000, 1001))
        self.assertEqual(px.parse_rate("30000/1001"), R2997)
        self.assertEqual(px.parse_rate(29.97), R2997)
        self.assertEqual(px.parse_rate("29.92 fps"), R2997)  # phone average
        self.assertEqual(px.parse_rate("30"), Fraction(30))
        with self.assertRaises(ValueError):
            px.parse_rate("fast")

    def test_timebase_and_ntsc_flag(self):
        self.assertEqual(px.timebase(R25), (25, False))
        self.assertEqual(px.timebase(R2997), (30, True))
        self.assertEqual(px.timebase(R50), (50, False))
        self.assertEqual(px.timebase(R5994), (60, True))
        self.assertEqual(px.timebase(Fraction(24000, 1001)), (24, True))

    def test_ticks_are_exact(self):
        self.assertEqual(px.ticks(1, R25), 10_160_640_000)
        self.assertEqual(px.ticks(1, R50), 5_080_320_000)
        self.assertEqual(px.ticks(1, R2997), 8_475_667_200)
        self.assertEqual(px.ticks(1, R5994), 4_237_833_600)
        self.assertEqual(px.ticks(750, R25), 30 * px.TICKS_PER_SECOND)


class FfmpegInfoTests(unittest.TestCase):
    def info(self, fps_text, extra=""):
        return (
            "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4':\n"
            "  Duration: 00:01:02.50, start: 0.000000, bitrate: 50000 kb/s\n"
            "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), "
            f"yuv420p(progressive), 3840x2160 [SAR 1:1 DAR 16:9], 99000 kb/s, {fps_text}, "
            "90k tbn (default)\n"
            f"{extra}"
            "  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, "
            "fltp, 317 kb/s (default)\n")

    def test_reads_each_rate(self):
        for text, fps in [("25 fps, 25 tbr", R25), ("29.97 fps, 29.97 tbr", R2997),
                          ("50 fps, 50 tbr", R50), ("59.94 fps, 59.94 tbr", R5994)]:
            m = px.parse_ffmpeg_info(self.info(text), r"D:\v\a.mp4")
            self.assertEqual(m.fps, fps, text)
            self.assertEqual(m.duration, 62.5)
            self.assertEqual((m.width, m.height), (3840, 2160))
            self.assertEqual((m.audio_channels, m.sample_rate), (2, 48000))
        self.assertEqual(px.parse_ffmpeg_info(self.info("59.94 tbr"), "a.mp4").fps, R5994)

    def test_rotated_phone_clip_is_portrait(self):
        extra = "      Side data:\n        displaymatrix: rotation of -90.00 degrees\n"
        m = px.parse_ffmpeg_info(self.info("29.97 fps, 29.97 tbr", extra), "a.mp4")
        self.assertEqual((m.width, m.height), (2160, 3840))

    def test_mono_and_silent(self):
        mono = self.info("25 fps").replace("stereo", "mono")
        self.assertEqual(px.parse_ffmpeg_info(mono, "a.mp4").audio_channels, 1)
        silent = "\n".join(ln for ln in self.info("25 fps").splitlines() if "Audio:" not in ln)
        self.assertEqual(px.parse_ffmpeg_info(silent, "a.mp4").audio_channels, 0)


class FiftyOnTwentyFiveRegressionTest(unittest.TestCase):
    """The old bug: a 50 fps clip on a 25 fps sequence got in/out in 50 fps
    frames, so Premiere started it twice as far in and ran past the media end."""

    def test_in_out_are_sequence_frames(self):
        clip = media(R50, seconds=60)
        seq, video, _, files = parse(px.build_xmeml([px.Cut(clip, 30, 45)], "25"))
        ci = video[0]
        self.assertEqual(rate_of(seq), R25)
        self.assertEqual(rate_of(ci), R25)              # clipitem uses the sequence rate
        self.assertEqual(frames(ci, "in"), 750)         # 30 s * 25, not 30 s * 50 = 1500
        self.assertEqual(frames(ci, "out"), 1125)       # 45 s * 25, not 2250
        self.assertEqual(frames(ci, "start"), 0)
        self.assertEqual(frames(ci, "end"), 375)
        self.assertEqual(frames(ci, "duration"), 1500)  # 60 s of media in sequence frames
        self.assertEqual(int(ci.findtext("pproTicksIn")), 30 * px.TICKS_PER_SECOND)
        self.assertEqual(int(ci.findtext("pproTicksOut")), 45 * px.TICKS_PER_SECOND)
        f = files[ci.find("file").get("id")]
        self.assertEqual(rate_of(f), R50)               # the file keeps its own rate
        self.assertEqual(int(f.findtext("duration")), 3000)
        self.assertEqual(rate_of(f.find("media/video/samplecharacteristics")), R50)

    def test_last_seconds_stay_inside_the_media(self):
        clip = media(R50, seconds=60)
        _, video, _, _ = parse(px.build_xmeml([px.Cut(clip, 50, 60)], "25"))
        self.assertEqual(frames(video[0], "in"), 1250)
        self.assertEqual(frames(video[0], "out"), 1500)
        self.assertLessEqual(frames(video[0], "out"), frames(video[0], "duration"))


class MixedRateTimelineTests(unittest.TestCase):
    CUT = (10.0, 14.0)

    def build(self, seq_rate, clips=None):
        clips = clips or [media(r) for r in RATES.values()]
        cuts = [px.Cut(c, *self.CUT) for c in clips]
        return clips, cuts, parse(px.build_xmeml(cuts, seq_rate, name="Teaser"))

    def test_all_rates_on_a_25_fps_sequence(self):
        # clip rate -> (file timebase, ntsc, file duration in own frames, clipitem duration @25)
        expected = {
            R25: (25, "FALSE", 1500, 1500),
            R2997: (30, "TRUE", 1798, 1499),   # 60 s = 1798.2 frames -> 1798 whole frames
            R50: (50, "FALSE", 3000, 1500),
            R5994: (60, "TRUE", 3596, 1499),
        }
        clips, _, (seq, video, _, files) = self.build("25")
        self.assertEqual(len(video), 4)
        for n, (clip, ci) in enumerate(zip(clips, video)):
            with self.subTest(fps=px.format_rate(clip.fps)):
                tb, ntsc, file_dur, clip_dur = expected[clip.fps]
                self.assertEqual(rate_of(ci), R25)
                self.assertEqual((frames(ci, "in"), frames(ci, "out")), (250, 350))
                self.assertEqual((frames(ci, "start"), frames(ci, "end")), (n * 100, n * 100 + 100))
                self.assertEqual(frames(ci, "duration"), clip_dur)
                f = files[ci.find("file").get("id")]
                self.assertEqual(f.findtext("rate/timebase"), str(tb))
                self.assertEqual(f.findtext("rate/ntsc"), ntsc)
                self.assertEqual(int(f.findtext("duration")), file_dur)
                self.assertEqual(f.findtext("name"), clip.name)
        self.assertEqual(int(seq.findtext("duration")), 400)

    def test_all_rates_on_a_29_97_fps_sequence(self):
        clips, _, (seq, video, _, files) = self.build("29.97")
        self.assertEqual(seq.findtext("rate/timebase"), "30")
        self.assertEqual(seq.findtext("rate/ntsc"), "TRUE")
        for n, (clip, ci) in enumerate(zip(clips, video)):
            with self.subTest(fps=px.format_rate(clip.fps)):
                self.assertEqual(rate_of(ci), R2997)
                # 10 s = 299.7 frames -> 300; 14 s = 419.58 -> 420
                self.assertEqual((frames(ci, "in"), frames(ci, "out")), (300, 420))
                self.assertEqual((frames(ci, "start"), frames(ci, "end")), (n * 120, n * 120 + 120))
                self.assertEqual(rate_of(files[ci.find("file").get("id")]), clip.fps)

    def test_all_rates_on_50_and_59_94_sequences(self):
        for seq_rate, fin, fout in [("50", 500, 700), ("59.94", 599, 839)]:
            clips, _, (_, video, _, files) = self.build(seq_rate)
            for clip, ci in zip(clips, video):
                with self.subTest(seq=seq_rate, fps=px.format_rate(clip.fps)):
                    self.assertEqual(rate_of(ci), RATES[seq_rate])
                    self.assertEqual((frames(ci, "in"), frames(ci, "out")), (fin, fout))
                    self.assertEqual(rate_of(files[ci.find("file").get("id")]), clip.fps)

    def test_every_combination_round_trips_to_the_requested_seconds(self):
        for seq_name, seq_rate in RATES.items():
            for clip_rate in RATES.values():
                cut = (12.34, 17.5)
                clip = media(clip_rate)
                _, video, audio, files = parse(px.build_xmeml([px.Cut(clip, *cut)], seq_name))
                for ci in video + [t.find("clipitem") for t in audio]:
                    with self.subTest(seq=seq_name, clip=px.format_rate(clip_rate), item=ci.get("id")):
                        r = rate_of(ci)
                        self.assertEqual(r, seq_rate)
                        half = Fraction(1, 2) / r
                        fin, fout = frames(ci, "in"), frames(ci, "out")
                        self.assertLessEqual(abs(fin / r - Fraction(str(cut[0]))), half)
                        self.assertLessEqual(abs(fout / r - Fraction(str(cut[1]))), half)
                        self.assertEqual(int(ci.findtext("pproTicksIn")),
                                         round(fin * px.TICKS_PER_SECOND / r))
                        self.assertEqual(int(ci.findtext("pproTicksOut")),
                                         round(fout * px.TICKS_PER_SECOND / r))
                        self.assertEqual(frames(ci, "end") - frames(ci, "start"), fout - fin)
                        # the out point is inside the media, measured in seconds
                        f = files[ci.find("file").get("id")]
                        media_secs = int(f.findtext("duration")) / rate_of(f)
                        self.assertLessEqual(fout / r, media_secs)

    def test_out_is_pulled_back_to_the_media_end(self):
        short = media(R5994, seconds=10.0)  # 599 whole frames = 9.993 s
        cuts = [px.Cut(short, 8, 12)]
        placements = px.plan_timeline(cuts, "25")
        self.assertTrue(placements[0].clamped)
        _, video, _, _ = parse(px.build_xmeml(cuts, "25"))
        ci = video[0]
        self.assertEqual(frames(ci, "duration"), 249)  # floor(599 / 59.94 * 25)
        self.assertEqual(frames(ci, "out"), 249)
        self.assertEqual(frames(ci, "in"), 200)

    def test_cut_after_the_end_is_an_error(self):
        with self.assertRaises(ValueError):
            px.build_xmeml([px.Cut(media(R50, seconds=10), 11, 12)], "25")
        with self.assertRaises(ValueError):
            px.build_xmeml([px.Cut(media(R50), 5, 5)], "25")

    def test_odd_sequence_rate_is_refused(self):
        with self.assertRaises(ValueError):
            px.build_xmeml([px.Cut(media(R25), 0, 1)], "12.5")


class StructureTests(unittest.TestCase):
    def test_header(self):
        xml = px.build_xmeml([px.Cut(media(R25), 0, 1)], "25")
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'))
        root = ET.fromstring(xml.split("<!DOCTYPE xmeml>", 1)[1])
        self.assertEqual((root.tag, root.get("version")), ("xmeml", "4"))

    def test_file_is_written_once_then_referenced(self):
        a, b = media(R50), media(R2997)
        cuts = [px.Cut(a, 1, 2), px.Cut(b, 3, 4), px.Cut(a, 5, 6)]
        _, video, audio, files = parse(px.build_xmeml(cuts, "25"))
        self.assertEqual(sorted(files), ["file-1", "file-2"])
        self.assertEqual([ci.find("file").get("id") for ci in video], ["file-1", "file-2", "file-1"])
        self.assertIsNotNone(video[0].find("file/pathurl"))
        self.assertIsNone(video[2].find("file/pathurl"))
        for t in audio:  # audio items only reference files already written above
            for ci in t.findall("clipitem"):
                self.assertIsNone(ci.find("file/pathurl"))
        self.assertEqual([ci.findtext("masterclipid") for ci in video],
                         ["masterclip-1", "masterclip-2", "masterclip-1"])

    def test_stereo_and_mono_audio_are_linked_to_the_video(self):
        stereo, mono = media(R50), media(R25, channels=1)
        silent = media(R5994, channels=0)
        cuts = [px.Cut(stereo, 30, 32), px.Cut(mono, 10, 11), px.Cut(silent, 1, 2),
                px.Cut(stereo, 40, 41)]
        _, video, audio, _ = parse(px.build_xmeml(cuts, "25"))
        self.assertEqual(len(audio), 2)
        a1, a2 = (t.findall("clipitem") for t in audio)
        self.assertEqual(len(a1), 3)  # stereo, mono, stereo
        self.assertEqual(len(a2), 2)  # right channel of the stereo cuts
        for ci in a1:
            self.assertEqual(ci.findtext("sourcetrack/trackindex"), "1")
        for ci in a2:
            self.assertEqual(ci.findtext("sourcetrack/trackindex"), "2")
        # audio sits exactly under its video, with the same sequence-rate frames
        for v, a in [(video[0], a1[0]), (video[0], a2[0]), (video[1], a1[1]),
                     (video[3], a1[2]), (video[3], a2[1])]:
            for tag in ("start", "end", "in", "out", "duration"):
                self.assertEqual(v.findtext(tag), a.findtext(tag), tag)
            self.assertEqual(rate_of(a), R25)
        links = [(ln.findtext("linkclipref"), ln.findtext("mediatype"),
                  ln.findtext("trackindex"), ln.findtext("clipindex"))
                 for ln in video[3].findall("link")]
        self.assertEqual(links, [(video[3].get("id"), "video", "1", "4"),
                                 (a1[2].get("id"), "audio", "1", "3"),
                                 (a2[1].get("id"), "audio", "2", "2")])
        self.assertEqual(video[2].findall("link"), [])  # silent clip: nothing to link

    def test_path_urls(self):
        self.assertEqual(px.path_to_url(r"D:\Videos\pw 01.mp4"),
                         "file://localhost/D%3A/Videos/pw%2001.mp4")
        khmer = r"E:\cluade\2026\មង្គលការ\A001.MP4"
        url = px.path_to_url(khmer)
        self.assertTrue(url.startswith("file://localhost/E%3A/cluade/2026/"))
        self.assertEqual(unquote(url[len("file://localhost/"):]).replace("/", "\\"), khmer)
        self.assertEqual(px.path_to_url("/Users/me/Movies/a.mov"),
                         "file://localhost/Users/me/Movies/a.mov")
        self.assertEqual(px.path_to_url(r"\\nas\footage\day 1\a.mp4"),
                         "file://nas/footage/day%201/a.mp4")
        self.assertEqual(media(R25, name=khmer).name, "A001.MP4")

    def test_clip_argument(self):
        self.assertEqual(px.parse_clip_arg(r"D:\v\a.mp4@0:30-0:45, 1:10-1:25"),
                         (r"D:\v\a.mp4", [(30.0, 45.0), (70.0, 85.0)]))
        self.assertEqual(px.parse_clip_arg(r'"D:\v\a.mp4"'), (r"D:\v\a.mp4", None))
        self.assertEqual(px.parse_clip_arg("me@studio.mp4"), ("me@studio.mp4", None))


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


@unittest.skipUnless(_ffmpeg(), "ffmpeg not available")
class EndToEndTests(unittest.TestCase):
    """Real clips made with ffmpeg, then cut_video.py / premiere_xml.py."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.clips = {}
        for name, rate in [("25", "25"), ("29.97", "30000/1001"), ("50", "50"),
                           ("59.94", "60000/1001")]:
            out = cls.dir / f"clip {name}fps.mp4"
            subprocess.run(
                [_ffmpeg(), "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={rate}:duration=6",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
                 "-ac", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", str(out)], check=True)
            cls.clips[name] = out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                           encoding="utf-8", env=env, cwd=str(TOOLKIT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_probe_reads_each_rate(self):
        for name, path in self.clips.items():
            m = px.probe_media(path, _ffmpeg())
            self.assertEqual(m.fps, RATES[name], name)
            self.assertEqual((m.width, m.height, m.audio_channels), (320, 180, 2))

    def test_cut_video_xml_only_for_a_50_fps_clip(self):
        src = self.clips["50"]
        out = self.run_tool(TOOLKIT / "cut_video.py", src, "--segments", "1-2, 2.5-3.5",
                            "--xml-only", "--xml-fps", "25")
        xml_path = src.with_name(src.stem + "_cut.xml")
        self.assertIn(str(xml_path), out)
        self.assertFalse(src.with_name(src.stem + "_cut.mp4").exists())  # no render
        seq, video, audio, files = parse(xml_path.read_text(encoding="utf-8"))
        # 2.5 s * 25 = 62.5 -> 63 (half up), 3.5 s * 25 = 87.5 -> 88
        self.assertEqual([(frames(c, "in"), frames(c, "out")) for c in video], [(25, 50), (63, 88)])
        self.assertEqual([(frames(c, "start"), frames(c, "end")) for c in video], [(0, 25), (25, 50)])
        self.assertEqual(rate_of(files["file-1"]), R50)
        self.assertEqual(int(files["file-1"].findtext("duration")), 300)
        self.assertEqual(unquote(files["file-1"].findtext("pathurl")),
                         "file://localhost" + src.resolve().as_posix())
        self.assertEqual(len(audio), 2)

    def test_mixed_rate_timeline_from_the_command_line(self):
        xml_path = self.dir / "teaser.xml"
        self.run_tool(TOOLKIT / "premiere_xml.py", "--fps", "25", "-o", xml_path,
                      f"{self.clips['25']}@1-2", f"{self.clips['29.97']}@1-2",
                      f"{self.clips['50']}@1-2", f"{self.clips['59.94']}@1-2, 4-9")
        seq, video, _, files = parse(xml_path.read_text(encoding="utf-8"))
        self.assertEqual(len(video), 5)
        for ci in video[:4]:
            self.assertEqual((frames(ci, "in"), frames(ci, "out")), (25, 50))
        # 4-9 s asked, clip is 6 s long: out stops at the media end
        last = video[4]
        self.assertEqual(frames(last, "in"), 100)
        self.assertLessEqual(frames(last, "out"), frames(last, "duration"))
        self.assertEqual([files[c.find("file").get("id")].findtext("rate/timebase")
                          for c in video[:4]], ["25", "30", "50", "60"])
        self.assertEqual(int(seq.findtext("duration")), frames(last, "end"))


if __name__ == "__main__":
    unittest.main()
