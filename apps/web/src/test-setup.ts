import "@testing-library/jest-dom/vitest";

import { installMatchMedia } from "./test/match-media";

Object.defineProperty(globalThis, "NodeFilter", {
  configurable: true,
  value: {
    FILTER_ACCEPT: 1,
    FILTER_REJECT: 2,
    FILTER_SKIP: 3,
    SHOW_ELEMENT: 1,
  },
  writable: true,
});

installMatchMedia();

class TestEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {}

  addEventListener(): void {}

  close(): void {}
}

Object.defineProperty(globalThis, "EventSource", {
  configurable: true,
  value: TestEventSource,
  writable: true,
});
