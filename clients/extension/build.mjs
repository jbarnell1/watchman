// Builds dist/chrome and dist/firefox: bundles the scripts, copies the pages, icons, stylesheet,
// the ONNX Runtime WASM files and the right manifest. `node build.mjs --zip` also writes the zips.

import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ortDist = join(here, "node_modules", "onnxruntime-web", "dist");
const zip = process.argv.includes("--zip");

for (const browser of ["chrome", "firefox"]) {
  const out = join(here, "dist", browser);
  rmSync(out, { recursive: true, force: true });
  mkdirSync(join(out, "ui"), { recursive: true });
  mkdirSync(join(out, "ort"), { recursive: true });
  mkdirSync(join(out, "icons"), { recursive: true });

  await build({
    entryPoints: {
      background: "src/background.ts",
      content: "src/content.ts",
      engine: "src/engine/page.ts",
      "ui/popup": "src/ui/popup.ts",
      "ui/options": "src/ui/options.ts",
    },
    bundle: true,
    format: "iife",
    target: ["chrome120", "firefox128"],
    outdir: out,
    sourcemap: false,
    minify: false,
    logLevel: "warning",
    define: { "process.env.NODE_ENV": '"production"' },
  });

  cpSync("src/content.css", join(out, "content.css"));
  cpSync("src/engine/engine.html", join(out, "engine.html"));
  for (const f of ["popup.html", "options.html", "ui.css"]) cpSync(`src/ui/${f}`, join(out, "ui", f));
  for (const f of readdirSync(ortDist)) {
    if (/^ort-wasm-simd-threaded\.(mjs|wasm)$/.test(f)) cpSync(join(ortDist, f), join(out, "ort", f));
  }
  if (existsSync("icons")) cpSync("icons", join(out, "icons"), { recursive: true });
  cpSync(`manifests/${browser}.json`, join(out, "manifest.json"));
  writeFileSync(join(out, "BUILD.txt"), `watchman-extension ${browser} ${new Date().toISOString()}\n`);
  console.log(`built dist/${browser}`);
}

if (zip) {
  const { execSync } = await import("node:child_process");
  for (const browser of ["chrome", "firefox"]) {
    const target = join(here, "dist", `watchman-${browser}.zip`);
    rmSync(target, { force: true });
    execSync(
      process.platform === "win32"
        ? `powershell -NoProfile -Command "Compress-Archive -Path '${join(here, "dist", browser)}\\*' -DestinationPath '${target}'"`
        : `cd ${join(here, "dist", browser)} && zip -qr ${target} .`,
    );
    console.log(`zipped ${target}`);
  }
}
