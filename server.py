"""Local-only media processor for files the user is permitted to process.

The loopback server fetches only direct, permitted public media URLs and local
uploads. It keeps sources and results in a temporary directory, then removes them on exit.
"""

from __future__ import annotations

import argparse
import atexit
import ipaddress
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import socket
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
VIDEO_HEIGHTS = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)
AUDIO_BITRATES = (320000, 256000, 192000, 128000)
ALLOWED_EXTENSIONS = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".m4v", ".mka",
    ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".oga", ".ogg",
    ".opus", ".wav", ".webm", ".wma", ".wmv", ".avi",
}
SOURCE_TTL_SECONDS = 14 * 60 * 60
MAX_JOB_SECONDS = 12 * 60 * 60
MAX_MEDIA_DURATION_SECONDS = 12 * 60 * 60
MAX_JSON_BYTES = 64 * 1024
CHUNK_SIZE = 1024 * 1024
MAX_REMOTE_REDIRECTS = 4
REMOTE_TIMEOUT_SECONDS = 25
ALLOW_PRIVATE_URL_SOURCES = False
REMOTE_MIME_EXTENSIONS = {
    "audio/aac": ".aac", "audio/flac": ".flac", "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/opus": ".opus",
    "audio/wav": ".wav", "audio/webm": ".webm", "video/mp4": ".mp4",
    "video/ogg": ".ogv", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-msvideo": ".avi", "video/x-ms-wmv": ".wmv",
}
BLOCKED_REMOTE_HOSTS = {"youtu.be", "youtube.com", "youtube-nocookie.com"}

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
YT_DLP = shutil.which("yt-dlp")
YT_DLP_COMMAND = [YT_DLP] if YT_DLP else [sys.executable, "-m", "yt_dlp"]


def _available_encoders() -> set[str]:
    if not FFMPEG:
        return set()
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    encoders = set()
    for line in result.stdout.splitlines():
        columns = line.strip().split()
        if len(columns) >= 2 and len(columns[0]) == 6 and columns[0][0] in "VAS":
            encoders.add(columns[1])
    return encoders


ENCODERS = _available_encoders()
VIDEO_ENCODER = "libx264" if "libx264" in ENCODERS else None
AAC_ENCODER = "aac" if "aac" in ENCODERS else None
MP3_ENCODER = "libmp3lame" if "libmp3lame" in ENCODERS else None
MAX_UPLOAD_BYTES = max(
    1,
    int(os.environ.get("YT_DOWNLOAD_MAX_UPLOAD_MB", "2048")),
) * 1024 * 1024

STORE_DIR = Path(tempfile.mkdtemp(prefix="yt-download-"))
SOURCES_DIR = STORE_DIR / "sources"
OUTPUTS_DIR = STORE_DIR / "outputs"
SOURCES_DIR.mkdir()
OUTPUTS_DIR.mkdir()
atexit.register(shutil.rmtree, STORE_DIR, ignore_errors=True)

STATE_LOCK = threading.RLock()
SOURCES: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
WORKER_THREADS: set[threading.Thread] = set()


def _clean_display_name(filename: str) -> str:
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(char for char in basename if char >= " " and char != "\x7f")
    return basename[:160] or "media"


def _safe_stem(filename: str) -> str:
    stem = Path(_clean_display_name(filename)).stem
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return (value[:72] or "media")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_remote_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Masukkan URL http:// atau https:// yang menunjuk ke file media langsung.")
    if parsed.username or parsed.password:
        raise ValueError("URL media tidak boleh berisi informasi login.")
    host = parsed.hostname.lower().rstrip(".")
    if host in BLOCKED_REMOTE_HOSTS or any(host.endswith(f".{item}") for item in BLOCKED_REMOTE_HOSTS):
        raise ValueError("URL platform video tidak didukung. Gunakan URL file media langsung dari sumber yang mengizinkan unduhan.")
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("Host URL media tidak dapat ditemukan.") from exc
    if not addresses:
        raise ValueError("Host URL media tidak memiliki alamat yang dapat diakses.")
    if not ALLOW_PRIVATE_URL_SOURCES:
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise ValueError("URL media harus menggunakan host publik, bukan alamat jaringan lokal.")
    return parsed.geturl()


def _is_youtube_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return host == "youtu.be" or host.endswith(".youtu.be") or host == "youtube.com" or host.endswith(".youtube.com")


