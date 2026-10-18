# yt-download

A lightweight, no-build frontend for preparing a YouTube media request. It validates supported link formats and custom time ranges in the browser. It does **not** extract, convert, trim, or download media.

## Run locally

Open `index.html` directly in a current desktop or mobile browser. There is no package installation, build step, or development server requirement. Clipboard permissions vary by browser; if clipboard access is unavailable, use the browser or device's normal paste and copy controls.

## What works

- Recognizes YouTube watch, `youtu.be`, Shorts, live, and embed URL formats and checks for a well-formed video ID. This is a format check only; it does not confirm that a video exists or can be accessed.
- Lets users select MP4 or MP3 as an intended output format.
- Accepts full duration or a custom `HH:MM:SS` start and end time, and checks that the end is later than the start.
- Offers clipboard paste and copy when browser permissions allow, with manual-use guidance and a copy fallback.
- Keeps URL validation in the page. The frontend does not send URLs to a server, save them, fetch video details, or create download files.
- Provides a responsive layout, mobile navigation, keyboard focus styles, reduced-motion support, and accessible status and validation messages.

## Current limitations

The quality selector is intentionally disabled because there is no processor to report real capabilities. Time ranges are validated for format and ordering, but the page cannot compare them with a video's duration. No loading progress, metadata, success state, or download link is simulated.

Downloading from YouTube may be restricted by the platform, a creator, copyright, or applicable law. Use media only with permission and in accordance with applicable terms and law. This independent project is not affiliated with or endorsed by YouTube.

## Future processor integration

Media processing belongs in a separate, explicitly configured backend or authorized media source. The frontend currently makes no API calls. A future integration can use a small JSON API such as:

1. `POST /api/analyze` with `{ "url": "…", "format": "mp4" }` returns actual capabilities, for example `{ "formats": ["mp4"], "qualities": [{ "id": "720p", "label": "720p" }], "durationSeconds": 183 }`. Return only formats and qualities that can really be produced for that input. `durationSeconds` may be `null` when unknown.
2. `POST /api/jobs` with `{ "url": "…", "format": "mp4", "quality": "720p", "range": { "startSeconds": 10, "endSeconds": 80 } }` returns `202 Accepted` and `{ "jobId": "…" }`. Omit `range` for the full duration.
3. `GET /api/jobs/{jobId}` returns the real job state, such as `{ "status": "processing", "progress": 42 }`, `{ "status": "failed", "message": "…" }`, or `{ "status": "complete", "downloadUrl": "…", "filename": "…" }`. Progress is optional and must reflect actual work.

Only expose controls after `/api/analyze` supplies the available options. The browser should submit a job only after the user requests it, show states returned by the processor, and offer a download only when a real authorized output is ready. The backend must independently validate inputs, follow platform terms and applicable law, protect credentials, and define clear data retention. No backend or processor is included in this repository.

## Deploy

The frontend is static and can be hosted by GitHub Pages or any static file host. For GitHub Pages, publish the repository root from the `main` branch in the repository's Pages settings. A static deployment will provide URL checks and request setup only; processing features will remain unavailable until a separate authorized service is added and integrated.
