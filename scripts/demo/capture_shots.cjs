/*
 * Capture the four review-stack screenshots for the page bundle.
 *
 * The demo stack (``make demo``) leaves three synthetic enquiries waiting for a
 * human. This script completes them the way a reviewer would: one enquiry is
 * edited and corrected, the other two are approved. It then writes
 *
 *   n8n-review-form.png      the edit form, filled but not yet submitted
 *   n8n-executions-list.png  the executions list, successful runs only
 *   n8n-execution-success.png the finished execution canvas, zoom to fit
 *   n8n-result.png           the result card rendered from the stored record
 *
 * and dumps the page text of every screen next to the run evidence so the
 * caller can run the forbidden-string scan before keeping the images.
 *
 * Usage: node capture_shots.cjs <config.json>
 *
 * The config carries local form URLs, so it lives outside the repository.
 * Nothing secret is printed; the owner credentials come from the environment.
 */

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const configPath = process.argv[2];
if (!configPath) {
  throw new Error("usage: capture_shots.cjs <config.json>");
}
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
const BASE_URL = (config.baseUrl || "http://127.0.0.1:5678").replace(/\/+$/, "");
const OWNER_EMAIL = process.env.AIPF_N8N_OWNER_EMAIL || "";
const OWNER_PASSWORD_RAW = process.env.AIPF_N8N_OWNER_PASSWORD || "";
const EDIT_ID = config.editId;
const ITEMS = config.items || [];
const SHOTS = config.screenshotsDir;
const EVIDENCE = config.evidenceDir;
const OUT_JSON = config.outDir;
const WORKFLOW_ID = config.workflowId || "enquiry-triage";

/**
 * n8n enforces a password policy. The throwaway secret is used as given and
 * only gets the character classes n8n asks for appended when they are missing;
 * demo setup and this login derive the same value.
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

function log(message) {
  process.stdout.write(`shots: ${message}\n`);
}

function writeEvidence(name, text) {
  fs.mkdirSync(EVIDENCE, { recursive: true });
  fs.writeFileSync(path.join(EVIDENCE, name), text, "utf8");
}

async function bodyText(page) {
  try {
    return await page.evaluate(() => document.body.innerText);
  } catch {
    return "";
  }
}

async function login(request) {
  const response = await request.post(`${BASE_URL}/rest/login`, {
    data: { emailOrLdapLoginId: OWNER_EMAIL, password: ownerPassword() },
    failOnStatusCode: false,
    headers: { Accept: "application/json" },
  });
  if (!response.ok()) {
    throw new Error(`n8n login answered ${response.status()}`);
  }
}

async function openForm(page, item) {
  await page.goto(item.form_url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => {});
  await page.locator("h1, [data-test-id='form-title']").first().waitFor({ state: "visible", timeout: 30000 });
  await page.waitForTimeout(500);
}

async function chooseDecision(page, decision) {
  const select = page.locator("select").first();
  if ((await select.count()) > 0) {
    await select.selectOption(decision);
    return;
  }
  const choice = page.locator(`input[value="${decision}"]`);
  if ((await choice.count()) > 0) {
    await choice.first().check({ force: true });
    return;
  }
  await page.getByText(decision, { exact: true }).first().click();
}

/**
 * Show every textarea at its full content height. The form fixes the field
 * height and scrolls the text inside it; the values themselves are not touched,
 * so the screenshot shows exactly what the reviewer typed and what the gate
 * prefilled.
 */
async function expandTextareas(page) {
  await page.evaluate(() => {
    for (const area of document.querySelectorAll("textarea")) {
      area.style.height = "auto";
      area.style.height = `${area.scrollHeight + 4}px`;
    }
  });
  await page.waitForTimeout(250);
}

async function replyArea(page) {
  const area = page.locator("textarea").last();
  await area.waitFor({ state: "visible", timeout: 30000 });
  const prefilled = await area.inputValue();
  if (!prefilled.trim()) {
    throw new Error("the review form did not prefill the restored draft");
  }
  return area;
}

