import { NextRequest } from "next/server";
import { accessDecision, searchWithoutKey, serverEnv } from "@/lib/proxyAccess";
import { SESSION_COOKIE, sessionValid } from "@/lib/session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DROP_REQUEST = new Set(["host", "connection", "cookie", "content-length", "x-beo-key"]);
const DROP_RESPONSE = new Set(["content-encoding", "content-length", "transfer-encoding", "connection"]);

async function relay(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const backend = (serverEnv("BACKEND_URL") || "http://127.0.0.1:8000").replace(/\/$/, "");
  const target = `${backend}/${path.map(encodeURIComponent).join("/")}${searchWithoutKey(request.nextUrl.search)}`;
  const headers = new Headers();
  request.headers.forEach((value, name) => {
    if (!DROP_REQUEST.has(name.toLowerCase())) headers.set(name, value);
  });
  const decision = accessDecision({
    signedIn: sessionValid(request.cookies.get(SESSION_COOKIE)?.value),
    adminKey: serverEnv("API_ADMIN_KEY"),
    clientKey: request.headers.get("x-beo-key") ?? "",
    authorization: request.headers.get("authorization") ?? "",
  });
  if (decision.forwardKey) headers.set("x-beo-key", decision.forwardKey);
  if (decision.dropAuthorization) headers.delete("authorization");
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  let response: Response;
  try {
    response = await fetch(target, { method: request.method, headers, body, cache: "no-store", redirect: "manual" });
  } catch {
    return Response.json({ detail: "השרת לא זמין" }, { status: 502 });
  }
  const out = new Headers();
  response.headers.forEach((value, name) => {
    if (!DROP_RESPONSE.has(name.toLowerCase())) out.set(name, value);
  });
  return new Response(response.body, { status: response.status, headers: out });
}

export const GET = relay;
export const HEAD = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
