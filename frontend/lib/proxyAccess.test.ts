import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { accessDecision, searchWithoutKey } from "./proxyAccess.ts";
import { issueSession, loginConfigured, sessionValid } from "./session.ts";

const saved = {
  DASHBOARD_USER: process.env.DASHBOARD_USER,
  DASHBOARD_PASSWORD: process.env.DASHBOARD_PASSWORD,
  SESSION_SECRET: process.env.SESSION_SECRET,
};

function restoreEnv() {
  for (const [name, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

describe("proxy access", { concurrency: 1 }, () => {
  test("a logged-in dashboard session gets the admin key and nothing else does", () => {
    process.env.DASHBOARD_USER = "ada";
    process.env.DASHBOARD_PASSWORD = "desk-password";
    process.env.SESSION_SECRET = "session-secret";
    try {
      assert.equal(loginConfigured(), true);
      const cookie = issueSession("ada");
      const signedIn = accessDecision({
        signedIn: sessionValid(cookie),
        adminKey: "admin-secret",
        clientKey: "",
        authorization: "",
      });
      assert.equal(signedIn.forwardKey, "admin-secret");
      assert.equal(signedIn.dropAuthorization, true);

      const anonymous = accessDecision({
        signedIn: sessionValid(undefined),
        adminKey: "admin-secret",
        clientKey: "",
        authorization: "",
      });
      assert.equal(anonymous.forwardKey, null);
      assert.equal(anonymous.dropAuthorization, false);

      const expired = accessDecision({
        signedIn: sessionValid("not-a-session"),
        adminKey: "admin-secret",
        clientKey: "",
        authorization: "Basic nope",
      });
      assert.equal(expired.forwardKey, null);
    } finally {
      restoreEnv();
    }
  });

  test("login is not configured means the admin key is never attached", () => {
    delete process.env.DASHBOARD_USER;
    delete process.env.DASHBOARD_PASSWORD;
    delete process.env.SESSION_SECRET;
    try {
      assert.equal(loginConfigured(), false);
      assert.equal(sessionValid("ada.1.sig"), false);
      const decision = accessDecision({
        signedIn: sessionValid("ada.1.sig"),
        adminKey: "admin-secret",
        clientKey: "",
        authorization: "",
      });
      assert.equal(decision.forwardKey, null);
    } finally {
      restoreEnv();
    }
  });

  test("a caller key or bearer is forwarded and is not replaced by the admin key", () => {
    const withKey = accessDecision({
      signedIn: true,
      adminKey: "admin-secret",
      clientKey: "read-secret",
      authorization: "Bearer read-secret",
    });
    assert.equal(withKey.forwardKey, "read-secret");
    assert.equal(withKey.dropAuthorization, false);

    const bearerOnly = accessDecision({
      signedIn: true,
      adminKey: "admin-secret",
      clientKey: "   ",
      authorization: "Bearer read-secret",
    });
    assert.equal(bearerOnly.forwardKey, null);
    assert.equal(bearerOnly.dropAuthorization, false);

    const anonymousRead = accessDecision({
      signedIn: false,
      adminKey: "admin-secret",
      clientKey: "read-secret",
      authorization: "",
    });
    assert.equal(anonymousRead.forwardKey, "read-secret");
    assert.equal(anonymousRead.dropAuthorization, false);
  });

  test("the key query parameter is removed before the URL is forwarded", () => {
    assert.equal(searchWithoutKey("?period=today&key=read-secret&start=2026-10-01"), "?period=today&start=2026-10-01");
    assert.equal(searchWithoutKey("?key=read-secret"), "");
    assert.equal(searchWithoutKey(""), "");
  });

  test("the proxy route no longer attaches the admin key for an anonymous session", () => {
    const routePath = join(dirname(fileURLToPath(import.meta.url)), "../app/backend/[...path]/route.ts");
    const source = readFileSync(routePath, "utf8");
    assert.match(source, /accessDecision/);
    assert.match(source, /searchWithoutKey/);
    assert.doesNotMatch(source, /!loginConfigured\(\)/);
    assert.doesNotMatch(source, /signedIn \|\|/);
  });
});
