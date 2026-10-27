export function LogoLockup({ className = "" }: { className?: string }) {
  return <img className={`brand-logo${className ? ` ${className}` : ""}`} src="/beo-trade-logo.png" alt="Beo-Trade" />;
}
