import { jest } from "@jest/globals";

const retryMock = jest.fn(
  async (
    fn: () => unknown,
    options?: { onFailedAttempt?: (error: any) => void },
  ) => {
    try {
      return await fn();
    } catch (error) {
      options?.onFailedAttempt?.({
        ...(error as object),
        attemptNumber: 1,
        retriesLeft: 0,
      });
      throw error;
    }
  },
);

export const __pRetryMock = retryMock;

export class AbortError extends Error {
  originalError: unknown;

  constructor(originalError: unknown) {
    super("AbortError");
    this.name = "AbortError";
    this.originalError = originalError;
  }
}

export default retryMock;
