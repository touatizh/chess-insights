import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import Header from "../components/Header";
import DocCard, { DocHeader, Section } from "../components/DocCard";
import {
  AccuracyTrend,
  BlunderDistribution,
  ChartSkeleton,
  ErrorsByPhase,
  TopOpenings,
} from "../components/charts";
import { AnalyzingBody, FailedBody, QueuedBody } from "../components/states";
import { useDocumentMeta } from "../useDocumentMeta";
import {
  createReport,
  getReportByUsername,
  getReportStatus,
  type ReportPayload,
  type ReportStatusResponse,
} from "../api";

export default function Report() {
  const { username = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rid = searchParams.get("rid");

  // --- Polling path: an active report id (rid) drives 2s polling until settled.
  const statusQuery = useQuery({
    queryKey: ["report-status", rid],
    queryFn: () => getReportStatus(rid!),
    enabled: rid != null,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "failed" ? false : 2000;
    },
  });

  // --- Fallback path: no rid → load the latest done report by username.
  const byUsernameQuery = useQuery({
    queryKey: ["report-by-username", username],
    queryFn: () => getReportByUsername(username),
    enabled: rid == null,
  });

  async function handleRetry() {
    // Re-file: the backend returns the report id whether it starts a new job or
    // one is already in progress, so we always resume polling on that id.
    const res = await createReport(username);
    setSearchParams({ rid: res.report_id });
  }

  const status: ReportStatusResponse | undefined = statusQuery.data;
  const displayName = status?.username || username;
  const caseNumber = caseNo(rid);

  return (
    <div className="pb-16">
      <Header meta={`${displayName} · lichess.org`} />
      <main className="mx-auto mt-7 max-w-[640px] px-[1.25rem]">
        <DocCard>
          {rid != null ? (
            <PollingView
              status={status}
              isLoading={statusQuery.isLoading}
              onRetry={handleRetry}
              username={displayName}
              caseNumber={caseNumber}
              reportId={rid}
            />
          ) : (
            <ByUsernameView
              payload={byUsernameQuery.data?.payload ?? null}
              isLoading={byUsernameQuery.isLoading}
              isError={byUsernameQuery.isError}
              username={username}
            />
          )}
        </DocCard>
      </main>
    </div>
  );
}

/** Zero-padded case number from the real Report.id (design guide: No. 0042). */
function caseNo(rid: string | null): string {
  if (!rid) return "····";
  const n = Number(rid);
  if (Number.isNaN(n)) return "····";
  return String(n).padStart(4, "0");
}

// --------------------------------------------------------------------------- //
// Polling view (rid present)
// --------------------------------------------------------------------------- //

interface PollingViewProps {
  status: ReportStatusResponse | undefined;
  isLoading: boolean;
  onRetry: () => void;
  username: string;
  caseNumber: string;
  reportId: string | null;
}

function PollingView({
  status,
  isLoading,
  onRetry,
  username,
  caseNumber,
  reportId,
}: PollingViewProps) {
  if (isLoading || !status) {
    return (
      <>
        <DocHeader username={username} caseNumber={caseNumber} />
        <QueuedBody queuePosition={null} />
      </>
    );
  }

  if (status.status === "done" && status.payload) {
    return <DoneReport payload={status.payload} caseNumber={caseNumber} reportId={reportId} />;
  }

  return (
    <>
      <DocHeader username={status.username || username} caseNumber={caseNumber} />
      {status.status === "failed" ? (
        <FailedBody error={status.error} onRetry={onRetry} />
      ) : status.status === "analyzing" || status.status === "fetching" ? (
        <AnalyzingBody
          analyzedNew={status.analyzed_new}
          totalNew={status.total_new}
          progress={status.progress}
        />
      ) : (
        <QueuedBody queuePosition={status.queue_position} />
      )}
    </>
  );
}

// --------------------------------------------------------------------------- //
// By-username view (no rid) — latest done report or empty state
// --------------------------------------------------------------------------- //

interface ByUsernameViewProps {
  payload: ReportPayload | null;
  isLoading: boolean;
  isError: boolean;
  username: string;
}

function ByUsernameView({ payload, isLoading, isError, username }: ByUsernameViewProps) {
  if (isLoading) {
    return (
      <>
        <DocHeader username={username} caseNumber="····" />
        <Section title="Loading">
          <ChartSkeleton height={120} />
        </Section>
      </>
    );
  }

  if (isError || !payload) {
    // Empty state as invitation: no report on file → point back to filing.
    return (
      <>
        <DocHeader username={username} caseNumber="····" />
        <div className="px-[1.6rem] pb-[2.8rem] pt-[2.4rem] text-center">
          <h3 className="mb-[0.4rem] font-mono text-[0.95rem] uppercase tracking-[0.06em]">
            No report on file
          </h3>
          <p className="m-0 mb-[1.4rem] font-serif text-[0.95rem] italic text-ink-soft">
            Nothing has been filed for {username} yet.
          </p>
          <a
            href="/"
            className="inline-block rounded-[3px] bg-brass px-[1.1rem] py-[0.65rem] font-mono text-[0.72rem] font-semibold uppercase tracking-[0.06em] text-ink no-underline shadow-[0_2px_0_#8C7238]"
          >
            File a report
          </a>
        </div>
      </>
    );
  }

  return <DoneReport payload={payload} caseNumber="····" reportId={null} />;
}

