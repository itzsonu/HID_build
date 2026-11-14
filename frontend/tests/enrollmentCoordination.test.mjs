import assert from "node:assert/strict";
import test from "node:test";

import {
  enrollmentForAttempt,
  waitForEnrollment,
} from "../src/enrollmentCoordination.ts";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("access waits for an enrollment started during capture to finish successfully", async () => {
  const enrollment = deferred();
  const operation = { generation: 4, settled: false, promise: enrollment.promise };
  const selected = enrollmentForAttempt(3, null, operation);
  assert.equal(selected, operation);

  let accessSubmitted = false;
  const submission = waitForEnrollment(selected.promise, new AbortController().signal)
    .then((registered) => {
      if (registered) {
        accessSubmitted = true;
      }
      return registered;
    });
  await Promise.resolve();
  assert.equal(accessSubmitted, false);
  enrollment.resolve(true);
  assert.equal(await submission, true);
  assert.equal(accessSubmitted, true);
});

test("an enrollment failure completed before capture ends is still observed", async () => {
  const failedOperation = {
    generation: 9,
    settled: true,
    promise: Promise.resolve(false),
  };
  const selected = enrollmentForAttempt(8, null, failedOperation);
  assert.equal(selected, failedOperation);
  let accessSubmitted = false;
  const registered = await waitForEnrollment(selected.promise, new AbortController().signal);
  if (registered) {
    accessSubmitted = true;
  }
  assert.equal(accessSubmitted, false);
});

test("cancelling while waiting rejects and cannot submit the access decision", async () => {
  const enrollment = deferred();
  const controller = new AbortController();
  let accessSubmitted = false;
  const submission = waitForEnrollment(enrollment.promise, controller.signal).then((registered) => {
    if (registered) {
      accessSubmitted = true;
    }
  });

  controller.abort();
  await assert.rejects(submission, { name: "AbortError" });
  enrollment.resolve(true);
  await Promise.resolve();
  assert.equal(accessSubmitted, false);
});

test("an enrollment completed before an attempt does not block a later verification", () => {
  const completed = {
    generation: 5,
    settled: true,
    promise: Promise.resolve(false),
  };
  assert.equal(enrollmentForAttempt(5, null, completed), null);
});
