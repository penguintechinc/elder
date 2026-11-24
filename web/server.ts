import express, { type Express } from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export interface ServerOptions {
  distDir?: string;
  apiUrl?: string;
}

const DEFAULT_DIST = join(dirname(fileURLToPath(import.meta.url)), "dist");

/**
 * Create the Express static server for the Elder Web UI.
 *
 * Route order is load-bearing:
 * 1. /healthz — dependency-free container health probe
 * 2. /api     — same-origin fallback proxy to hub-api (Envoy handles /api in-cluster)
 * 3. static   — hashed Vite bundles cached immutably for 1 year
 * 4. fallback — SPA deep links serve index.html with Cache-Control: no-cache
 */
export function createApp(options: ServerOptions = {}): Express {
  const distDir = options.distDir ?? DEFAULT_DIST;
  const apiUrl = options.apiUrl ?? process.env.API_URL ?? "http://hub-api:5000";

  const app = express();
  app.disable("x-powered-by");

  app.get("/healthz", (_req, res) => {
    res.status(200).type("text/plain").send("ok");
  });

  app.use(
    createProxyMiddleware({
      pathFilter: "/api",
      target: apiUrl,
      changeOrigin: true,
      ws: true,
      xfwd: true,
    }),
  );

  app.use(
    express.static(distDir, {
      index: false,
      setHeaders: (res, filePath) => {
        if (filePath.endsWith("index.html")) {
          res.setHeader("Cache-Control", "no-cache");
        } else if (
          /\.(?:js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$/.test(
            filePath,
          )
        ) {
          res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
        }
      },
    }),
  );

  // Express 5 requires named wildcard parameter syntax rather than bare '*'.
  app.get("/{*splat}", (_req, res) => {
    res.setHeader("Cache-Control", "no-cache");
    res.sendFile(join(distDir, "index.html"));
  });

  return app;
}

const isEntrypoint =
  process.argv[1] !== undefined &&
  fileURLToPath(import.meta.url) === process.argv[1];

if (isEntrypoint) {
  const port = Number(process.env.PORT ?? 8080);
  const app = createApp();
  const server = app.listen(port, "0.0.0.0", () => {
    console.info(`[hub-webui] listening on :${port}`);
  });

  const shutdown = (signal: string) => {
    console.info(`[hub-webui] received ${signal}, shutting down`);
    server.close(() => process.exit(0));
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}
