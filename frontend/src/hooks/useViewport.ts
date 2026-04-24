// Viewport-size hook for responsive component logic.
//
// CSS media queries handle layout reflow; this hook handles the cases
// where component behavior itself changes — BookSidebar rendering as a
// 100vw fullscreen sheet vs a 420px side panel, the App nav swapping
// to a hamburger drawer, etc. Don't reach for this when a CSS media
// query would do the job — it triggers re-renders on resize and adds
// hydration concerns that pure CSS avoids.
//
// Breakpoint constants are exported so components can reference the
// same values whether they're branching in JS or in inline styles.
import { useEffect, useState } from "react";

export const MOBILE_MAX = 700; // phone landscape + portrait
export const TABLET_MAX = 1024; // small tablets / larger phones in landscape

export interface Viewport {
  width: number;
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
}

function read(): Viewport {
  if (typeof window === "undefined") {
    // SSR safety. Seshat's a CSR-only app today, so this branch is
    // theoretical — keeping it so the hook can be lifted into an
    // eventual SSR setup without a refactor.
    return {
      width: 1920,
      isMobile: false,
      isTablet: false,
      isDesktop: true,
    };
  }
  const w = window.innerWidth;
  return {
    width: w,
    isMobile: w <= MOBILE_MAX,
    isTablet: w > MOBILE_MAX && w <= TABLET_MAX,
    isDesktop: w > TABLET_MAX,
  };
}

export function useViewport(): Viewport {
  const [vp, setVp] = useState<Viewport>(read);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let raf = 0;
    const update = () => {
      // rAF-debounce so a rapid drag doesn't fire dozens of state
      // updates per frame.
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        setVp(read());
      });
    };
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return vp;
}
