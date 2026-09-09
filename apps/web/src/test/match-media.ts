type Listener = (event: MediaQueryListEvent) => void;

export function installMatchMedia(matches: Record<string, boolean> = {}): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string): MediaQueryList => {
      const listeners = new Set<Listener>();
      const mediaQueryList: MediaQueryList = {
        media: query,
        matches: matches[query] ?? false,
        onchange: null,
        addEventListener: (
          _type: string,
          listener: EventListenerOrEventListenerObject | null,
        ) => {
          if (typeof listener === "function") listeners.add(listener);
        },
        removeEventListener: (
          _type: string,
          listener: EventListenerOrEventListenerObject | null,
        ) => {
          if (typeof listener === "function") listeners.delete(listener);
        },
        addListener: (listener) => {
          if (listener) listeners.add(listener as Listener);
        },
        removeListener: (listener) => {
          if (listener) listeners.delete(listener as Listener);
        },
        dispatchEvent: (event) => {
          listeners.forEach((listener) => {
            listener(event as MediaQueryListEvent);
          });
          return true;
        },
      };
      return mediaQueryList;
    },
  });
}
