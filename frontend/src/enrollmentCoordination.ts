export interface EnrollmentOperation {
  generation: number;
  settled: boolean;
  promise: Promise<boolean>;
}

export function enrollmentForAttempt(
  generationAtStart: number,
  operationAtStart: EnrollmentOperation | null,
  latestOperation: EnrollmentOperation | null,
): EnrollmentOperation | null {
  if (latestOperation && latestOperation.generation > generationAtStart) {
    return latestOperation;
  }
  return operationAtStart;
}

export function waitForEnrollment(
  promise: Promise<boolean>,
  signal: AbortSignal,
): Promise<boolean> {
  if (signal.aborted) {
    return Promise.reject(new DOMException("Verification cancelled.", "AbortError"));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    const onAbort = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(new DOMException("Verification cancelled.", "AbortError"));
    };

    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (registered) => {
        if (!settled) {
          settled = true;
          cleanup();
          resolve(registered);
        }
      },
      (error: unknown) => {
        if (!settled) {
          settled = true;
          cleanup();
          reject(error);
        }
      },
    );
  });
}
