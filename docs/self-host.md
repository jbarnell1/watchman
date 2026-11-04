# Self-hosting Watchman

Written for a household that is not ours. Follow this page from the top; everything after the
quick start is reference.

You need one always-on machine with Docker (Docker Engine with the Compose plugin on Linux, or
Docker Desktop on Windows and macOS) and git. A GPU is optional. Then one of:

- **Tailscale** (the default): a free Tailscale account, with Tailscale on every device that opens
  the portal or is protected. Nothing is exposed anywhere else and there are no passwords.
- **Your own OIDC provider**: an identity provider you already run (Pocket ID, Authelia,
  Authentik, Keycloak) and a reverse proxy with HTTPS in front of Watchman. For households that do
  not want Tailscale on every device (ADR-027).

## Quick start

```bash
git clone https://github.com/jbarnell1/watchman.git
cd watchman
./scripts/setup.sh                      # Linux, macOS, WSL
```

On Windows with Docker Desktop, in PowerShell:

```powershell
git clone https://github.com/jbarnell1/watchman.git
cd watchman
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

The script:

1. copies `.env.example` to `.env` if there is no `.env` yet;
2. asks whether people sign in with Tailscale or with your OIDC provider, and asks for what that
   mode needs (an auth key, or the issuer, client ID, client secret and portal address). Secrets are
   read without echo and stored only in `.env`;
3. runs `models/fetch.py`, which downloads every model and checks it against the SHA-256 in
   `models/manifest.json`, and explains what to do if some are missing (see Models below);
4. runs `docker compose up -d --build`.

Run it again at any time to change an answer; Enter keeps what is in `.env`.

Then open the portal: `https://watchman.<your-tailnet>.ts.net` in Tailscale mode, or the address
your reverse proxy serves in OIDC mode. The first person to open it becomes the admin and is
walked through setup: household name, time zone, the protected person, the first partner and, if
you have one, an SMTP account for email. Pair devices next (Devices, below).

## Signing in: Tailscale

Before running the script, make an auth key in the Tailscale admin console (Settings, Keys:
reusable, pre-authorized; tagged if your tailnet policy uses tags, in which case also give the
script `--advertise-tags=tag:<your tag>` as the extra flags). The server joins your tailnet as the
node `watchman` (`WATCHMAN_NODE_NAME` in `.env` renames it). If the admin console asks you to
approve the node, approve it once.

Nothing is reachable from the public internet. There are no passwords: the portal knows who you are
from the headers `tailscale serve` adds, and trusts them only from the sidecar on loopback.

## Signing in: your own OIDC provider (Pocket ID)

In Pocket ID (other providers have the same fields under other names):

1. OIDC Clients, Add: name it Watchman, callback URL `https://<portal address>/auth/callback`,
   keep PKCE on. Leave it confidential (you get a client secret) or make it public (PKCE only, no
   secret); Watchman supports both.
2. Allowed user groups: choose the group that holds your household and partners. On a fresh
   server the first person to sign in becomes Watchman's admin, so do this before the first start.
3. Make sure every person has an email address: Watchman's login is the `email` claim,
   lower-cased, and partners' logins on the People page must match it.

Run the setup script and choose OIDC, or set these in `.env` by hand:

```bash
COMPOSE_FILE=compose.yaml:deploy/compose.oidc.yaml
COMPOSE_PATH_SEPARATOR=:
WATCHMAN_OIDC_ISSUER=https://id.example.home
WATCHMAN_OIDC_CLIENT_ID=<client id>
WATCHMAN_OIDC_CLIENT_SECRET=<client secret, or empty for a public client>
WATCHMAN_OIDC_REDIRECT_URL=https://watchman.example.home/auth/callback
WATCHMAN_PUBLISH=127.0.0.1:8030          # where your reverse proxy connects
```

`deploy/compose.oidc.yaml` leaves the Tailscale sidecar out and publishes the server's port on
`WATCHMAN_PUBLISH` instead. Leave `TS_AUTHKEY` at its placeholder. Point your reverse proxy at that
address, over HTTPS, for every path (the portal, `/auth/*` and the device API under `/api/v1`).
With Caddy, for example:

```
watchman.example.home {
    reverse_proxy 127.0.0.1:8030
}
```

Keep the proxy on your home network or VPN: Watchman is built to stay off the public internet
(ADR-006), and protected devices away from home must still reach it to report. A sign-in lasts a
week (`WATCHMAN_SESSION_HOURS`); "Sign out" at the bottom of the menu ends it on that browser. The
session key is created in `data/keys/session.key` on first start.

## Models

