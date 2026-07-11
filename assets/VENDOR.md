# Vendored third-party assets

## `lichess-pgn-viewer.min.js` + `lichess-pgn-viewer.css`

- **Upstream:** [lichess-org/pgn-viewer](https://github.com/lichess-org/pgn-viewer) (npm: [`lichess-pgn-viewer`](https://www.npmjs.com/package/lichess-pgn-viewer))
- **License:** **GPL-3.0-or-later** — see the upstream repo. This is a separately-licensed component; the project's own MIT license (root `LICENSE`) covers this repository's code, **not** these two files, which remain under their upstream GPL license.
- **Why vendored, not npm-installed:** the app has no JavaScript build step (it's a Python/Dash app). The pre-built ES-module bundle and its self-contained CSS are served directly from `/assets/` on demand — `assets/lpv-init.js` imports the module dynamically. Vendoring keeps the board working offline and in the Docker image with zero Node tooling.
- **Version:** not recorded in the minified bundle. **To re-vendor:** take the built `dist/` from a tagged upstream release, drop it in as these two files, and record the tag here so the version is pinned.

The board is rendered by this viewer as a local asset (not an iframe), which is why the `## AI usage` note in the README about the mount-contract remount loop refers to *this* code.
