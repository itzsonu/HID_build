import assert from "node:assert/strict";
import test from "node:test";

import {
  screenChallengeLabel,
  screenDirectionForChallenge,
} from "../src/challengeDisplay.ts";

test("challenge labels match the mirrored preview direction", () => {
  assert.equal(screenDirectionForChallenge("MOVE_LEFT"), "right");
  assert.equal(screenChallengeLabel("MOVE_LEFT"), "MOVE_RIGHT");
  assert.equal(screenDirectionForChallenge("MOVE_RIGHT"), "left");
  assert.equal(screenChallengeLabel("MOVE_RIGHT"), "MOVE_LEFT");
  assert.equal(screenChallengeLabel(null), null);
});
