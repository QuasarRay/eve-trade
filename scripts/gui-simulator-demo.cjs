const { chromium } = require("playwright");
const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..");
const ARTIFACT_ROOT = process.env.GUI_ARTIFACT_ROOT
  ? path.resolve(process.env.GUI_ARTIFACT_ROOT)
  : path.join(REPO_ROOT, "artifacts", "gui-simulator-demo");
const SCREENSHOT_DIR = path.join(ARTIFACT_ROOT, "screenshots");
const VIDEO_DIR = path.join(ARTIFACT_ROOT, "video");
const VIDEO_STAGING_DIR = path.join(VIDEO_DIR, ".playwright");
const LOG_DIR = path.join(ARTIFACT_ROOT, "logs");
const BASE_URL = process.env.EVE_TRADE_SIMULATOR_URL || "http://127.0.0.1:8000";
const DATABASE_URL = process.env.EVE_TRADE_DATABASE_URL || "postgres://postgres:postgres@127.0.0.1:5432/eve_trade";
const ENCORE_URL = process.env.EVE_TRADE_ENCORE_URL || "http://127.0.0.1:4000";
const INCLUDE_OUTAGE = process.env.GUI_DEMO_ENABLE_OUTAGE === "1";

const SELLER_ID = 1001;
const BUYER_ID = 2002;
const OTHER_ID = 3003;
const STATION_ID = 60003760;
const OTHER_STATION_ID = 60008494;
const ITEM_TYPE_ID = 34;
const SELLER_STACK_ID = "11111111-1111-4111-8111-111111111111";
const BUYER_STACK_ID = "33333333-3333-4333-8333-333333333333";
const SELLER_WALLET_ID = "00000000-0000-4000-8000-000000001001";
const BUYER_WALLET_ID = "00000000-0000-4000-8000-000000002002";

const runId = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
const logSince = new Date().toISOString();
const key = (name) => `gui-demo-${runId}-${name}`;
const results = [];
const cues = [];
const consoleMessages = [];
let screenshotSequence = 0;
let recordingStartedAt = Date.now();

function command(executable, args, options = {}) {
  const result = childProcess.spawnSync(executable, args, {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
    ...options,
  });
  if (result.status !== 0 && !options.allowFailure) {
    throw new Error(
      `${executable} ${args.join(" ")} failed (${result.status})\n${result.stdout || ""}\n${result.stderr || ""}`,
    );
  }
  return result;
}

function psql(sql) {
  return command("psql", [DATABASE_URL, "-X", "-A", "-t", "-c", sql]).stdout.trim();
}

function dbJson(sql) {
  const text = psql(`SELECT COALESCE(row_to_json(result)::text, 'null') FROM (${sql}) result;`);
  return text ? JSON.parse(text) : null;
}

function sqlLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function tradeRow(tradeId) {
  return dbJson(`
    SELECT trade_instance_id, issuer_id, item_type_id, station_id, total_quantity,
           remaining_quantity, unit_price_isk, trade_state
    FROM trade_instance WHERE trade_instance_id = ${sqlLiteral(tradeId)}::uuid
  `);
}

function stackRow(stackId) {
  return dbJson(`
    SELECT item_stack_id, owner_id, item_type_id, station_id, quantity, stack_state, stack_version
    FROM item_stack WHERE item_stack_id = ${sqlLiteral(stackId)}::uuid
  `);
}

function walletRow(walletId) {
  return dbJson(`
    SELECT wallet_id, capsuleer_id, isk_amount, wallet_state, wallet_version
    FROM wallet WHERE wallet_id = ${sqlLiteral(walletId)}::uuid
  `);
}

function settlementCount(idempotencyKey) {
  return Number(psql(`SELECT count(*) FROM settlement_batch WHERE idempotency_key = ${sqlLiteral(idempotencyKey)};`));
}

function settlementRow(idempotencyKey) {
  return dbJson(`
    SELECT settlement_batch_id, batch_state, failure_code, failure_message
    FROM settlement_batch
    WHERE idempotency_key = ${sqlLiteral(idempotencyKey)}
    ORDER BY created_at DESC
    LIMIT 1
  `);
}

function waitForSettlement(idempotencyKey, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let lastState = "missing";
  while (Date.now() < deadline) {
    const row = settlementRow(idempotencyKey);
    if (row) {
      lastState = row.batch_state;
      if (row.batch_state === "COMPLETED") return row;
      if (row.batch_state === "FAILED") {
        throw new Error(`settlement ${idempotencyKey} failed: ${row.failure_code || "unknown"} ${row.failure_message || ""}`);
      }
    }
    childProcess.spawnSync("powershell", ["-NoProfile", "-Command", "Start-Sleep -Milliseconds 100"]);
  }
  throw new Error(`timed out waiting for settlement ${idempotencyKey}; last state=${lastState}`);
}

function tradeCount() {
  return Number(psql("SELECT count(*) FROM trade_instance;"));
}

function latestTradeByPrice(unitPrice) {
  return dbJson(`
    SELECT trade_instance_id, issuer_id, total_quantity, remaining_quantity, unit_price_isk, trade_state
    FROM trade_instance WHERE unit_price_isk = ${Number(unitPrice)} ORDER BY created_at DESC LIMIT 1
  `);
}

function record(name, passed, evidence) {
  results.push({ name, passed: Boolean(passed), evidence: String(evidence) });
  const marker = passed ? "PASS" : "FAIL";
  process.stdout.write(`[${marker}] ${name}: ${evidence}\n`);
}

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

function nestedResponse(responseText) {
  const outer = JSON.parse(responseText);
  return { outer, gateway: outer.response_payload || (outer.code ? outer : {}) };
}

function isAccepted(response) {
  return response.gateway && ["accepted", "queued"].includes(response.gateway.status);
}

function responseEvidence(response) {
  if (isAccepted(response)) {
    return `accepted; settlement=${response.gateway.settlement_batch_id || "n/a"}`;
  }
  return `${response.gateway.code || response.outer.status || "unknown"}: ${response.gateway.message || response.outer.error_message || "no message"}`;
}

