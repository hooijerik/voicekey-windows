import { useEffect, useRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export type LiveWaveformProps = HTMLAttributes<HTMLDivElement> & {
  active?: boolean;
  processing?: boolean;
  level?: number;
  barWidth?: number;
  barHeight?: number;
  barGap?: number;
  barRadius?: number;
  barColor?: string;
  fadeEdges?: boolean;
  fadeWidth?: number;
  height?: string | number;
  sensitivity?: number;
  updateRate?: number;
  mode?: "scrolling" | "static";
  idleLineStyle?: "dotted" | "solid" | "none";
};

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}

function ensureLength(values: number[], count: number): number[] {
  if (count <= 0) return [];
  if (values.length === count) return values;
  if (values.length === 0) return Array.from({ length: count }, () => 0.04);

  if (values.length > count) {
    const center = Math.floor(values.length / 2);
    const half = Math.floor(count / 2);
    const start = Math.max(0, center - half);
    return values.slice(start, start + count);
  }

  const result = values.slice();
  while (result.length < count) {
    result.push(result[result.length - 1] ?? 0.04);
  }
  return result;
}

function buildListeningBars(count: number, inputLevel: number, phase: number): number[] {
  const level = clamp01(inputLevel);
  const center = count / 2;
  const floor = 0.06;
  const bars = new Array<number>(count);

  for (let index = 0; index < count; index += 1) {
    const offset = Math.abs((index - center) / Math.max(1, center));
    const centerWeight = 1 - offset * 0.55;
    const pulseA = Math.sin(phase * 1.9 + index * 0.28) * 0.22;
    const pulseB = Math.cos(phase * 1.1 - index * 0.16) * 0.16;
    const motion = (pulseA + pulseB) * (0.35 + level * 0.75);
    const value = floor + centerWeight * (0.18 + level * 0.92 + motion);
    bars[index] = clamp01(Math.max(floor, value));
  }

  if (count > 2) {
    const mirrored = bars.slice();
    const half = Math.floor(count / 2);
    for (let index = 0; index < half; index += 1) {
      const mirrorIndex = count - 1 - index;
      const avg = (bars[index] + bars[mirrorIndex]) / 2;
      mirrored[index] = avg;
      mirrored[mirrorIndex] = avg;
    }
    return mirrored;
  }

  return bars;
}

function buildProcessingBars(count: number, phase: number): number[] {
  const bars = new Array<number>(count);
  const center = count / 2;

  for (let index = 0; index < count; index += 1) {
    const normalized = (index - center) / Math.max(1, center);
    const centerWeight = 1 - Math.min(1, Math.abs(normalized) * 0.45);
    const waveA = Math.sin(phase * 1.45 + normalized * 3.2) * 0.24;
    const waveB = Math.cos(phase * 0.95 - normalized * 2.4) * 0.18;
    const wave = (0.26 + waveA + waveB) * centerWeight;
    bars[index] = clamp01(Math.max(0.08, wave));
  }

  return bars;
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = Math.max(0, Math.min(radius, Math.min(width, height) / 2));
  if (r <= 0) {
    ctx.fillRect(x, y, width, height);
    return;
  }

  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.fill();
}

