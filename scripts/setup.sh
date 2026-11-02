#!/usr/bin/env bash
# Set up a Watchman server on this machine (Linux, macOS or WSL with Docker).
#
#   ./scripts/setup.sh
#
# It creates .env from .env.example if there is none, asks how people sign in to the portal
# (Tailscale or your own OIDC provider) and for the values that mode needs, fetches and verifies
# the models, and starts the stack with `docker compose up -d --build`. Run it again to change
# answers: Enter keeps what is already in .env. Nothing is sent anywhere except the model
# downloads listed in models/manifest.json and your own identity provider.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"

say() { printf '%s\n' "$*"; }
die() { printf 'setup: %s\n' "$*" >&2; exit 1; }

# The value of KEY in .env, or nothing. Commented lines do not count.
get_env() {
  [ -f "$ENV_FILE" ] || return 0
  awk -v k="$1" 'index($0, k "=") == 1 { v = substr($0, length(k) + 2) } END { printf "%s", v }' "$ENV_FILE"
}

# Set KEY=VALUE in .env, replacing an existing or commented-out line, else appending.
set_env() {
  local tmp
  tmp="$(mktemp "$ROOT/.env.XXXXXX")"
  KEY="$1" VALUE="$2" awk '
    BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
    {
      line = $0; sub(/^#[ \t]*/, "", line)
      if (index(line, k "=") == 1) { if (!done) { print k "=" v; done = 1 }; next }
      print
    }
    END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
}

# Comment out KEY in .env if it is set.
unset_env() {
  local tmp
  tmp="$(mktemp "$ROOT/.env.XXXXXX")"
  KEY="$1" awk 'BEGIN { k = ENVIRON["KEY"] } index($0, k "=") == 1 { print "# " $0; next } { print }' \
    "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
}

# ask PROMPT KEY [DEFAULT]: prompt with the current value (or DEFAULT) offered; Enter keeps it.
ask() {
  local prompt="$1" key="$2" current reply
  current="$(get_env "$key")"
  current="${current:-${3:-}}"
  read -r -p "$prompt${current:+ [$current]}: " reply
  set_env "$key" "${reply:-$current}"
}

# ask_secret PROMPT KEY [optional]: read without echo; Enter keeps the current value.
ask_secret() {
  local prompt="$1" key="$2" optional="${3:-}" current reply
  current="$(get_env "$key")"
  case "$current" in *...) current="" ;; esac          # the .env.example placeholder
  while :; do
    read -r -s -p "$prompt${current:+ (Enter keeps the current one)}: " reply
    printf '\n'
    reply="${reply:-$current}"
    if [ -n "$reply" ] || [ -n "$optional" ]; then break; fi
    say "This one is needed."
  done
  set_env "$key" "$reply"
}

# 1. Prerequisites and .env

command -v docker >/dev/null 2>&1 || die "Docker is not installed. Install Docker Engine or Docker Desktop first."
docker compose version >/dev/null 2>&1 || die "docker compose is missing. Install the Compose plugin."
if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
  say "Created .env from .env.example."
fi
chmod 600 "$ENV_FILE"

# 2. How people sign in

say ""
say "How will people sign in to the portal?"
say "  t  Tailscale: everyone opens it on your tailnet; no passwords anywhere (the default)."
say "  o  OIDC: your own identity provider, such as Pocket ID, behind your own reverse proxy."
current_mode="t"
case "$(get_env COMPOSE_FILE)" in *compose.oidc.yaml*) current_mode="o" ;; esac
read -r -p "Tailscale or OIDC? (t/o) [$current_mode]: " mode
mode="${mode:-$current_mode}"