// --------------------------------------------------------------------------- //
// Done report body — the full layout
// --------------------------------------------------------------------------- //

// Verdict glyph: the heavier ?? blunder mark stamps in on mount.
const VERDICT_GLYPH = "??";

function DoneReport({
  payload,
  caseNumber,
  reportId,
}: {
  payload: ReportPayload;
  caseNumber: string;
  reportId: string | null;
}) {
  const [copied, setCopied] = useState(false);

  const filed = formatDate(payload.generated_at);
  const whitePct = winPct(payload.win_rate["white"]);
  const blackPct = winPct(payload.win_rate["black"]);
  const totalBlunders = sumBlunders(payload);
  const avgLoss = overallAvgLoss(payload.accuracy_trend);

  // Per-report share card: only when we know the numeric report id (polling
  // path). The og:image is an absolute URL so scrapers can fetch it.
  useDocumentMeta({
    title: `${payload.username} — Adjudication Report`,
    description: payload.signature_leak.headline,
    url: `${window.location.origin}/report/${encodeURIComponent(payload.username)}`,
    image: reportId
      ? `${window.location.origin}/api/reports/${encodeURIComponent(reportId)}/og-image`
      : undefined,
  });

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(
        `${window.location.origin}/report/${encodeURIComponent(payload.username)}`,
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <>
      <DocHeader
        username={payload.username}
        caseNumber={caseNumber}
        gamesLogged={payload.games_analyzed}
        filed={filed}
      />

      {/* Signature leak — visually dominant verdict with stamp-in glyph. */}
      <div className="rule-dashed flex flex-col items-start gap-[1.1rem] px-[1.6rem] py-[1.8rem] sm:flex-row">
        <div
          className="flex-shrink-0 animate-stamp-in font-marker text-[2.6rem] leading-none text-stamp [filter:drop-shadow(0_1px_0_rgba(0,0,0,0.15))]"
          style={{ transform: "rotate(-6deg)" }}
          aria-hidden="true"
        >
          {VERDICT_GLYPH}
        </div>
        <div className="font-serif text-[1.18rem] italic leading-[1.4]">
          {payload.signature_leak.headline}
          <div className="mt-[0.5rem] font-mono text-[0.72rem] not-italic text-ink-soft">
            {payload.signature_leak.detail}
          </div>
        </div>
      </div>

      {/* Stat chips */}
      <div className="grid grid-cols-2 gap-px border-b border-dashed border-paper-line bg-paper-line sm:grid-cols-4">
        <Chip value={`${whitePct}%`} label="as White" />
        <Chip value={`${blackPct}%`} label="as Black" />
        <Chip value={String(totalBlunders)} label="Blunders" red />
        <Chip value={`${avgLoss}cp`} label="Avg loss" />
      </div>

      <Section title="Accuracy Log">
        <AccuracyTrend data={payload.accuracy_trend} />
      </Section>

      <Section title="Errors by Phase">
        <ErrorsByPhase data={payload.errors_by_phase} />
      </Section>

      <Section title="Blunders by Move">
        <BlunderDistribution data={payload.blunder_distribution_by_move} />
      </Section>

      <Section title="Top Openings">
        <TopOpenings data={payload.top_openings} />
      </Section>

      <div className="flex items-center justify-between px-[1.6rem] py-[1.3rem]">
        <button
          onClick={copyLink}
          className="rounded-[3px] bg-brass px-[1.1rem] py-[0.65rem] font-mono text-[0.72rem] font-semibold uppercase tracking-[0.06em] text-ink shadow-[0_2px_0_#8C7238] transition-transform hover:-translate-y-px active:translate-y-px"
        >
          {copied ? "Link copied" : "Copy report link"}
        </button>
        <span className="font-mono text-[0.6rem] text-ink-soft/60">generated by chess-insights</span>
      </div>
    </>
  );
}

function Chip({ value, label, red }: { value: string; label: string; red?: boolean }) {
  return (
    <div className="bg-paper px-[0.6rem] py-[0.85rem] text-center">
      <div
        className={`font-mono text-[1.15rem] font-bold ${red ? "text-stamp" : "text-ink"}`}
      >
        {value}
      </div>
      <div className="mt-[0.2rem] font-mono text-[0.58rem] uppercase tracking-[0.04em] text-ink-soft">
        {label}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Derivations from the payload
// --------------------------------------------------------------------------- //

function winPct(c: { win: number; loss: number; draw: number } | undefined): number {
  if (!c) return 0;
  const total = c.win + c.loss + c.draw;
  if (total === 0) return 0;
  return Math.round((c.win / total) * 100);
}

function sumBlunders(payload: ReportPayload): number {
  return Object.values(payload.errors_by_phase).reduce((acc, p) => acc + p.blunders, 0);
}

function overallAvgLoss(trend: ReportPayload["accuracy_trend"]): number {
  if (trend.length === 0) return 0;
  const sum = trend.reduce((acc, t) => acc + t.avg_cp_loss, 0);
  return Math.round(sum / trend.length);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
