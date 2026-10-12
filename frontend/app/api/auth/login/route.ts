import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, SESSION_DAYS, credentialsMatch, issueSession, loginConfigured } from "@/lib/session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILURES = 8;
const failures = new Map<string, number[]>();

function caller(request: NextRequest) {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "local";
}

export async function POST(request: NextRequest) {
  if (!loginConfigured()) {
    return NextResponse.json({ detail: "הכניסה עוד לא הוגדרה בשרת" }, { status: 503 });
  }
  const who = caller(request);
  const now = Date.now();
  const recent = (failures.get(who) ?? []).filter((stamp) => now - stamp < WINDOW_MS);
  if (recent.length >= MAX_FAILURES) {
    return NextResponse.json({ detail: "יותר מדי ניסיונות. נסה שוב בעוד רבע שעה." }, { status: 429 });
  }
  const body = (await request.json().catch(() => ({}))) as { user?: unknown; password?: unknown };
  const user = typeof body.user === "string" ? body.user : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (!credentialsMatch(user, password)) {
    recent.push(now);
    failures.set(who, recent);
    return NextResponse.json({ detail: "שם משתמש או סיסמה שגויים" }, { status: 401 });
  }
  failures.delete(who);
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, issueSession(user.trim().toLowerCase()), {
    httpOnly: true,
    secure: request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DAYS * 24 * 60 * 60,
  });
  return response;
}
