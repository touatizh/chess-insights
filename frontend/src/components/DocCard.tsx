import { ReactNode } from "react";

/** The single bordered "document" card: paper surface, deckle top edge, brass
 * bottom border shadow. Every report state renders inside one of these. */
export default function DocCard({ children }: { children: ReactNode }) {
  return (
    <div className="deckle-edge relative overflow-hidden rounded-sm bg-paper text-ink shadow-[0_1px_0_rgba(255,255,255,0.4)_inset,0_18px_40px_-12px_rgba(0,0,0,0.45),0_2px_0_#8C7238]">
      {children}
    </div>
  );
}

interface DocHeaderProps {
  username: string;
  caseNumber: string; // zero-padded, e.g. "0042"
  gamesLogged?: number | null;
  filed?: string | null; // formatted date
  engine?: string;
}

/** Scoresheet-style header: kicker + case number, username title, form fields. */
export function DocHeader({
  username,
  caseNumber,
  gamesLogged,
  filed,
  engine = "Stockfish",
}: DocHeaderProps) {
  return (
    <div className="rule-dashed px-[1.6rem] pb-[1.1rem] pt-[1.6rem]">
      <div className="flex justify-between font-mono text-[0.68rem] uppercase tracking-[0.12em] text-ink-soft">
        <span>Adjudication Report</span>
        <span>No. {caseNumber}</span>
      </div>
      <h1 className="my-[0.35rem] mb-[0.9rem] font-serif text-[1.55rem] font-bold tracking-[-0.01em]">
        {username}
      </h1>
      <div className="flex flex-wrap gap-x-[1.6rem] gap-y-1 font-mono text-[0.74rem] text-ink-soft">
        {gamesLogged != null ? (
          <span>
            Games logged <b className="font-semibold text-ink">{gamesLogged}</b>
          </span>
        ) : null}
        {filed ? (
          <span>
            Filed <b className="font-semibold text-ink">{filed}</b>
          </span>
        ) : null}
        <span>
          Engine <b className="font-semibold text-ink">{engine}</b>
        </span>
      </div>
    </div>
  );
}

/** A dashed-ruled section with a mono title and trailing hairline. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rule-dashed px-[1.6rem] py-[1.5rem] last:border-b-0">
      <div className="mb-4 flex items-center gap-2 font-mono text-[0.68rem] uppercase tracking-[0.1em] text-ink-soft">
        {title}
        <span className="h-px flex-1 bg-paper-line" aria-hidden="true" />
      </div>
      {children}
    </div>
  );
}
