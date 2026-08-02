import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import Header from "../components/Header";
import { ApiError, createReport, getFeatured } from "../api";

// Real severity glyphs only (?? blunder / ?! mistake). No brilliant-move glyph:
// the design rests on structure encoding real data. Featured tabs show the
// heavier ?? mark as the case's filed glyph.
const CASE_GLYPH = "??";

export default function Home() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const featured = useQuery({
    queryKey: ["featured"],
    queryFn: getFeatured,
  });

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = username.trim();
    if (!trimmed) {
      setError("Enter a Lichess username to file a report.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const res = await createReport(trimmed);
      // Navigate with the report id so polling resumes regardless of state: a
      // cached "done" resolves instantly, "queued"/"analyzing" (including a
      // report already in progress from an earlier submit) shows live progress.
      navigate(`/report/${encodeURIComponent(trimmed)}?rid=${encodeURIComponent(res.report_id)}`);
    } catch (err) {
      if (err instanceof ApiError) {
        // Document-voice copy: errors don't apologize and aren't vague.
        if (err.status === 404) {
          setError(`No Lichess account found for “${trimmed}”.`);
        } else if (err.status === 429) {
          setError(err.message || "Report limit reached; try again in an hour.");
        } else {
          setError(err.message);
        }
      } else {
        setError("Request failed; try again later.");
      }
      setSubmitting(false);
    }
  }

  return (
    <>
      <Header />
      <main className="mx-auto max-w-[620px] px-[1.4rem] pb-16 pt-[3.4rem] text-center">
        <div className="mb-[0.9rem] font-mono text-[0.7rem] uppercase tracking-[0.14em] text-brass">
          Free · Lichess only
        </div>
        <h1 className="mb-[0.6rem] font-serif text-[2rem] font-bold leading-[1.25]">
          Find out what&rsquo;s <em className="italic text-brass">wrong</em> with your chess.
        </h1>
        <p className="mb-[2.2rem] font-serif text-base italic text-chalk/65">
          Not &ldquo;play more solid.&rdquo; The specific line, the specific phase, the specific
          number.
        </p>

        {/* Filing form — paper document card */}
        <form
          onSubmit={onSubmit}
          className="mb-[2.6rem] rounded-sm bg-paper p-[1.4rem] text-left text-ink shadow-[0_1px_0_rgba(255,255,255,0.4)_inset,0_18px_40px_-12px_rgba(0,0,0,0.45),0_2px_0_#8C7238]"
        >
          <div className="mb-2 font-mono text-[0.68rem] uppercase tracking-[0.1em] text-ink-soft">
            File a report
          </div>
          <div className="flex items-center gap-[0.7rem] border-b border-dashed border-paper-line pb-[0.9rem]">
            <span className="font-mono text-ink-soft">lichess.org/@/</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="your username"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              aria-label="Lichess username"
              className="flex-1 border-none bg-transparent py-[0.3rem] font-mono text-base text-ink outline-none placeholder:text-ink-soft/50"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="mt-4 w-full rounded-[3px] bg-brass p-3 font-mono text-[0.78rem] font-semibold uppercase tracking-[0.06em] text-ink shadow-[0_2px_0_#8C7238] transition-transform active:translate-y-px disabled:cursor-not-allowed disabled:opacity-70"
          >
            {submitting ? "Filing…" : "Analyze last 30 games"}
          </button>
          {error ? (
            <p className="mt-[0.7rem] font-mono text-[0.68rem] text-stamp">{error}</p>
          ) : (
            <p className="mt-[0.7rem] font-mono text-[0.62rem] text-chalk/40">
              2 reports / hour per visitor · results are public and shareable
            </p>
          )}
        </form>

        {/* Featured reports — filed case tabs */}
        <div className="mb-[0.9rem] flex items-center gap-[0.6rem] text-left font-mono text-[0.68rem] uppercase tracking-[0.12em] text-chalk/50">
          On file
          <span className="h-px flex-1 bg-chalk/15" aria-hidden="true" />
        </div>
        <FeaturedDrawer
          items={featured.data?.featured}
          isLoading={featured.isLoading}
          isError={featured.isError}
        />
      </main>
    </>
  );
}

interface FeaturedDrawerProps {
  items: { username: string; report_id: string }[] | undefined;
  isLoading: boolean;
  isError: boolean;
}

function FeaturedDrawer({ items, isLoading, isError }: FeaturedDrawerProps) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-[0.55rem]">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-[3.1rem] animate-pulse rounded-sm bg-paper/40 shadow-[0_4px_0_#8C7238]"
          />
        ))}
      </div>
    );
  }

  // Empty state is an invitation, not a dead end — the filing form is right above.
  if (isError || !items || items.length === 0) {
    return (
      <p className="rounded-sm border border-dashed border-chalk/20 px-4 py-6 text-center font-serif text-[0.85rem] italic text-chalk/50">
        No reports on file yet. File the first one above.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-[0.55rem]">
      {items.map((item, index) => (
        <a
          key={item.report_id}
          href={`/report/${encodeURIComponent(item.username)}?rid=${encodeURIComponent(item.report_id)}`}
          className="relative flex items-center gap-[0.9rem] rounded-sm bg-paper px-4 py-[0.85rem] text-left text-ink no-underline shadow-[0_4px_0_#8C7238,0_10px_20px_-8px_rgba(0,0,0,0.4)] transition-transform duration-[120ms] ease-out hover:translate-x-[3px]"
        >
          <span className="w-[46px] flex-shrink-0 font-mono text-[0.65rem] text-ink-soft">
            No.{String(index + 1).padStart(4, "0")}
          </span>
          <span className="flex-1 font-mono text-[0.88rem] font-semibold">{item.username}</span>
          <span
            className="font-marker text-[1.1rem] text-stamp"
            style={{ transform: "rotate(-6deg)" }}
            aria-hidden="true"
          >
            {CASE_GLYPH}
          </span>
        </a>
      ))}
    </div>
  );
}
