"use client";

import { FormEvent, useEffect, useState } from "react";
import { LogoLockup } from "@/components/Logo";
import { copy } from "@/lib/copy";
import { dubaiTime, nyCashOpen } from "@/lib/format";

const REMEMBER_KEY = "beo.login.user";
const WAVE = "M0 300 C80 280 120 310 200 270 S330 210 400 240 520 300 600 220 720 150 800 190 930 240 1000 160 1120 90 1200 130 1330 120 1440 60";

function readRemembered() {
  try {
    return localStorage.getItem(REMEMBER_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function LoginPage() {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const saved = readRemembered();
    if (saved) setUser(saved);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

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
        setError(typeof body.detail === "string" ? body.detail : copy.errors.request);
        return;
      }
      try {
        if (remember) localStorage.setItem(REMEMBER_KEY, user);
        else localStorage.removeItem(REMEMBER_KEY);
      } catch {
        /* a private window can refuse storage; the session still stands */
      }
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch {
      setError(copy.errors.down);
    } finally {
      setBusy(false);
    }
  }

  const open = nyCashOpen(now);
  const market = `${open ? copy.login.marketOpen : copy.login.marketClosed} · ${dubaiTime(now, false)} ${copy.shell.dubai}`;

  return (
    <main className="login-screen">
      <div className="login-aura" />
      <div className="login-chart" aria-hidden="true">
        <svg viewBox="0 0 2880 400" preserveAspectRatio="none">
          <defs>
            <linearGradient id="loginFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#22C38E" stopOpacity="0.28" />
              <stop offset="1" stopColor="#22C38E" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${WAVE} L1440 400 L0 400 Z`} fill="url(#loginFill)" />
          <path d={WAVE} fill="none" stroke="#22C38E" strokeWidth="1.6" />
          <path d={`${WAVE} L1440 400 L0 400 Z`} fill="url(#loginFill)" transform="translate(1440 0)" />
          <path d={WAVE} fill="none" stroke="#22C38E" strokeWidth="1.6" transform="translate(1440 0)" />
        </svg>
      </div>
      <div className="login-wrap">
        <LogoLockup />
        <form onSubmit={submit} className="login-card">
          <h1>{copy.login.title}</h1>
          <p className="lead">{copy.login.lead}</p>
          {error ? (
            <p className="err" role="alert">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 1.6 15 14.2H1L8 1.6Z" fill="none" stroke="currentColor" strokeWidth="1.3" />
                <path d="M8 6.2v4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                <circle cx="8" cy="12.1" r="0.7" fill="currentColor" />
              </svg>
              {error}
            </p>
          ) : null}
          <div className="field">
            <label htmlFor="login-user">{copy.login.user}</label>
            <input
              id="login-user"
              autoFocus
              type="email"
              autoComplete="username"
              value={user}
              onChange={(event) => setUser(event.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="login-password">{copy.login.password}</label>
            <span className="pw">
              <input
                id="login-password"
                type={show ? "text" : "password"}
                autoComplete="current-password"
                dir="ltr"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
              <button type="button" className="eye" onClick={() => setShow((value) => !value)} aria-pressed={show} aria-label={show ? copy.login.hidePassword : copy.login.showPassword}>
                {show ? copy.login.hide : copy.login.show}
              </button>
            </span>
          </div>
          <label className="remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => {
                const next = event.target.checked;
                setRemember(next);
                if (!next) {
                  try {
                    localStorage.removeItem(REMEMBER_KEY);
                  } catch {
                    /* ignore */
                  }
                }
              }}
            />
            {copy.login.remember}
          </label>
          <button className="cta" type="submit" disabled={busy}>{busy ? copy.login.submitting : copy.login.submit}</button>
          <div className="login-foot">
            <span className="login-chip">
              <i className={open ? "dot live" : "dot"} />
              {market}
            </span>
            <span className="login-chip paper">PAPER</span>
          </div>
        </form>
        <p className="login-secure">{copy.login.secure}</p>
      </div>
    </main>
  );
}
