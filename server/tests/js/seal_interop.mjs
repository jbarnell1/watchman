// Runs app/portal/static/seal.js in Node for tests/test_keys.py: the browser half of ADR-053.
// Phase "setup": make keys, sign a payload, print what the browser would send.
// Phase "open": unlock the stored bundle and open an envelope the server sealed.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
new Function(readFileSync(path.join(here, "..", "..", "app", "portal", "static", "seal.js"), "utf8"))();
const S = globalThis.WatchmanSeal;
const input = JSON.parse(readFileSync(0, "utf8"));

if (input.phase === "setup") {
  let sent = null;
  const fp = await S.setup(input.partner_id, input.passphrase, async (url, body) => { sent = body; return {fingerprint: "test"}; });
  const keys = await S.unlocked();
  const approval = await S.sign(input.partner_id, input.action, input.params, keys, async () => ({nonce: input.nonce}));
  console.log(JSON.stringify({body: sent, approval, fp}));
} else {
  const n = await S.unlock(input.passphrase, {keys: [{partner_id: input.partner_id, wrapped: input.wrapped, fingerprint: "x"}]});
  const keys = await S.unlocked();
  const plain = await S.open(input.envelope, input.aad, input.partner_id, keys[input.partner_id].enc);
  console.log(JSON.stringify({unlocked: n, text: new TextDecoder().decode(plain)}));
}
