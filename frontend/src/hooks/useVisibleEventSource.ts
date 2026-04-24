// Visibility-aware Server-Sent Events subscriber.
//
// Wraps `EventSource` with the same visibility-pause semantics as
// `useVisibleInterval`: open the connection when the tab is
// visible, close it when the tab goes hidden, reopen on return.
// Saves backend CPU (the qBit snapshot loop doesn't have to fan
// out to an idle tab) and browser memory (no buffered events
// accumulating while the tab is parked).
//
// Auto-reconnect on transport errors uses exponential backoff
// capped at 30s. The backoff resets on any successful `open` event
// so a transient blip doesn't permanently slow reconnects.
//
// The hook is event-dispatch-based, not value-based: callers
// register per-event-type handlers instead of polling the current
// state. That matches how SSE semantically works (push, not pull)
// and keeps the consumer code tiny — a single `useSseEvents` call
// with a handlers object, no extra `useState` for the payloads.

import { useEffect, useRef } from "react";

// ─── Event type shapes (mirror backend sse_publishers) ──────────

export interface TorrentProgressEvent {
  hash: string;
  name: string;
  state: string;
  progress: number; // 0.0-1.0
  dlspeed: number;  // bytes/sec
  eta: number;      // seconds, or 8640000 (qBit's infinity)
  size: number;     // bytes
}

export interface ClientStatusEvent {
  reachable: boolean;
}

export interface MamStatsEvent {
  ratio: number;
  seedbonus: number;
  upload_buffer_bytes: number;
  wedges: number;
}

export interface ToastEvent {
  level: "success" | "info" | "warn" | "error";
  message: string;
}

// Map of event type → handler. All keys optional so a consumer
// can opt into only the events it cares about.
export interface SseEventHandlers {
  "torrent-progress"?: (e: TorrentProgressEvent) => void;
  "client-status"?: (e: ClientStatusEvent) => void;
  "mam-stats"?: (e: MamStatsEvent) => void;
  toast?: (e: ToastEvent) => void;
  // Dispatched when the connection (re-)opens, with the total
  // number of successful opens since mount. Useful to trigger a
  // catch-up fetch after a reconnect, since events that fired
  // while disconnected won't replay.
  open?: (openCount: number) => void;
}

const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 30_000;
const ENDPOINT = "/api/v1/events";

/**
 * Subscribe to the backend SSE stream for the lifetime of the
 * consuming component, automatically pausing while the tab is hidden.
 *
 * @param handlers — object mapping event type to callback. The hook
 *        holds the latest reference via a ref so callers can pass
 *        fresh closures on every render without tearing down the
 *        connection.
 * @param enabled — opt-out gate. Default true. Pass `false` to
 *        temporarily disable the subscription (e.g. during a logged-
 *        out state) without unmounting the consumer.
 */
export function useVisibleEventSource(
  handlers: SseEventHandlers,
  enabled: boolean = true,
): void {
  const handlersRef = useRef<SseEventHandlers>(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  }, [handlers]);

  useEffect(() => {
    if (!enabled) return;

    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelay = RECONNECT_MIN_MS;
    let openCount = 0;
    let cancelled = false;

    const dispatch = <K extends keyof SseEventHandlers>(
      key: K,
      raw: string,
    ) => {
      const handler = handlersRef.current[key];
      if (!handler) return;
      try {
        const parsed = JSON.parse(raw);
        // Invoking each handler in its own try so one broken
        // consumer can't take down the whole stream.
        (handler as (e: unknown) => void)(parsed);
      } catch {
        /* swallow — bad JSON shouldn't kill the connection */
      }
    };

    const connect = () => {
      if (cancelled || document.hidden) return;
      if (source) return;

      source = new EventSource(ENDPOINT);

      source.addEventListener("open", () => {
        reconnectDelay = RECONNECT_MIN_MS;
        openCount += 1;
        handlersRef.current.open?.(openCount);
      });

      source.addEventListener("torrent-progress", (e) => {
        dispatch("torrent-progress", (e as MessageEvent).data);
      });
      source.addEventListener("client-status", (e) => {
        dispatch("client-status", (e as MessageEvent).data);
      });
      source.addEventListener("mam-stats", (e) => {
        dispatch("mam-stats", (e as MessageEvent).data);
      });
      source.addEventListener("toast", (e) => {
        dispatch("toast", (e as MessageEvent).data);
      });

      source.addEventListener("error", () => {
        // The browser auto-reconnects on transient errors, but it
        // does so with its own internal cadence and doesn't respect
        // our backoff. Close + schedule our own reconnect so we
        // control the retry rhythm.
        close();
        if (!cancelled && !document.hidden) {
          reconnectTimer = setTimeout(connect, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
        }
      });
    };

    const close = () => {
      if (source) {
        source.close();
        source = null;
      }
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const onVisibility = () => {
      if (cancelled) return;
      if (document.hidden) {
        close();
      } else {
        reconnectDelay = RECONNECT_MIN_MS;
        connect();
      }
    };

    connect();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      close();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled]);
}
