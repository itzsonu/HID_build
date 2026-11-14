import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_FRAME_BYTES,
  MAX_TOTAL_BASE64_BYTES,
  decodedBase64Length,
  validateFrameBatch,
} from "../src/frameCapture.ts";

const validJpegBase64 = "/9j/2Q==";

test("validates the backend frame-count range and accepts an ordered JPEG batch", () => {
  assert.match(validateFrameBatch(Array(4).fill(validJpegBase64)), /between 5 and 20/);
  assert.equal(validateFrameBatch(Array(10).fill(validJpegBase64)), null);
  assert.match(validateFrameBatch(Array(21).fill(validJpegBase64)), /between 5 and 20/);
});

test("rejects malformed, non-JPEG, and oversized frame data", () => {
  assert.match(validateFrameBatch(Array(5).fill("not-base64")), /Base64 JPEG/);
  assert.match(validateFrameBatch(Array(5).fill("aGVsbG8=")), /not a JPEG/);

  const oversizedFrame = `/9j/${"A".repeat(7_000_000)}`;
  assert.ok(decodedBase64Length(oversizedFrame) > MAX_FRAME_BYTES);
  assert.match(
    validateFrameBatch([oversizedFrame, ...Array(4).fill(validJpegBase64)]),
    /5 MiB/,
  );
});

test("rejects a combined Base64 payload over the backend limit", () => {
  const largeJpeg = `/9j/${"A".repeat(1_100_000)}`;
  const frames = Array(20).fill(largeJpeg);
  assert.ok(frames.reduce((sum, frame) => sum + frame.length, 0) > MAX_TOTAL_BASE64_BYTES);
  assert.match(validateFrameBatch(frames), /20 MiB/);
});
