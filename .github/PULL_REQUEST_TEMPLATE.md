<!-- Thanks for contributing to GIMP Studio MCP! -->

## What & why

<!-- What does this change, and why? Link any related issue. -->

## How tested

<!-- Tier-1 (`pytest tests/`) is required. If you touched tool behavior, also run
     the GIMP tier (`pytest tests/gimp --run-gimp`) and say which GIMP version. -->

- [ ] `ruff check .` is clean
- [ ] `pytest tests/` passes (Tier-1, no GIMP)
- [ ] GIMP-tier tests run where behavior changed (`pytest tests/gimp --run-gimp`)
- [ ] Tool count / `EXPECTED_TOOLS` updated if tools were added or removed
