// Global SSE events provider.
//
// Single EventSource connection per tab, mounted at the app root.
// Handles the two event types that shouldn't be page-scoped:
//
//   * `toast`         → routes straight into `lib/toast.ts` so
//                        backend-initiated notifications surface no
//                        matter which page the user is on.
//   * `client-status` → stored in context so any page can render a
//                        live qBit reachability indicator (e.g., the
//                        Downloader pill on the Dashboard).
//
// Pages that need additional page-scoped events (MamPage's mam-stats,
// Dashboard's mam-stats) still subscribe independently via the same
// useVisibleEventSource hook. That costs an extra connection per tab
// when those pages are mounted, which is acceptable on a single-user
// deployment — backend fans one qBit snapshot out to all subscribers
// with no extra qBit load.
import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useVisibleEventSource } from "../hooks/useVisibleEventSource";
import { toast } from "../lib/toast";

export interface SseEventsState {
  // qBit reachability from the most recent backend `client-status`
  // event. `null` until the first event arrives (which happens on
  // the first publish_client_status call after a backend transition,
  // or on the first tick after this tab's connection opens — the
  // publisher publishes on initial-state so every connected client
  // gets a baseline).
  clientReachable: boolean | null;
}

const SseEventsContext = createContext<SseEventsState>({
  clientReachable: null,
});

export function useSseEvents(): SseEventsState {
  return useContext(SseEventsContext);
}

export function SseEventsProvider({ children }: { children: ReactNode }) {
  const [clientReachable, setClientReachable] = useState<boolean | null>(null);
  const lastReachable = useRef<boolean | null>(null);

  useVisibleEventSource({
    toast: (e) => {
      // Backend-initiated notification. `lib/toast.ts` fires the
      // `seshat:toast` window event; the <Toaster /> component
      // mounted in App picks it up and renders the banner.
      toast[e.level](e.message);
    },
    "client-status": (e) => {
      // Update the shared state so subscribers (Dashboard pill) see
      // the new reachability. Only toast on transitions AFTER the
      // first event — otherwise every tab refresh toasts
      // "qBittorrent reachable" because the backend's initial-state
      // publish counts as a transition from null.
      setClientReachable(e.reachable);
      const prev = lastReachable.current;
      lastReachable.current = e.reachable;
      if (prev === null || prev === e.reachable) return;
      if (e.reachable) {
        toast.success("qBittorrent reachable");
      } else {
        toast.warn("qBittorrent unreachable — check logs");
      }
    },
  });

  return (
    <SseEventsContext.Provider value={{ clientReachable }}>
      {children}
    </SseEventsContext.Provider>
  );
}
