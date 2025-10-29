import { jest } from "@jest/globals";

const postMock = jest.fn();
const createMock = jest.fn(() => ({ post: postMock }));

const isAxiosErrorImpl = (error: unknown): boolean =>
  Boolean(error && typeof error === "object" && (error as { isAxiosError?: boolean }).isAxiosError);

const axiosStub = {
  create: createMock,
  isAxiosError: isAxiosErrorImpl,
};

export const __axiosMocks = {
  create: createMock,
  post: postMock,
};

export const create = createMock;
export const isAxiosError = isAxiosErrorImpl;

export default axiosStub;
