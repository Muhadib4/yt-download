import unittest
import json
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class CapabilityTests(unittest.TestCase):
    def test_video_capabilities_match_source_size_and_real_encoder_set(self):
        media = {
            "durationSeconds": 12.0,
            "video": {"width": 1280, "height": 720, "codec": "h264"},
            "audio": {"codec": "aac"},
        }
        with (
            mock.patch.object(server, "VIDEO_ENCODER", "libx264"),
            mock.patch.object(server, "AAC_ENCODER", "aac"),
            mock.patch.object(server, "MP3_ENCODER", "libmp3lame"),
        ):
            formats, reasons = server._build_capabilities(media)

        by_id = {item["id"]: item for item in formats}
        self.assertIn("mp4", by_id)
        self.assertIn("mp3", by_id)
        self.assertEqual(by_id["mp4"]["qualities"][0]["id"], "source")
        self.assertEqual(by_id["mp4"]["qualities"][0]["label"], "Original (1280 × 720)")
        self.assertIn("height-480", {item["id"] for item in by_id["mp4"]["qualities"]})
        self.assertNotIn("height-1080", {item["id"] for item in by_id["mp4"]["qualities"]})
        self.assertEqual(by_id["mp3"]["qualities"][0]["id"], "bitrate-320000")
        self.assertEqual(reasons, {})

    def test_unavailable_formats_are_not_advertised(self):
        media = {
            "durationSeconds": 4.0,
            "video": {"width": 640, "height": 360, "codec": "h264"},
            "audio": None,
        }
        with (
            mock.patch.object(server, "VIDEO_ENCODER", None),
            mock.patch.object(server, "AAC_ENCODER", None),
            mock.patch.object(server, "MP3_ENCODER", None),
        ):
            formats, reasons = server._build_capabilities(media)
        self.assertEqual(formats, [])
        self.assertIn("H.264", reasons["mp4"])
        self.assertIn("trek audio", reasons["mp3"])

    def test_audio_only_file_only_advertises_mp3_when_supported(self):
        media = {"durationSeconds": 7.0, "video": None, "audio": {"codec": "pcm_s16le"}}
        with (
            mock.patch.object(server, "VIDEO_ENCODER", "libx264"),
            mock.patch.object(server, "AAC_ENCODER", "aac"),
            mock.patch.object(server, "MP3_ENCODER", "libmp3lame"),
        ):
            formats, reasons = server._build_capabilities(media)
        self.assertEqual([item["id"] for item in formats], ["mp3"])
        self.assertIn("trek video", reasons["mp4"])


class CommandAndFilenameTests(unittest.TestCase):
    def test_mp4_command_uses_selected_resolution_and_trim(self):
        source = {
            "path": Path("input.mov"),
            "media": {"audio": {"codec": "aac"}},
        }
        job = {
            "format": "mp4",
            "quality": "height-360",
            "range": {"startSeconds": 5.0, "endSeconds": 9.0},
        }
        with (
            mock.patch.object(server, "FFMPEG", "ffmpeg"),
            mock.patch.object(server, "VIDEO_ENCODER", "libx264"),
            mock.patch.object(server, "AAC_ENCODER", "aac"),
        ):
            command = server._make_ffmpeg_command(source, job, Path("result.mp4"))
        self.assertIn("-ss", command)
        self.assertIn("5", command)
        self.assertIn("-t", command)
        self.assertIn("4", command)
        self.assertIn("scale=-2:360:force_original_aspect_ratio=decrease", command)
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertEqual(command[-1], "result.mp4")

    def test_mp3_command_uses_selected_bitrate_and_audio_only_mapping(self):
        source = {"path": Path("input.mp4"), "media": {"audio": {"codec": "aac"}}}
        job = {"format": "mp3", "quality": "bitrate-192000", "range": None}
        with mock.patch.object(server, "FFMPEG", "ffmpeg"), mock.patch.object(server, "MP3_ENCODER", "libmp3lame"):
            command = server._make_ffmpeg_command(source, job, Path("result.mp3"))
        self.assertEqual(command[command.index("-map") + 1], "0:a:0")
        self.assertEqual(command[command.index("-b:a") + 1], "192k")
        self.assertIn("-vn", command)
        self.assertEqual(command[-1], "result.mp3")

    def test_output_names_never_include_user_path_components(self):
        self.assertEqual(server._safe_stem("../../My trip (final).mov"), "My_trip_final")

    def test_remote_url_rejects_private_hosts_by_default(self):
        with mock.patch.object(server, "ALLOW_PRIVATE_URL_SOURCES", False):
            with self.assertRaises(ValueError):
                server._validate_remote_url("http://127.0.0.1/example.mp4")


class StaticServingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LocalHandler)
        cls.httpd.daemon_threads = True
        cls.thread = Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_page_and_both_scripts_are_served_by_local_processor(self):
        with urlopen(f"{self.base_url}/") as response:
            html = response.read().decode("utf-8")
        self.assertIn('src="js/validation.js"', html)
        self.assertIn('src="js/app.js"', html)

        with urlopen(f"{self.base_url}/js/validation.js") as response:
            validation_script = response.read().decode("utf-8")
            self.assertIn("javascript", response.headers.get_content_type())
        self.assertIn("validateTimeRange", validation_script)

        with urlopen(f"{self.base_url}/js/app.js") as response:
            app_script = response.read().decode("utf-8")
        self.assertIn("YtDownloadValidation", app_script)

    def test_file_inspection_requires_permission_confirmation(self):
        request = Request(
            f"{self.base_url}/api/analyze",
            data=b"not a media file",
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as response:
            urlopen(request)
        self.assertEqual(response.exception.code, 403)

    def test_url_inspection_requires_permission_confirmation(self):
        request = Request(
            f"{self.base_url}/api/analyze-url",
            data=json.dumps({"url": "https://media.example.com/file.mp4"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as response:
            urlopen(request)
        self.assertEqual(response.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
