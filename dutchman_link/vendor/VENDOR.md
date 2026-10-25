# Vendored dependencies

solidcraft_bridge 0.1.0 implements bridge/1, copied from Dutchman Bridge's
`python/solidcraft_bridge` directory. No modifications. Upstream repository has
no remote yet; the sibling `dutchman-bridge` checkout is the source for this copy.
Licensing: MIT OR Apache-2.0; both complete texts are beside the package.
Dutchman Link's Blender integration and 3MF decoder remain GPL-3.0-or-later.

To update, copy the package's Python files and both license files, update this
version, and run the Bridge and Link transport tests. Include `dutchman_link/`
and `mesh.py` in extension archives.
