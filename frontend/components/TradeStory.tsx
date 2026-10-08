export type Story = { entry: string[]; exit: string[]; closed: boolean };

export function TradeStory({ story }: { story?: Story | null }) {
  if (!story) return <p className="trade-story-empty">אין עדיין הסבר לעסקה הזו.</p>;
  return (
    <div className="trade-story">
      <section>
        <h3>למה נכנסנו</h3>
        <ul>{story.entry.map((line) => <li key={line}>{line}</li>)}</ul>
      </section>
      <section className={story.closed ? "out" : "live"}>
        <h3>{story.closed ? "למה יצאנו" : "מה יוציא אותה"}</h3>
        <ul>{story.exit.map((line) => <li key={line}>{line}</li>)}</ul>
      </section>
    </div>
  );
}
