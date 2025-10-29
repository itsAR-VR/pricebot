import { jest } from "@jest/globals";

const extractMessageContentMock = jest.fn();
const getContentTypeMock = jest.fn();
const jidNormalizedUserMock = jest.fn();

export const BufferJSON = {
  replacer: (_key: unknown, value: unknown) => value,
};

export const extractMessageContent = extractMessageContentMock;
export const getContentType = getContentTypeMock;
export const jidNormalizedUser = jidNormalizedUserMock;

export class WAMessage {}
export class WASocket {}

export const __baileysMocks = {
  extractMessageContent: extractMessageContentMock,
  getContentType: getContentTypeMock,
  jidNormalizedUser: jidNormalizedUserMock,
};

export default {
  BufferJSON,
  extractMessageContent,
  getContentType,
  jidNormalizedUser,
  WAMessage,
  WASocket,
};