async function waitForResponse(page, idempotencyKey, timeoutMs = 12000) {
  const locator = page.getByTestId("gateway-response");
  const deadline = Date.now() + timeoutMs;
  let text = "";
  while (Date.now() < deadline) {
    text = (await locator.textContent()) || "";
    if (text.includes(idempotencyKey) && !text.includes("Sending UDP packet")) return text;
    await page.waitForTimeout(100);
  }
  throw new Error(`timed out waiting for GUI response for ${idempotencyKey}; last response: ${text}`);
}

async function ensureOverlay(page, title, description) {
  await page.evaluate(({ title, description }) => {
    let overlay = document.getElementById("gui-demo-qa-overlay");
    if (!overlay) {
      overlay = document.createElement("aside");
      overlay.id = "gui-demo-qa-overlay";
      overlay.style.cssText = [
        "position:fixed", "z-index:2147483647", "left:20px", "right:20px", "bottom:18px",
        "padding:13px 16px", "border:1px solid #e2b650", "border-radius:6px",
        "background:rgba(7,10,11,.94)", "color:#e6ecef", "font:16px/1.35 Arial,sans-serif",
        "box-shadow:0 6px 24px rgba(0,0,0,.55)", "pointer-events:none",
      ].join(";");
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<strong style="color:#e2b650">${title}</strong><br><span>${description}</span>`;
  }, { title, description });
}

function safeFileName(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

async function checkpoint(page, title, description, focusTestId = null) {
  if (focusTestId) await page.getByTestId(focusTestId).scrollIntoViewIfNeeded();
  await ensureOverlay(page, title, description);
  cues.push({ startMs: Date.now() - recordingStartedAt, title, description });
  await page.waitForTimeout(850);
  screenshotSequence += 1;
  const file = `${String(screenshotSequence).padStart(2, "0")}-${safeFileName(title)}.png`;
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, file), fullPage: true });
  return file;
}

async function fill(page, testId, value) {
  await page.getByTestId(testId).fill(String(value));
}

async function setCommonFields(page, options = {}) {
  const values = {
    "item-stack-id": options.itemStackId ?? SELLER_STACK_ID,
    "trade-instance-id": options.tradeId ?? "",
    "buyer-wallet-id": options.buyerWalletId ?? BUYER_WALLET_ID,
    "quantity": options.quantity ?? 1,
    "unit-price-isk": options.unitPrice ?? 25,
    "buyer-destination-item-stack-id": options.destinationStackId ?? BUYER_STACK_ID,
    "seller-capsuleer-id": options.sellerId ?? SELLER_ID,
    "buyer-capsuleer-id": options.buyerId ?? BUYER_ID,
    "station-id": options.stationId ?? STATION_ID,
    "item-type-id": options.itemTypeId ?? ITEM_TYPE_ID,
    "idempotency-key": options.key,
    "external-request-id": options.key,
    "extra-payload": options.extraPayload ?? "{}",
  };
  for (const [testId, value] of Object.entries(values)) await fill(page, testId, value);
}

async function press(page, action, options = {}) {
  const button = page.getByTestId(`action-${action}`);
  if (options.doubleClick) await button.dblclick();
  else await button.click();
  if (options.afterClick) await options.afterClick(button);
  if (options.reloadImmediately) {
    await page.reload({ waitUntil: "domcontentloaded" });
    return null;
  }
  if (options.doubleClick) await page.waitForTimeout(500);
  const responseText = await waitForResponse(page, options.key, options.timeoutMs);
  const response = nestedResponse(responseText);
  if (isAccepted(response) && options.key) waitForSettlement(options.key);
  return response;
}

async function issue(page, action, scenarioKey, quantity, unitPrice, options = {}) {
  await setCommonFields(page, {
    key: scenarioKey,
    quantity,
    unitPrice,
    itemStackId: options.itemStackId,
    sellerId: options.sellerId,
    stationId: options.stationId,
    extraPayload: options.extraPayload,
  });
  return press(page, action, {
    key: scenarioKey,
    doubleClick: options.doubleClick,
    reloadImmediately: options.reloadImmediately,
    afterClick: options.afterClick,
    timeoutMs: options.timeoutMs,
  });
}

async function accept(page, action, scenarioKey, tradeId, quantity, options = {}) {
  await setCommonFields(page, {
    key: scenarioKey,
    tradeId,
    quantity,
    buyerId: options.buyerId,
    buyerWalletId: options.buyerWalletId,
    destinationStackId: options.destinationStackId,
    extraPayload: options.extraPayload,
  });
  return press(page, action, {
    key: scenarioKey,
    doubleClick: options.doubleClick,
    timeoutMs: options.timeoutMs,
  });
}

async function cancel(page, scenarioKey, tradeId, options = {}) {
  await setCommonFields(page, {
    key: scenarioKey,
    tradeId,
    sellerId: options.sellerId,
    extraPayload: options.extraPayload,
  });
  return press(page, "market_cancel_order", {
    key: scenarioKey,
    doubleClick: options.doubleClick,
    timeoutMs: options.timeoutMs,
  });
}

function assertRejected(label, response, expectedText, beforeTradeCount = null) {
  const evidence = responseEvidence(response);
  const textMatches = evidence.toLowerCase().includes(expectedText.toLowerCase());
  const didNotMutate = beforeTradeCount === null || tradeCount() === beforeTradeCount;
  record(label, !isAccepted(response) && textMatches && didNotMutate, evidence);
}

async function invalidIssue(page, label, suffix, quantity, unitPrice, expectedText, options = {}) {
  const before = tradeCount();
  const scenarioKey = key(`invalid-issue-${suffix}`);
  const response = await issue(page, "market_place_sell_order", scenarioKey, quantity, unitPrice, options);
  assertRejected(label, response, expectedText, before);
  return response;
}

function formatTimestamp(ms) {
  const total = Math.max(0, Math.floor(ms));
  const hours = Math.floor(total / 3600000);
  const minutes = Math.floor((total % 3600000) / 60000);
  const seconds = Math.floor((total % 60000) / 1000);
  const millis = total % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function writeNarrationArtifacts() {
  let vtt = "WEBVTT\n\n";
  const narration = ["# GUI simulator QA narration", "", `Run: ${runId}`, ""];
  cues.forEach((cue, index) => {
    const nextStart = cues[index + 1]?.startMs ?? cue.startMs + 5500;
    const end = Math.max(cue.startMs + 1200, Math.min(nextStart - 100, cue.startMs + 6500));
    vtt += `${index + 1}\n${formatTimestamp(cue.startMs)} --> ${formatTimestamp(end)}\n${cue.title}: ${cue.description}\n\n`;
    narration.push(`## ${index + 1}. ${cue.title}`, "", cue.description, "");
  });
  fs.writeFileSync(path.join(VIDEO_DIR, "gui-simulator-qa.vtt"), vtt, "utf8");
  fs.writeFileSync(path.join(ARTIFACT_ROOT, "narration.md"), narration.join("\n"), "utf8");
}

function collectProvenance() {
  const commit = command("git", ["rev-parse", "HEAD"]).stdout.trim();
  const dirtyFiles = command("git", ["status", "--porcelain"], { allowFailure: true }).stdout.trim().split(/\r?\n/).filter(Boolean);
  const lock = fs.readFileSync(path.join(REPO_ROOT, "pnpm-lock.yaml"));
  const encoreVersion = command("encore", ["version"], { allowFailure: true }).stdout.trim();
  return {
    gitCommit: commit,
    dirty: dirtyFiles.length > 0,
    dirtyFiles,
    ciRun: process.env.GITHUB_RUN_ID || process.env.CI_PIPELINE_ID || null,
    pnpmLockSha256: crypto.createHash("sha256").update(lock).digest("hex"),
    encoreVersion,
    playwrightVersion: require("playwright/package.json").version,
    nodeVersion: process.version,
  };
}

function writeRunSummary(initial, final, videoFile, provenance) {
  const passed = results.filter((result) => result.passed).length;
  const failed = results.length - passed;
  const rows = results.map((result) => `| ${result.passed ? "PASS" : "FAIL"} | ${result.name} | ${result.evidence.replaceAll("|", "\\|").replaceAll("\n", " ")} |`);
  const lines = [
    "# GUI simulator demo run summary",
    "",
    `- Run ID: \`${runId}\``,
    `- Git commit: \`${provenance.gitCommit}\``,
    `- Dirty worktree: ${provenance.dirty ? `yes (${provenance.dirtyFiles.length} paths)` : "no"}`,
    `- CI run: ${provenance.ciRun || "local"}`,
    `- pnpm lock SHA-256: \`${provenance.pnpmLockSha256}\``,
    `- Playwright / Node: ${provenance.playwrightVersion} / ${provenance.nodeVersion}`,
    `- Assertions: ${passed} passed, ${failed} failed`,
    `- Video: \`${path.relative(ARTIFACT_ROOT, videoFile).replaceAll("\\", "/")}\``,
    `- Initial seller stack: ${initial.sellerStack.quantity}`,
    `- Final seller stack: ${final.sellerStack.quantity}`,
    `- Initial/final total wallet ISK: ${initial.totalWalletISK}/${final.totalWalletISK}`,
    `- Initial/final total item quantity: ${initial.totalItemQuantity}/${final.totalItemQuantity}`,
    `- Final open trades: ${final.openTrades}`,
    "",
    "| Result | Check | Evidence |",
    "|---|---|---|",
    ...rows,
    "",
  ];
  fs.writeFileSync(path.join(ARTIFACT_ROOT, "run-summary.md"), lines.join("\n"), "utf8");
}

function worldSnapshot() {
  return {
    sellerStack: stackRow(SELLER_STACK_ID),
    buyerStack: stackRow(BUYER_STACK_ID),
    sellerWallet: walletRow(SELLER_WALLET_ID),
    buyerWallet: walletRow(BUYER_WALLET_ID),
    tradeCount: Number(psql("SELECT count(*) FROM trade_instance;")),
    openTrades: Number(psql("SELECT count(*) FROM trade_instance WHERE trade_state = 'OPEN';")),
    totalWalletISK: Number(psql("SELECT sum(isk_amount) FROM wallet;")),
    totalItemQuantity: Number(psql("SELECT sum(quantity) FROM item_stack;")),
  };
}

async function waitForMarketHealthy(timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "not checked";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${ENCORE_URL.replace(/\/$/, "")}/market/readyz`);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Encore Market service did not return to ready state: ${lastError}`);
}