case "$mode" in
  t|T|tailscale)
    mode="tailscale"
    unset_env COMPOSE_FILE
    unset_env COMPOSE_PATH_SEPARATOR
    say ""
    say "Make an auth key in the Tailscale admin console (Settings, Keys): reusable, pre-authorized,"
    say "tagged if your tailnet policy uses tags."
    ask_secret "Tailscale auth key (tskey-auth-...)" TS_AUTHKEY
    ask "Node name on the tailnet" WATCHMAN_NODE_NAME watchman
    ask "Extra tailscale up flags, for example --advertise-tags=tag:watchman (Enter for none)" TS_EXTRA_ARGS
    ;;
  o|O|oidc)
    mode="oidc"
    set_env COMPOSE_FILE "compose.yaml:deploy/compose.oidc.yaml"
    set_env COMPOSE_PATH_SEPARATOR ":"
    say ""
    say "In your identity provider, create an OIDC client for Watchman with the callback URL"
    say "<portal address>/auth/callback, and allow only your household's group to use it."
    say "The first person to sign in becomes Watchman's admin."
    ask "Issuer URL, for example https://id.example.home" WATCHMAN_OIDC_ISSUER
    ask "Client ID" WATCHMAN_OIDC_CLIENT_ID
    ask_secret "Client secret (Enter for a public client)" WATCHMAN_OIDC_CLIENT_SECRET optional
    current_url="$(get_env WATCHMAN_OIDC_REDIRECT_URL)"
    current_url="${current_url%/auth/callback}"
    read -r -p "Portal address as your reverse proxy serves it, for example https://watchman.example.home${current_url:+ [$current_url]}: " portal
    portal="${portal:-$current_url}"
    [ -n "$portal" ] || die "The portal address is needed for the callback URL."
    set_env WATCHMAN_OIDC_REDIRECT_URL "${portal%/}/auth/callback"
    ask "Host address and port your reverse proxy forwards to" WATCHMAN_PUBLISH 127.0.0.1:8030
    ;;
  *) die "Answer t or o." ;;
esac

# 3. Models

say ""
say "Fetching and verifying the models (models/manifest.json)."
say "If you downloaded the watchman-models artifact from this repository's Actions (models"
say "workflow), unzip it and give the folder here. Otherwise press Enter."
read -r -p "Artifact folder (Enter to skip): " artifact
fetch_args=()
if [ -n "$artifact" ]; then
  [ -d "$artifact" ] || die "$artifact is not a folder."
  fetch_args+=(--from-artifact "$artifact")
fi
if [ -z "${GITHUB_TOKEN:-}" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  # GitHub sends anonymous downloads of the NudeNet release files to a sign-in page.
  GITHUB_TOKEN="$(gh auth token)"
  export GITHUB_TOKEN
fi
if command -v uv >/dev/null 2>&1; then
  fetch=(uv run --directory "$ROOT/models" python fetch.py)
elif command -v python3 >/dev/null 2>&1; then
  fetch=(python3 "$ROOT/models/fetch.py")
else
  die "Neither uv nor python3 is installed; one of them runs models/fetch.py."
fi
models_ok=1
"${fetch[@]}" ${fetch_args[@]+"${fetch_args[@]}"} || models_ok=0

if [ "$models_ok" = 0 ]; then
  missing=()
  for f in nsfw-384.onnx nsfw-384-int8.onnx nudenet-320n.onnx nudenet-640m.onnx vit-base-nsfw-int8.onnx; do
    [ -f "$ROOT/models/cache/$f" ] || missing+=("$f")
  done
  say ""
  say "Some model files are missing or did not verify: ${missing[*]:-see the lines above}."
  case " ${missing[*]:-} " in
    *nsfw-384*)
      say "The Marqo classifier files (nsfw-384.onnx and nsfw-384-int8.onnx) are not downloaded:"
      say "they are exported from the published weights, which needs torch. Three ways to get them:"
      say "  1. Download the watchman-models artifact (Actions, models, a run started by hand),"
      say "     unzip it, and run this script again with that folder."
      say "  2. Install the export toolchain and export here (a few GB of downloads):"
      say "       cd models && uv sync --group export && uv run python fetch.py --export"
      say "  3. Copy models/cache from another Watchman machine and run fetch.py to verify it."
      ;;
  esac
  case " ${missing[*]:-} " in
    *nudenet*)
      say "The NudeNet files come from a GitHub release that GitHub serves only to signed-in"
      say "clients: set GITHUB_TOKEN to a token from any GitHub account (or sign in with gh) and"
      say "run this script again, or use the artifact folder."
      ;;
  esac
  say "The server starts without them, but devices cannot download a model it does not have,"
  say "and without vit-base-nsfw-int8.onnx the second opinion is a stand-in that scores 0."
  read -r -p "Start the server anyway? (y/n) [y]: " go_on
  case "${go_on:-y}" in y|Y|yes) ;; *) say "Stopped before starting the server."; exit 1 ;; esac
fi

# 4. Start

say ""
say "Starting: docker compose up -d --build"
docker compose up -d --build

say ""
if [ "$mode" = "tailscale" ]; then
  node="$(get_env WATCHMAN_NODE_NAME)"
  say "Done. Open https://${node:-watchman}.<your-tailnet>.ts.net from a device on your tailnet."
  say "If the node waits for approval: docker compose exec tailscale tailscale status"
else
  say "Done. Point your reverse proxy at $(get_env WATCHMAN_PUBLISH) and open ${portal%/}."
fi
say "The first person to open the portal becomes the admin and is walked through setup."
