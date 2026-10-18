const test = require("node:test");
const assert = require("node:assert/strict");
const { validateYouTubeUrl, parseTimestamp, validateTimeRange } = require("../js/validation.js");

test("recognizes supported YouTube URL shapes without claiming extraction", () => {
  for (const url of [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=30",
    "https://m.youtube.com/shorts/dQw4w9WgXcQ",
    "https://youtube.com/live/dQw4w9WgXcQ",
    "https://youtube.com/embed/dQw4w9WgXcQ",
  ]) {
    assert.equal(validateYouTubeUrl(url).valid, true, url);
  }
});

test("rejects unrelated hosts, lookalike hosts, credentials, and malformed IDs", () => {
  for (const url of [
    "https://example.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.example.net/watch?v=dQw4w9WgXcQ",
    "https://user:pass@youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=short",
    "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
  ]) {
    assert.equal(validateYouTubeUrl(url).valid, false, url);
  }
  assert.equal(validateYouTubeUrl("").valid, false);
});

test("parses and validates custom time ranges against actual duration", () => {
  assert.equal(parseTimestamp("01:02:03"), 3723);
  assert.equal(parseTimestamp("00:61:00"), null);
  assert.equal(validateTimeRange("00:00:05", "00:00:09", 10).valid, true);
  assert.equal(validateTimeRange("00:00:09", "00:00:05", 10).field, "end");
  assert.equal(validateTimeRange("00:00:05", "00:00:11", 10).field, "end");
  assert.equal(validateTimeRange("00:00:05", "00:00:09", null).valid, false);
});
