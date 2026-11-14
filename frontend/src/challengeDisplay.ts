import type { LivenessChallenge } from "./services/api";

export type ScreenDirection = "left" | "right";

export function screenDirectionForChallenge(
  challenge: LivenessChallenge | null,
): ScreenDirection | null {
  if (challenge === "MOVE_LEFT") {
    return "right";
  }
  if (challenge === "MOVE_RIGHT") {
    return "left";
  }
  return null;
}

export function screenChallengeLabel(
  challenge: LivenessChallenge | null,
): LivenessChallenge | null {
  const direction = screenDirectionForChallenge(challenge);
  return direction ? `MOVE_${direction.toUpperCase()}` as LivenessChallenge : null;
}