function captureServiceLogs(since) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const logFile = process.env.EVE_TRADE_SERVICE_LOG || "";
  const logs = logFile && fs.existsSync(logFile)
    ? fs.readFileSync(logFile, "utf8").split(/\r?\n/).filter((line) => line >= since).join("\n")
    : "";
  fs.writeFileSync(path.join(LOG_DIR, "service.log"), logs || "No service log file was provided via EVE_TRADE_SERVICE_LOG.\n", "utf8");
  const issues = serviceEvidenceIssues(logs);
  fs.writeFileSync(path.join(LOG_DIR, "fatal-scan.txt"), issues.length ? issues.join("\n") : "No panic/fatal/OOM signatures were found in the provided service log.\n", "utf8");
  return issues;
}

function serviceEvidenceIssues(logs) {
  return logs.split(/\r?\n/).filter((line) => /\b(panic|fatal|out of memory|oomkilled|unhandled exception|stack trace)\b|sql.*(fatal|panic)/i.test(line));
}

function enforceSuccessfulGate(runResults, serviceHealthIssues) {
  const failedResults = runResults.filter((result) => !result.passed);
  if (serviceHealthIssues.length > 0) {
    throw new Error(`service health scan found ${serviceHealthIssues.length} severe log or container-state issues`);
  }
  if (failedResults.length > 0) {
    throw new Error(`${failedResults.length} GUI QA assertions failed: ${failedResults.map((result) => result.name).join(", ")}`);
  }
}