def _youtube_command_available() -> bool:
    try:
        result = subprocess.run([*YT_DLP_COMMAND, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _analyze_youtube_source(source_url: str) -> dict:
    parsed = urlsplit(source_url)
    if not _is_youtube_url(source_url):
        raise ValueError("Gunakan URL video YouTube yang valid.")
    if parsed.scheme != "https":
        raise ValueError("URL YouTube harus menggunakan HTTPS.")
    if not _youtube_command_available():
        raise RuntimeError("yt-dlp belum terpasang. Jalankan: python -m pip install -r requirements.txt, lalu mulai ulang server.")
    try:
        result = subprocess.run(
            [*YT_DLP_COMMAND, "--ignore-config", "--no-warnings", "--js-runtimes", "node", "--no-playlist", "--skip-download", "--dump-single-json", source_url],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Pemeriksaan metadata YouTube melewati batas waktu.") from exc
    except OSError as exc:
        raise RuntimeError("yt-dlp tidak dapat dijalankan. Periksa instalasi Python dan yt-dlp.") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").lower()
        if "sign in" in detail or "private video" in detail or "members-only" in detail:
            raise ValueError("Video ini memerlukan akses atau autentikasi yang tidak didukung. Gunakan video publik yang boleh Anda unduh.")
        raise ValueError("YouTube tidak menyediakan metadata video ini. Periksa URL, ketersediaan video, dan versi yt-dlp.")
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("yt-dlp mengembalikan metadata yang tidak terbaca.") from exc
    if metadata.get("is_live") is True and not metadata.get("duration"):
        raise ValueError("Live stream yang masih berlangsung belum didukung. Gunakan arsip live stream yang sudah tersedia.")
    try:
        duration = float(metadata.get("duration"))
    except (TypeError, ValueError):
        duration = None
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None

    available_heights = set()
    max_width = 0
    has_video = False
    has_audio = False
    for item in metadata.get("formats", []):
        if not isinstance(item, dict):
            continue
        video_codec, audio_codec = item.get("vcodec"), item.get("acodec")
        if video_codec and video_codec != "none":
            has_video = True
            try:
                height, width = int(item.get("height") or 0), int(item.get("width") or 0)
            except (TypeError, ValueError):
                height, width = 0, 0
            if height > 0:
                available_heights.add(height)
            max_width = max(max_width, width)
        if audio_codec and audio_codec != "none":
            has_audio = True
    max_height = max(available_heights, default=0)
    title = "".join(char for char in str(metadata.get("title") or "YouTube video") if char >= " " and char != "\x7f")[:200].strip() or "YouTube video"
    media = {
        "title": title,
        "durationSeconds": duration,
        "video": {"width": max_width or 0, "height": max_height, "codec": "youtube"} if has_video and max_height else None,
        "audio": {"codec": "youtube"} if has_audio else None,
    }
    formats, reasons = [], {}
    if media["video"]:
        if not VIDEO_ENCODER:
            reasons["mp4"] = "FFmpeg tidak memiliki encoder H.264 (libx264)."
        else:
            qualities = [{"id": f"height-{target}", "label": f"{target}p"} for target in VIDEO_HEIGHTS if target in available_heights]
            if qualities:
                formats.append({"id": "mp4", "label": "MP4 video", "qualities": qualities})
    else:
        reasons["mp4"] = "Video tidak memiliki format video yang didukung."
    if media["audio"]:
        if not MP3_ENCODER:
            reasons["mp3"] = "FFmpeg tidak memiliki encoder MP3 (libmp3lame)."
        else:
            formats.append({"id": "mp3", "label": "MP3 audio", "qualities": [
                {"id": f"bitrate-{bitrate}", "label": f"{bitrate // 1000} kbps"} for bitrate in AUDIO_BITRATES
            ]})
    else:
        reasons["mp3"] = "Video tidak memiliki trek audio yang didukung."

    source_id = uuid.uuid4().hex
    with STATE_LOCK:
        SOURCES[source_id] = {
            "path": None, "fileName": title, "safeStem": _safe_stem(title), "sizeBytes": 0,
            "createdAt": time.time(), "media": media, "thumbnailPath": None, "formats": formats,
            "reasons": reasons, "kind": "youtube", "url": source_url,
            "availableHeights": sorted(available_heights),
        }
    return {
        "sourceId": source_id, "fileName": title, "title": title, "sizeBytes": 0,
        "durationSeconds": duration, "video": media["video"], "audio": media["audio"],
        "thumbnailUrl": None, "formats": formats,
        "unavailableReasons": reasons, "sourceType": "youtube",
    }


def _format_timestamp(value: float) -> str:
    whole = max(0, int(value))
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _run_youtube_job(job_id: str, source: dict, job: dict) -> None:
    output_path = job["outputPath"]
    work_dir = SOURCES_DIR / f"job-{job_id}"
    process = None
    watchdog = None
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        command = [*YT_DLP_COMMAND, "--ignore-config", "--no-warnings", "--js-runtimes", "node", "--no-playlist", "--newline", "--progress"]
        time_range = job["range"]
        if time_range:
            # Avoid --force-keyframes-at-cuts: it forces a costly video re-encode for
            # trimmed sections, which can leave 4K jobs appearing stuck at 0%.
            command.extend(["--download-sections", f"*{_format_timestamp(time_range['startSeconds'])}-{_format_timestamp(time_range['endSeconds'])}"])
        if job["format"] == "mp4":
            height = int(job["quality"].removeprefix("height-"))
            # Prefer MP4 video/audio streams when available; otherwise allow the best
            # resolution-limited streams and remux to MP4 without re-encoding video.
            format_selector = (
                f"bv[height<={height}][ext=mp4]+ba[ext=m4a]/"
                f"b[height<={height}][ext=mp4]/"
                f"bv[height<={height}]+ba/b[height<={height}]"
            )
            command.extend(["--format", format_selector, "--merge-output-format", "mp4", "--remux-video", "mp4"])
        else:
            bitrate = int(job["quality"].removeprefix("bitrate-")) // 1000
            command.extend(["--format", "bestaudio/best", "--extract-audio", "--audio-format", "mp3",
                            "--audio-quality", f"{bitrate}K"])
        command.extend(["--output", str(work_dir / "download.%(ext)s"), source["url"]])

        output_tail = deque(maxlen=40)
        with STATE_LOCK:
            current = JOBS.get(job_id)
            if current:
                current.update(status="processing", progress=0, stage="Menghubungkan ke YouTube…", startedAt=time.time())
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with STATE_LOCK:
            ACTIVE_PROCESSES[job_id] = process
        watchdog = threading.Timer(MAX_JOB_SECONDS, process.kill)
        watchdog.daemon = True
        watchdog.start()
        assert process.stdout
        for raw_line in process.stdout:
            line = raw_line.strip()
            # yt-dlp's human-readable progress is reliable with --newline and also
            # covers download-section jobs; the old custom template missed some cases.
            progress_match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
            if progress_match:
                try:
                    progress = max(0, min(99, int(float(progress_match.group(1)))))
                except ValueError:
                    progress = None
                if progress is not None:
                    with STATE_LOCK:
                        current = JOBS.get(job_id)
                        if current and current["status"] == "processing":
                            current["progress"] = progress
                            current["stage"] = "Mengunduh media dari YouTube…"
                continue

            if line:
                output_tail.append(line[-500:])
                print(f"[yt-dlp {job_id[:8]}] {line[-500:]}", flush=True)
                lowered = line.lower()
                if "downloading webpage" in lowered or "extracting url" in lowered:
                    stage = "Mengambil informasi video dari YouTube…"
                elif "downloading m3u8" in lowered or "downloading api json" in lowered:
                    stage = "Memeriksa stream video…"
                elif "downloading 1 format" in lowered or "requested formats" in lowered or "has already been downloaded" in lowered:
                    stage = "Menyiapkan stream video/audio…"
                elif "destination:" in lowered or "[download]" in lowered or "fragment" in lowered or " at " in lowered and "eta" in lowered:
                    stage = "Mengunduh video/audio…"
                elif "merging formats" in lowered or "merger" in lowered:
                    stage = "Menggabungkan video dan audio…"
                elif any(word in lowered for word in ("post-process", "converting", "recode", "fixing")):
                    stage = f"Mengonversi hasil ke {job['format'].upper()}…"
                elif "error:" in lowered or "http error" in lowered or "unable to download" in lowered:
                    stage = "YouTube melaporkan kendala; memeriksa hasil proses…"
                else:
                    # Keep the last meaningful stage instead of replacing it with a
                    # generic message for every informational yt-dlp line.
                    with STATE_LOCK:
                        current = JOBS.get(job_id)
                        if current and current["status"] == "processing" and not current.get("stage"):
                            current["stage"] = "Menyiapkan unduhan dari YouTube…"
                    continue
                with STATE_LOCK:
                    current = JOBS.get(job_id)
                    if current and current["status"] == "processing":
                        current["stage"] = stage
        return_code = process.wait()
        candidates = [path for path in work_dir.glob("download.*") if path.is_file() and path.stat().st_size > 0]
        expected_suffix = ".mp4" if job["format"] == "mp4" else ".mp3"
        candidate = next((path for path in candidates if path.suffix.lower() == expected_suffix), None)
        if return_code != 0 or candidate is None:
            output_path.unlink(missing_ok=True)
            detail = "\n".join(output_tail).lower()
            error = "Unduhan gagal. Periksa koneksi internet, ketersediaan video publik, dan versi yt-dlp."
            if "requested format is not available" in detail:
                error = "Format stream yang dipilih tidak tersedia dari YouTube. Coba resolusi yang lebih rendah."
            elif "sign in to confirm you're not a bot" in detail or "confirm you're not a bot" in detail:
                error = "YouTube meminta verifikasi tambahan untuk video ini. Coba lagi nanti atau gunakan video publik lain."
            elif "private video" in detail or "members-only" in detail or "sign in to watch" in detail:
                error = "Video ini memerlukan akses khusus dan tidak didukung oleh processor lokal."
            elif "http error 403" in detail or "http error 429" in detail:
                error = "YouTube menolak permintaan unduhan sementara. Tunggu sebentar lalu coba lagi."
            with STATE_LOCK:
                current = JOBS.get(job_id)
                if current:
                    current.update(status="failed", progress=None, stage="Unduhan gagal", error=error, finishedAt=time.time())
            return
        if candidate.resolve() != output_path.resolve():
            shutil.move(str(candidate), str(output_path))
        with STATE_LOCK:
            current = JOBS.get(job_id)
            if current:
                current.update(status="complete", progress=100, stage="Selesai", error=None, finishedAt=time.time(),
                               outputBytes=output_path.stat().st_size)
    except (OSError, subprocess.SubprocessError, ValueError):
        output_path.unlink(missing_ok=True)
        with STATE_LOCK:
            current = JOBS.get(job_id)
            if current:
                current.update(status="failed", progress=None, stage="Proses gagal",
                               error="yt-dlp gagal dijalankan. Pastikan yt-dlp dan FFmpeg terpasang dan tersedia.",
                               finishedAt=time.time())
    finally:
        if watchdog:
            watchdog.cancel()
        if process:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout:
                process.stdout.close()
        shutil.rmtree(work_dir, ignore_errors=True)
        with STATE_LOCK:
            ACTIVE_PROCESSES.pop(job_id, None)
            WORKER_THREADS.discard(threading.current_thread())


def _remote_filename(url: str, content_type: str, disposition: str) -> tuple[str, str]:
    candidates = [unquote(match.group(1).strip().strip('"')) for match in re.finditer(r"filename\*?=(?:UTF-8''|)([^;]+)", disposition, re.I)]
    candidates.append(Path(urlsplit(url).path).name)
    extension = next((Path(item).suffix.lower() for item in candidates if Path(item).suffix.lower() in ALLOWED_EXTENSIONS), "")
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    is_media_type = normalized_type.startswith(("audio/", "video/")) or normalized_type == "application/ogg"
    if not extension and is_media_type:
        extension = REMOTE_MIME_EXTENSIONS.get(normalized_type, "")
    if not extension or (not is_media_type and normalized_type not in {"", "application/octet-stream"}):
        raise ValueError("URL harus menunjuk langsung ke file audio atau video yang dapat diunduh.")
    filename = next((_clean_display_name(item) for item in candidates if Path(item).suffix.lower() == extension), f"media{extension}")
    return filename, extension


def _download_remote_media(source_id: str, source_url: str) -> tuple[Path, str, int]:
    current_url = _validate_remote_url(source_url)
    opener = build_opener(_NoRedirect())
    response = None
    for _ in range(MAX_REMOTE_REDIRECTS + 1):
        request = Request(current_url, headers={"User-Agent": "yt-download-local/1.0", "Accept": "audio/*,video/*;q=0.9,*/*;q=0.1"})
        try:
            response = opener.open(request, timeout=REMOTE_TIMEOUT_SECONDS)
            break
        except HTTPError as exc:
            location = exc.headers.get("Location") if exc.code in {301, 302, 303, 307, 308} else None
            if not location:
                raise ValueError(f"Sumber media merespons dengan HTTP {exc.code}.") from exc
            current_url = _validate_remote_url(urljoin(current_url, location))
        except URLError as exc:
            raise ValueError("Sumber media tidak dapat dihubungi.") from exc
    if response is None:
        raise ValueError("Terlalu banyak pengalihan URL media.")

    with response:
        content_length = response.headers.get("Content-Length", "")
        try:
            if content_length and int(content_length) > MAX_UPLOAD_BYTES:
                raise ValueError(f"Ukuran file sumber melebihi batas {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        except ValueError as exc:
            if "melebihi" in str(exc):
                raise
        filename, extension = _remote_filename(response.geturl(), response.headers.get("Content-Type", ""), response.headers.get("Content-Disposition", ""))
        source_path = SOURCES_DIR / f"{source_id}{extension}"
        total = 0
        try:
            with source_path.open("wb") as output:
                while chunk := response.read(CHUNK_SIZE):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ValueError(f"Ukuran file sumber melebihi batas {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                    output.write(chunk)
        except Exception:
            source_path.unlink(missing_ok=True)
            raise
    if total < 1:
        source_path.unlink(missing_ok=True)
        raise ValueError("File media dari URL kosong.")
    return source_path, filename, total


def _probe_media(path: Path, fallback_title: str | None = None) -> dict:
    if not FFPROBE:
        raise RuntimeError("ffprobe was not found. Install FFmpeg and add it to PATH.")
    try:
        result = subprocess.run(
            [
                FFPROBE,
                "-v", "error",
                "-protocol_whitelist", "file,pipe",
                "-show_streams",
                "-show_format",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Media inspection took too long.") from exc
    except OSError as exc:
        raise RuntimeError("ffprobe could not be started.") from exc
    if result.returncode != 0:
        raise ValueError("The selected file is not a readable audio or video file.")
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("The selected file returned unreadable media metadata.") from exc

    format_metadata = metadata.get("format", {})
    format_tags = format_metadata.get("tags", {})
    media_title = ""
    if isinstance(format_tags, dict):
        raw_title = next((value for key, value in format_tags.items() if str(key).lower() == "title"), "")
        media_title = "".join(char for char in str(raw_title) if char >= " " and char != "\x7f")[:200].strip()
    media_title = media_title or fallback_title or path.name
    streams = metadata.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video_stream and not audio_stream:
        raise ValueError("The selected file does not contain an audio or video stream.")

    duration_values = [format_metadata.get("duration")]
    duration_values.extend(item.get("duration") for item in streams)
    duration = None
    for raw_value in duration_values:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            duration = value
            break

    video = None
    if video_stream:
        try:
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
        except (TypeError, ValueError):
            width, height = 0, 0
        if width < 1 or height < 1:
            video_stream = None
        else:
            video = {
                "width": width,
                "height": height,
                "codec": video_stream.get("codec_name", "unknown"),
            }

    audio = None
    if audio_stream:
        audio = {"codec": audio_stream.get("codec_name", "unknown")}

    if not video and not audio:
        raise ValueError("The selected file does not contain a supported audio or video stream.")
    return {"title": media_title, "durationSeconds": duration, "video": video, "audio": audio}


def _create_thumbnail(source_id: str, media_path: Path) -> Path | None:
    if not FFMPEG:
        return None
    thumbnail_path = SOURCES_DIR / f"{source_id}.jpg"
    command = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", "0", "-protocol_whitelist", "file,pipe", "-i", str(media_path),
        "-frames:v", "1", "-vf", "scale=640:-2:force_original_aspect_ratio=decrease",
        "-q:v", "4", str(thumbnail_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        thumbnail_path.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not thumbnail_path.is_file() or thumbnail_path.stat().st_size == 0:
        thumbnail_path.unlink(missing_ok=True)
        return None
    return thumbnail_path


def _build_capabilities(media: dict) -> tuple[list[dict], dict[str, str]]:
    formats: list[dict] = []
    reasons: dict[str, str] = {}
    video = media.get("video")
    audio = media.get("audio")

    if video:
        if not VIDEO_ENCODER:
            reasons["mp4"] = "FFmpeg tidak memiliki encoder H.264 (libx264)."
        elif audio and not AAC_ENCODER:
            reasons["mp4"] = "FFmpeg tidak memiliki encoder AAC untuk trek audio file ini."
        else:
            height = video["height"]
            qualities = [{"id": "source", "label": f"Original ({video['width']} × {height})"}]
            qualities.extend(
                {"id": f"height-{target}", "label": f"{target}p"}
                for target in VIDEO_HEIGHTS
                if target <= height
            )
            formats.append({"id": "mp4", "label": "MP4 video", "qualities": qualities})
    else:
        reasons["mp4"] = "File ini tidak memiliki trek video."

    if audio:
        if not MP3_ENCODER:
            reasons["mp3"] = "FFmpeg tidak memiliki encoder MP3 (libmp3lame)."
        else:
            qualities = [
                {"id": f"bitrate-{bitrate}", "label": f"{bitrate // 1000} kbps"}
                for bitrate in AUDIO_BITRATES
            ]
            formats.append({"id": "mp3", "label": "MP3 audio", "qualities": qualities})
    else:
        reasons["mp3"] = "File ini tidak memiliki trek audio."

    return formats, reasons


def _expire_old_items() -> None:
    cutoff = time.time() - SOURCE_TTL_SECONDS
    paths_to_remove: list[Path] = []
    with STATE_LOCK:
        active_sources = {
            job["sourceId"]
            for job in JOBS.values()
            if job["status"] in {"queued", "processing"}
        }
        for source_id, source in list(SOURCES.items()):
            if source["createdAt"] < cutoff and source_id not in active_sources:
                if source.get("path"):
                    paths_to_remove.append(source["path"])
                if source.get("thumbnailPath"):
                    paths_to_remove.append(source["thumbnailPath"])
                del SOURCES[source_id]
        for job_id, job in list(JOBS.items()):
            if job["createdAt"] < cutoff and job["status"] not in {"queued", "processing"}:
                if job.get("outputPath"):
                    paths_to_remove.append(job["outputPath"])
                del JOBS[job_id]
    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _format_seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _make_ffmpeg_command(source: dict, job: dict, output_path: Path) -> list[str]:
    format_id = job["format"]
    quality_id = job["quality"]
    time_range = job["range"]
    command = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-stats_period", "0.25"]

    if time_range:
        command.extend(["-ss", _format_seconds(time_range["startSeconds"])])
    command.extend(["-protocol_whitelist", "file,pipe", "-i", str(source["path"])])
    if time_range:
        command.extend(["-t", _format_seconds(time_range["endSeconds"] - time_range["startSeconds"])])

    if format_id == "mp4":
        command.extend(["-map", "0:v:0", "-map", "0:a:0?", "-c:v", VIDEO_ENCODER, "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"])
        if quality_id != "source":
            height = int(quality_id.removeprefix("height-"))
            command.extend(["-vf", f"scale=-2:{height}:force_original_aspect_ratio=decrease"])
        if source["media"]["audio"]:
            command.extend(["-c:a", AAC_ENCODER, "-b:a", "192k"])
        else:
            command.append("-an")
        command.extend(["-movflags", "+faststart"])
    else:
        bitrate = int(quality_id.removeprefix("bitrate-"))
        command.extend(["-map", "0:a:0", "-vn", "-c:a", MP3_ENCODER, "-b:a", f"{bitrate // 1000}k"])

    command.extend(["-progress", "pipe:1", str(output_path)])
    return command


def _run_job(job_id: str) -> None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        source = SOURCES.get(job["sourceId"])
        if not source:
            job.update(status="failed", error="The temporary source file has expired. Inspect the file again.")
            WORKER_THREADS.discard(threading.current_thread())
            return
        output_path = job["outputPath"]
        youtube_source = source if source.get("kind") == "youtube" else None
        if youtube_source is None:
            job.update(status="processing", progress=0 if job["durationSeconds"] else None, startedAt=time.time())

    if youtube_source is not None:
        _run_youtube_job(job_id, youtube_source, job)
        return

    stderr_tail: deque[str] = deque(maxlen=40)
    process = None
    stderr_thread = None
    watchdog = None
    try:
        command = _make_ffmpeg_command(source, job, output_path)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        with STATE_LOCK:
            ACTIVE_PROCESSES[job_id] = process

        def drain_stderr() -> None:
            assert process and process.stderr
            for raw_line in process.stderr:
                stderr_tail.append(raw_line.decode("utf-8", errors="replace").rstrip())

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()
        watchdog = threading.Timer(MAX_JOB_SECONDS, process.kill)
        watchdog.daemon = True
        watchdog.start()

        duration = job["durationSeconds"]
        assert process.stdout
        for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"out_time_us", "out_time_ms"} and duration:
                try:
                    out_seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                progress = max(0, min(99, int(out_seconds / duration * 100)))
                with STATE_LOCK:
                    current = JOBS.get(job_id)
                    if current and current["status"] == "processing":
                        current["progress"] = max(current["progress"], progress)
            elif key == "progress" and value == "end":
                with STATE_LOCK:
                    current = JOBS.get(job_id)
                    if current and current["status"] == "processing":
                        current["progress"] = max(current["progress"], 99)

        return_code = process.wait()
        if stderr_thread:
            stderr_thread.join(timeout=2)
        if return_code != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
            detail = " ".join(stderr_tail).strip()
            error = "FFmpeg could not process this file."
            if "No space left on device" in detail:
                error = "There is not enough free disk space to finish this conversion."
            elif "Invalid data found" in detail or "moov atom not found" in detail:
                error = "The media file is incomplete or damaged."
            elif return_code == 0:
                error = "The processor finished without producing an output file."
            with STATE_LOCK:
                current = JOBS.get(job_id)
                if current:
                    current.update(status="failed", progress=None, error=error, finishedAt=time.time())
            return

        with STATE_LOCK:
            current = JOBS.get(job_id)
            if current:
                current.update(
                    status="complete",
                    progress=100,
                    error=None,
                    finishedAt=time.time(),
                    outputBytes=output_path.stat().st_size,
                )
    except (OSError, subprocess.SubprocessError, ValueError):
        output_path.unlink(missing_ok=True)
        with STATE_LOCK:
            current = JOBS.get(job_id)
            if current:
                current.update(status="failed", progress=None, error="FFmpeg could not start or finish. Check the local processor installation.", finishedAt=time.time())
    finally:
        if watchdog:
            watchdog.cancel()
        if process:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()
        with STATE_LOCK:
            ACTIVE_PROCESSES.pop(job_id, None)
            WORKER_THREADS.discard(threading.current_thread())


def _stop_active_jobs() -> None:
    with STATE_LOCK:
        processes = list(ACTIVE_PROCESSES.values())
        workers = list(WORKER_THREADS)
    for process in processes:
        try:
            process.kill()
        except OSError:
            pass
    for worker in workers:
        worker.join(timeout=10)


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "yt-download-local/1.0"
    sys_version = ""

    def log_message(self, _format: str, *_args) -> None:
        # Do not log file names, URLs, or request bodies.
        return

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server.server_port
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _valid_local_request(self) -> bool:
        host = self.headers.get("Host", "").lower()
        if host not in self.allowed_hosts:
            self._send_json(403, {"error": "This local processor only accepts loopback requests."})
            return False
        origin = self.headers.get("Origin")
        allowed_origins = {f"http://{host}" for host in self.allowed_hosts}
        if origin and origin not in allowed_origins:
            self._send_json(403, {"error": "Cross-origin requests are not allowed."})
            return False
        return True

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid request length."})
            return None
        if length < 1 or length > MAX_JSON_BYTES:
            self._send_json(413, {"error": "The request body is empty or too large."})
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "The request must contain valid JSON."})
            return None
        if not isinstance(data, dict):
            self._send_json(400, {"error": "The request must be a JSON object."})
            return None
        return data

    def do_GET(self) -> None:
        if not self._valid_local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, {
                "ready": bool(FFMPEG and FFPROBE),
                "ytDlp": _youtube_command_available(),
                "ffmpeg": bool(FFMPEG),
                "ffprobe": bool(FFPROBE),
                "maxUploadBytes": MAX_UPLOAD_BYTES,
                "maxUploadMb": MAX_UPLOAD_BYTES // (1024 * 1024),
                "processor": "local-ffmpeg",
            })
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            if not re.fullmatch(r"[0-9a-f]{32}", job_id):
                self._send_json(404, {"error": "Job not found."})
                return
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self._send_json(404, {"error": "Job not found or expired."})
                    return
                payload = {
                    "jobId": job_id,
                    "status": job["status"],
                    "progress": job["progress"],
                    "stage": job.get("stage"),
                    "error": job.get("error"),
                }
                if job["status"] == "complete":
                    payload["fileName"] = job["outputName"]
                    payload["outputBytes"] = job["outputBytes"]
                    payload["downloadUrl"] = f"/api/download/{job_id}"
                self._send_json(200, payload)
            return
        if path.startswith("/api/download/"):
            job_id = path.removeprefix("/api/download/")
            if not re.fullmatch(r"[0-9a-f]{32}", job_id):
                self._send_json(404, {"error": "File not found."})
                return
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if not job or job["status"] != "complete":
                    self._send_json(404, {"error": "The file is not ready or has expired."})
                    return
                output_path = job["outputPath"]
                output_name = job["outputName"]
            try:
                file_size = output_path.stat().st_size
                stream = output_path.open("rb")
            except OSError:
                self._send_json(404, {"error": "The output file has expired."})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Content-Disposition", f"attachment; filename=\"{output_name}\"; filename*=UTF-8''{quote(output_name)}")
            self._security_headers()
            self.end_headers()
            try:
                shutil.copyfileobj(stream, self.wfile, length=CHUNK_SIZE)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                stream.close()
            return
        if path.startswith("/api/thumbnails/"):
            source_id = path.removeprefix("/api/thumbnails/")
            if not re.fullmatch(r"[0-9a-f]{32}", source_id):
                self._send_json(404, {"error": "Thumbnail not found."})
                return
            with STATE_LOCK:
                source = SOURCES.get(source_id)
                thumbnail_path = source.get("thumbnailPath") if source else None
            if not thumbnail_path:
                self._send_json(404, {"error": "Thumbnail not found or expired."})
                return
            try:
                thumbnail_bytes = thumbnail_path.read_bytes()
            except OSError:
                self._send_json(404, {"error": "Thumbnail not found or expired."})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(thumbnail_bytes)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(thumbnail_bytes)
            return

        static_paths = {
            "/": ROOT / "index.html",
            "/index.html": ROOT / "index.html",
            "/css/styles.css": ROOT / "css" / "styles.css",
            "/js/validation.js": ROOT / "js" / "validation.js",
            "/js/app.js": ROOT / "js" / "app.js",
        }
        static_path = static_paths.get(path)
        if not static_path:
            self._send_json(404, {"error": "Not found."})
            return
        try:
            body = static_path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "Static file not found."})
            return
        content_type = mimetypes.guess_type(str(static_path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._valid_local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/analyze":
            self._handle_analyze()
        elif path == "/api/analyze-url":
            self._handle_analyze_url()
        elif path == "/api/jobs":
            self._handle_create_job()
        else:
            self._send_json(404, {"error": "Not found."})

    def _handle_analyze(self) -> None:
        if not FFMPEG or not FFPROBE:
            self._send_json(503, {"error": "FFmpeg and ffprobe must be installed and available on PATH."})
            return
        if self.headers.get("X-Processing-Permission") != "confirmed":
            self._send_json(403, {"error": "Konfirmasikan bahwa Anda berhak memproses file media ini."})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/octet-stream":
            self._send_json(415, {"error": "Upload the selected media file as a binary request body."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid upload length."})
            return
        if content_length < 1:
            self._send_json(400, {"error": "The selected file is empty."})
            return
        if content_length > MAX_UPLOAD_BYTES:
            self._send_json(413, {"error": f"This file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB local upload limit."})
            return

        encoded_name = self.headers.get("X-Upload-Filename", "")
        if not encoded_name or len(encoded_name) > 1024:
            self._send_json(400, {"error": "The file name is missing or too long."})
            return
        filename = _clean_display_name(unquote(encoded_name))
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            self._send_json(415, {"error": "Choose a supported audio or video file."})
            return

        _expire_old_items()
        source_id = uuid.uuid4().hex
        source_path = SOURCES_DIR / f"{source_id}{extension}"
        remaining = content_length
        try:
            with source_path.open("wb") as output:
                while remaining:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError("The upload ended before the complete file arrived.")
                    output.write(chunk)
                    remaining -= len(chunk)
        except ValueError as exc:
            source_path.unlink(missing_ok=True)
            self._send_json(400, {"error": str(exc)})
            return
        except OSError:
            source_path.unlink(missing_ok=True)
            self._send_json(507, {"error": "The local temporary directory could not store this file."})
            return

        try:
            media = _probe_media(source_path, filename)
        except (ValueError, RuntimeError) as exc:
            source_path.unlink(missing_ok=True)
            self._send_json(422, {"error": str(exc)})
            return
        formats, reasons = _build_capabilities(media)
        thumbnail_path = _create_thumbnail(source_id, source_path) if media["video"] else None
        source = {
            "path": source_path,
            "fileName": filename,
            "safeStem": _safe_stem(filename),
            "sizeBytes": content_length,
            "createdAt": time.time(),
            "media": media,
            "thumbnailPath": thumbnail_path,
            "formats": formats,
            "reasons": reasons,
        }
        with STATE_LOCK:
            SOURCES[source_id] = source
        self._send_json(200, {
            "sourceId": source_id,
            "fileName": filename,
            "title": media["title"],
            "sizeBytes": content_length,
            "durationSeconds": media["durationSeconds"],
            "video": media["video"],
            "audio": media["audio"],
            "thumbnailUrl": f"/api/thumbnails/{source_id}" if thumbnail_path else None,
            "formats": formats,
            "unavailableReasons": reasons,
        })

    def _handle_analyze_url(self) -> None:
        if not FFMPEG or not FFPROBE:
            self._send_json(503, {"error": "FFmpeg and ffprobe must be installed and available on PATH."})
            return
        data = self._read_json()
        if data is None:
            return
        source_url = data.get("url")
        if data.get("permissionConfirmed") is not True:
            self._send_json(403, {"error": "Konfirmasikan bahwa Anda berhak mengunduh dan memproses media ini."})
            return
        if not isinstance(source_url, str) or len(source_url) > 4096:
            self._send_json(400, {"error": "Masukkan URL file media yang valid."})
            return

        _expire_old_items()
        source_id = uuid.uuid4().hex
        if _is_youtube_url(source_url.strip()):
            try:
                payload = _analyze_youtube_source(source_url.strip())
            except (ValueError, RuntimeError) as exc:
                self._send_json(422, {"error": str(exc)})
                return
            self._send_json(200, payload)
            return

        try:
            source_path, filename, size_bytes = _download_remote_media(source_id, source_url.strip())
            media = _probe_media(source_path, filename)
        except (ValueError, RuntimeError) as exc:
            for candidate in SOURCES_DIR.glob(f"{source_id}.*"):
                candidate.unlink(missing_ok=True)
            self._send_json(422, {"error": str(exc)})
            return
        except OSError:
            self._send_json(507, {"error": "Direktori sementara lokal tidak dapat menyimpan file sumber."})
            return

        formats, reasons = _build_capabilities(media)
        thumbnail_path = _create_thumbnail(source_id, source_path) if media["video"] else None
        source = {
            "path": source_path,
            "fileName": filename,
            "safeStem": _safe_stem(filename),
            "sizeBytes": size_bytes,
            "createdAt": time.time(),
            "media": media,
            "thumbnailPath": thumbnail_path,
            "formats": formats,
            "reasons": reasons,
        }
        with STATE_LOCK:
            SOURCES[source_id] = source
        self._send_json(200, {
            "sourceId": source_id,
            "fileName": filename,
            "title": media["title"],
            "sizeBytes": size_bytes,
            "durationSeconds": media["durationSeconds"],
            "video": media["video"],
            "audio": media["audio"],
            "thumbnailUrl": f"/api/thumbnails/{source_id}" if thumbnail_path else None,
            "formats": formats,
            "unavailableReasons": reasons,
        })

    def _handle_create_job(self) -> None:
        data = self._read_json()
        if data is None:
            return
        source_id = data.get("sourceId")
        format_id = data.get("format")
        quality_id = data.get("quality")
        if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{32}", source_id):
            self._send_json(400, {"error": "Inspect a media file before starting a conversion."})
            return
        if format_id not in {"mp4", "mp3"} or not isinstance(quality_id, str):
            self._send_json(400, {"error": "Select a supported output format and quality."})
            return

        with STATE_LOCK:
            source = SOURCES.get(source_id)
            if not source:
                self._send_json(404, {"error": "The temporary source has expired. Inspect the file again."})
                return
            format_entry = next((item for item in source["formats"] if item["id"] == format_id), None)
            if not format_entry:
                self._send_json(422, {"error": source["reasons"].get(format_id, "This format is unavailable for the selected file.")})
                return
            if quality_id not in {item["id"] for item in format_entry["qualities"]}:
                self._send_json(422, {"error": "That quality is not available for this file."})
                return

            raw_range = data.get("range")
            time_range = None
            if raw_range is not None:
                if not isinstance(raw_range, dict):
                    self._send_json(400, {"error": "The custom time range is invalid."})
                    return
                try:
                    start_seconds = float(raw_range.get("startSeconds"))
                    end_seconds = float(raw_range.get("endSeconds"))
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "Enter valid start and end times."})
                    return
                duration = source["media"]["durationSeconds"]
                if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or start_seconds < 0 or end_seconds <= start_seconds:
                    self._send_json(400, {"error": "End time must be later than the start time."})
                    return
                if duration is None:
                    self._send_json(422, {"error": "The processor could not determine this file's duration, so custom trimming is unavailable."})
                    return
                if end_seconds > duration + 0.05:
                    self._send_json(422, {"error": "The end time is beyond this file's actual duration."})
                    return
                if end_seconds - start_seconds > MAX_MEDIA_DURATION_SECONDS:
                    self._send_json(422, {"error": "Durasi pilihan maksimal 12 jam."})
                    return
                time_range = {"startSeconds": start_seconds, "endSeconds": end_seconds}

            job_id = uuid.uuid4().hex
            extension = ".mp4" if format_id == "mp4" else ".mp3"
            output_path = OUTPUTS_DIR / f"{job_id}{extension}"
            duration_for_progress = (
                time_range["endSeconds"] - time_range["startSeconds"]
                if time_range else source["media"]["durationSeconds"]
            )
            if duration_for_progress is not None and duration_for_progress > MAX_MEDIA_DURATION_SECONDS:
                self._send_json(422, {"error": "Durasi pilihan maksimal 12 jam. Pilih rentang khusus yang lebih pendek."})
                return
            if duration_for_progress is None and time_range is None:
                self._send_json(422, {"error": "Durasi media tidak diketahui. Pilih media dengan durasi yang dapat dibaca atau gunakan rentang khusus maksimal 12 jam."})
                return
            job = {
                "sourceId": source_id,
                "format": format_id,
                "quality": quality_id,
                "range": time_range,
                "durationSeconds": duration_for_progress,
                "outputPath": output_path,
                "outputName": f"{source['safeStem']}.{format_id}",
                "status": "queued",
                "stage": "Menunggu processor…",
                "progress": 0 if duration_for_progress else None,
                "error": None,
                "createdAt": time.time(),
            }
            JOBS[job_id] = job
        worker = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        with STATE_LOCK:
            WORKER_THREADS.add(worker)
        worker.start()
        self._send_json(202, {"jobId": job_id, "status": "queued"})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local yt-download media processor.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("YT_DOWNLOAD_PORT", "8765")))
    parser.add_argument("--max-upload-mb", type=int, default=None)
    parser.add_argument("--allow-private-url-sources", action="store_true", help="Allow private-network URL sources for local testing only.")
    return parser.parse_args()


def main() -> None:
    global ALLOW_PRIVATE_URL_SOURCES, MAX_UPLOAD_BYTES
    args = _parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("Port must be between 1 and 65535.")
    if args.max_upload_mb is not None:
        if args.max_upload_mb < 1:
            raise SystemExit("--max-upload-mb must be at least 1.")
        MAX_UPLOAD_BYTES = args.max_upload_mb * 1024 * 1024
    ALLOW_PRIVATE_URL_SOURCES = args.allow_private_url_sources
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), LocalHandler)
    except OSError as exc:
        raise SystemExit(f"Could not start on 127.0.0.1:{args.port}: {exc}") from exc
    server.daemon_threads = True
    print(f"Local processor ready at http://127.0.0.1:{args.port}")
    print(f"FFmpeg: {'available' if FFMPEG else 'not found'} | ffprobe: {'available' if FFPROBE else 'not found'}")
    print(f"Available encoders: {', '.join(name for name in (VIDEO_ENCODER, AAC_ENCODER, MP3_ENCODER) if name) or 'none'}")
    print("Files are temporary and are removed when this server stops. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping local processor...")
    finally:
        _stop_active_jobs()
        server.server_close()


if __name__ == "__main__":
    main()
