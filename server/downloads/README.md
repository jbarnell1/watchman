Release builds copy that version's client files here, with `SHA256SUMS`, before building the
server image (`.github/workflows/release.yml`, ADR-060). The server offers them on `/downloads`.
Nothing else belongs in this folder; built files are ignored by git.
