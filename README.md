# Dutchman Link

A local M1+M2 prototype that live-links a running SolidCraft design into Blender.
Dutchman Link **talks to SolidCraft over its control channel** on `127.0.0.1`.
It does not embed, link, or modify SolidCraft. Nothing is uploaded or published.

Requires Blender 4.2+ and SolidCraft with authenticated control-channel support.

## Install

Create an extension zip with these files at its root:

```sh
python -m zipfile -c dutchman_link-0.1.0.zip blender_manifest.toml __init__.py transport.py mesh.py dutchman_link LICENSE README.md
```

In Blender: **Preferences > Get Extensions > Install from Disk**, select the zip,
and enable Dutchman Link. Open the 3D View sidebar (N), **Dutchman Link** tab.

## Use

Start SolidCraft with `--control 7878`, open a design, set the same port in Blender,
and click **Link**. Leave Token blank to automatically read the launch token from
`~/.local/share/solidcraft/control/PORT.token` (XDG and platform locations supported,
also `SOLIDCRAFT_CONTROL_DIR`). Blender from Flathub runs in a sandbox with its own data
folder: Link looks in the host's `~/.local/share/solidcraft/control` too, once Blender may
read it:

```sh
flatpak override --user --filesystem=~/.local/share/solidcraft:ro org.blender.Blender
```

Or paste the token instead. Tokens are password fields and never saved in `.blend`.
The default poll interval is 1 second. Errors appear in the sidebar and retry on the
next interval. **Unlink** stops polling and retains imported objects. Linking again
creates a fresh collection; delete an old collection yourself if no longer needed.
Opening another `.blend` disconnects the link.

Each design imports into `SolidCraft: <design name>`, with one mesh object per body.
Mesh coordinates convert SolidCraft millimetres to Blender metres, with Z up.
Changed bodies receive new mesh data in the same object. Object transforms and
modifiers survive. **Keep my materials** defaults on: material slots survive, and
polygon indices survive where topology corresponds (new faces use slot zero).
Disable it to use SolidCraft colours on subsequent changed bodies. Unchanged bodies
are untouched. New bodies are added; deleted bodies are removed. Do not manually
edit linked mesh geometry you need to keep: the next source edit replaces it.

## Transport and polling

OBJ was checked: it exports one `o` per body but replaces whitespace in names and
writes no colours/materials. 3MF exports one named object and build item per body,
with base materials including face colours. This prototype reads that flat subset
using Python's standard library. It refuses component references or explicit build
transforms; current SolidCraft exports bake placements into mesh coordinates.
`transport.export_snapshot()` is the single format boundary for a future GLB path.
Parsing and socket requests run in a worker; only the main-thread timer touches bpy.
A complete snapshot is built in a hidden staging collection before reconciliation.
Large meshes can still cause a main-thread import pause: Blender data creation
must happen on that thread. There is no overlapping export queue.

The control client is vendored from Dutchman Bridge 0.1.0 (`bridge/1`); see
[dutchman_link/vendor/VENDOR.md](dutchman_link/vendor/VENDOR.md). It handles
authentication, framing, request limits and typed errors. Polling exports 3MF on
each interval and compares decoded geometry and colour hashes, excluding ZIP
metadata. Document metadata is checked before and after export. Rapid edits can
still race an export until an atomic upstream revision is documented.

Future documented revision queries and GLB support are tracked in
[SolidCraft #6646](https://github.com/tangency-labs/solidcraft/issues/6646) and
[#1553](https://github.com/tangency-labs/solidcraft/issues/1553).

## Limits

Prototype: flat body mapping by exact source name, no hierarchy or selection sync.
Renaming a body removes/recreates its Blender object; names must be unique. Blender
may suffix display names if unrelated objects already have the same name; source
names are retained in the `dutchman_body` custom property. Body deletion removes its
Blender object, including local modifiers. Geometry changes baked into source
coordinates combine with the user's preserved Blender transform. Topology-changing
updates cannot retain semantic face assignments. Leave Edit Mode to update meshes.
One active link per Blender window. No link/token restoration on file load.
After restarting SolidCraft, Unlink/Link again to read its new launch token.
Identically named unsaved documents can have colliding revision/body metadata;
relink when switching between those until upstream exposes a document ID.
The token grants SolidCraft control access; communication stays on localhost.

## Validation

`tests/test_link.py` installs/enables a real extension zip, connects to the running
SolidCraft sample, makes two deterministic bodies, checks names/counts, renders
before/after PNGs, edits a body over the control channel, and checks geometry,
object identity, untouched mesh, materials, transform, modifiers, add/delete, cleanup,
unlink, invalid authentication and offline error handling. Background Blender has no
interactive timer loop: the test pumps the registered callback on the main thread
while real worker I/O and interval deadlines run normally.

```sh
DUTCHMAN_TEST_PORT=17894 flatpak run --filesystem=~/.local/share/solidcraft:ro --filesystem="$PWD" \
  org.blender.Blender -b --factory-startup --python-exit-code 1 --python "$PWD/tests/test_link.py"
```

See `tests/VALIDATION.md` for the local run and its environment limitations.

## License and API references

GPL-3.0-or-later; see LICENSE. This add-on is a separate GPL program. SolidCraft is
only contacted over its control channel. The vendored Bridge package is MIT OR Apache-2.0; its license files are included. Blender public API references:
[application timers](https://docs.blender.org/api/current/bpy.app.timers.html),
[Mesh.from_pydata](https://docs.blender.org/api/current/bpy.types.Mesh.html#bpy.types.Mesh.from_pydata),
and [extension manifest](https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html).
