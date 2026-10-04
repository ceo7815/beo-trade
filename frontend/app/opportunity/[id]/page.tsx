"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Shell } from "@/components/Shell";
import { apiGet, type BuyDetail } from "@/lib/api";

export default function OpportunityPage() {
  const params = useParams<{ id: string }>();
  const [item, setItem] = useState<BuyDetail | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    apiGet<BuyDetail>(`/api/v1/recommendations/${params.id}`)
      .then(setItem)
      .catch(() => setMessage("ההמלצה לא נמצאה."));
  }, [params.id]);

  if (!item) {
    return (
      <Shell>
        <p className="text-mist">{message || "טוען..."}</p>
      </Shell>
    );
  }

  const maxMove = Math.max(...item.scenarios.map((point) => Math.abs(point.change_percent)), 1);

  return (
    <Shell>
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <aside className="space-y-4 rounded-xl border border-line bg-panel p-4">
          <div className="text-mint">BUY</div>
          <h1 className="text-xl">
            {item.underlying} {item.call_put === "CALL" ? "Call" : "Put"} {item.strike}
          </h1>
          <p className="text-sm text-mist">{item.thesis}</p>
          <div>
            <div className="text-xs text-mist">זרז</div>
            <p className="text-sm">{item.catalyst}</p>
          </div>
          <div>
            <div className="text-xs text-rose">סיכון מרכזי</div>
            <p className="text-sm">{item.risk}</p>
          </div>
          <div>
            <div className="text-xs text-amber">תנאי ביטול</div>
            <p className="text-sm">{item.invalidation}</p>
          </div>
          <p className="text-sm text-[var(--muted)]">המערכת מבצעת את ההחלטה בעצמה. המסך הזה לצפייה בלבד.</p>
          {message ? <p className="text-sm text-mist">{message}</p> : null}
        </aside>
        <section className="space-y-4">
          <div className="rounded-xl border border-line bg-panel p-4">
            <h2 className="mb-3 text-sm text-mist">תרחישי מחיר האופציה</h2>
            <div className="space-y-2">
              {item.scenarios.map((point) => (
                <div key={point.move_percent} className="grid grid-cols-[4.5rem_1fr_4rem] items-center gap-3 text-sm">
                  <span className="num text-mist">{point.move_percent}%</span>
                  <div className="h-2 rounded bg-ink">
                    <div
                      className={`h-2 rounded ${point.change_percent >= 0 ? "bg-mint" : "bg-rose"}`}
                      style={{ width: `${Math.min(Math.abs(point.change_percent) / maxMove, 1) * 100}%` }}
                    />
                  </div>
                  <span className="num">${point.option_price.toFixed(2)}</span>
                </div>
              ))}
            </div>
            <p className="mt-4 text-sm text-mist">גרף מחיר האופציה בזמן אמת יופיע אחרי חיבור ספק הנתונים.</p>
          </div>
          <div className="grid gap-3 rounded-xl border border-line bg-panel p-4 sm:grid-cols-4">
            <Metric label="Bid" value={item.bid} />
            <Metric label="Ask" value={item.ask} />
            <Metric label="נפח" value={String(item.volume)} />
            <Metric label="Open Interest" value={String(item.open_interest)} />
            <Metric label="Delta" value={item.delta} />
            <Metric label="Gamma" value={item.gamma} />
            <Metric label="Theta" value={item.theta} />
            <Metric label="Vega" value={item.vega} />
            <Metric label="IV" value={item.iv} />
            <Metric label="כמות" value={String(item.quantity)} />
          </div>
          <ul className="space-y-2 text-sm text-mist">
            {item.greek_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      </div>
    </Shell>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-mist">{label}</div>
      <div className="num text-sm">{value}</div>
    </div>
  );
}
