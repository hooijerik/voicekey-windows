import { BarVisualizer, type AgentState } from "@/components/ui/bar-visualizer";
import { bubbleLabel, deriveMode, modePalette } from "@/lib/overlay";
import type { OverlayState } from "@/types/overlay";

interface VoiceOverlayProps {
  state: OverlayState;
}

export function VoiceOverlay({ state }: VoiceOverlayProps) {
  const level = Math.max(0, Math.min(1, Number.isFinite(state.level) ? state.level : 0));
  const mode = deriveMode(state, level);
  const palette = modePalette(mode);
  const bubbleText = bubbleLabel(state, mode);
  const isLoading = mode === "loading";
  const isProcessing = state.processing === "processing";
  const isListening = state.listening === "listening" && !isProcessing;
  const waveformLevel = isListening ? Math.max(level, 0.012) : level;
  const idleLineStyle = (isListening || isLoading) ? "none" : mode === "listening_wait" ? "solid" : "dotted";
  const bubbleKey = `${mode}|${state.message ?? ""}|${state.target}|${state.connection}|${state.listening}|${state.processing}`;
  const visualizerState: AgentState | undefined = isLoading
    ? "initializing"
    : isProcessing
      ? "thinking"
      : isListening
        ? level >= 0.03
          ? "speaking"
          : "listening"
        : mode === "listening_wait"
          ? "listening"
          : undefined;
  const minBarHeight = isLoading ? 4 : 12;

  if (!state.visible) return null;

  return (
    <div className="pointer-events-none relative select-none" style={{ width: 194, height: 126 }}>
      {bubbleText ? (
        <div
          key={bubbleKey}
          className="absolute left-1/2 top-[14px] z-10 -translate-x-1/2 [animation:voicekey-bubble-hide_2s_ease-out_forwards]"
        >
          <div className="relative rounded-[7px] border border-[#75757566] bg-[#2c2c2cf2] px-3 py-[9px] text-[14px] leading-5 text-white shadow-[0_8px_16px_rgba(0,0,0,0.16)] backdrop-blur-[22px]">
            {bubbleText}
            <span className="absolute left-1/2 top-full h-0 w-0 -translate-x-1/2 border-l-[7px] border-r-[7px] border-t-[7px] border-l-transparent border-r-transparent border-t-[#2c2c2cf2]" />
          </div>
        </div>
      ) : null}

      <div className="absolute bottom-4 left-1/2 h-[47px] w-[160px] -translate-x-1/2">
        <div className="absolute inset-0 rounded-[7px] bg-[#2e2e2eeb] shadow-[0_2px_6px_rgba(0,0,0,0.15),0_9px_18px_rgba(0,0,0,0.19)] backdrop-blur-[30px]" />
        <div className="absolute inset-0 rounded-[7px] border border-[#75757566]" />

        <div className="absolute inset-[1px] overflow-hidden rounded-[6px]">
          <BarVisualizer
            state={visualizerState}
            level={waveformLevel}
            barCount={15}
            minHeight={minBarHeight}
            maxHeight={96}
            centerAlign={false}
            barColor="rgba(255,255,255,0.24)"
            highlightColor={palette.main}
            className="h-full w-full rounded-none bg-transparent px-3 py-2"
          />
          {!isListening && !isProcessing && !isLoading && idleLineStyle !== "none" ? (
            <div
              className={`absolute left-3 right-3 top-1/2 -translate-y-1/2 border-t border-white/35 ${idleLineStyle === "dotted" ? "border-dotted" : "border-solid"}`}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
