"use client";

import { FormEvent, useState } from "react";
import { LogoLockup } from "@/components/Logo";

export default function LoginPage() {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user, password }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(typeof body.detail === "string" ? body.detail : "הכניסה נכשלה");
        return;
      }
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch {
      setError("השרת לא זמין");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main dir="rtl" style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}>
      <form onSubmit={submit} className="ops-pane" style={{ width: "100%", maxWidth: 380, display: "grid", gap: 14, padding: 24 }}>
        <LogoLockup />
        <h1 style={{ margin: 0, fontSize: 22 }}>כניסה לדשבורד</h1>
        <label style={{ display: "grid", gap: 6 }}>
          <span>שם משתמש</span>
          <input type="email" autoComplete="username" dir="ltr" value={user} onChange={(event) => setUser(event.target.value)} required style={field} />
        </label>
        <label style={{ display: "grid", gap: 6 }}>
          <span>סיסמה</span>
          <input type="password" autoComplete="current-password" dir="ltr" value={password} onChange={(event) => setPassword(event.target.value)} required style={field} />
        </label>
        {error ? <p className="down" style={{ margin: 0 }}>{error}</p> : null}
        <button type="submit" className="ops-pip on" disabled={busy} style={{ justifyContent: "center", padding: "12px 16px", cursor: "pointer" }}>
          {busy ? "נכנס" : "כניסה"}
        </button>
      </form>
    </main>
  );
}

const field: React.CSSProperties = {
  padding: "12px 14px",
  borderRadius: 10,
  border: "1px solid var(--line)",
  background: "var(--panel)",
  color: "var(--text)",
  fontSize: 16,
};
