import type { OverlayState } from "@/types/overlay";

export type OverlayMode =
  | "idle"
  | "loading"
  | "listening_wait"
  | "listening_audio"
  | "processing"
  | "done"
  | "warning"
  | "error";

export interface OverlayPalette {
  main: string;
  soft: string;
  dim: string;
}

export interface WaveSet {
  main: string;
  soft: string;
  dim: string;
}

const LISTENING_AUDIO_THRESHOLD = 0.05;

export function deriveMode(state: OverlayState, filteredLevel: number): OverlayMode {
  if (state.listening === "error" || state.processing === "error") return "error";
  if (state.connection === "offline") return "error";
  if (state.target === "not_selected") return "warning";
  if (state.listening === "arming") return "loading";
  if (state.processing === "processing") return "processing";
  if (state.listening === "listening") {
    return filteredLevel >= LISTENING_AUDIO_THRESHOLD ? "listening_audio" : "listening_wait";
  }
  if (state.processing === "done") return "done";
  return "idle";
}

export function bubbleLabel(state: OverlayState, mode: OverlayMode): string | null {
  if (state.target === "not_selected") return "Select a text box";
  if (state.connection === "offline") return "No connection";
  if (mode === "error") return "Try again";
  if (state.message && state.message.trim()) return state.message.trim();
  if (mode === "loading") return "Starting...";
  if (mode === "listening_wait") return "Listening...";
  return null;
}

export function modePalette(mode: OverlayMode): OverlayPalette {
  if (mode === "error") {
    return { main: "#ffd0d7", soft: "#f2a9b5", dim: "#cc8f99" };
  }
  if (mode === "warning") {
    return { main: "#ffe4b3", soft: "#f4cf8f", dim: "#dcb874" };
  }
  if (mode === "processing") {
    return { main: "#fafafa", soft: "#e6e6e6", dim: "#cbcbcb" };
  }
  if (mode === "loading") {
    return { main: "#f6f6f6", soft: "#e2e2e2", dim: "#c7c7c7" };
  }
  return { main: "#ffffff", soft: "#ececec", dim: "#cfcfcf" };
}

interface Point {
  x: number;
  y: number;
}

function wavePoints(mode: OverlayMode, depth: number, phase: number, phaseOffset = 0): Point[] {
  const width = 156;
  const baseline = 41;
  const points: Point[] = [];
  for (let px = 0; px <= width; px += 2) {
    const t = px / width;
    const x = px + 1;
    let y: number;

    if (mode === "listening_audio") {
      const leftPeak = Math.exp(-(((t - 0.22) / 0.12) ** 2));
      const midPeak = Math.exp(-(((t - 0.56) / 0.22) ** 2));
      const rightTail = Math.exp(-(((t - 0.84) / 0.11) ** 2));
      const profile = 1.15 * leftPeak + 0.68 * midPeak + 0.24 * rightTail;
      const shimmer = 1 + 0.06 * Math.sin(phase * 1.4 + t * 8 + phaseOffset);
      const amp = ((5 + 7.5 * depth) * profile + 1.5) * shimmer;
      y = baseline - amp;
    } else if (mode === "loading") {
      const arch = Math.sin(Math.PI * t) ** 0.92;
      const pulse = 1 + 0.05 * Math.sin(phase * 0.8 + phaseOffset);
      y = baseline - (6.8 + 1.2 * depth) * arch * pulse;
    } else if (mode === "processing") {
      const arch = Math.sin(Math.PI * t) ** 0.92;
      const pulse = 1 + 0.05 * Math.sin(phase * 0.6 + phaseOffset);
      y = baseline - (6.8 + 1.2 * depth) * arch * pulse;
    } else if (mode === "listening_wait") {
      const arch = Math.sin(Math.PI * t) ** 0.9;
      const skew = 0.82 + 0.18 * Math.cos((t - 0.5) * Math.PI);
      const breathe = 1 + 0.05 * Math.sin(phase * 0.55 + phaseOffset);
      y = baseline - (6.6 + 2.2 * depth) * arch * skew * breathe;
    } else if (mode === "done") {
      const arch = Math.sin(Math.PI * t);
      y = baseline - (6 + 0.8 * Math.sin(phase * 0.45 + phaseOffset)) * arch;
    } else if (mode === "warning") {
      const arch = Math.sin(Math.PI * t) ** 0.9;
      y = baseline - (6.2 + 0.8 * Math.sin(phase * 1 + phaseOffset)) * arch;
    } else if (mode === "error") {
      const arch = Math.sin(Math.PI * t) ** 0.9;
      y = baseline - (5.8 + 0.6 * Math.sin(phase * 1.7 + phaseOffset)) * arch;
    } else {
      const arch = Math.sin(Math.PI * t) ** 0.9;
      y = baseline - (5.8 + 0.6 * Math.sin(phase * 0.7 + phaseOffset)) * arch;
    }

    points.push({ x, y });
  }
  return points;
}

function smoothPath(points: Point[]): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;

  let d = `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
  for (let i = 1; i < points.length - 1; i += 1) {
    const current = points[i];
    const next = points[i + 1];
    const midX = (current.x + next.x) / 2;
    const midY = (current.y + next.y) / 2;
    d += ` Q ${current.x.toFixed(2)} ${current.y.toFixed(2)} ${midX.toFixed(2)} ${midY.toFixed(2)}`;
  }
  const last = points[points.length - 1];
  d += ` T ${last.x.toFixed(2)} ${last.y.toFixed(2)}`;
  return d;
}

export function buildWaves(mode: OverlayMode, level: number, phase: number): WaveSet {
  const depth = mode === "listening_audio" ? Math.max(0.35, level) : level * 0.45;
  return {
    dim: smoothPath(wavePoints(mode, depth * 0.55, phase, 0.85)),
    soft: smoothPath(wavePoints(mode, depth * 0.78, phase, 0.45)),
    main: smoothPath(wavePoints(mode, depth, phase, 0.1)),
  };
}
