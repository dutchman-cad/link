# Validation

## Latest live run

- SolidCraft 0.22.1, the official Linux x86_64 release (checksum checked against `SHA256SUMS.txt`),
  started with `--sample --control 17894` under Xvfb.
- Blender 5.2.0 LTS from Flathub, run headless, with the token folder shared into the sandbox
  (`--filesystem=~/.local/share/solidcraft:ro`).
- `tests/test_transport.py`: 4 passed.
- `tests/test_link.py`: passed. Checks:
  - extension install and enable;
  - sample names and count;
  - a live geometry update, keeping unchanged mesh identity, object identity, transform, modifier,
    and materials;
  - add and delete;
  - staging cleanup;
  - source colours;
  - export error and recovery;
  - unlink, bad token, and offline.

## Found by the live run, and fixed

- **Flatpak token lookup.** Blender from Flathub has its own data folder, so the token was looked
  up in the sandbox's folder instead of SolidCraft's. Inside a Flatpak sandbox, Bridge now also
  checks the host's `~/.local/share/solidcraft/control` (README: `flatpak override`).
- **Flatpak export folder.** A sandbox's `/tmp` is private, so SolidCraft's export landed out of
  Blender's reach. Inside a sandbox, Bridge exports into the sandbox's own cache folder, a real host
  path both sides can use.
- **3MF colours.** SolidCraft writes `#RRGGBB`, but Link compared `#RRGGBBAA`, so colour edits never
  matched. Colours are now normalised to `#RRGGBBAA`.

## How to run it

```sh
# Terminal 1: SolidCraft with a control port (a display, or Xvfb).
SOLIDCRAFT_CONTROL_TOKEN=$(openssl rand -hex 32) solidcraft --sample --control 17894

# Terminal 2: from this repository.
python3 -m unittest discover -s tests -p test_transport.py -v
DUTCHMAN_SHOTS="$PWD/shots" DUTCHMAN_TEST_PORT=17894 \
  flatpak run --filesystem=~/.local/share/solidcraft:ro --filesystem="$PWD" \
  org.blender.Blender -b --factory-startup --python-exit-code 1 --python "$PWD/tests/test_link.py"
```

The Blender test writes `before.png`, `after.png`, and `test-results.json` to `DUTCHMAN_SHOTS`.
