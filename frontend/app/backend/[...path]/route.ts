import { NextRequest } from "next/server";
import { SESSION_COOKIE, loginConfigured, sessionValid } from "@/lib/session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DROP_REQUEST = new Set(["host", "connection", "cookie", "content-length", "x-beo-key"]);
const DROP_RESPONSE = new Set(["content-encoding", "content-length", "transfer-encoding", "connection"]);

async function relay(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const backend = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const target = `${backend}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  request.headers.forEach((value, name) => {
    if (!DROP_REQUEST.has(name.toLowerCase())) headers.set(name, value);
  });
  const signedIn = sessionValid(request.cookies.get(SESSION_COOKIE)?.value);
  const admin = process.env.API_ADMIN_KEY ?? "";
  if (admin && (signedIn || !loginConfigured())) {
    headers.set("x-beo-key", admin);
    headers.delete("authorization");
  } else {
    const own = request.headers.get("x-beo-key");
    if (own) headers.set("x-beo-key", own);
  }
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
  if ((response.headers.get("content-type") || "").includes("text/event-stream")) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("X-Accel-Buffering", "no");
    out.set("Connection", "keep-alive");
  }
  return new Response(response.body, { status: response.status, headers: out });
}

export const GET = relay;
export const HEAD = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
