import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import type { BlunderBucket, OpeningSummary, PhaseErrors, TrendPoint } from "../api";

const INK = "#1E1B16";
const INK_SOFT = "#58524A";
const PAPER_LINE = "#D8CDB0";
const STAMP = "#B3402E";
const STAMP_SOFT = "#C97260";
const BRASS_DIM = "#8C7238";

const AXIS_TICK = { fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fill: INK_SOFT };

// Chart severity glyphs are DERIVED from each game's avg_cp_loss (the only real
// per-game signal the payload exposes) using the §6.3 severity thresholds:
//   blunder ≥ 300cp → ??   |   mistake 150–299cp → ?!   |   quieter games: no mark.
// No brilliant-move glyph exists — do not invent one (design guide honesty rule).
function glyphFor(avgCpLoss: number): "??" | "?!" | null {
  if (avgCpLoss >= 300) return "??";
  if (avgCpLoss >= 150) return "?!";
  return null;
}

const RESULT_COLOR: Record<string, string> = {
  win: BRASS_DIM,
  draw: INK_SOFT,
  loss: STAMP,
};

interface GlyphTip {
  x: number;
  y: number;
  glyph: string;
  detail: string;
}

/** Accuracy trend line with tap-to-reveal severity glyphs on noisy games. */
export function AccuracyTrend({ data }: { data: TrendPoint[] }) {
  const [tip, setTip] = useState<GlyphTip | null>(null);

  if (data.length === 0) {
    return <EmptyChart label="No games to chart yet." />;
  }

  // Custom dot: colored by result; overlays a marker glyph when the game's
  // avg cp loss crosses the mistake/blunder line. Tapping reveals the real data.
  interface DotProps {
    cx?: number;
    cy?: number;
    payload?: TrendPoint;
  }
  const renderDot = (props: DotProps) => {
    const { cx, cy, payload } = props;
    if (cx == null || cy == null || !payload) return <g key="empty" />;
    const glyph = glyphFor(payload.avg_cp_loss);
    const color = RESULT_COLOR[payload.result] ?? INK_SOFT;
    const key = `dot-${payload.game_index}`;
    if (!glyph) {
      return <circle key={key} cx={cx} cy={cy} r={3} fill={color} />;
    }
    const detail = `Game ${payload.game_index + 1} · avg ${payload.avg_cp_loss}cp · ${payload.result}`;
    return (
      <g key={key}>
        <circle cx={cx} cy={cy} r={4} fill={STAMP} />
        <text
          x={cx}
          y={cy - 10}
          textAnchor="middle"
          fill={STAMP}
          style={{ fontFamily: "'Permanent Marker', cursive", fontSize: 13, cursor: "pointer" }}
          onClick={(e) => {
            e.stopPropagation();
            setTip({ x: cx, y: cy - 10, glyph, detail });
          }}
        >
          {glyph}
        </text>
      </g>
    );
  };

  return (
    <div className="relative" onClick={() => setTip(null)}>
      <ResponsiveContainer width="100%" height={150}>
        <LineChart data={data} margin={{ top: 18, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={PAPER_LINE} strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="game_index"
            tick={AXIS_TICK}
            tickFormatter={(v: number) => String(v + 1)}
            stroke={PAPER_LINE}
          />
          <YAxis tick={AXIS_TICK} stroke={PAPER_LINE} width={52} />
          <Line
            type="monotone"
            dataKey="avg_cp_loss"
            stroke={INK}
            strokeWidth={1.6}
            strokeOpacity={0.75}
            dot={renderDot}
            isAnimationActive
            animationDuration={700}
          />
        </LineChart>
      </ResponsiveContainer>
      {tip ? (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-[3px] bg-ink px-[0.6rem] py-[0.4rem] font-mono text-[0.68rem] text-chalk shadow-[0_4px_10px_rgba(0,0,0,0.3)]"
          style={{ left: tip.x, top: tip.y }}
        >
          <span className="text-stamp-soft">{tip.glyph}</span> {tip.detail}
        </div>
      ) : null}
      <div className="mt-[0.3rem] text-right font-mono text-[0.62rem] italic text-ink-soft/60">
        tap a mark for the game
      </div>
    </div>
  );
}

interface PhaseDatum {
  phase: string;
  blunders: number;
  mistakes: number;
  inaccuracies: number;
  total: number;
}

// Severity mapped to red intensity within the six-token palette: heaviest fault
// = stamp red, mistakes = muted stamp, inaccuracies = brass-dim (least alarm).
const SEVERITIES = [
  { key: "blunders", label: "Blunders", color: STAMP },
  { key: "mistakes", label: "Mistakes", color: STAMP_SOFT },
  { key: "inaccuracies", label: "Inaccuracies", color: BRASS_DIM },
] as const;

/** Errors-by-phase as stacked segmented bars: one segment per severity, scaled
 * to the largest phase total. Legend maps color → severity; tap/hover a segment
 * to reveal its count. */
export function ErrorsByPhase({ data }: { data: Record<string, PhaseErrors> }) {
  const order = ["opening", "middlegame", "endgame"];
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  const rows: PhaseDatum[] = order
    .filter((p) => data[p])
    .map((p) => {
      const e = data[p];
      return {
        phase: p,
        blunders: e.blunders,
        mistakes: e.mistakes,
        inaccuracies: e.inaccuracies,
        total: e.blunders + e.mistakes + e.inaccuracies,
      };
    });
  const max = Math.max(1, ...rows.map((r) => r.total));

  if (rows.length === 0) return <EmptyChart label="No phase data yet." />;

  return (
    <div className="relative" onClick={() => setTip(null)}>
      {/* Legend */}
      <div className="mb-[0.9rem] flex flex-wrap gap-x-[1rem] gap-y-1 font-mono text-[0.62rem] text-ink-soft">
        {SEVERITIES.map((s) => (
          <span key={s.key} className="flex items-center gap-[0.35rem]">
            <span
              className="inline-block h-[9px] w-[9px] rounded-[2px]"
              style={{ background: s.color }}
              aria-hidden="true"
            />
            {s.label}
          </span>
        ))}
      </div>

      <div className="flex flex-col gap-[0.7rem]">
        {rows.map((r) => (
          <div key={r.phase} className="flex items-center gap-[0.8rem] font-mono text-[0.7rem]">
            <span className="w-[88px] flex-shrink-0 capitalize text-ink-soft">{r.phase}</span>
            <div className="flex h-2 flex-1 overflow-hidden rounded-full bg-paper-line">
              {SEVERITIES.map((s) => {
                const count = r[s.key];
                if (count === 0) return null;
                return (
                  <div
                    key={s.key}
                    className="h-full cursor-pointer"
                    style={{ width: `${(count / max) * 100}%`, background: s.color }}
                    onClick={(e) => {
                      e.stopPropagation();
                      const host = (e.currentTarget.offsetParent as HTMLElement) ?? e.currentTarget;
                      const hostRect = host.getBoundingClientRect();
                      const segRect = e.currentTarget.getBoundingClientRect();
                      setTip({
                        x: segRect.left - hostRect.left + segRect.width / 2,
                        y: segRect.top - hostRect.top,
                        text: `${r.phase} · ${count} ${s.label.toLowerCase()}`,
                      });
                    }}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {tip ? (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-[3px] bg-ink px-[0.6rem] py-[0.4rem] font-mono text-[0.68rem] capitalize text-chalk shadow-[0_4px_10px_rgba(0,0,0,0.3)]"
          style={{ left: tip.x, top: tip.y }}
        >
          {tip.text}
        </div>
      ) : null}

      <div className="mt-[0.5rem] text-right font-mono text-[0.62rem] italic text-ink-soft/60">
        tap a segment for the count
      </div>
    </div>
  );
}

/** Blunder distribution by move-bucket bar chart (stamp red). */
export function BlunderDistribution({ data }: { data: BlunderBucket[] }) {
  if (data.length === 0 || data.every((d) => d.count === 0)) {
    return <EmptyChart label="No blunders bucketed yet." />;
  }
  return (
    <ResponsiveContainer width="100%" height={140}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid stroke={PAPER_LINE} vertical={false} />
        <XAxis dataKey="move_bucket" tick={AXIS_TICK} stroke={PAPER_LINE} />
        <YAxis tick={AXIS_TICK} stroke={PAPER_LINE} width={52} allowDecimals={false} />
        <Bar dataKey="count" isAnimationActive animationDuration={600}>
          {data.map((d) => (
            <Cell key={d.move_bucket} fill={STAMP} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Top openings ranked list with score bars. */
export function TopOpenings({ data }: { data: OpeningSummary[] }) {
  if (data.length === 0) return <EmptyChart label="No openings logged yet." />;
  return (
    <div>
      {data.map((o) => (
        <div
          key={`${o.name}-${o.eco}`}
          className="flex items-baseline justify-between border-b border-paper-line py-[0.55rem] font-mono text-[0.76rem] last:border-b-0"
        >
          <span className="text-ink">
            {o.name} <span className="text-[0.64rem] text-ink-soft">{o.eco}</span>{" "}
            <span className="text-[0.64rem] text-ink-soft">· {o.games}g</span>
          </span>
          <span className="font-semibold text-stamp">{scorePct(o.score_pct)}%</span>
        </div>
      ))}
    </div>
  );
}

// Backend emits score_pct already as a 0–100 percentage (not a 0–1 fraction),
// so render it directly, clamped defensively.
function scorePct(value: number): number {
  return Math.round(Math.max(0, Math.min(100, value)));
}

function EmptyChart({ label }: { label: string }) {
  return (
    <p className="py-6 text-center font-serif text-[0.8rem] italic text-ink-soft">{label}</p>
  );
}

/** Shared skeleton block for chart sections while a report loads. */
export function ChartSkeleton({ height = 140 }: { height?: number }) {
  return (
    <div className="animate-pulse rounded-sm bg-paper-line/50" style={{ height }} />
  );
}
