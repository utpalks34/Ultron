import { useEffect, useRef, useCallback } from "react";
import { useUltron } from "../store/ultronStore";

const URL = "ws://127.0.0.1:8765/ws";

export function useUltronSocket(token) {
  const ws = useRef(null);
  const retry = useRef(0);
  const apply = useUltron((s) => s.apply);

  useEffect(() => {
    let alive = true;
    let timer;

    const connect = () => {
      if (!alive) return;
      const sock = new WebSocket(`${URL}?token=${encodeURIComponent(token)}`);
      ws.current = sock;

      sock.onopen = () => {
        retry.current = 0;
        useUltron.setState({ connected: true });
        console.log("[ultron] connected");
        sock.send(JSON.stringify({ v: 1, type: "resync", payload: {} }));
      };

      sock.onmessage = (e) => {
        let evt;
        try {
          evt = JSON.parse(e.data);
        } catch {
          console.warn("[ultron] bad frame", e.data);
          return;
        }
        if (evt.type === "audio_level") {
          window.__ultronRms = evt.payload.rms; // 30 Hz: bypass React entirely
          return;
        }
        console.log("[ultron]", evt.type, evt);
        apply(evt);
      };

      sock.onclose = () => {
        if (!alive) return;
        useUltron.setState({ connected: false });
        const wait = Math.min(5000, 250 * 2 ** retry.current++);
        console.log(`[ultron] disconnected, reconnecting in ${wait} ms`);
        timer = setTimeout(connect, wait);
      };

      sock.onerror = () => sock.close();
    };

    connect();
    return () => {
      alive = false;
      clearTimeout(timer);
      ws.current?.close();
    };
  }, [token, apply]);

  const send = useCallback((type, payload = {}) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(
        JSON.stringify({ v: 1, type, payload, id: crypto.randomUUID(), ts: Date.now() })
      );
      return true;
    }
    return false;
  }, []);

  return { send };
}
