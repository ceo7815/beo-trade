import { createHash, createHmac, timingSafeEqual } from "crypto";

export const SESSION_COOKIE = "beo_session";
export const SESSION_DAYS = 30;

function envValue(name: string): string {
  return process.env[name] ?? "";
}

export function loginConfigured() {
  return Boolean(envValue("DASHBOARD_USER") && envValue("DASHBOARD_PASSWORD") && envValue("SESSION_SECRET"));
}

function signature(user: string, expires: number) {
  return createHmac("sha256", envValue("SESSION_SECRET")).update(`${user}|${expires}`).digest("base64url");
}

function same(given: string, expected: string) {
  const a = createHash("sha256").update(given).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

export function credentialsMatch(user: string, password: string) {
  const userOk = same(user.trim().toLowerCase(), envValue("DASHBOARD_USER").trim().toLowerCase());
  const passwordOk = same(password, envValue("DASHBOARD_PASSWORD"));
  return loginConfigured() && userOk && passwordOk;
}

export function issueSession(user: string) {
  const expires = Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000;
  return `${Buffer.from(user).toString("base64url")}.${expires}.${signature(user, expires)}`;
}

export function sessionValid(value: string | undefined) {
  if (!value || !loginConfigured()) return false;
  const [encoded, stamp, sig] = value.split(".");
  if (!encoded || !stamp || !sig) return false;
  const expires = Number(stamp);
  if (!Number.isFinite(expires) || expires < Date.now()) return false;
  const user = Buffer.from(encoded, "base64url").toString();
  return same(sig, signature(user, expires));
}
