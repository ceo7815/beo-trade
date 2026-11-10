import { archiveRangeLabel, ARCHIVE_BADGE } from "@/lib/archiveView";
import { money, num, signedMoney, signedPct } from "@/lib/format";

export type ArchiveResult = {
  capital_basis_usd?: string | null;
  final_equity?: string | null;
  pnl?: string | null;
  return_pct?: string | null;
  start?: string | null;
  end?: string | null;
  sessions?: number;
  read_only?: boolean;
};

export function ArchiveBadge() {
  return <span className="archive-badge">{ARCHIVE_BADGE}</span>;
}

export function ArchiveSummary({ result }: { result?: ArchiveResult | null }) {
  const pnl = num(result?.pnl);
  const pct = num(result?.return_pct);
  const basis = num(result?.capital_basis_usd);
  const range = archiveRangeLabel(result?.start, result?.end);
  return (
    <div className="archive-mark">
      <ArchiveBadge />
      {pnl === null ? null : (
        <p>
          <b className={`num ${pnl > 0 ? "up" : pnl < 0 ? "down" : ""}`}>{signedMoney(pnl, 2)}</b>
          {pct === null ? null : <span className="num">{signedPct(pct, 0)}</span>}
          {basis === null ? null : <span>מ-{money(basis, 0)}</span>}
          {range ? <span>{range}</span> : null}
        </p>
      )}
    </div>
  );
}
