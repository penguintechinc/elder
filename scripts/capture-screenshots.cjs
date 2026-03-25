const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const http = require('http');

const BASE_URL = process.env.BASE_URL || 'http://localhost:3005';
const API_URL = process.env.API_URL || 'http://localhost:4000';
const OUTPUT_DIR = process.env.OUTPUT_DIR || '/home/penguin/code/elder/docs/screenshots';
const TEST_EMAIL = process.env.ELDER_TEST_EMAIL || 'admin@localhost.local';
const TEST_PASSWORD = process.env.ELDER_TEST_PASSWORD || 'admin123';

// All pages to capture - comprehensive list
const pages = [
  { name: 'login', path: '/login', requiresAuth: false },
  { name: 'dashboard', path: '/' },
  { name: 'organizations', path: '/organizations' },
  { name: 'entities', path: '/entities' },
  { name: 'software', path: '/software' },
  { name: 'services', path: '/services' },
  { name: 'data-stores', path: '/data-stores' },
  { name: 'issues', path: '/issues' },
  { name: 'projects', path: '/projects' },
  { name: 'milestones', path: '/milestones' },
  { name: 'labels', path: '/labels' },
  { name: 'keys', path: '/keys' },
  { name: 'secrets', path: '/secrets' },
  { name: 'certificates', path: '/certificates' },
  { name: 'dependencies', path: '/dependencies' },
  { name: 'discovery', path: '/discovery' },
  { name: 'profile', path: '/profile' },
  { name: 'identities', path: '/iam' },
  { name: 'networking', path: '/networking' },
  { name: 'ipam', path: '/ipam' },
  { name: 'vulnerabilities', path: '/vulnerabilities' },
  { name: 'sbom', path: '/sbom' },
  { name: 'service-endpoints', path: '/service-endpoints' },
  { name: 'on-call-rotations', path: '/on-call-rotations' },
  { name: 'webhooks', path: '/webhooks' },
  { name: 'backups', path: '/backups' },
  { name: 'map', path: '/map' },
  { name: 'search', path: '/search' },
  // Admin pages
  { name: 'admin-tenants', path: '/admin/tenants' },
  { name: 'admin-sso', path: '/admin/sso' },
  { name: 'admin-audit-logs', path: '/admin/audit-logs' },
  { name: 'admin-settings', path: '/admin/settings' },
  { name: 'admin-sync-config', path: '/admin/sync-config' },
  { name: 'admin-license-policies', path: '/admin/license-policies' },
];

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function captureScreenshots() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080 });

  // Capture login page first (unauthenticated)
  console.log('Capturing login...');
  await page.goto(`${BASE_URL}/login`, { waitUntil: 'networkidle0', timeout: 60000 });
  await sleep(1000);
  await page.screenshot({ path: path.join(OUTPUT_DIR, 'login.png') });
  console.log('  Saved login.png');

  // Authenticate via API and inject token directly (bypasses UI login form)
  console.log(`Authenticating as ${TEST_EMAIL}...`);
  const token = await new Promise((resolve) => {
    const body = JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD });
    const opts = {
      hostname: new URL(API_URL).hostname,
      port: new URL(API_URL).port || 80,
      path: '/api/v1/portal-auth/login',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    };
    const req = http.request(opts, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve(parsed.token || null);
        } catch {
          resolve(null);
        }
      });
    });
    req.on('error', () => resolve(null));
    req.write(body);
    req.end();
  });

  if (!token) {
    console.error(`Login failed — could not get token from ${API_URL}. Cannot capture authenticated pages.`);
    await browser.close();
    return;
  }
  console.log('  Got JWT token. Injecting into browser...');

  // Navigate to the app and inject the token into localStorage
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.evaluate((t) => {
    localStorage.setItem('elder_token', t);
  }, token);
  // Navigate to dashboard (forces React Router to re-render with auth state)
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(1500);
  console.log('  Login successful — current URL:', page.url());

  // Capture all other pages
  let successCount = 0;
  let skipCount = 0;
  let errorCount = 0;

  for (const pageInfo of pages) {
    if (pageInfo.name === 'login') continue;

    try {
      console.log(`Capturing ${pageInfo.name}...`);

      // Navigate to the page
      await page.goto(`${BASE_URL}${pageInfo.path}`, {
        waitUntil: 'networkidle0',
        timeout: 60000
      });

      // Wait for content to load
      await sleep(2500);

      // Check if we got redirected to login (session expired or auth issue)
      const currentUrl = page.url();
      if (currentUrl.includes('/login')) {
        console.log(`  WARNING: Redirected to login for ${pageInfo.name}`);

        // Try to re-login
        console.log('  Attempting re-login...');
        const inputs = await page.$$('input');
        if (inputs.length >= 2) {
          await inputs[0].type('admin@localhost.local');
          await inputs[1].type('admin123');
          await page.click('button[type="submit"]');
          await sleep(2000);

          // Navigate back to the target page
          await page.goto(`${BASE_URL}${pageInfo.path}`, {
            waitUntil: 'networkidle0',
            timeout: 60000
          });
          await sleep(2500);

          // Check again
          const newUrl = page.url();
          if (newUrl.includes('/login')) {
            console.log(`  SKIP: Still redirected to login for ${pageInfo.name}`);
            skipCount++;
            continue;
          }
        } else {
          skipCount++;
          continue;
        }
      }

      // Take screenshot
      await page.screenshot({
        path: path.join(OUTPUT_DIR, `${pageInfo.name}.png`),
        fullPage: false,
      });
      console.log(`  Saved ${pageInfo.name}.png`);
      successCount++;

    } catch (error) {
      console.error(`  Error capturing ${pageInfo.name}: ${error.message}`);
      errorCount++;
    }
  }

  await browser.close();

  console.log('\n========================================');
  console.log('Screenshot capture complete!');
  console.log(`  Success: ${successCount}`);
  console.log(`  Skipped: ${skipCount}`);
  console.log(`  Errors:  ${errorCount}`);
  console.log(`  Output:  ${OUTPUT_DIR}`);
  console.log('========================================\n');
}

captureScreenshots().catch(console.error);
