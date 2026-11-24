#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');

const BASE_URL = (process.env.BASE_URL || 'http://localhost:3005').replace(/\/+$/, '');
const API_URL = (process.env.API_URL || '').replace(/\/+$/, '');
const DEFAULT_OUTPUT_DIR = path.resolve(__dirname, '..', 'docs', 'screenshots');
const OUTPUT_DIR = process.env.OUTPUT_DIR || DEFAULT_OUTPUT_DIR;
const ARTIFACT_DIR = process.env.PLAYWRIGHT_ARTIFACT_DIR || '/tmp/playwright-elder';
const TEST_EMAIL = process.env.ELDER_TEST_EMAIL || 'admin@localhost.local';
const TEST_PASSWORD = process.env.ELDER_TEST_PASSWORD || 'admin123';
const PRUNE_STALE = process.argv.includes('--prune') || process.env.PRUNE_STALE === '1';

// Comprehensive list of Elder v4.0.x pages to capture (unauthenticated + authenticated).
// Keep in sync with web/src/App.tsx and web/src/modules/*/index.tsx.
const PAGES = [
  // Unauthenticated pages
  { name: 'login', path: '/login', requiresAuth: false },
  { name: 'register', path: '/register', requiresAuth: false },

  // Core authenticated pages
  { name: 'dashboard', path: '/' },
  { name: 'search', path: '/search' },
  { name: 'profile', path: '/profile' },
  { name: 'map', path: '/map' },

  // Infrastructure & Assets
  { name: 'organizations', path: '/organizations' },
  { name: 'entities', path: '/entities' },
  { name: 'entity-detail', path: '/entities/1', dynamicEntityId: true },
  { name: 'compute', path: '/compute' },
  { name: 'compute-kubernetes', path: '/compute?tab=Kubernetes' },
  { name: 'data-stores', path: '/data-inventory' },
  { name: 'networking', path: '/networking' },
  { name: 'ipam', path: '/ipam' },
  { name: 'dependencies', path: '/dependencies' },
  { name: 'discovery', path: '/discovery' },

  // Software, Services, AI & SBOM
  { name: 'software', path: '/software' },
  { name: 'services', path: '/services' },
  { name: 'service-endpoints', path: '/service-endpoints' },
  { name: 'sbom', path: '/sbom' },
  { name: 'ai', path: '/ai' },

  // Security & IAM
  { name: 'identities', path: '/iam' },
  { name: 'keys', path: '/keys' },
  { name: 'secrets', path: '/secrets' },
  { name: 'certificates', path: '/certificates' },
  { name: 'vulnerabilities', path: '/vulnerabilities' },

  // Work Management & On-Call
  { name: 'on-call-rotations', path: '/on-call-rotations' },
  { name: 'projects', path: '/projects' },
  { name: 'issues', path: '/issues' },
  { name: 'milestones', path: '/milestones' },
  { name: 'labels', path: '/labels' },

  // Workstreams & Automation (CodeRuns, Playbooks, Executions, Approvals, CSV, Webhooks, Backups)
  { name: 'runs', path: '/runs' },
  { name: 'streams', path: '/streams' },
  { name: 'streams-executions', path: '/executions' },
  { name: 'streams-approvals', path: '/approvals' },
  { name: 'csv-templates', path: '/csv-templates' },
  { name: 'webhooks', path: '/webhooks' },
  { name: 'backups', path: '/backups' },

  // Documents, Pages & Diagrams
  { name: 'diagrams', path: '/diagrams' },
  { name: 'documents', path: '/documents' },
  { name: 'pages', path: '/pages' },

  // Enterprise Admin
  { name: 'admin-tenants', path: '/admin/tenants' },
  { name: 'admin-sso', path: '/admin/sso' },
  { name: 'admin-audit-logs', path: '/admin/audit-logs' },
  { name: 'admin-settings', path: '/admin/settings' },
  { name: 'admin-integrations', path: '/admin/integrations' },
  { name: 'admin-license-policies', path: '/admin/license-policies' },
  { name: 'admin-modules', path: '/admin/modules' },
  { name: 'admin-workers', path: '/admin/workers' },
];

const FATAL_BODY_PATTERNS = [
  /\bSomething went wrong\b/i,
  /\bUnexpected Application Error\b/i,
  /\bChunkLoadError\b/i,
  /\b404\s*[-–:]?\s*Not Found\b/i,
  /\b500\s*[-–:]?\s*Internal Server Error\b/i,
  /\b502\s*[-–:]?\s*Bad Gateway\b/i,
  /\b503\s*[-–:]?\s*Service Unavailable\b/i,
  /Traceback \(most recent call last\)/i,
];

