import { Link } from "react-router-dom";

interface HeaderProps {
  /** Optional right-side meta, e.g. "touatizh · lichess.org". */
  meta?: string;
}

/** Mono wordmark header bar with the ♞ brass knight (design mockups). */
export default function Header({ meta }: HeaderProps) {
  return (
    <header className="flex items-center justify-between border-b border-chalk/15 px-6 py-[1.1rem]">
      <Link
        to="/"
        className="flex items-center gap-2 font-mono text-[0.82rem] font-bold uppercase tracking-[0.14em] text-chalk no-underline"
      >
        <span className="text-[1.1rem] text-brass" aria-hidden="true">
          ♞
        </span>
        Chess Insights
      </Link>
      {meta ? (
        <div className="font-mono text-[0.72rem] tracking-[0.04em] text-chalk/55">{meta}</div>
      ) : null}
    </header>
  );
}
