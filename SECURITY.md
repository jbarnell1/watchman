# Security policy

Watchman holds sensitive data for the households that run it, so security reports are taken
seriously and handled in private first.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository:

1. Open the [Security tab](https://github.com/jbarnell1/watchman/security).
2. Choose **Report a vulnerability**.
3. Describe the problem, the part it affects (server, extension, Android, Windows, iOS, models),
   the version or commit, and the steps to reproduce it.

Do not open a public issue or pull request for a security problem, and do not include real
household data, portal addresses, tokens or explicit images in a report. Example values are enough.

This is a one-maintainer project, so a reply may take some days. Once a fix is ready it lands on
`main` with a note crediting the reporter, unless you would rather not be named.

## What counts

Anything that breaks a promise the project makes, for example:

- a partner, or anyone else, obtaining an original image or more than the blurred versions;
- reaching the portal or the device API without the identity or token it requires;
- one household's server reading more of another's than the witness API allows;
- pausing, loosening or switching off protection without a partner's code or approval and without
  the alert that should follow;
- a web page defeating the browser extension's blur without a tamper report;
- secrets written to logs, emails or the record where they should not be.

Not in scope: someone who already controls the server replacing its code (the threat model in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) says what is and is not covered), the owner of a
device uninstalling the client (that is reported, not prevented), and mistakes by the image models
(false alarms and misses are bugs worth an ordinary issue, with numbers and never with pictures).

## Supported versions

There are no releases yet. Fixes land on `main`; run a current checkout.