Weights are not in the repository. `models/fetch.py` (run by the setup script) downloads them into
`models/cache/`, which the compose file mounts read-only into the server, and refuses any file whose
SHA-256 differs from the manifest. Devices download their copies from your server, so phones never
contact Hugging Face or GitHub.

```bash
python3 models/fetch.py                                   # or: cd models && uv run python fetch.py
python3 models/fetch.py --from-artifact ~/watchman-models # copy verified files from a folder
```

Two kinds of file need more than a download:

- **The Marqo crop classifier** (`nsfw-384.onnx`, `nsfw-384-int8.onnx`) has no ONNX upstream; it is
  exported from the published weights, which needs torch. Either download the `watchman-models`
  artifact from this repository's Actions (the models workflow, a run started by hand), unzip it
  and pass the folder with `--from-artifact`; or install the toolchain and export it yourself
  (`cd models && uv sync --group export && uv run python fetch.py --export`, a few GB); or copy
  `models/cache` from another Watchman machine and run `fetch.py` to verify it.
- **NudeNet** comes from a GitHub release that GitHub only serves to signed-in clients. Set
  `GITHUB_TOKEN` to a token from any GitHub account (the setup script uses `gh auth token` if the
  GitHub CLI is signed in), or use the artifact folder.

The server starts without them, but devices cannot download a model it does not have, and without
the second opinion model it uses a stand-in that scores 0. `/healthz` shows how many files verified
(`"models": 5` when all are present). Restart the server after fetching:
`docker compose restart server`.

To run the second opinion on an NVIDIA GPU, install the NVIDIA container toolkit and start with
`docker compose -f compose.yaml -f compose.gpu.yaml up -d`, then choose GPU under Settings,
Protection.

## Partners

Two ways, chosen per partner on the People settings page.

- Linked: the partner has a portal login, and gets the review page, timed peeks and pause codes.
  In Tailscale mode they need a Tailscale account: in your admin console, share the `watchman`
  node with them (Machines, the node, Share); they accept the share and open the portal. In OIDC
  mode they need an account in your identity provider, in the allowed group. Either way, their
  login must match what you entered.
- Email only: the partner gets the digest and alerts with heavy blur, and pause codes by email
  when the protected person asks for one. No portal.

Once your partners are set up, each linked partner should open Your key and make a key with a
passphrase only they know. From then on nothing that loosens protection changes without a
partner approving it with their key, and the addresses and peekable pictures partners review are
sealed so only partner keys open them (ADR-053). A partner who loses their passphrase makes a new
key; everyone is told and the new key waits three days.

## Watching each other's households

If your partner runs Watchman too, each server can keep an eye on the other's record (ADR-054).
On the household being watched, the partner opens Record and presses Let my own server watch; it
shows a token once. On the partner's own server, the admin opens Watching and adds the other
server's address, the token and the email that should hear about problems. The watching server
must be able to reach the other one over Tailscale: share the other `watchman` node with the
partner's tailnet, and check from the watching server that `/healthz` answers. Problems and a
server that stays unreachable for 30 minutes are emailed through the watching server's own mail.

## Devices

On the Devices page create a pairing code (valid 15 minutes, one device). Type it into the Android
app, the Windows installer or a browser extension, with the portal address as the server. Each
device gets its own token; revoking a device on that page ends it and alerts the partners. The
device API is the same in both sign-in modes.

## Email

Settings, Email takes any SMTP account (a mailbox at your domain, a transactional provider, or a
relay you already run). Without it, alerts and digests are only visible on the portal.

## DNS filtering

Optional. Run AdGuard Home (or any filtering resolver) on your tailnet or home network, set it as
the protected devices' resolver, and add each protected device as a client with adult categories
blocked and safe search forced. Put its URL under Settings, General so the Devices page links to it.

## Monitoring

`/healthz` needs no sign-in and returns only `ok`, the server version, the verified model count and
whether the second opinion is loaded. `deploy/gatus/watchman.yaml` and
`deploy/homepage/services.yaml` are a ready Gatus check and Homepage tile; `docs/RUNBOOK.md` has
the details.

## Backups

Everything is in `./data`: the SQLite database, the encrypted blurred images, the image key and the
session key. Back up the whole folder. Without `data/keys/images.key` the stored images cannot be
read, which is intended; keep the key with the backup.

## Updating

```bash
git pull
docker compose up -d --build
```

`COMPOSE_FILE` in `.env` keeps OIDC mode across updates. Updates come from this repository when you
choose. The server never talks to anyone else's server; in OIDC mode it talks to your own identity
provider.
