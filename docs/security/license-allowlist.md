# License Allowlist

The release gate accepts only SPDX license expressions listed below. A component without an identifiable SPDX expression fails closed. Adding an identifier requires legal review and this document update.

The allowlist applies to application and language-package dependencies. Operating-system packages, base-runtime binaries, file inventory, and nested detector artifacts remain in the release SBOM for vulnerability and provenance checks but are not duplicated in the application notice. When an upstream package ships a license file without machine-readable SPDX metadata, a reviewed exact-version fallback may be recorded in `license-overrides.json`. Package upgrades fail closed until the new version is reviewed.

```text
0BSD
Apache-2.0
BSD-2-Clause
BSD-3-Clause
CC0-1.0
ISC
MIT
MPL-2.0
PSF-2.0
Python-2.0
Unlicense
Unicode-DFS-2016
Zlib
```

Acquired Anthropic document skill content is source-available material, not redistributed by this repository, and is excluded from generated SBOM and third-party notice content.
