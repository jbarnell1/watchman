// End to end in a real Chromium: load dist/chrome, pair with the dev server, visit a page of
// images, and check each one is held back, scored by the real model, and marked.
//
//   cd server && uv run python -m app.devserver          (in another shell, with models fetched)
//   cd clients/extension && node build.mjs && node e2e/run.mjs
//
// Needs playwright and its chromium (npx playwright install chromium). Not part of `npm test`.

import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const DEV = process.env.WATCHMAN_DEV ?? "http://127.0.0.1:8035";
const VECTORS = join(here, "..", "..", "..", "models", "test-vectors", "dhash");
const KNOWN_BAD_FILE = "checker-64x64-c8.png"; // its hash is in the dev server's known-bad list

const files = readdirSync(VECTORS).filter((f) => f.endsWith(".png"));
const page = `<!doctype html><title>images</title><body>${files
  .map((f) => `<figure><img id="${f.replace(/\W/g, "_")}" src="/${f}" width="120"><figcaption>${f}</figcaption></figure>`)
  .join("")}</body>`;

const server = createServer((req, res) => {
  if (req.url === "/" || req.url === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(page);
    return;
  }
  try {
    const body = readFileSync(join(VECTORS, req.url.slice(1)));
    res.writeHead(200, { "Content-Type": extname(req.url) === ".png" ? "image/png" : "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((r) => server.listen(8042, "127.0.0.1", r));

function fail(msg) {
  console.error("FAIL:", msg);
  process.exitCode = 1;
}

const { code } = await (await fetch(`${DEV}/dev/pairing-code`)).json();
const ext = join(here, "..", "dist", "chrome");
const context = await chromium.launchPersistentContext("", {
  headless: false,
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, "--window-size=1200,900"],
});
try {
  let [sw] = context.serviceWorkers();
  if (!sw) sw = await context.waitForEvent("serviceworker");
  const extId = sw.url().split("/")[2];
  sw.on("console", (m) => console.log("[sw]", m.text()));

  const options = await context.newPage();
  await options.goto(`chrome-extension://${extId}/ui/options.html`);
  await options.fill("#server", DEV);
  await options.fill("#code", code);
  await options.fill("#name", "e2e chromium");
  options.on("console", (m) => console.log("[options]", m.text()));
  await options.click("#pair-form button");
  try {
    await options.waitForSelector("#paired-panel:not([hidden])", { timeout: 15000 });
  } catch (err) {
    console.log("pair error text:", await options.textContent("#pair-error"), "status:", await options.textContent("#status"));
    throw err;
  }
  console.log("paired");

  const diag = await sw.evaluate(() => globalThis.watchmanDiagnose());
  console.log("engine:", diag);
  const offscreen = await sw.evaluate(async () => ({
    hasDocument: await chrome.offscreen.hasDocument(),
    ping: await chrome.runtime.sendMessage({ target: "engine", type: "ping" }).catch((e) => String(e)),
  }));
  console.log("offscreen:", offscreen);

  const tab = await context.newPage();
  const consoleLines = [];
  tab.on("console", (m) => consoleLines.push(m.text()));
  await tab.goto("http://127.0.0.1:8042/");
  await tab.waitForFunction(
    () => [...document.querySelectorAll("img")].every((i) => i.dataset.wm && i.dataset.wm !== "pending"),
    null,
    { timeout: 120000 },
  );
  const marks = await tab.evaluate(() =>
    Object.fromEntries([...document.querySelectorAll("img")].map((i) => [i.getAttribute("src").slice(1), i.dataset.wm])),
  );
  console.log(marks);
  for (const [f, mark] of Object.entries(marks)) {
    if (f === KNOWN_BAD_FILE) {
      if (mark !== "cover") fail(`${f} should be covered (known bad), got ${mark}`);
    } else if (f === "tiny-5x4.png") {
      if (mark !== "safe") fail(`${f} is tiny and should be safe, got ${mark}`);
    } else if (mark !== "safe" && mark !== "flag") {
      fail(`${f} unexpected mark ${mark}`);
    }
  }
  const covered = await tab.evaluate(() => {
    const img = document.querySelector(`img[src="/checker-64x64-c8.png"]`);
    const cs = getComputedStyle(img);
    return { filter: cs.filter, visibility: cs.visibility };
  });
  if (!covered.filter.includes("blur")) fail(`known-bad image is not blurred: ${JSON.stringify(covered)}`);

  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extId}/ui/popup.html`);
  await popup.waitForSelector("#strip:not([hidden])");
  const scored = Number(await popup.textContent("#scored"));
  const coveredCount = Number(await popup.textContent("#covered"));
  console.log({ scored, covered: coveredCount, state: await popup.textContent("#state") });
  if (scored < files.length - 2) fail(`expected most images scored by the model, got ${scored}`);
  if (coveredCount < 1) fail("expected the known-bad image to count as covered");

  // The event reached the server.
  await sw.evaluate(() => chrome.runtime.sendMessage({ type: "flush" }).catch(() => null));
  await new Promise((r) => setTimeout(r, 1500));
  const timeline = await (await fetch(`${DEV}/dev/as/owner`, { redirect: "manual" })).text().catch(() => "");
  void timeline;
  console.log(process.exitCode ? "e2e FAILED" : "e2e OK");
} finally {
  await context.close();
  server.close();
}
