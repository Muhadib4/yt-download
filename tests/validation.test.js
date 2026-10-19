const test = require("node:test");
const assert = require("node:assert/strict");
const { validateMediaSourceUrl, parseTimestamp, validateTimeRange } = require("../js/validation.js");

test("recognizes direct HTTP(S) media URLs before server-side media checks", () => {
  for (const url of [
    "https://media.example.com/movie.mp4",
    "http://cdn.example.com/audio?file=recording",
  ]) {
    assert.equal(validateMediaSourceUrl(url).valid, true, url);
  }
});

test("rejects credentials and non-HTTP URL schemes", () => {
  for (const url of [
    "https://user:pass@media.example.com/movie.mp4",
    "ftp://media.example.com/movie.mp4",
  ]) {
    assert.equal(validateMediaSourceUrl(url).valid, false, url);
  }
  assert.equal(validateMediaSourceUrl("").valid, false);
});

test("parses and validates custom time ranges against actual duration", () => {
  assert.equal(parseTimestamp("01:02:03"), 3723);
  assert.equal(parseTimestamp("00:61:00"), null);
  assert.equal(validateTimeRange("00:00:05", "00:00:09", 10).valid, true);
  assert.equal(validateTimeRange("00:00:09", "00:00:05", 10).field, "end");
  assert.equal(validateTimeRange("00:00:05", "00:00:11", 10).field, "end");
  assert.equal(validateTimeRange("00:00:05", "00:00:09", null).valid, false);
});


test("limits a selected time range to twelve hours", () => {
  assert.equal(validateTimeRange("00:00:00", "12:00:00", 43200).valid, true);
  assert.equal(validateTimeRange("00:00:00", "12:00:01", 43201).valid, false);
});