function loadPlaywright() {
  try {
    return require('@playwright/test');
  } catch {
    return require(path.resolve(__dirname, '..', 'web', 'node_modules', '@playwright/test'));
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function cleanupArtifactDir(dir = ARTIFACT_DIR) {
  try {
    if (fs.existsSync(dir)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  } catch (err) {
    console.warn(`Warning: failed to clean up ${dir}: ${err.message}`);
  }
}

function pruneStaleScreenshots(outputDir = OUTPUT_DIR, pages = PAGES) {
  if (!fs.existsSync(outputDir)) return [];
  const expected = new Set(pages.map((p) => `${p.name}.png`));
  const removed = [];
  for (const entry of fs.readdirSync(outputDir)) {
    if (entry.endsWith('.png') && !expected.has(entry)) {
      fs.unlinkSync(path.join(outputDir, entry));
      removed.push(entry);
    }
  }
  return removed;
}

/**
 * Analyze a rendered page snapshot for visual/runtime error states.
 * Pure function so it can be unit-tested without launching a browser.
 */
function detectPageErrors({
  pageInfo,
  finalUrl,
  bodyText = '',
  alertTexts = [],
  brokenImages = 0,
  pageErrors = [],
}) {
  const issues = [];
  const requiresAuth = pageInfo.requiresAuth !== false;

  let pathname = '';
  try {
    pathname = new URL(finalUrl).pathname;
  } catch {
    pathname = finalUrl || '';
  }

  if (requiresAuth && pathname.startsWith('/login')) {
    issues.push(`Redirected to /login while capturing authenticated page ${pageInfo.path}`);
  }

  const expectedPath = pageInfo.path.split('?')[0];
  if (requiresAuth && expectedPath !== '/' && pathname === '/') {
    issues.push(
      `Unexpected redirect to / for ${pageInfo.path} (route missing or module disabled)`
    );
  }

  for (const pattern of FATAL_BODY_PATTERNS) {
    if (pattern.test(bodyText)) {
      issues.push(`Matched fatal error text (${pattern.source})`);
    }
  }

  for (const alertText of alertTexts) {
    if (/(failed|error|exception|unauthorized|forbidden|500|404)/i.test(alertText)) {
      issues.push(`Visible error alert: ${alertText.slice(0, 120)}`);
    }
  }

  if (brokenImages > 0) {
    issues.push(`${brokenImages} broken image(s) detected`);
  }

  for (const err of pageErrors) {
    issues.push(`Uncaught pageerror: ${err}`);
  }

  return issues;
}

/**
 * Authenticate the Playwright BrowserContext via /api/v1/portal-auth/login.
 * Uses HttpOnly cookies (set by the backend) plus the non-sensitive
 * localStorage `elder_authenticated` flag required by web/src/lib/api.ts.
 */
async function authenticateContext(context, page) {
  const authBase = API_URL || BASE_URL;
  const loginUrl = `${authBase}/api/v1/portal-auth/login`;
  console.log(`Authenticating as ${TEST_EMAIL} via ${loginUrl}...`);

  const response = await context.request.post(loginUrl, {
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok()) {
    throw new Error(
      `Login failed with HTTP ${response.status()} at ${loginUrl}. Cannot capture authenticated pages.`
    );
  }

  // If API_URL is on a different origin/port than BASE_URL, mirror cookies onto BASE_URL origin
  // so browser requests to BASE_URL (and its /api proxy) carry the session cookies.
  if (API_URL && new URL(API_URL).origin !== new URL(BASE_URL).origin) {
    const apiCookies = await context.cookies(API_URL);
    const baseHostname = new URL(BASE_URL).hostname;
    if (apiCookies.length > 0) {
      await context.addCookies(
        apiCookies.map((c) => ({
          ...c,
          domain: baseHostname,
        }))
      );
    }
  }

  // Navigate to same-origin /login to set non-sensitive client flags
  await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.evaluate(() => {
    localStorage.setItem('elder_authenticated', '1');
    localStorage.setItem(
      'gdpr_consent',
      JSON.stringify({
        accepted: true,
        necessary: true,
        functional: true,
        analytics: true,
        marketing: true,
      })
    );
  });

  // Confirm session works on dashboard
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForLoadState('networkidle').catch(() => {});
  await sleep(1000);

  if (new URL(page.url()).pathname.startsWith('/login')) {
    throw new Error('Authentication did not persist — still redirected to /login');
  }
  console.log('  Login successful — current URL:', page.url());
}

async function resolveFirstEntityPath(context, fallbackPath) {
  try {
    const apiBase = API_URL || BASE_URL;
    const res = await context.request.get(`${apiBase}/api/v1/entities?limit=1`);
    if (res.ok()) {
      const body = await res.json();
      const items = Array.isArray(body) ? body : body.items || body.entities || [];
      if (items.length > 0 && items[0].id != null) {
        return `/entities/${items[0].id}`;
      }
    }
  } catch {
    // Fall back to default path
  }
  return fallbackPath;
}

async function inspectPageState(page, pageInfo, pageErrors) {
  const domState = await page.evaluate(() => {
    const bodyText = document.body ? document.body.innerText : '';
    const alerts = Array.from(document.querySelectorAll('[role="alert"], .toast-error'))
      .filter((el) => {
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
      })
      .map((el) => (el.textContent || '').trim())
      .filter(Boolean);
    const brokenImages = Array.from(document.querySelectorAll('img')).filter(
      (img) => img.complete && img.naturalWidth === 0
    ).length;
    return { bodyText, alertTexts: alerts, brokenImages };
  });

  return detectPageErrors({
    pageInfo,
    finalUrl: page.url(),
    bodyText: domState.bodyText,
    alertTexts: domState.alertTexts,
    brokenImages: domState.brokenImages,
    pageErrors,
  });
}

async function capturePage(page, pageInfo, resolvedPath) {
  const pageErrors = [];
  const onPageError = (err) => pageErrors.push(err.message || String(err));
  page.on('pageerror', onPageError);

  try {
    await page.goto(`${BASE_URL}${resolvedPath}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await sleep(1500);

    const issues = await inspectPageState(page, { ...pageInfo, path: resolvedPath }, pageErrors);
    await page.screenshot({
      path: path.join(OUTPUT_DIR, `${pageInfo.name}.png`),
      fullPage: false,
    });

    return issues;
  } finally {
    page.off('pageerror', onPageError);
  }
}

async function captureScreenshots() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  let successCount = 0;
  let errorCount = 0;
  const failures = [];

  try {
    // 1. Capture unauthenticated pages in a clean, logged-out context
    const unauthContext = await browser.newContext({
      viewport: { width: 1920, height: 1080 },
      ignoreHTTPSErrors: true,
    });
    const unauthPage = await unauthContext.newPage();

    for (const pageInfo of PAGES.filter((p) => p.requiresAuth === false)) {
      console.log(`Capturing [unauth] ${pageInfo.name} (${pageInfo.path})...`);
      try {
        const issues = await capturePage(unauthPage, pageInfo, pageInfo.path);
        if (issues.length > 0) {
          console.error(`  ERROR on ${pageInfo.name}: ${issues.join('; ')}`);
          failures.push({ name: pageInfo.name, issues });
          errorCount++;
        } else {
          console.log(`  Saved ${pageInfo.name}.png`);
          successCount++;
        }
      } catch (err) {
        console.error(`  ERROR capturing ${pageInfo.name}: ${err.message}`);
        failures.push({ name: pageInfo.name, issues: [err.message] });
        errorCount++;
      }
    }
    await unauthContext.close();

    // 2. Capture authenticated pages in an authenticated context
    const authContext = await browser.newContext({
      viewport: { width: 1920, height: 1080 },
      ignoreHTTPSErrors: true,
    });
    const authPage = await authContext.newPage();
    await authenticateContext(authContext, authPage);

    for (const pageInfo of PAGES.filter((p) => p.requiresAuth !== false)) {
      const targetPath = pageInfo.dynamicEntityId
        ? await resolveFirstEntityPath(authContext, pageInfo.path)
        : pageInfo.path;
      console.log(`Capturing [auth] ${pageInfo.name} (${targetPath})...`);

      try {
        let issues = await capturePage(authPage, pageInfo, targetPath);

        // If session expired mid-run, re-authenticate once and retry
        if (issues.some((i) => i.includes('Redirected to /login'))) {
          console.log(`  Re-authenticating and retrying ${pageInfo.name}...`);
          await authenticateContext(authContext, authPage);
          issues = await capturePage(authPage, pageInfo, targetPath);
        }

        if (issues.length > 0) {
          console.error(`  ERROR on ${pageInfo.name}: ${issues.join('; ')}`);
          failures.push({ name: pageInfo.name, issues });
          errorCount++;
        } else {
          console.log(`  Saved ${pageInfo.name}.png`);
          successCount++;
        }
      } catch (err) {
        console.error(`  ERROR capturing ${pageInfo.name}: ${err.message}`);
        failures.push({ name: pageInfo.name, issues: [err.message] });
        errorCount++;
      }
    }
    await authContext.close();

    if (PRUNE_STALE) {
      const removed = pruneStaleScreenshots(OUTPUT_DIR, PAGES);
      if (removed.length > 0) {
        console.log(`Pruned ${removed.length} stale screenshot(s): ${removed.join(', ')}`);
      }
    }
  } finally {
    await browser.close();
    cleanupArtifactDir(ARTIFACT_DIR);
  }

  console.log('\n========================================');
  console.log('Screenshot capture complete!');
  console.log(`  Examined: ${PAGES.length}`);
  console.log(`  Success:  ${successCount}`);
  console.log(`  Errors:   ${errorCount}`);
  console.log(`  Output:   ${OUTPUT_DIR}`);
  console.log('========================================\n');

  if (failures.length > 0) {
    console.error('Pages with detected errors:');
    for (const f of failures) {
      console.error(`  - ${f.name}: ${f.issues.join('; ')}`);
    }
  }

  if (errorCount > 0 || successCount === 0) {
    process.exitCode = 1;
  }
}

module.exports = {
  PAGES,
  DEFAULT_OUTPUT_DIR,
  ARTIFACT_DIR,
  detectPageErrors,
  pruneStaleScreenshots,
  cleanupArtifactDir,
  captureScreenshots,
};

if (require.main === module) {
  captureScreenshots().catch((err) => {
    console.error(err);
    cleanupArtifactDir(ARTIFACT_DIR);
    process.exit(1);
  });
}
