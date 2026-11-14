import assert from "node:assert/strict";
import test from "node:test";

import {
  availableEnrollmentPhotoSlots,
  captureEnrollmentPhoto,
  MAX_ENROLLMENT_PHOTO_BYTES,
  MAX_ENROLLMENT_PHOTOS,
  prepareEnrollmentPhoto,
  stopOwnedCameraStream,
} from "../src/enrollmentPhoto.ts";

const jpegFile = () => new File(["jpeg"], "reference.jpg", { type: "image/jpeg" });

test("prepares uploaded and captured photos as bounded JPEGs", async () => {
  let closed = false;
  let preparedDimensions = null;
  const file = jpegFile();
  const base64 = await prepareEnrollmentPhoto(file, {
    decodeImage: async () => ({
      width: 3_000,
      height: 2_000,
      close: () => { closed = true; },
    }),
    createCanvas: () => {
      const canvas = {
        width: 0,
        height: 0,
        getContext: () => ({ drawImage: () => {} }),
        toDataURL: (type) => {
          assert.equal(type, "image/jpeg");
          preparedDimensions = [canvas.width, canvas.height];
          return "data:image/jpeg;base64,/9j/2Q==";
        },
      };
      return canvas;
    },
  });
  assert.equal(base64, "/9j/2Q==");
  assert.deepEqual(preparedDimensions, [1_280, 853]);
  assert.equal(closed, true);

  const video = { videoWidth: 1_920, videoHeight: 1_080 };
  const captured = await captureEnrollmentPhoto(video, () => {
    const canvas = {
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: () => {} }),
      toBlob: (callback, type) => {
        assert.equal(type, "image/jpeg");
        callback(new Blob(["captured JPEG"], { type }));
      },
    };
    return canvas;
  });
  assert.equal(captured.type, "image/jpeg");
  assert.equal(captured.size, "captured JPEG".length);
  const preparedCapture = await prepareEnrollmentPhoto(captured, {
    decodeImage: async () => ({ width: 640, height: 480, close: () => {} }),
    createCanvas: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: () => {} }),
      toDataURL: () => "data:image/jpeg;base64,/9j/2Q==",
    }),
  });
  assert.equal(preparedCapture, "/9j/2Q==");
});

test("rejects invalid and oversized photos and enforces the batch limit", async () => {
  await assert.rejects(
    prepareEnrollmentPhoto(new File(["x"], "bad.gif", { type: "image/gif" })),
    /JPEG, PNG, or WebP/,
  );
  await assert.rejects(
    prepareEnrollmentPhoto(
      new File([new Uint8Array(MAX_ENROLLMENT_PHOTO_BYTES + 1)], "large.jpg", { type: "image/jpeg" }),
    ),
    /smaller than 5 MiB/,
  );

  assert.equal(availableEnrollmentPhotoSlots(0), MAX_ENROLLMENT_PHOTOS);
  assert.equal(availableEnrollmentPhotoSlots(9), 1);
  assert.equal(availableEnrollmentPhotoSlots(10), 0);
  assert.equal(availableEnrollmentPhotoSlots(11), 0);
});

test("rejects unusable or oversized camera captures", async () => {
  await assert.rejects(
    captureEnrollmentPhoto({ videoWidth: 0, videoHeight: 480 }, () => {
      throw new Error("Canvas must not be created for an unready camera.");
    }),
    /preview is not ready/,
  );
  await assert.rejects(
    captureEnrollmentPhoto({ videoWidth: 640, videoHeight: 480 }, () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: () => {} }),
      toBlob: (callback, type) => {
        callback(new Blob([new Uint8Array(MAX_ENROLLMENT_PHOTO_BYTES + 1)], { type }));
      },
    })),
    /still too large/,
  );
});

test("camera cleanup stops only the enrollment-owned stream", () => {
  let accessStopped = false;
  let enrollmentStopped = false;
  const accessTrack = { stop: () => { accessStopped = true; } };
  const enrollmentTrack = { stop: () => { enrollmentStopped = true; } };
  const accessStream = { getTracks: () => [accessTrack] };
  const enrollmentStream = { getTracks: () => [enrollmentTrack] };

  stopOwnedCameraStream(enrollmentStream, accessStream);
  assert.equal(enrollmentStopped, true);
  assert.equal(accessStopped, false);

  stopOwnedCameraStream(accessStream, accessStream);
  assert.equal(accessStopped, false);
});