export function LiveWaveform({
  active = false,
  processing = false,
  level = 0,
  barWidth = 3,
  barHeight: baseBarHeight = 4,
  barGap = 1,
  barRadius = 1.5,
  barColor,
  fadeEdges = true,
  fadeWidth = 24,
  height = 64,
  sensitivity = 1,
  updateRate = 30,
  mode = "static",
  idleLineStyle = "dotted",
  className,
  ...props
}: LiveWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const barsRef = useRef<number[]>([]);
  const historyRef = useRef<number[]>([]);
  const phaseRef = useRef(0);
  const lastUpdateAtRef = useRef(0);

  const heightStyle = typeof height === "number" ? `${height}px` : height;

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const observer = new ResizeObserver(() => {
      const rect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let rafId = 0;

    const render = (timeMs: number) => {
      const rect = canvas.getBoundingClientRect();
      const width = rect.width;
      const heightPx = rect.height;

      if (width <= 0 || heightPx <= 0) {
        rafId = requestAnimationFrame(render);
        return;
      }

      const step = Math.max(1, barWidth + barGap);
      const barCount = Math.max(6, Math.floor(width / step));
      barsRef.current = ensureLength(barsRef.current, barCount);

      if (timeMs - lastUpdateAtRef.current >= updateRate) {
        lastUpdateAtRef.current = timeMs;

        if (active) {
          phaseRef.current += 0.28;
          const next = buildListeningBars(barCount, clamp01(level * sensitivity), phaseRef.current);
          barsRef.current = next.map((value, index) => {
            const previous = barsRef.current[index] ?? 0.04;
            const blend = value > previous ? 0.34 : 0.20;
            return previous + (value - previous) * blend;
          });
          if (mode === "scrolling") {
            historyRef.current.push(clamp01(level));
            if (historyRef.current.length > barCount) {
              historyRef.current.shift();
            }
          }
        } else if (processing) {
          phaseRef.current += 0.2;
          const next = buildProcessingBars(barCount, phaseRef.current);
          barsRef.current = next.map((value, index) => {
            const previous = barsRef.current[index] ?? 0.04;
            return previous + (value - previous) * 0.30;
          });
          historyRef.current = [];
        } else {
          barsRef.current = barsRef.current.map((value) => value * 0.84);
          historyRef.current = [];
        }
      }

      ctx.clearRect(0, 0, width, heightPx);

      const shouldDrawBars = active || processing || (mode === "scrolling" && historyRef.current.length > 0);
      if (shouldDrawBars) {
        const color = barColor ?? "#ffffff";
        const values = mode === "scrolling" && historyRef.current.length > 0
          ? ensureLength(historyRef.current, barCount)
          : barsRef.current;
        const topInset = 6;
        const bottomInset = 4;
        const maxBarArea = Math.max(baseBarHeight, heightPx - topInset - bottomInset);
        const contentWidth = barCount * barWidth + Math.max(0, barCount - 1) * barGap;
        const xOffset = Math.max(0, (width - contentWidth) / 2);

        for (let index = 0; index < barCount; index += 1) {
          const value = values[index] ?? 0.04;
          const x = xOffset + index * step;
          const barH = Math.max(baseBarHeight, value * maxBarArea);
          const y = heightPx - bottomInset - barH;

          ctx.fillStyle = color;
          ctx.globalAlpha = 0.26 + value * 0.74;
          drawRoundedRect(ctx, x, y, barWidth, barH, barRadius);
        }
      }

      if (fadeEdges && fadeWidth > 0) {
        const fadePercent = Math.min(0.35, fadeWidth / Math.max(1, width));
        const gradient = ctx.createLinearGradient(0, 0, width, 0);
        gradient.addColorStop(0, "rgba(255,255,255,1)");
        gradient.addColorStop(fadePercent, "rgba(255,255,255,0)");
        gradient.addColorStop(1 - fadePercent, "rgba(255,255,255,0)");
        gradient.addColorStop(1, "rgba(255,255,255,1)");
        ctx.globalCompositeOperation = "destination-out";
        ctx.globalAlpha = 1;
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, width, heightPx);
        ctx.globalCompositeOperation = "source-over";
      }

      ctx.globalAlpha = 1;
      rafId = requestAnimationFrame(render);
    };

    rafId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(rafId);
  }, [
    active,
    processing,
    level,
    barWidth,
    baseBarHeight,
    barGap,
    barRadius,
    barColor,
    fadeEdges,
    fadeWidth,
    sensitivity,
    updateRate,
    mode,
  ]);

  return (
    <div
      ref={containerRef}
      className={cn("relative h-full w-full", className)}
      style={{ height: heightStyle }}
      aria-label={
        active ? "Live audio waveform" : processing ? "Processing waveform" : "Waveform idle"
      }
      role="img"
      {...props}
    >
      {!active && !processing && idleLineStyle !== "none" ? (
        <div
          className={cn(
            "absolute left-0 right-0 top-1/2 -translate-y-1/2 border-t border-white/35",
            idleLineStyle === "dotted" ? "border-dotted" : "border-solid",
          )}
        />
      ) : null}
      <canvas ref={canvasRef} className="block h-full w-full" aria-hidden="true" />
    </div>
  );
}
