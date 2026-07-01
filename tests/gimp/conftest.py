"""Tier 2/3 pytest foundation.

Tier 2/3 REQUIRE a real GIMP. To keep the default ``pytest tests/`` run Tier-1
fast and GIMP-free, every test in this package is marked ``gimp`` and is SKIPPED
unless ``--run-gimp`` is passed::

    pytest tests/                      # Tier 1 only (gimp tests skipped)
    pytest tests/gimp --run-gimp       # Tier 2/3 against ONE headless GIMP
    pytest tests/gimp --run-gimp --regen-golden   # rebuild Tier-3 goldens

Determinism (Tier 3): the session fixture forces the CPU GEGL path
(``GEGL_USE_OPENCL=no``) and always spawns a **headless** GIMP, so tests never
touch a live GUI session. Golden compares use a per-channel max-abs-diff hard
gate (<=2 u8) plus an SSIM backstop (scikit-image if installed, else a
self-contained numpy SSIM).
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# --- paths -----------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FIXTURES_DIR = os.path.join(ROOT, "tests", "fixtures")
GOLDEN_DIR = os.path.join(FIXTURES_DIR, "golden")
OUTPUT_DIR = os.path.join(ROOT, "tests", "_output")  # gitignored render/diff artifacts


# --- CLI options -----------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption(
        "--run-gimp", action="store_true", default=False,
        help="Run the Tier 2/3 tests that require a real (headless) GIMP.",
    )
    parser.addoption(
        "--regen-golden", action="store_true", default=False,
        help="Regenerate Tier-3 golden PNGs from current GIMP output instead of comparing.",
    )


def pytest_collection_modifyitems(config, items):
    """Skip every ``gimp``-marked test unless ``--run-gimp`` was given."""
    if config.getoption("--run-gimp"):
        return
    skip = pytest.mark.skip(reason="needs --run-gimp (Tier 2/3 require a headless GIMP)")
    for item in items:
        if "gimp" in item.keywords:
            item.add_marker(skip)


# --- group loader (mirrors the spike harnesses) ----------------------------
def _load_group(name: str):
    """Import a tool group module BY PATH (no sibling-group side effects)."""
    path = os.path.join(SRC, "gimp_mcp", "tools", name + ".py")
    spec = importlib.util.spec_from_file_location("grp_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def load_group():
    """Session-cached ``load_group(name) -> module`` (the tool group under test)."""
    cache: dict[str, object] = {}

    def get(name: str):
        if name not in cache:
            cache[name] = _load_group(name)
        return cache[name]

    return get


# --- the one long-lived headless GIMP --------------------------------------
@pytest.fixture(scope="session")
def gimp():
    """ONE headless GIMP for the whole test session (§7 #6 Tier 2).

    Skips the entire GIMP tier (rather than erroring) if a headless GIMP cannot
    be reached, so the suite degrades gracefully on a machine without GIMP.
    """
    # Determinism + isolation MUST be set before the child GIMP spawns.
    os.environ.setdefault("GEGL_USE_OPENCL", "no")
    os.environ["GIMP_MCP_HEADLESS"] = "1"

    from gimp_mcp.server import GimpContext

    ctx = GimpContext(prefer_headless=True)
    try:
        info = ctx.run("_result = {'v': Gimp.version()}", undo_group=False).to_dict()
    except Exception as exc:  # boot/launch failure
        ctx.close()
        pytest.skip(f"headless GIMP unavailable: {exc!r}")
    if not info.get("ok"):
        ctx.close()
        pytest.skip(f"headless GIMP unreachable: {info.get('error')!r}")

    yield ctx
    ctx.close()


# --- Tier-3 golden-image comparison ----------------------------------------
_EXPORT_PNG = """
img = find_image(args.get("image"))
dup = img.duplicate()
if args.get("flatten"):
    try:
        Gimp.context_set_background(compat.color((255, 255, 255)))
    except Exception:
        pass
    dup.flatten()
f = Gio.File.new_for_path(args["path"])
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, f)
dup.delete()
_result = {"saved": args["path"]}
"""


def _export_png(ctx, image, path, flatten=True):
    r = ctx.run(_EXPORT_PNG, args={"image": image, "path": path, "flatten": bool(flatten)},
                undo_group=False).to_dict()
    assert r["ok"], f"export failed: {r['error']!r}"
    return path


def _read_u8(path):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def _ssim_luma(a, b, win=7):
    """SSIM on RGB luma. Uses scikit-image when available, else a numpy fallback."""
    import numpy as np

    def luma(x):
        x = x.astype(np.float64)
        return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]

    la, lb = luma(a), luma(b)
    try:
        from skimage.metrics import structural_similarity as _sk_ssim
        return float(_sk_ssim(la, lb, data_range=255.0))
    except Exception:
        pass
    # Self-contained windowed SSIM (Wang et al. 2004), uniform window — fine for
    # the tiny (<=256px) goldens we compare. Primary gate is max-diff anyway.
    from numpy.lib.stride_tricks import sliding_window_view
    if min(la.shape) < win:
        win = max(1, min(la.shape))
    wa = sliding_window_view(la, (win, win))
    wb = sliding_window_view(lb, (win, win))
    mu_a = wa.mean((-1, -2))
    mu_b = wb.mean((-1, -2))
    va = wa.var((-1, -2))
    vb = wb.var((-1, -2))
    cov = ((wa - mu_a[..., None, None]) * (wb - mu_b[..., None, None])).mean((-1, -2))
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    smap = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / (
        (mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(smap.mean())


@pytest.fixture
def assert_golden(request, gimp):
    """Return ``assert_golden(image, name, max_diff=2, min_ssim=0.995)``.

    Exports the GIMP image to ``tests/_output/<name>.png``, then compares against
    ``tests/fixtures/golden/<name>.png``. On a missing golden (first run) or
    ``--regen-golden`` it writes the golden and SKIPs (never silently passes).
    """
    import numpy as np

    regen = request.config.getoption("--regen-golden")
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _assert(image, name, max_diff=2, min_ssim=0.995, flatten=True):
        from PIL import Image
        cur_path = os.path.join(OUTPUT_DIR, name + ".png")
        _export_png(gimp, image, cur_path, flatten=flatten)
        cur = _read_u8(cur_path)
        golden_path = os.path.join(GOLDEN_DIR, name + ".png")

        if regen or not os.path.exists(golden_path):
            Image.fromarray(cur, "RGBA").save(golden_path)
            pytest.skip(f"golden {name!r} "
                        + ("regenerated" if regen else "created (first run) — eyeball + commit"))

        golden = _read_u8(golden_path)
        assert cur.shape == golden.shape, \
            f"{name}: shape {cur.shape} != golden {golden.shape}"
        diff = np.abs(cur.astype(np.int16) - golden.astype(np.int16))
        max_d = int(diff.max())
        if max_d > max_diff:
            Image.fromarray(diff.clip(0, 255).astype(np.uint8), "RGBA").save(
                os.path.join(OUTPUT_DIR, name + "_diff.png"))
        assert max_d <= max_diff, \
            f"{name}: max per-channel diff {max_d} > {max_diff} (diff PNG in tests/_output/)"
        ssim = _ssim_luma(cur, golden)
        assert ssim >= min_ssim, f"{name}: SSIM {ssim:.4f} < {min_ssim}"
        return {"max_diff": max_d, "ssim": ssim}

    return _assert
