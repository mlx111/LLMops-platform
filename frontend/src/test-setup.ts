import '@testing-library/jest-dom';
import '../src/i18n';

// Polyfill window.matchMedia for Ant Design components (used in jsdom)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => { },
    removeListener: () => { },
    addEventListener: () => { },
    removeEventListener: () => { },
    dispatchEvent: () => false,
  }),
});

// Polyfill ResizeObserver for Ant Design 6 (Select/Table/InputNumber/Timeline use it)
// jsdom does not implement ResizeObserver; provide a no-op stub.
class ResizeObserverStub {
  observe(): void { }
  unobserve(): void { }
  disconnect(): void { }
}
(globalThis as { ResizeObserver?: unknown }).ResizeObserver =
  ResizeObserverStub as unknown as typeof ResizeObserver;

// Some AntD components call scrollIntoView during measurement in jsdom
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView(): void { };
}


