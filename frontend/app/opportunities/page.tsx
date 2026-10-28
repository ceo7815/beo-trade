"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { copy } from "@/lib/copy";
import { sessionStamp } from "@/lib/format";

type Card = {
  recommendation_id: string;
  timestamp: string;
  underlying: string;
  option_symbol: string;
  call_put: string;
  strike: string;
  expiration: string;
  option_price: string;
  quantity: number;
  why: string[];
};

type Page = { items: Card[]; next_cursor: string | null; limit: number };

export default function OpportunitiesPage() {
  const [items, setItems] = useState<Card[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet<Page>("/api/v1/recommendations?limit=40")
      .then((body) => {
        if (!alive) return;
        setItems(body.items);
        setCursor(body.next_cursor);
        setFailed(false);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const loadMore = () => {
    if (!cursor || busy) return;
    setBusy(true);
    apiGet<Page>(`/api/v1/recommendations?limit=40&cursor=${encodeURIComponent(cursor)}`)
      .then((body) => {
        setItems((current) => [...(current ?? []), ...body.items]);
        setCursor(body.next_cursor);
        setFailed(false);
      })
      .catch(() => setFailed(true))
      .finally(() => setBusy(false));
  };

  return (
    <Shell>
      <section className="engine book">
        <header className="ops-head">
          <div>
            <h1>{copy.screens.recommendations}</h1>
            <p>BUY · PAPER</p>
          </div>
          <span className={`ops-pip ${failed || items === null ? "warn" : "on"}`}>
            <i />
            {failed ? "אין נתונים" : items ? "המסד" : "טוען"}
          </span>
        </header>
        <div className="desk-blotter">
          {items === null ? (
            <p className="desk-empty">{failed ? "אין נתונים" : "טוען"}</p>
          ) : items.length === 0 ? (
            <p className="desk-empty">אין המלצות BUY בעותק הזה</p>
          ) : (
            items.map((item) => (
              <Link key={item.recommendation_id} href={`/opportunity/${item.recommendation_id}`} className="decision-row">
                <strong className="verdict yes">כן</strong>
                <b>
                  {item.underlying || "—"} <small>{item.call_put} {item.strike}</small>
                </b>
                <span>
                  <em className="num">{sessionStamp(item.timestamp)}</em>
                  {item.why.length ? item.why.join(" · ") : item.option_symbol}
                </span>
              </Link>
            ))
          )}
        </div>
        {cursor ? (
          <button type="button" className="plain-row" onClick={loadMore} disabled={busy}>
            <b>{busy ? "טוען" : "טען עוד"}</b>
            <strong>{items ? String(items.length) : ""}</strong>
          </button>
        ) : null}
      </section>
    </Shell>
  );
}
