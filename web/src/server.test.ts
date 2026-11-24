// @vitest-environment node
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "../server";

describe("hub-webui Express server", () => {
  let distDir: string;
  let baseUrl: string;
  let server: ReturnType<ReturnType<typeof createApp>["listen"]>;

  beforeAll(async () => {
    distDir = mkdtempSync(join(tmpdir(), "elder-webui-test-"));
    mkdirSync(join(distDir, "assets"), { recursive: true });
    writeFileSync(
      join(distDir, "index.html"),
      "<!doctype html><html><body>elder-spa</body></html>",
    );
    writeFileSync(
      join(distDir, "assets", "app-abc123.js"),
      "console.log('elder');",
    );

    const app = createApp({ distDir, apiUrl: "http://127.0.0.1:59999" });
    server = await new Promise((resolve) => {
      const s = app.listen(0, "127.0.0.1", () => resolve(s));
    });
    const addr = server.address() as AddressInfo;
    baseUrl = `http://127.0.0.1:${addr.port}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    rmSync(distDir, { recursive: true, force: true });
  });

  it("responds 200 ok on /healthz without x-powered-by header", async () => {
    const res = await fetch(`${baseUrl}/healthz`);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("ok");
    expect(res.headers.get("x-powered-by")).toBeNull();
  });

  it("serves hashed static assets with immutable 1y cache header", async () => {
    const res = await fetch(`${baseUrl}/assets/app-abc123.js`);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe(
      "public, max-age=31536000, immutable",
    );
    expect(await res.text()).toContain("elder");
  });

  it("serves index.html with no-cache on SPA root and deep links", async () => {
    for (const path of ["/", "/vulnerabilities", "/networking?tab=ipam"]) {
      const res = await fetch(`${baseUrl}${path}`);
      expect(res.status).toBe(200);
      expect(res.headers.get("cache-control")).toBe("no-cache");
      expect(await res.text()).toContain("elder-spa");
    }
  });
});