async function submitReview(page, item) {
  await openForm(page, item);
  await chooseDecision(page, item.decision);
  const area = await replyArea(page);
  if (item.decision === "edit") {
    if (!item.reply || !item.reply.trim()) {
      throw new Error("the edit decision needs a replacement reply");
    }
    await area.fill(item.reply);
    const typed = await area.inputValue();
    if (typed.trim() !== item.reply.trim()) {
      throw new Error("the corrected reply did not stay in the reply field");
    }
    await page.waitForTimeout(400);
    await expandTextareas(page);
    writeEvidence(`review-form-${item.id}.txt`, await bodyText(page));
    fs.mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({
      path: path.join(SHOTS, "n8n-review-form.png"),
      fullPage: true,
    });
    log(`review form captured before submit (${item.id}, decision edit)`);
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
    .waitForFunction(() => /submitted|recorded|danke|thank you/i.test(document.body.innerText), undefined, {
      timeout: 30000,
    })
    .catch(() => {});
}

async function waitForSuccess(context) {
  const deadline = Date.now() + 180000;
  let rows = [];
  while (Date.now() < deadline) {
    const response = await context.request.get(`${BASE_URL}/rest/executions?limit=20`, {
      headers: { Accept: "application/json" },
    });
    const body = await response.json();
    rows = (body.data && body.data.results) || body.results || [];
    rows = rows.filter((row) => row.workflowId === WORKFLOW_ID);
    if (rows.length >= ITEMS.length && rows.every((row) => row.status === "success")) {
      return rows;
    }
    if (rows.some((row) => ["error", "crashed", "failed"].includes(row.status))) {
      throw new Error("an execution failed; the list should only show successful runs");
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error(`executions did not all succeed: ${rows.map((row) => row.status).join(", ")}`);
}

async function captureList(page, rows) {
  await page.goto(`${BASE_URL}/executions`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.locator("[data-test-id='table-base-scroll']").waitFor({ state: "visible", timeout: 60000 });
  await page.waitForTimeout(2500);

  const tableText = await page.locator("[data-test-id='table-base-scroll']").innerText();
  if (/waiting|error|failed|unknown/i.test(tableText)) {
    throw new Error(`executions list is not all-success: ${tableText.replace(/\s+/g, " ")}`);
  }
  const successes = (tableText.match(/success/gi) || []).length;
  if (successes < rows.length) {
    throw new Error(`executions list shows ${successes} successes, expected ${rows.length}`);
  }
  writeEvidence("executions-list.txt", await bodyText(page));

  const tabs = await page.locator("[data-test-id='tab-Executions']").first().boundingBox();
  const table = await page.locator("[data-test-id='table-base-scroll']").first().boundingBox();
  if (!tabs || !table) {
    throw new Error("could not measure the executions list");
  }
  const top = Math.max(0, Math.min(tabs.y, table.y) - 12);
  const bottom = table.y + table.height + 16;
  await page.screenshot({
    path: path.join(SHOTS, "n8n-executions-list.png"),
    clip: { x: table.x, y: top, width: table.width, height: bottom - top },
  });
  log(`executions list captured (${rows.length} successful runs)`);
}

async function captureCanvas(page, execution) {
  // A wide, short viewport fits the long chain at a readable zoom: node labels
  // and the "1 item" counts stay visible and the whole graph stays unclipped.
  await page.setViewportSize({ width: 2600, height: 760 });
  await page.goto(
    `${BASE_URL}/workflow/${encodeURIComponent(WORKFLOW_ID)}/executions/${execution.id}`,
    { waitUntil: "domcontentloaded", timeout: 60000 },
  );
  const canvas = page.locator("[data-test-id='canvas']").first();
  await canvas.waitFor({ state: "visible", timeout: 60000 });
  await page.waitForTimeout(3000);

  // Dismiss the production-checklist popup and the bottom panel, then fit.
  await page.keyboard.press("Escape").catch(() => {});
  const close = page.locator("[data-test-id='canvas'] button[aria-label='Close this dialog']");
  if ((await close.count()) > 0) {
    await close.first().click().catch(() => {});
  }
  const fit = page.locator("[data-test-id='zoom-to-fit']").first();
  await fit.waitFor({ state: "visible", timeout: 30000 });
  await fit.click();
  await page.waitForTimeout(1500);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(600);

  const text = await bodyText(page);
  if (!/true/.test(text) || !/false/.test(text)) {
    throw new Error("the canvas text does not show both branches");
  }
  if (!/item/i.test(text)) {
    throw new Error("the canvas text does not show item counts");
  }
  writeEvidence("execution-success.txt", text);
  await canvas.screenshot({ path: path.join(SHOTS, "n8n-execution-success.png") });
  log("execution canvas captured with zoom to fit");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function resultCard(record) {
  const decision = String(record.decision || "").toLowerCase();
  const edited = decision === "edit";
  const title = edited ? "Edited reply" : decision === "reject" ? "Rejected reply" : "Approved reply";
  const badge = edited ? "Edit" : decision === "reject" ? "Rejected" : "Approved";
  const note = edited
    ? "The reviewer chose edit and replaced the draft. Placeholders were restored locally before the review; this corrected text is what the result record stores."
    : "Placeholders restored locally after the model reply; the approved text is the one written to the result record.";
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${escapeHtml(title)}</title>
<style>
  :root {
    --bg: #f6f7f9;
    --card: #ffffff;
    --ink: #1f2430;
    --muted: #6b7280;
    --line: #e6e8ee;
    --accent: ${edited ? "#9a5b00" : "#12855b"};
    --accent-bg: ${edited ? "#fdf0dc" : "#e7f6ee"};
    --panel: #f9fafb;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    background: var(--bg);
    color: var(--ink);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { padding: 48px 24px; display: flex; justify-content: center; }
  .card {
    width: 760px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 12px 32px rgba(16, 24, 40, .06);
    overflow: hidden;
  }
  .head { padding: 28px 32px 22px; border-bottom: 1px solid var(--line); }
  .eyebrow { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 10px; }
  .title-row { display: flex; align-items: center; gap: 12px; }
  h1 { font-size: 22px; line-height: 1.2; margin: 0; font-weight: 650; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--accent-bg); color: var(--accent);
    border-radius: 999px; padding: 4px 11px;
    font-size: 12.5px; font-weight: 600;
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
  .meta { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 22px; }
  .meta dt { font-size: 11.5px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 0 0 5px; }
  .meta dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .chip {
    display: inline-block; border-radius: 6px; padding: 2px 8px;
    background: #eef1f6; color: #374151; font-size: 13px; font-weight: 600;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .body { padding: 24px 32px 30px; }
  .section + .section { margin-top: 22px; }
  h2 { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 9px; font-weight: 600; }
  pre {
    margin: 0; padding: 16px 18px;
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    font: 13.5px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    color: #2f3540; white-space: pre-wrap; word-break: break-word;
  }
  .note { margin: 18px 0 0; font-size: 12.5px; color: var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <article class="card">
    <header class="head">
      <p class="eyebrow">ai-privacy-filter &middot; n8n triage</p>
      <div class="title-row">
        <h1>${escapeHtml(title)}</h1>
        <span class="badge"><span class="dot"></span>${escapeHtml(badge)}</span>
      </div>
      <dl class="meta">
        <div><dt>Enquiry</dt><dd>${escapeHtml(record.id)}</dd></div>
        <div><dt>Category</dt><dd><span class="chip">${escapeHtml(record.category)}</span></dd></div>
        <div><dt>Urgency</dt><dd><span class="chip">${escapeHtml(record.urgency)}</span></dd></div>
        <div><dt>Decision</dt><dd>${escapeHtml(decision)}</dd></div>
      </dl>
    </header>
    <div class="body">
      <section class="section">
        <h2>The model only saw this text</h2>
        <pre>${escapeHtml(record.redacted_text)}</pre>
      </section>
      <section class="section">
        <h2>Final reply</h2>
        <pre>${escapeHtml(record.final_reply)}</pre>
      </section>
      <p class="note">${escapeHtml(note)}</p>
    </div>
  </article>
</div>
</body>
</html>`;
}

async function captureResult(context) {
  const recordPath = path.join(OUT_JSON, `${EDIT_ID}.json`);
  const record = JSON.parse(fs.readFileSync(recordPath, "utf8"));
  if (String(record.decision).toLowerCase() !== "edit") {
    throw new Error(`the stored record for ${EDIT_ID} is not an edit decision`);
  }
  if (!String(record.final_reply || "").trim()) {
    throw new Error("the stored record has no final reply");
  }
  const page = await context.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.setContent(resultCard(record), { waitUntil: "load" });
  await page.waitForTimeout(300);
  writeEvidence("result-card.txt", await bodyText(page));
  await page.screenshot({
    path: path.join(SHOTS, "n8n-result.png"),
    fullPage: true,
  });
  await page.close();
  log("result card captured from the stored edited record");
}

async function main() {
  if (!EDIT_ID || !SHOTS || !EVIDENCE || !OUT_JSON) {
    throw new Error("config needs editId, screenshotsDir, evidenceDir and outDir");
  }
  const editItems = ITEMS.filter((item) => item.id === EDIT_ID);
  if (editItems.length !== 1 || editItems[0].decision !== "edit") {
    throw new Error("exactly one item must carry the edit decision");
  }
  fs.mkdirSync(SHOTS, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      colorScheme: "light",
      deviceScaleFactor: 1,
    });
    await login(context.request);

    const phases = config.phases || ["forms", "list", "canvas", "result"];
    const page = await context.newPage();
    if (phases.includes("forms")) {
      // Submit the edited enquiry last so it is the newest execution.
      const ordered = [...ITEMS.filter((item) => item.id !== EDIT_ID), ...editItems];
      for (const item of ordered) {
        await submitReview(page, item);
        log(`review submitted (${item.id}, decision ${item.decision})`);
      }
    }
    const rows = await waitForSuccess(context);
    log(`all ${rows.length} executions succeeded`);

    if (phases.includes("list")) {
      await captureList(page, rows);
    }
    if (phases.includes("canvas")) {
      const newest = rows.reduce((left, right) =>
        new Date(right.startedAt) > new Date(left.startedAt) ? right : left,
      );
      await captureCanvas(page, newest);
    }
    await page.close();
    if (phases.includes("result")) {
      await captureResult(context);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`n8n_browser: ${error.message}\n`);
  process.exit(1);
});
