// Builds dist/chrome and dist/firefox: bundles the scripts, copies the pages, icons, stylesheet,
// the ONNX Runtime WASM files and the right manifest. `node build.mjs --zip` also writes the zips.

import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ortDist = join(here, "node_modules", "onnxruntime-web", "dist");
const zip = process.argv.includes("--zip");
// The release workflow passes the tag (ADR-059). Manifests take only numbers; Chrome shows the
// full version, pre-release suffix included, as version_name.
const version = process.env.WATCHMAN_VERSION || JSON.parse(readFileSync(join(here, "package.json"), "utf8")).version;
const numeric = version.split("-")[0];

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
  const manifest = JSON.parse(readFileSync(join(here, "manifests", `${browser}.json`), "utf8"));
  manifest.version = numeric;
  if (browser === "chrome" && version !== numeric) manifest.version_name = version;
  writeFileSync(join(out, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  writeFileSync(join(out, "BUILD.txt"), `watchman-extension ${browser} ${version} ${new Date().toISOString()}\n`);
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
