(function (root) {
  "use strict";

  function validateMediaSourceUrl(value) {
    const trimmed = String(value || "").trim();
    if (!trimmed) return { valid: false, message: "Masukkan URL file media." };

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
    if (endSeconds - startSeconds > 12 * 60 * 60) {
      return { valid: false, field: "end", message: "Durasi pilihan maksimal 12 jam." };
    }
    if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
      return { valid: false, message: "Durasi file belum diketahui; rentang khusus tidak tersedia." };
    }
    if (endSeconds > durationSeconds + 0.05) {
      return { valid: false, field: "end", message: "Waktu akhir melebihi durasi file." };
    }
    return { valid: true, startSeconds, endSeconds };
  }

  const api = { validateMediaSourceUrl, parseTimestamp, validateTimeRange };
  root.YtDownloadValidation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
