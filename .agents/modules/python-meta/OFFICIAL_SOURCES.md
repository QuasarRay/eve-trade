# Optional Python-meta sources

Version ranges were reviewed on 2026-08-08 against the authoritative PyPI project pages:

- <https://pypi.org/project/mcpyrate/>
- <https://pypi.org/project/unpythonic/>

The extension remains optional. Discovery reads distribution metadata only; the explicit verifier imports
and functionally probes installed packages, and exits nonzero when either package is absent or outside the
tested major-version range.
