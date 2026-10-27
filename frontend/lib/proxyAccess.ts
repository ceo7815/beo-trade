export type AccessDecision = {
  forwardKey: string | null;
  dropAuthorization: boolean;
};

function bearerToken(authorization: string): string {
  const match = /^bearer\s+(\S+)/i.exec(authorization.trim());
  return match?.[1] ?? "";
}

/**
 * A caller-supplied X-Beo-Key or Authorization Bearer is forwarded as-is.
 * The admin key is attached only for a valid dashboard session, and never
 * when that session is missing — including when login is not configured.
 */
export function accessDecision(input: {
  signedIn: boolean;
  adminKey: string;
  clientKey: string;
  authorization: string;
}): AccessDecision {
  const clientKey = input.clientKey.trim();
  if (clientKey || bearerToken(input.authorization)) {
    return { forwardKey: clientKey || null, dropAuthorization: false };
  }
  const adminKey = input.adminKey.trim();
  if (input.signedIn && adminKey) {
    return { forwardKey: adminKey, dropAuthorization: true };
  }
  return { forwardKey: null, dropAuthorization: false };
}

export function serverEnv(name: string): string {
  return process.env[name] ?? "";
}

/** Drop ?key= before the proxy forwards the URL, so the value is not logged. */
export function searchWithoutKey(search: string): string {
  if (!search) return "";
  const raw = search.startsWith("?") ? search.slice(1) : search;
  const kept = raw.split("&").filter((part) => part.length > 0 && part !== "key" && !part.startsWith("key="));
  return kept.length ? `?${kept.join("&")}` : "";
}
