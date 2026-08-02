/** In-document report states: queued, analyzing, failed. `Done` lives in Report.tsx. */

interface QueuedProps {
  queuePosition: number | null;
}

export function QueuedBody({ queuePosition }: QueuedProps) {
  // Honest queue text — no fake progress while nothing is happening.
  let line: string;
  if (queuePosition == null) {
    line = "Waiting for a free adjudicator";
  } else if (queuePosition <= 0) {
    line = "Next in line";
  } else {
    line = `${queuePosition} report${queuePosition === 1 ? "" : "s"} filed ahead of yours`;
  }
  return (
    <div className="px-[1.6rem] pb-[2.8rem] pt-[2.4rem] text-center">
      <Clock />
      <h3 className="mb-[0.4rem] font-mono text-[0.95rem] uppercase tracking-[0.06em]">
        In queue
      </h3>
      <p className="m-0 font-serif text-[0.95rem] italic text-ink-soft">{line}</p>
    </div>
  );
}

interface AnalyzingProps {
  analyzedNew: number | null;
  totalNew: number | null;
  progress: number;
}

export function AnalyzingBody({ analyzedNew, totalNew, progress }: AnalyzingProps) {
  // Label from real counters — NEVER a hardcoded /30. A repeat visit may only
  // have 2–3 new games. Fall back to the progress % if counters aren't set yet.
  const hasCounters = totalNew != null && totalNew > 0 && analyzedNew != null;
  const pct = hasCounters
    ? Math.min(100, Math.round((analyzedNew! / totalNew!) * 100))
    : Math.max(0, Math.min(100, progress));
  const label = hasCounters
    ? `${analyzedNew} / ${totalNew} games`
    : progress < 20
      ? "Fetching games"
      : "Preparing report";

  return (
    <div className="px-[1.6rem] pb-[2.8rem] pt-[2.4rem] text-center">
      <h3 className="mb-[0.4rem] font-mono text-[0.95rem] uppercase tracking-[0.06em]">
        Analyzing new games
      </h3>
      <div className="mx-auto mt-[1.4rem] h-[10px] w-full max-w-[300px] overflow-hidden rounded-[5px] border border-ink/15 bg-paper-line">
        <div className="hatch-fill h-full transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-[0.6rem] font-mono text-[0.72rem] text-ink-soft">
        {label}
        <span className="ml-1 inline-block h-[1.1em] w-[7px] animate-blink bg-stamp align-text-bottom" />
      </p>
    </div>
  );
}

interface FailedProps {
  error: string | null;
  onRetry: () => void;
}

export function FailedBody({ error, onRetry }: FailedProps) {
  return (
    <div className="px-[1.6rem] pb-[2.8rem] pt-[2.4rem] text-center">
      <h3 className="mb-[0.4rem] font-mono text-[0.95rem] uppercase tracking-[0.06em] text-stamp">
        Report failed
      </h3>
      <p className="m-0 mb-[1.4rem] font-serif text-[0.95rem] italic text-ink-soft">
        {/* Errors don't apologize and aren't vague. */}
        {error || "Analysis could not be completed."}
      </p>
      <button
        onClick={onRetry}
        className="rounded-[3px] bg-brass px-[1.1rem] py-[0.65rem] font-mono text-[0.72rem] font-semibold uppercase tracking-[0.06em] text-ink shadow-[0_2px_0_#8C7238] transition-transform active:translate-y-px"
      >
        Re-file report
      </button>
    </div>
  );
}

/** CSS analog clock face — an honest "we're waiting" signal, not a progress bar. */
function Clock() {
  return (
    <div className="clock relative mx-auto mb-[1.1rem] h-[52px] w-[52px] rounded-full border-[3px] border-ink">
      <style>{`
        .clock::before, .clock::after {
          content: ""; position: absolute; background: #1E1B16;
          top: 50%; left: 50%; transform-origin: 0 0;
        }
        .clock::before {
          width: 2px; height: 15px;
          transform: rotate(20deg) translate(-1px,-15px);
          animation: tick 4s steps(12) infinite;
        }
        .clock::after {
          width: 2px; height: 10px; background: #B3402E;
          transform: rotate(120deg) translate(-1px,-10px);
          animation: tick 12s steps(12) infinite;
        }
        @keyframes tick { to { transform: rotate(380deg) translate(-1px,-15px); } }
        @media (prefers-reduced-motion: reduce) {
          .clock::before, .clock::after { animation: none !important; }
        }
      `}</style>
    </div>
  );
}
