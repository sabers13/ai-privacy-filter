/*
 * Headless Chromium steps for the n8n end-to-end check.
 *
 * Three commands are used by scripts/e2e/run_e2e.py:
 *
 *   setup      create the throwaway instance owner (AIPF_N8N_OWNER_*)
 *   approve    submit the review form for every enquiry in a JSON plan
 *   screenshot write the workflow canvas to a PNG at 1280 px width
 *
 * The script never prints a password, an owner e-mail or any enquiry text.
 * It talks to the local n8n instance only.
 */

const fs = require("node:fs");
const { chromium } = require("playwright");

const BASE_URL = (process.env.AIPF_N8N_URL || "http://127.0.0.1:5678").replace(/\/+$/, "");
const OWNER_EMAIL = process.env.AIPF_N8N_OWNER_EMAIL || "";
const OWNER_PASSWORD_RAW = process.env.AIPF_N8N_OWNER_PASSWORD || "";
const OWNER_FIRST_NAME = process.env.AIPF_N8N_OWNER_FIRST_NAME || "Demo";
const OWNER_LAST_NAME = process.env.AIPF_N8N_OWNER_LAST_NAME || "Owner";

/**
 * n8n enforces a password policy. The throwaway secret is used as given and
 * only gets the character classes n8n asks for appended when they are missing;
 * setup and login derive the same value.
 */
function ownerPassword() {
  let value = OWNER_PASSWORD_RAW;
  if (!/[A-Z]/.test(value)) value += "A";
  if (!/[a-z]/.test(value)) value += "a";
  if (!/[0-9]/.test(value)) value += "1";
  if (!/[^A-Za-z0-9]/.test(value)) value += "!";
  while (value.length < 8) value += "x";
  return value;
}

function requireOwner() {
  if (!OWNER_EMAIL || !OWNER_PASSWORD_RAW) {
    throw new Error("AIPF_N8N_OWNER_EMAIL and AIPF_N8N_OWNER_PASSWORD are required");
  }
}

async function login(request) {
  requireOwner();
  const response = await request.post(`${BASE_URL}/rest/login`, {
    data: { emailOrLdapLoginId: OWNER_EMAIL, password: ownerPassword() },
    failOnStatusCode: false,
    headers: { Accept: "application/json" },
  });
  if (!response.ok()) {
    throw new Error(`n8n login answered ${response.status()}`);
  }
  return response;
}

async function setupOwner(context) {
  let settings;
  let settingsResponse;
  let settingsText = "";
  for (let attempt = 0; attempt < 120; attempt += 1) {
    settingsResponse = await context.request.get(`${BASE_URL}/rest/settings`, {
      failOnStatusCode: false,
      headers: { Accept: "application/json" },
    });
    settingsText = await settingsResponse.text();
    try {
      settings = JSON.parse(settingsText);
      break;
    } catch {
      settings = null;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  if (!settings || !settingsResponse || !settingsResponse.ok()) {
    throw new Error(
      `n8n settings did not answer JSON (${settingsResponse ? settingsResponse.status() : "?"}): ` +
        settingsText.slice(0, 80),
    );
  }
  const showSetup = Boolean(
    settings && settings.data && settings.data.userManagement && settings.data.userManagement.showSetupOnFirstLoad,
  );
  if (!showSetup) {
    await login(context.request);
    return { created: false };
  }
  requireOwner();
  const response = await context.request.post(`${BASE_URL}/rest/owner/setup`, {
    data: {
      email: OWNER_EMAIL,
      firstName: OWNER_FIRST_NAME,
      lastName: OWNER_LAST_NAME,
      password: ownerPassword(),
    },
    failOnStatusCode: false,
    headers: { Accept: "application/json" },
  });
  if (!response.ok()) {
    throw new Error(`n8n owner setup answered ${response.status()}`);
  }
  return { created: true };
}

async function openForm(page, item) {
  await page.goto(item.form_url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => {});
  const title = page.locator("h1, [data-test-id='form-title']").first();
  await title.waitFor({ state: "visible", timeout: 30000 });
}

async function chooseDecision(page, decision) {
  const select = page.locator("select").first();
  if ((await select.count()) > 0) {
    await select.selectOption(decision);
    return;
  }
  // Older Wait forms render the radio field as a checkbox input.
  const choice = page.locator(`input[value="${decision}"]`);
  if ((await choice.count()) > 0) {
    await choice.first().check({ force: true });
    return;
  }
  await page.getByText(decision, { exact: true }).first().click();
}

async function replyArea(page) {
  const area = page.locator("textarea").last();
  await area.waitFor({ state: "visible", timeout: 30000 });
  if (!(await area.inputValue()).trim()) {
    throw new Error("the review form did not prefill the restored draft");
  }
  return area;
}

async function submitReview(page, item) {
  await openForm(page, item);
  await chooseDecision(page, item.decision);
  const area = await replyArea(page);
  const prefilled = await area.inputValue();
  if (item.expected_draft && prefilled.trim() !== item.expected_draft.trim()) {
    throw new Error(`prefilled draft did not match for ${item.id}`);
  }
  if (item.decision === "edit") {
    if (!item.reply || !item.reply.trim()) {
      throw new Error(`edit decision needs a replacement reply for ${item.id}`);
    }
    await area.fill(item.reply);
  }
  const submitted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      (response.url().includes("/form-waiting/") || response.url().includes("/form/")),
    { timeout: 60000 },
  );
  await page.locator("button[type='submit']").first().click();
  const response = await submitted;
  if (response.status() >= 400) {
    throw new Error(`form submission answered ${response.status()} for ${item.id}`);
  }
  await page
    .waitForFunction(
      () => /submitted|recorded|danke|thank you/i.test(document.body.innerText),
      undefined,
      { timeout: 30000 },
    )
    .catch(() => {});
}

async function runApprove(planPath) {
  const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      colorScheme: "light",
    });
    const page = await context.newPage();
    for (const item of plan) {
      await submitReview(page, item);
    }
  } finally {
    await browser.close();
  }
}

async function runScreenshot(workflowId, outputPath) {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      colorScheme: "light",
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    await login(context.request);
    await page.goto(`${BASE_URL}/workflow/${encodeURIComponent(workflowId)}`, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    await page.waitForSelector("#canvas, .canvas, [data-test-id='canvas']", { timeout: 60000 });
    await page.waitForTimeout(2500);
    await page.emulateMedia({ colorScheme: "light" });
    const fit = page.locator(
      "[data-test-id='zoom-to-fit'], button[title*='fit' i], button[aria-label*='fit' i]",
    );
    if ((await fit.count()) > 0) {
      await fit.first().click();
      await page.waitForTimeout(1200);
    }
    // Deselect any node so the canvas does not show parameter values.
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    fs.mkdirSync(require("node:path").dirname(outputPath), { recursive: true });
    await page.screenshot({ path: outputPath, fullPage: false });
  } finally {
    await browser.close();
  }
}

async function runSetup() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const result = await setupOwner(context);
    process.stdout.write(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === "setup") {
    await runSetup();
    return;
  }
  if (command === "approve" && args.length === 1) {
    await runApprove(args[0]);
    return;
  }
  if (command === "screenshot" && args.length === 2) {
    await runScreenshot(args[0], args[1]);
    return;
  }
  throw new Error("usage: n8n_browser.cjs setup | approve <plan.json> | screenshot <id> <png>");
}

main().catch((error) => {
  process.stderr.write(`n8n_browser: ${error.message}\n`);
  process.exit(1);
});
