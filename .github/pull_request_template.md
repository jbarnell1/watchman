## What this changes

<!-- One or two plain sentences. Link the issue if there is one. -->

## How it was checked

<!-- Which tests you ran, and for a device client which real device you tried it on (or that you could not). -->

## Checklist

- [ ] Tests ship with the change and pass locally (see CONTRIBUTING.md for the commands).
- [ ] If the device API changed, `docs/api-contract.md` and both sides changed in this pull request.
- [ ] If a design choice was made, it has an entry at the top of `docs/DECISIONS.md`.
- [ ] Household values are settings with defaults, not constants.
- [ ] Nothing here lets protection turn off, or loosen, without a partner and without an alert.
- [ ] No explicit images, model weights, secrets or household details are in the diff.
- [ ] No em dashes in docs, comments, commit messages or UI copy.
