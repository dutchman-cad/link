# Local validation — 2026-10-25

The extension implementation is ready for review, but the requested full
SolidCraft → Blender proof, sample renders and commit are **not completed**.

## Completed checks

- Blender 5.2.0 LTS (`fbe6228777e7`), installed Flatpak application's own executable:
  extension ZIP installs and enables successfully through Blender's extension API.
- `tests/test_reconcile.py`: passed. Checks named objects, changed geometry,
  preserved object identity, unchanged mesh identity, local display renaming,
  transforms, modifiers, manual materials, source colours with preservation off,
  adding/deleting bodies, staging cleanup and isolation after a failed staging import.
- Two standard-library transport checks pass in the current sandbox: named/coloured
  3MF decoding with mm-to-metre conversion and content hashes independent of ZIP
  metadata; revision changes at equal undo depth.
- All three transport tests (including a real local socket server checking
  first-request authentication and export-error replies) passed before the session's
  permissions changed. The socket test is now blocked, not counted as a current pass.
- Python compilation passes. Blender 4.2 compatibility has not been run.

Current Blender reconciliation command (no network or SolidCraft required):

```sh
flatpak run org.blender.Blender -b --factory-startup --python-exit-code 1 --python tests/test_reconcile.py
```

The sandbox prevents the Flatpak launcher creating `~/.var/app/org.blender.Blender`.
The checks used the same installation's executable directly:
`/var/lib/flatpak/app/org.blender.Blender/x86_64/stable/active/files/blender/blender`.
Blender printed the reconciliation pass, but its PulseAudio shutdown hung under
socket restrictions. A local runner executes the test, flushes output, then calls
`os._exit(0)` only after every assertion completes, avoiding that shutdown. That
runner completed successfully. It is in `.local/run_reconcile.py`; its log is
`.local/reconcile.log`. This does not establish the requested Flatpak-launcher test.

## SolidCraft build

Created a new detached worktree at
`/var/home/ray/Documents/GitHub/solidcraft-dutchman-link-test`, from local
`origin/main` commit `d5eca6cc7837bbf8fbe933234624ef9d1f374a4d`.
No SolidCraft source was modified. Initial build used:

```sh
RUSTUP_TOOLCHAIN=1.98.0 \
CARGO_TARGET_DIR=/var/home/ray/Documents/GitHub/solidcraft-dutchman-link-test/target-link \
CARGO_BUILD_JOBS=6 nice -n 10 cargo build --release -p solidcraft
```

The first attempt failed because ccache's default folder was read-only. The retry
used a dedicated ccache in the worktree. It passed that dependency and reached
SolidCraft IO and cadmpeg registry compilation, but stopped during the prior session
before producing a SolidCraft executable. Original log:
`/var/home/ray/Documents/dutchman-link-build.log`.

After write access narrowed, the partial target was copied into this repo at
`.local/solidcraft/target-link` for a resumable build. A new build would need:

```sh
cd /var/home/ray/Documents/GitHub/solidcraft-dutchman-link-test
RUSTUP_TOOLCHAIN=1.98.0 \
CARGO_TARGET_DIR=/var/home/ray/Documents/GitHub/dutchman-link/.local/solidcraft/target-link \
CCACHE_DIR=/var/home/ray/Documents/GitHub/dutchman-link/.local/solidcraft/ccache \
CARGO_BUILD_JOBS=6 nice -n 10 cargo build --offline --release -p solidcraft
```

The resumed build waited for machine load below 24, as instructed by SolidCraft's
AGENTS.md. Load remained above that threshold; the waiting process was stopped
once the socket restriction made full integration impossible. No build is left
running by this session. The cache is on the home disk, is private to this worktree,
and is excluded from the add-on package and Git.

## Blocked checks and artifacts

The current sandbox denies `socket.socket(AF_INET)` with
`PermissionError: [Errno 1] Operation not permitted`, including localhost. Therefore
SolidCraft's authenticated local control service cannot be exercised here.
Xvfb `:94` was reachable before the permission change. SolidCraft was not launched.

`tests/test_link.py` installs/enables the extension, then fails at the absent launch
token because SolidCraft is not running. Its geometry edit, add/delete, material,
colour, export-error recovery, offline and bad-token integration assertions have
not been executed. Its background driver pumps the actual registered timer callback
and updates the dependency graph while real workers and polling deadlines run;
background Blender does not run the interactive timer loop on its own.

No sample before/after PNGs were produced. Intended paths remain:
`/home/ray/Documents/dutchman-link-shots/before.png` and `after.png`.
The current sandbox also excludes that directory from writable roots. No synthetic
fixture renders are presented as proof of a SolidCraft live link.

Git staging and the requested Ray commit command both fail with:
`Unable to create .../.git/index.lock: Read-only file system`.
There is no commit hash. No remote was added, nothing was pushed or published.
The pre-existing staged BRIEF.md was left unchanged.

## Remaining run after permissions are restored

Finish the build above, then start its executable under the existing display:

```sh
DISPLAY=:94 SOLIDCRAFT_CONTROL_TOKEN="$(openssl rand -hex 32)" \
/var/home/ray/Documents/GitHub/dutchman-link/.local/solidcraft/target-link/release/solidcraft \
  --sample --control 17894
```

From the add-on repo:

```sh
python -m unittest discover -s tests -p test_transport.py -v
DUTCHMAN_TEST_PORT=17894 flatpak run org.blender.Blender -b --factory-startup \
  --python-exit-code 1 --python tests/test_link.py
```

Inspect both PNGs and the test-results JSON before claiming integration passes.
Then stage the implementation and tests and commit using Ray's explicitly supplied
identity. No push is required or authorized.

Prepared commit command (only after the real integration test passes):

```sh
git add .gitignore LICENSE README.md __init__.py blender_manifest.toml transport.py tests/
git -c user.name=Ray -c user.email=raykholo@users.noreply.github.com commit -F .local/commit-message.txt
```
