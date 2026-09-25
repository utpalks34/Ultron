import { useEffect } from "react";
import { useUltron } from "../store/ultronStore";

// Mic -> AnalyserNode -> smoothed RMS written to window.__ultronMicRms (0..1).
// Never touches React state per frame; the orb reads the global inside useFrame.
export function useAudioLevel() {
  const enabled = useUltron((s) => s.micEnabled);

  useEffect(() => {
    if (!enabled) {
      window.__ultronMicRms = 0;
      return;
    }
    let cancelled = false;
    let raf = 0;
    let stream = null;
    let ctx = null;

    const cleanup = () => {
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close().catch(() => {});
      window.__ultronMicRms = 0;
    };

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
        });
        if (cancelled) return cleanup();
        ctx = new AudioContext();
        await ctx.resume();
        if (cancelled) return cleanup();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0;
        ctx.createMediaStreamSource(stream).connect(analyser);
        const buf = new Float32Array(analyser.fftSize);

        const tick = () => {
          analyser.getFloatTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
          const level = Math.min(1, Math.sqrt(sum / buf.length) * 8);
          const prev = window.__ultronMicRms ?? 0;
          // fast attack, slower release
          window.__ultronMicRms = prev + (level - prev) * (level > prev ? 0.6 : 0.12);
          raf = requestAnimationFrame(tick);
        };
        tick();
      } catch (err) {
        cleanup();
        if (!cancelled) {
          useUltron.getState().setMicError(`${err.name}: ${err.message}`);
          useUltron.getState().setMicEnabled(false);
        }
      }
    };

    start();
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [enabled]);
}