async function main() {
  for (const directory of [SCREENSHOT_DIR, VIDEO_DIR, LOG_DIR]) {
    fs.rmSync(directory, { recursive: true, force: true });
    fs.mkdirSync(directory, { recursive: true });
  }
  fs.mkdirSync(VIDEO_STAGING_DIR, { recursive: true });

  const provenance = collectProvenance();
  const initial = worldSnapshot();
  requireCondition(initial.sellerStack && initial.sellerStack.quantity >= 45, "seller seed stack needs at least 45 units; rerun with scripts/run-gui-demo.ps1 -ResetData");
  requireCondition(initial.buyerWallet && initial.buyerWallet.isk_amount >= 900000, "buyer seed wallet needs at least 900,000 ISK; rerun with scripts/run-gui-demo.ps1 -ResetData");

  const browser = await chromium.launch({ headless: true, slowMo: 80 });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: VIDEO_STAGING_DIR, size: { width: 1440, height: 900 } },
  });
  const page = await context.newPage();
  const video = page.video();
  recordingStartedAt = Date.now();
  page.on("console", (message) => consoleMessages.push(`${message.type()}: ${message.text()}`));

  try {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    const expectedButtons = [
      "market_place_sell_order", "market_buy_from_sell_order", "market_cancel_order",
      "contract_create_item_exchange", "contract_accept_item_exchange", "direct_trade_offer", "direct_trade_accept",
    ];
    const visibility = [];
    for (const action of expectedButtons) visibility.push(await page.getByTestId(`action-${action}`).isVisible());
    record("Homepage loads with all seven seeded actions", visibility.every(Boolean), `${visibility.filter(Boolean).length}/7 visible`);
    const buttonTitles = await page.locator("button[data-testid^='action-']").evaluateAll((buttons) => buttons.map((button) => button.getAttribute("title")));
    record("Every seeded action exposes a tooltip", buttonTitles.length === 7 && buttonTitles.every(Boolean), `${buttonTitles.filter(Boolean).length}/7 tooltips populated`);
    const neocomState = await page.locator(".neo-btn").evaluateAll((nodes) => nodes.map((node) => ({ tag: node.tagName, role: node.getAttribute("role"), tabIndex: node.tabIndex })));
    record("Neocom shortcuts are functional controls", neocomState.every((entry) => entry.tag === "BUTTON" || entry.role === "button" || entry.tabIndex >= 0), JSON.stringify(neocomState));
    record("Initial state has a readable empty response", (await page.getByTestId("gateway-response").textContent()).includes("No interaction sent"), "response panel says no interaction sent");
    await checkpoint(page, "1. Simulator loaded", "The one-page client exposes seven seeded controls and an explicit empty gateway-response state.");

    await page.reload({ waitUntil: "domcontentloaded" });
    const afterReloadVisible = await page.getByTestId("simulator-shell").isVisible();
    record("Browser refresh preserves a working simulator", afterReloadVisible, `loaded ${page.url()}`);
    await checkpoint(page, "2. Refresh stability", "A full browser reload returns to a clean form. No selected trade or persisted player context exists in this GUI.");

    const happyIssueKey = key("happy-issue");
    const happyIssue = await issue(page, "market_place_sell_order", happyIssueKey, 4, 25);
    requireCondition(isAccepted(happyIssue), `happy issue failed: ${responseEvidence(happyIssue)}`);
    const happyTradeId = happyIssue.gateway.trade_instance_id;
    const happyOpen = tradeRow(happyTradeId);
    record("Seller issues a valid market sell order", happyOpen.trade_state === "OPEN" && happyOpen.remaining_quantity === 4, JSON.stringify(happyOpen));
    record("External Request ID field is propagated by the GUI packet", happyIssue.outer.raw_packet?.input?.external_request_id === happyIssueKey, `raw packet external_request_id=${happyIssue.outer.raw_packet?.input?.external_request_id ?? "absent"}`);
    await checkpoint(page, "3. Happy path — order issued", "The GUI packet traverses Quilkin and the gateway. PostgreSQL confirms an OPEN order with four units in escrow.", "gateway-response");

    const happyWalletBefore = { seller: walletRow(SELLER_WALLET_ID).isk_amount, buyer: walletRow(BUYER_WALLET_ID).isk_amount };
    const happyStackBefore = stackRow(BUYER_STACK_ID).quantity;
    const happyAccept = await accept(page, "market_buy_from_sell_order", key("happy-accept"), happyTradeId, 4);
    const happyClosed = tradeRow(happyTradeId);
    const happyWalletAfter = { seller: walletRow(SELLER_WALLET_ID).isk_amount, buyer: walletRow(BUYER_WALLET_ID).isk_amount };
    const happyStackAfter = stackRow(BUYER_STACK_ID).quantity;
    record("Buyer accepts the full market order", isAccepted(happyAccept) && happyClosed.trade_state === "COMPLETED" && happyClosed.remaining_quantity === 0, JSON.stringify(happyClosed));
    record("Full acceptance transfers items and ISK exactly once", happyStackAfter - happyStackBefore === 4 && happyWalletAfter.seller - happyWalletBefore.seller === 100 && happyWalletBefore.buyer - happyWalletAfter.buyer === 100, `buyer items +${happyStackAfter - happyStackBefore}; seller ISK +${happyWalletAfter.seller - happyWalletBefore.seller}; buyer ISK ${happyWalletAfter.buyer - happyWalletBefore.buyer}`);
    await checkpoint(page, "4. Happy path — completed", "Full acceptance completes the order, adds four Tritanium to the buyer stack, credits 100 ISK, and debits 100 ISK.", "gateway-response");

    const partialIssue = await issue(page, "market_place_sell_order", key("partial-issue"), 10, 7);
    requireCondition(isAccepted(partialIssue), `partial issue failed: ${responseEvidence(partialIssue)}`);
    const partialTradeId = partialIssue.gateway.trade_instance_id;
    await checkpoint(page, "5. Partial fill — order issued", "A ten-unit order is created to validate remaining quantity and multi-step completion.", "gateway-response");
    const partialFirst = await accept(page, "market_buy_from_sell_order", key("partial-accept-4"), partialTradeId, 4);
    const partialOpen = tradeRow(partialTradeId);
    record("Partial acceptance keeps the order open", isAccepted(partialFirst) && partialOpen.trade_state === "OPEN" && partialOpen.remaining_quantity === 6, JSON.stringify(partialOpen));
    await checkpoint(page, "6. Partial fill — six remain", "The first buyer action fills four units. Database evidence shows OPEN with six units remaining.", "gateway-response");
    const partialFinal = await accept(page, "market_buy_from_sell_order", key("partial-accept-6"), partialTradeId, 6);
    const partialClosed = tradeRow(partialTradeId);
    record("Final partial acceptance completes without a phantom order", isAccepted(partialFinal) && partialClosed.trade_state === "COMPLETED" && partialClosed.remaining_quantity === 0, JSON.stringify(partialClosed));
    await checkpoint(page, "7. Partial fill — completed", "The remaining six units settle and the same trade row moves to COMPLETED; no duplicate trade row is created.", "gateway-response");

    const cancelIssue = await issue(page, "market_place_sell_order", key("cancel-issue"), 5, 9);
    requireCondition(isAccepted(cancelIssue), `cancel issue failed: ${responseEvidence(cancelIssue)}`);
    const cancelTradeId = cancelIssue.gateway.trade_instance_id;
    const sellerBeforeCancel = stackRow(SELLER_STACK_ID).quantity;
    const cancelResult = await cancel(page, key("cancel-open"), cancelTradeId);
    const cancelled = tradeRow(cancelTradeId);
    const sellerAfterCancel = stackRow(SELLER_STACK_ID).quantity;
    record("Seller cancels an outstanding order", isAccepted(cancelResult) && cancelled.trade_state === "CANCELLED" && sellerAfterCancel - sellerBeforeCancel === 5, `${JSON.stringify(cancelled)}; seller refund=${sellerAfterCancel - sellerBeforeCancel}`);
    await checkpoint(page, "8. Cancel outstanding order", "Cancellation marks the trade CANCELLED and returns all five unfilled items to the seller stack.", "gateway-response");

    const partialCancelIssue = await issue(page, "market_place_sell_order", key("partial-cancel-issue"), 8, 3);
    const partialCancelTradeId = partialCancelIssue.gateway.trade_instance_id;
    const buyerBeforePartialCancel = stackRow(BUYER_STACK_ID).quantity;
    await accept(page, "market_buy_from_sell_order", key("partial-cancel-accept"), partialCancelTradeId, 3);
    const sellerBeforePartialCancel = stackRow(SELLER_STACK_ID).quantity;
    const partialCancel = await cancel(page, key("partial-cancel"), partialCancelTradeId);
    const partialCancelled = tradeRow(partialCancelTradeId);
    const buyerAfterPartialCancel = stackRow(BUYER_STACK_ID).quantity;
    const sellerAfterPartialCancel = stackRow(SELLER_STACK_ID).quantity;
    record("Cancel after partial fill preserves accepted quantity", isAccepted(partialCancel) && partialCancelled.trade_state === "CANCELLED" && buyerAfterPartialCancel - buyerBeforePartialCancel === 3 && sellerAfterPartialCancel - sellerBeforePartialCancel === 5, `buyer retained ${buyerAfterPartialCancel - buyerBeforePartialCancel}; seller refund=${sellerAfterPartialCancel - sellerBeforePartialCancel}`);
    await checkpoint(page, "9. Cancel after partial fill", "Three purchased units remain with the buyer; only the five unfilled units are refunded before the order is cancelled.", "gateway-response");

    await invalidIssue(page, "Reject issue with zero quantity", "zero-quantity", 0, 5, "quantity");
    await invalidIssue(page, "Reject issue with negative quantity", "negative-quantity", -1, 5, "quantity");
    const currentSellerQuantity = stackRow(SELLER_STACK_ID).quantity;
    await invalidIssue(page, "Reject issue above owned quantity", "too-many", currentSellerQuantity + 1, 5, "quantity");
    await invalidIssue(page, "Reject issue with zero price", "zero-price", 1, 0, "unit_price_isk");
    await invalidIssue(page, "Reject issue with negative price", "negative-price", 1, -1, "unit_price_isk");
    await invalidIssue(page, "Reject issue with blank quantity", "blank-quantity", "", 5, "quantity");
    await invalidIssue(page, "Reject issue with blank price", "blank-price", 1, "", "unit_price_isk");
    await invalidIssue(page, "Reject issue with missing seller", "missing-seller", 1, 5, "issued_by_capsuleer_id", { sellerId: 0 });
    await invalidIssue(page, "Reject issue with missing item stack", "missing-stack", 1, 5, "item_stack_id", { itemStackId: "" });
    await invalidIssue(page, "Reject non-numeric quantity payload", "nonnumeric", 1, 5, "decode", { extraPayload: JSON.stringify({ quantity: "not-a-number" }) });
    await invalidIssue(page, "Reject extremely large quantity", "huge-quantity", 1, 5, "decode", { extraPayload: JSON.stringify({ quantity: 9.223372036854776e25 }) });
    await invalidIssue(page, "Reject extremely large price", "huge-price", 1, 5, "decode", { extraPayload: JSON.stringify({ unit_price_isk: 9.223372036854776e25 }) });
    await invalidIssue(page, "Reject item offered by the wrong owner", "wrong-owner", 1, 5, "owner", { sellerId: OTHER_ID });
    await invalidIssue(page, "Reject item projection at the wrong station", "wrong-station", 1, 5, "station", { stationId: OTHER_STATION_ID });
    const malformedBefore = tradeCount();
    await setCommonFields(page, { key: key("invalid-json"), quantity: 1, unitPrice: 5, extraPayload: "{not-json" });
    await page.getByTestId("action-market_place_sell_order").click();
    const malformedDeadline = Date.now() + 3000;
    let malformedResponse = "";
    while (Date.now() < malformedDeadline) {
      malformedResponse = (await page.getByTestId("gateway-response").textContent()) || "";
      if (malformedResponse.includes("Invalid JSON in extra payload")) break;
      await page.waitForTimeout(50);
    }
    record("Malformed extra JSON is rejected client-side", malformedResponse.includes("Invalid JSON in extra payload") && tradeCount() === malformedBefore, malformedResponse);
    await checkpoint(page, "10. Invalid issue matrix", "Zero, negative, excessive, missing, non-numeric, huge, wrong-owner, and wrong-station inputs are rejected without creating trades.", "gateway-response");

    const invalidAcceptIssue = await issue(page, "market_place_sell_order", key("invalid-accept-target"), 3, 5);
    const invalidAcceptTradeId = invalidAcceptIssue.gateway.trade_instance_id;
    const invalidAcceptState = tradeRow(invalidAcceptTradeId);
    const invalidAcceptCases = [
      ["Reject accept with zero quantity", "zero", 0, {}, "quantity_requested"],
      ["Reject accept with negative quantity", "negative", -1, {}, "quantity_requested"],
      ["Reject accept above remaining quantity", "too-many", 4, {}, "requested 4"],
      ["Reject seller accepting own order", "self", 1, { buyerId: SELLER_ID, buyerWalletId: SELLER_WALLET_ID }, "buyer and seller"],
      ["Reject wallet owned by another player", "wrong-wallet-owner", 1, { buyerId: OTHER_ID, buyerWalletId: BUYER_WALLET_ID }, "not owned"],
      ["Reject accept with missing wallet", "missing-wallet", 1, { buyerWalletId: "" }, "wallet"],
      ["Reject non-numeric accept quantity", "nonnumeric", 1, { extraPayload: JSON.stringify({ quantity_requested: "not-a-number" }) }, "decode"],
      ["Reject extremely large accept quantity", "huge", 1, { extraPayload: JSON.stringify({ quantity_requested: 9.223372036854776e25 }) }, "decode"],
    ];
    for (const [label, suffix, quantity, options, expected] of invalidAcceptCases) {
      const response = await accept(page, "market_buy_from_sell_order", key(`invalid-accept-${suffix}`), invalidAcceptTradeId, quantity, options);
      const unchanged = tradeRow(invalidAcceptTradeId);
      record(label, !isAccepted(response) && responseEvidence(response).toLowerCase().includes(expected) && unchanged.remaining_quantity === invalidAcceptState.remaining_quantity, responseEvidence(response));
    }

    const expensiveIssue = await issue(page, "market_place_sell_order", key("insufficient-isk-issue"), 2, 600000);
    const expensiveTradeId = expensiveIssue.gateway.trade_instance_id;
    const insufficient = await accept(page, "market_buy_from_sell_order", key("insufficient-isk-accept"), expensiveTradeId, 2);
    record("Reject accept with insufficient ISK", !isAccepted(insufficient) && responseEvidence(insufficient).toLowerCase().includes("requested 1200000") && tradeRow(expensiveTradeId).remaining_quantity === 2, responseEvidence(insufficient));
    await cancel(page, key("insufficient-isk-cleanup"), expensiveTradeId);

    const cancelledIssue = await issue(page, "market_place_sell_order", key("cancelled-accept-issue"), 2, 6);
    const cancelledAcceptTradeId = cancelledIssue.gateway.trade_instance_id;
    await cancel(page, key("cancelled-accept-cancel"), cancelledAcceptTradeId);
    const acceptCancelled = await accept(page, "market_buy_from_sell_order", key("accept-cancelled"), cancelledAcceptTradeId, 1);
    record("Reject accept of a cancelled trade", !isAccepted(acceptCancelled) && responseEvidence(acceptCancelled).toLowerCase().includes("cancelled"), responseEvidence(acceptCancelled));
    const cancelAgain = await cancel(page, key("cancel-already-cancelled"), cancelledAcceptTradeId);
    record("Reject cancelling an already cancelled trade", !isAccepted(cancelAgain) && responseEvidence(cancelAgain).toLowerCase().includes("cancelled"), responseEvidence(cancelAgain));

    const completedIssue = await issue(page, "market_place_sell_order", key("completed-target-issue"), 2, 6);
    const completedTradeId = completedIssue.gateway.trade_instance_id;
    await accept(page, "market_buy_from_sell_order", key("completed-target-accept"), completedTradeId, 2);
    const acceptCompleted = await accept(page, "market_buy_from_sell_order", key("accept-completed"), completedTradeId, 1);
    record("Reject accept of a completed trade", !isAccepted(acceptCompleted) && responseEvidence(acceptCompleted).toLowerCase().includes("completed"), responseEvidence(acceptCompleted));
    const cancelCompleted = await cancel(page, key("cancel-completed"), completedTradeId);
    record("Reject cancelling a completed trade", !isAccepted(cancelCompleted) && responseEvidence(cancelCompleted).toLowerCase().includes("completed"), responseEvidence(cancelCompleted));
    const missingTradeAccept = await accept(page, "market_buy_from_sell_order", key("accept-missing-trade"), "", 1);
    record("Reject accept with missing trade ID", !isAccepted(missingTradeAccept) && responseEvidence(missingTradeAccept).toLowerCase().includes("trade_instance_id"), responseEvidence(missingTradeAccept));
    await checkpoint(page, "11. Invalid accept matrix", "Invalid quantities, ownership, wallet, funds, missing IDs, cancelled trades, and completed trades return specific errors without corrupting state.", "gateway-response");

    const roleIssue = await issue(page, "market_place_sell_order", key("role-target-issue"), 2, 8);
    const roleTradeId = roleIssue.gateway.trade_instance_id;
    const nonSellerCancel = await cancel(page, key("role-nonseller-cancel"), roleTradeId, { sellerId: BUYER_ID });
    record("Reject cancellation by a non-seller", !isAccepted(nonSellerCancel) && responseEvidence(nonSellerCancel).toLowerCase().includes("issuer") && tradeRow(roleTradeId).trade_state === "OPEN", responseEvidence(nonSellerCancel));
    await cancel(page, key("role-owner-cleanup"), roleTradeId);
    const allActionsStillVisible = await page.locator("button[data-testid^='action-']").count();
    record("Multi-principal test controls remain available", allActionsStillVisible === 7, `${allActionsStillVisible}/7 test actions visible`);
    await checkpoint(page, "12. Identity and authorization", "The authenticated UDP edge rejects a principal mismatch; this development-only harness intentionally keeps every seeded test action visible.", "gateway-response");

    const duplicateIssueKey = key("duplicate-issue");
    const beforeDuplicateTrades = tradeCount();
    await issue(page, "market_place_sell_order", duplicateIssueKey, 2, 13, { doubleClick: true });
    const duplicateIssueTrade = latestTradeByPrice(13);
    record("Double-click issue creates one trade and one settlement", tradeCount() - beforeDuplicateTrades === 1 && settlementCount(duplicateIssueKey) === 1, `trade delta=${tradeCount() - beforeDuplicateTrades}; settlements=${settlementCount(duplicateIssueKey)}`);

    const duplicateAcceptKey = key("duplicate-accept");
    const duplicateBuyerBefore = stackRow(BUYER_STACK_ID).quantity;
    const duplicateWalletBefore = walletRow(BUYER_WALLET_ID).isk_amount;
    await accept(page, "market_buy_from_sell_order", duplicateAcceptKey, duplicateIssueTrade.trade_instance_id, 2, { doubleClick: true });
    const duplicateBuyerAfter = stackRow(BUYER_STACK_ID).quantity;
    const duplicateWalletAfter = walletRow(BUYER_WALLET_ID).isk_amount;
    record("Double-click accept settles once", duplicateBuyerAfter - duplicateBuyerBefore === 2 && duplicateWalletBefore - duplicateWalletAfter === 26 && settlementCount(duplicateAcceptKey) === 1, `buyer items +${duplicateBuyerAfter - duplicateBuyerBefore}; buyer ISK ${duplicateWalletAfter - duplicateWalletBefore}; settlements=${settlementCount(duplicateAcceptKey)}`);

    const duplicateCancelIssue = await issue(page, "market_place_sell_order", key("duplicate-cancel-target"), 3, 14);
    const duplicateCancelTradeId = duplicateCancelIssue.gateway.trade_instance_id;
    const duplicateCancelKey = key("duplicate-cancel");
    const sellerBeforeDoubleCancel = stackRow(SELLER_STACK_ID).quantity;
    await cancel(page, duplicateCancelKey, duplicateCancelTradeId, { doubleClick: true });
    const sellerAfterDoubleCancel = stackRow(SELLER_STACK_ID).quantity;
    record("Double-click cancel refunds once", sellerAfterDoubleCancel - sellerBeforeDoubleCancel === 3 && settlementCount(duplicateCancelKey) === 1, `seller items +${sellerAfterDoubleCancel - sellerBeforeDoubleCancel}; settlements=${settlementCount(duplicateCancelKey)}`);

    const refreshKey = key("refresh-in-flight");
    const refreshTradesBefore = Number(psql("SELECT count(*) FROM trade_instance WHERE unit_price_isk = 17;"));
    await issue(page, "market_place_sell_order", refreshKey, 1, 17, { reloadImmediately: true });
    await page.waitForTimeout(1000);
    const refreshSettlements = settlementCount(refreshKey);
    const refreshTradesAfter = Number(psql("SELECT count(*) FROM trade_instance WHERE unit_price_isk = 17;"));
    const refreshTradeDelta = refreshTradesAfter - refreshTradesBefore;
    record("Immediate refresh commits exactly one idempotent issue", refreshSettlements === 1 && refreshTradeDelta === 1, `settlements=${refreshSettlements}; trade delta=${refreshTradeDelta}`);
    if (refreshTradeDelta === 1) {
      const refreshTrade = latestTradeByPrice(17);
      if (refreshTrade.trade_state === "OPEN") await cancel(page, key("refresh-cleanup"), refreshTrade.trade_instance_id);
    }
    await checkpoint(page, "13. Duplicate and retry safety", "Rapid create, buy, and cancel clicks produce one settlement each. Immediate refresh produces at most one order.", "gateway-response");

    const contractIssue = await issue(page, "contract_create_item_exchange", key("contract-issue"), 1, 10);
    const contractTradeId = contractIssue.gateway.trade_instance_id;
    const contractAccept = await accept(page, "contract_accept_item_exchange", key("contract-accept"), contractTradeId, 1);
    record("Contract create and accept controls execute the lifecycle", isAccepted(contractIssue) && isAccepted(contractAccept) && tradeRow(contractTradeId).trade_state === "COMPLETED", JSON.stringify(tradeRow(contractTradeId)));
    await checkpoint(page, "14. Item-exchange controls", "The contract-labeled controls map to the same settlement lifecycle and complete a one-unit item exchange.", "gateway-response");

    const directIssue = await issue(page, "direct_trade_offer", key("direct-issue"), 1, 11);
    const directTradeId = directIssue.gateway.trade_instance_id;
    const directAccept = await accept(page, "direct_trade_accept", key("direct-accept"), directTradeId, 1);
    record("Direct offer and accept controls execute the lifecycle", isAccepted(directIssue) && isAccepted(directAccept) && tradeRow(directTradeId).trade_state === "COMPLETED", JSON.stringify(tradeRow(directTradeId)));
    await checkpoint(page, "15. Direct-trade controls", "The direct-trade Offer and Accept buttons also reach settlement and complete exactly one trade row.", "gateway-response");

    const raceIssue = await issue(page, "market_place_sell_order", key("race-issue"), 2, 19);
    const raceTradeId = raceIssue.gateway.trade_instance_id;
    const raceBuyerBefore = stackRow(BUYER_STACK_ID).quantity;
    const raceWalletBefore = walletRow(BUYER_WALLET_ID).isk_amount;
    const secondContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const secondPage = await secondContext.newPage();
    await secondPage.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await setCommonFields(page, { key: key("race-accept-a"), tradeId: raceTradeId, quantity: 2 });
    await setCommonFields(secondPage, { key: key("race-accept-b"), tradeId: raceTradeId, quantity: 2 });
    const [raceResponseA, raceResponseB] = await Promise.all([
      press(page, "market_buy_from_sell_order", { key: key("race-accept-a") }),
      press(secondPage, "market_buy_from_sell_order", { key: key("race-accept-b") }),
    ]);
    await secondPage.screenshot({ path: path.join(SCREENSHOT_DIR, `${String(++screenshotSequence).padStart(2, "0")}-two-tab-race-second-tab.png`), fullPage: true });
    await secondContext.close();
    const raceBuyerAfter = stackRow(BUYER_STACK_ID).quantity;
    const raceWalletAfter = walletRow(BUYER_WALLET_ID).isk_amount;
    const raceAcceptedCount = [raceResponseA, raceResponseB].filter(isAccepted).length;
    record("Two tabs racing to accept settle exactly once", raceAcceptedCount === 1 && raceBuyerAfter - raceBuyerBefore === 2 && raceWalletBefore - raceWalletAfter === 38 && tradeRow(raceTradeId).trade_state === "COMPLETED", `accepted responses=${raceAcceptedCount}; buyer items +${raceBuyerAfter - raceBuyerBefore}; buyer ISK ${raceWalletAfter - raceWalletBefore}`);
    await checkpoint(page, "16. Two-tab acceptance race", "Two browser tabs submit different acceptance IDs against the same order. One succeeds; the other is rejected; items and ISK move once.", "gateway-response");

    if (INCLUDE_OUTAGE) {
      await waitForMarketHealthy();
      const recoveryIssue = await issue(page, "market_place_sell_order", key("recovery-issue"), 1, 23);
      record("GUI reaches Encore Market readiness before recovery request", isAccepted(recoveryIssue), responseEvidence(recoveryIssue));
      if (isAccepted(recoveryIssue)) await cancel(page, key("recovery-cleanup"), recoveryIssue.gateway.trade_instance_id);
    }

    await cancel(page, key("invalid-accept-cleanup"), invalidAcceptTradeId);

    const final = worldSnapshot();
    record("Wallet conservation invariant holds", final.totalWalletISK === initial.totalWalletISK, `${initial.totalWalletISK} -> ${final.totalWalletISK}`);
    record("Item quantity conservation invariant holds", final.totalItemQuantity === initial.totalItemQuantity, `${initial.totalItemQuantity} -> ${final.totalItemQuantity}`);
    record("Demo leaves no open trades", final.openTrades === 0, `open trades=${final.openTrades}`);

    const passed = results.filter((result) => result.passed).length;
    const failed = results.length - passed;
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
      body{margin:0;background:#101314;color:#e6ecef;font:20px/1.5 Arial,sans-serif;padding:54px}
      h1{color:#e2b650;font-size:40px;margin:0 0 22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
      section{background:#1b2022;border:1px solid #394247;border-radius:8px;padding:20px}.pass{color:#79c98c}.fail{color:#e57b68}
      code{color:#b8c5cb}ul{margin:10px 0}</style></head><body>
      <h1>GUI Simulator Reliability Gate Complete</h1><div class="grid"><section><h2>Recorded evidence</h2><p class="pass">${passed} passing checks</p><p class="fail">${failed} failed checks</p><p>Major checkpoints: ${screenshotSequence}</p></section>
      <section><h2>Integrity summary</h2><ul><li>Wallet conservation: ${results.find((item) => item.name === "Wallet conservation invariant holds")?.passed ? "PASS" : "FAIL"}</li><li>Item conservation: ${results.find((item) => item.name === "Item quantity conservation invariant holds")?.passed ? "PASS" : "FAIL"}</li><li>Final open trades: ${final.openTrades}</li><li>Duplicate/retry checks: ${results.filter((item) => /Double-click|refresh|racing/i.test(item.name)).every((item) => item.passed) ? "PASS" : "FAIL"}</li></ul></section>
      <section><h2>Harness scope</h2><ul><li>No market listing or selected-trade state</li><li>No player, wallet, or inventory views</li><li>Multi-principal development controls are intentionally all visible</li><li>Action controls disable while requests are active</li></ul></section>
      <section><h2>Artifacts</h2><p><code>artifacts/gui-simulator-demo/</code></p><p>Video, screenshots, subtitles, service logs, and assertion report.</p></section></div></body></html>`);
    await checkpoint(page, "18. QA summary", `${passed} checks pass. ${failed} GUI/UX checks expose real gaps; backend conservation and duplicate-settlement checks hold.`);

    fs.writeFileSync(path.join(ARTIFACT_ROOT, "run-results.json"), JSON.stringify({ runId, baseUrl: BASE_URL, provenance, initial, final, results }, null, 2));
    fs.writeFileSync(path.join(LOG_DIR, "browser-console.log"), consoleMessages.join("\n"), "utf8");
  } finally {
    await context.close();
    await browser.close();
  }

  const sourceVideo = await video.path();
  const finalVideo = path.join(VIDEO_DIR, "gui-simulator-qa.webm");
  fs.copyFileSync(sourceVideo, finalVideo);
  fs.rmSync(VIDEO_STAGING_DIR, { recursive: true, force: true });
  writeNarrationArtifacts();

  const serviceHealthIssues = captureServiceLogs(logSince);

  const runData = JSON.parse(fs.readFileSync(path.join(ARTIFACT_ROOT, "run-results.json"), "utf8"));
  writeRunSummary(runData.initial, runData.final, finalVideo, runData.provenance);
  process.stdout.write(`\nArtifacts written to ${ARTIFACT_ROOT}\nVideo: ${finalVideo}\n`);
  enforceSuccessfulGate(runData.results, serviceHealthIssues);
}

module.exports = { enforceSuccessfulGate, serviceEvidenceIssues };

if (require.main === module) {
  if (process.argv[2] === "--refresh-logs") {
    captureServiceLogs(process.argv[3] || logSince);
  } else {
    main().catch((error) => {
      process.stderr.write(`${error.stack || error}\n`);
      process.exitCode = 1;
    });
  }
}
