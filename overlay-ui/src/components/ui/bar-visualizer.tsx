import * as React from "react";
import { useMemo } from "react";

import { cn } from "@/lib/utils";

type AnimationState =
  | "connecting"
  | "initializing"
  | "listening"
  | "speaking"
  | "thinking"
  | undefined;

export type AgentState =
  | "connecting"
  | "initializing"
  | "listening"
  | "speaking"
  | "thinking";

const clamp01 = (value: number) => {
  if (!Number.isFinite(value)) return 0;
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
};

const useBarAnimator = (
  state: AnimationState,
  columns: number,
  interval: number,
): number[] => {
  const indexRef = React.useRef(0);
  const [currentFrame, setCurrentFrame] = React.useState<number[]>([]);
  const animationFrameId = React.useRef<number | null>(null);

  const sequence = useMemo(() => {
    if (state === "thinking" || state === "listening") {
      return generateListeningSequenceBar(columns);
    }
    if (state === "initializing") {
      return generateInitializingSequenceBar(columns);
    }
    if (state === "connecting") {
      return generateConnectingSequenceBar(columns);
    }
    if (state === undefined || state === "speaking") {
      return [new Array(columns).fill(0).map((_, idx) => idx)];
    }
    return [[]];
  }, [state, columns]);

  React.useEffect(() => {
    indexRef.current = 0;
    setCurrentFrame(sequence[0] || []);
  }, [sequence]);

  React.useEffect(() => {
    let startTime = performance.now();

    const animate = (time: DOMHighResTimeStamp) => {
      if (time - startTime >= interval) {
        indexRef.current = (indexRef.current + 1) % sequence.length;
        setCurrentFrame(sequence[indexRef.current] || []);
        startTime = time;
      }

      animationFrameId.current = requestAnimationFrame(animate);
    };

    animationFrameId.current = requestAnimationFrame(animate);

    return () => {
      if (animationFrameId.current !== null) {
        cancelAnimationFrame(animationFrameId.current);
      }
    };
  }, [interval, sequence]);

  return currentFrame;
};

const generateConnectingSequenceBar = (columns: number): number[][] => {
  const seq = [];
  for (let x = 0; x < columns; x += 1) {
    seq.push([x, columns - 1 - x]);
  }
  return seq;
};

const generateListeningSequenceBar = (columns: number): number[][] => {
  const center = Math.floor(columns / 2);
  const noIndex = -1;
  return [[center], [noIndex]];
};

const generateInitializingSequenceBar = (columns: number): number[][] => {
  if (columns <= 1) return [[0], [-1]];
  return [[0, columns - 1], [-1]];
};

function computeBars(state: AgentState | undefined, level: number, barCount: number): number[] {
  const safeLevel = clamp01(level);
  const center = (barCount - 1) / 2;
  const phase = performance.now() / 420;

  return Array.from({ length: barCount }, (_, index) => {
    const distance = Math.abs(index - center) / Math.max(1, center);
    const profile = 1 - distance * 0.62;
    const waveA = (Math.sin(phase * 2.0 + index * 0.58) + 1) / 2;
    const waveB = (Math.cos(phase * 1.25 - index * 0.42) + 1) / 2;

    if (state === "initializing") {
      return 0.02;
    }
    if (state === "connecting") {
      return 0.02;
    }
    if (state === "thinking") {
      return 0.08;
    }
    if (state === "listening") {
      return clamp01(0.12 + profile * 0.18 + waveA * 0.12);
    }
    if (state === "speaking") {
      const boostedInput = clamp01(safeLevel * 2.6);
      const speechEnergy = Math.pow(boostedInput, 0.72);
      const voice = speechEnergy * (0.62 + profile * 0.70);
      const movement = (waveA * 0.20 + waveB * 0.16) * (0.28 + speechEnergy * 0.60);
      return clamp01(0.12 + voice + movement);
    }
    return clamp01(0.06 + profile * 0.05 + waveA * 0.04);
  });
}

export interface BarVisualizerProps extends React.HTMLAttributes<HTMLDivElement> {
  state?: AgentState;
  barCount?: number;
  minHeight?: number;
  maxHeight?: number;
  centerAlign?: boolean;
  level?: number;
  barColor?: string;
  highlightColor?: string;
}

const BarVisualizerComponent = React.forwardRef<HTMLDivElement, BarVisualizerProps>(
  (
    {
      state,
      barCount = 15,
      minHeight = 20,
      maxHeight = 100,
      centerAlign = false,
      level = 0,
      barColor = "rgba(255,255,255,0.65)",
      highlightColor = "#ffffff",
      className,
      style,
      ...props
    },
    ref,
  ) => {
    const [, forceTick] = React.useState(0);

    React.useEffect(() => {
      let raf = 0;
      let last = 0;
      const tick = (time: number) => {
        if (time - last >= 33) {
          forceTick((value) => (value + 1) % 10000);
          last = time;
        }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }, []);

    const volumeBands = useMemo(() => computeBars(state, level, barCount), [state, level, barCount]);

    const highlightedIndices = useBarAnimator(
      state,
      barCount,
      state === "connecting"
        ? 2000 / barCount
        : state === "initializing"
          ? 360
        : state === "thinking"
          ? 150
          : state === "listening"
            ? 500
            : 1000,
    );

    return (
      <div
        ref={ref}
        data-state={state}
        className={cn(
          "relative flex justify-center gap-1.5",
          centerAlign ? "items-center" : "items-end",
          "bg-muted h-32 w-full overflow-hidden rounded-lg p-4",
          className,
        )}
        style={{ ...style }}
        {...props}
      >
        {volumeBands.map((volume, index) => {
          const heightPct = Math.min(maxHeight, Math.max(minHeight, volume * 100 + 5));
          const isHighlighted = highlightedIndices?.includes(index) ?? false;

          return (
            <Bar
              key={index}
              heightPct={heightPct}
              isHighlighted={isHighlighted}
              state={state}
              barColor={barColor}
              highlightColor={highlightColor}
            />
          );
        })}
      </div>
    );
  },
);

const Bar = React.memo<{
  heightPct: number;
  isHighlighted: boolean;
  state?: AgentState;
  barColor: string;
  highlightColor: string;
}>(({ heightPct, isHighlighted, state, barColor, highlightColor }) => (
  <div
    data-highlighted={isHighlighted}
    className={cn(
      "max-w-[12px] min-w-[8px] flex-1 transition-all duration-150",
      "rounded-full",
      state === "thinking" && isHighlighted && "animate-pulse",
    )}
    style={{
      height: `${heightPct}%`,
      backgroundColor: state === "speaking" || isHighlighted ? highlightColor : barColor,
      opacity: state === "initializing" && !isHighlighted ? 0.14 : 1,
      animationDuration: state === "thinking" ? "300ms" : undefined,
    }}
  />
));

Bar.displayName = "Bar";

const BarVisualizer = React.memo(BarVisualizerComponent);

BarVisualizerComponent.displayName = "BarVisualizerComponent";
BarVisualizer.displayName = "BarVisualizer";

export { BarVisualizer };
