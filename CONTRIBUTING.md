# Contributing to Watchman

Thanks for looking. Watchman is a small project run by one household, built with heavy help from an
AI coding agent, so the most useful contributions right now are the ones that add evidence: a bug
report with exact steps, a test result on a device nobody has tried, a fix with a test.

## Before anything else

- **Never attach or link explicit images**, in issues, pull requests, tests or fixtures. Describe
  what happened with numbers: scores, sizes, the app or site, the thresholds in force. The browser
  extension's "Score this page" button and `models/eval/` produce numbers only for this reason.
- **Do not post private data.** Portal addresses, tailnet names, email addresses, device tokens
  and pairing codes do not belong in a public issue. Replace them with `example.com` names.
- **Security problems go to a private report**, not a public issue. See [SECURITY.md](SECURITY.md).
- Be kind. The [Code of Conduct](CODE_OF_CONDUCT.md) applies everywhere in this project.

## What this project will not accept

Watchman is for adults who choose accountability for themselves ([README](README.md#who-this-is-for-and-consent)).
Changes that work against that will be declined, whatever their quality:

- a hidden or stealth mode, or anything that hides Watchman from the person being protected;
- a way for partners to see an original image;
- a pause, bypass or loosening that does not need a partner and does not alert everyone;
- telemetry, analytics, crash reporting to a third party, or calls to a cloud model.

## Reporting a bug or asking for a feature

Use the issue templates. For a bug, say which part (server, extension, Android, Windows, iOS,
models), the version or commit, what you expected and what happened. Server logs come from
`docker compose logs server`; remove names and addresses before pasting.

## Development

Each part has its own tests, which run with fakes and no network:

```bash
cd server && uv sync && uv run pytest && uv run ruff check app tests     # Python 3.12, uv
cd models && uv sync && uv run pytest && uv run ruff check .
cd clients/extension && npm ci && npm run typecheck && npm test           # Node 22
cd clients/windows && dotnet test                                         # .NET 10, Windows
cd clients/android && flutter test && (cd android && ./gradlew :app:testDebugUnitTest)
cd clients/ios/WatchmanCore && swift test                                 # macOS
```

To look at the portal with example data and fake sign-in:

```bash
cd server && uv run python -m app.devserver      # http://127.0.0.1:8035, then /dev/as/owner, /dev/as/sam, /dev/as/pat
```

[docs/RUNBOOK.md](docs/RUNBOOK.md) has the build and install steps for every client, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how the parts fit.

## Pull requests

The working agreement is in [CLAUDE.md](CLAUDE.md). In short:

1. One pull request per slice of work, on a branch named `pr/<slug>`. Keep it small.
2. Tests ship with the code. Every external system (mail, identity, models, screen capture, the
   clock) sits behind an interface with a fake, and unit tests use the fakes.
3. The API between devices and the server is [docs/api-contract.md](docs/api-contract.md). If you
   change the API, change that document and both sides in the same pull request.
4. Anything that differs between households is a server setting with a default, never a constant.
5. A design decision gets a new entry at the top of [docs/DECISIONS.md](docs/DECISIONS.md), with
   what was rejected and why.
6. Protection fails closed and loud: any way protection can turn off must produce an event the
   partners see.
7. Plain, specific prose. No em dashes in docs, comments, commits or UI copy.
8. CI must pass. If your change touches a device client, say in the pull request which real
   device you tried it on, or that you could not.

Model weights are never committed; `models/fetch.py` downloads them and checks their hashes.

## Licence

Watchman is licensed under [AGPL-3.0](LICENSE). By contributing you agree that your contribution
is licensed under the same terms.
