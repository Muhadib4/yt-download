(function (root) {
  "use strict";

  function validateYouTubeUrl(value) {
    const trimmed = String(value || "").trim();
    if (!trimmed) return { valid: false, message: "Masukkan URL YouTube." };

    let parsed;
    try {
      parsed = new URL(trimmed);
    } catch {
      return { valid: false, message: "Masukkan URL lengkap yang diawali http:// atau https://." };
    }

    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return { valid: false, message: "URL harus menggunakan http:// atau https://." };
    }
    if (parsed.username || parsed.password) {
      return { valid: false, message: "Hapus informasi login dari URL." };
    }

    const host = parsed.hostname.toLowerCase();
    const isYouTube = ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(host);
    const isShortDomain = host === "youtu.be";
    if (!isYouTube && !isShortDomain) {
      return { valid: false, message: "Gunakan URL youtube.com atau youtu.be." };
    }

    let videoId = "";
    if (isShortDomain) {
      videoId = parsed.pathname.split("/").filter(Boolean)[0] || "";
    } else if (parsed.pathname === "/watch") {
      videoId = parsed.searchParams.get("v") || "";
    } else {
      const match = parsed.pathname.match(/^\/(?:shorts|live|embed)\/([^/?#]+)/);
      videoId = match ? match[1] : "";
    }

    if (!videoId) return { valid: false, message: "URL ini tidak memuat ID video YouTube yang didukung." };
    if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) {
      return { valid: false, message: "ID video pada URL ini tidak valid." };
    }
    return { valid: true };
  }

  function parseTimestamp(value) {
    const match = String(value || "").trim().match(/^(\d{2,}):([0-5]\d):([0-5]\d)$/);
    if (!match) return null;
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
  }

  function validateTimeRange(startText, endText, durationSeconds) {
    const startSeconds = parseTimestamp(startText);
    const endSeconds = parseTimestamp(endText);
    if (startSeconds === null || endSeconds === null) {
      return {
        valid: false,
        field: startSeconds === null ? "start" : "end",
        message: "Isi kedua waktu dengan format HH:MM:SS, misalnya 00:03:15.",
      };
    }
    if (endSeconds <= startSeconds) {
      return { valid: false, field: "end", message: "Waktu akhir harus setelah waktu mulai." };
    }
    if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
      return { valid: false, message: "Durasi file belum diketahui; rentang khusus tidak tersedia." };
    }
    if (endSeconds > durationSeconds + 0.05) {
      return { valid: false, field: "end", message: "Waktu akhir melebihi durasi file." };
    }
    return { valid: true, startSeconds, endSeconds };
  }

  const api = { validateYouTubeUrl, parseTimestamp, validateTimeRange };
  root.YtDownloadValidation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
