export function FloorMark({ closed, amount }: { closed: boolean; amount: string }) {
  const number = Number(amount);
  const label = Number.isFinite(number)
    ? number.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    : "";
  const text = closed ? "היום נסגר ברצפה" : "נפתחה הגנה";
  return (
    <span className={`floor-mark${closed ? " closed" : ""}`} aria-label={`${text} ${label}`}>
      <b>{closed ? "נסגר" : "הגנה"}</b>
      <small className="num">{label}</small>
    </span>
  );
}
