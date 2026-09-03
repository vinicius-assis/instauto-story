#!/usr/bin/env python3
"""
post_story.py — Automates the creation of Stories with a link sticker in
Meta Business Suite, placing the sticker just above the price badge (tuned
for the 1080x1920 image template used by Vinicius / Bons Achados).

============================================================================
IMPORTANT NOTICE
============================================================================
This script automates the Meta Business Suite WEB INTERFACE (clicks, field
typing, element dragging), not Meta's official API. This is NOT a
Meta-supported method and it is against the platform's Terms of Service,
which forbid UI automation outside the official channels (the API).
Possible consequences: feature restrictions, temporary or permanent
suspension of the business account if the usage pattern is flagged as
automated.

Use at your own risk. Recommendations:
  - Do not run very large batches at once (avoid looking like a "bot").
  - Keep realistic delays between actions (already included below).
  - Always review visually before clicking "Compartilhar" (Share). By
    default the script STOPS before that final click — it never shares.
============================================================================

HOW TO USE
============================================================================
1. Install the dependencies (once):
     python3 -m venv venv
     source venv/bin/activate        (Windows: venv\\Scripts\\activate)
     pip install -r requirements.txt
     playwright install chromium

2. First run — manual login (only once):
     python post_story.py --login
   This opens a visible Chromium window. Log into Meta Business Suite
   normally (including 2FA if enabled). Then, back in the terminal, press
   ENTER to save the session to "auth_state.json". Later runs do not need
   a login.

3. Normal use (one image + one link):
     python post_story.py --image path/to/image.jpg --link "https://yoursite.com/product"

   Optional parameters:
     --page "bonsachados.links"      (page/account name to select, if there
                                      is more than one)
     --sticker-text "Buy now"        (custom link sticker text)
     --sticker-y-ratio 0.73          (target vertical position of the
                                      sticker, as a fraction of the image
                                      height; default validated on the Omo
                                      artwork — see CALIBRATION below)
     --headless                      (run without showing the browser
                                      window; not recommended until you
                                      have confirmed the flow is fully
                                      calibrated)

   The script NEVER clicks "Compartilhar" (Share) — it builds the stories
   in the composer and STOPS. You review and publish manually.

============================================================================
CALIBRATION (read before the first real run)
============================================================================
The editor was walked through live on a real account (last checked 2026-09)
and confirmed:

  - Upload: the "Adicionar foto/vídeo" button triggers a native file
    chooser; Playwright intercepts it with expect_file_chooser (no OS
    popup).
  - Link sticker: "Figurinhas" panel -> "Mais figurinhas" -> "Link" button
    -> "Adicionar figurinha de link" popover with 2 <input> fields: URL (no
    maxlength) and sticker text (maxlength=25) -> the popover's "Aplicar"
    button creates the sticker.
  - The sticker is a <div> with margin-left/margin-top (px) + position
    absolute; it is moved through JS mouse listeners (not HTML5 drag), so
    the script does mousedown -> mousemoves -> mouseup.
  - The reference frame is <img._5i4g> (the 9:16 artwork rendered in the
    editor); the target position is a FRACTION of that image, measured at
    runtime.

  On the first run, RUN WITHOUT --headless, check that the sticker landed
  snug just above the badge, and adjust --sticker-y-ratio if needed
  (increase to move it down, decrease to move it up). The default (0.73)
  was calibrated on the Omo artwork (badge "R$ 80,18" at y ~= 0.80), with
  the sticker resting just above it.
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

AUTH_STATE_FILE = "auth_state.json"
BUSINESS_SUITE_URL = "https://business.facebook.com/latest/story_composer/"

# Supported manifest format version (see MANIFEST_SPEC.md).
MANIFEST_VERSION = 1
PROGRESS_SUFFIX = ".progress.json"

# Fraction of the preview height where the CENTER of the sticker should sit.
# 0.0 = top of the image, 1.0 = bottom of the image.
# Default calibrated for the "Bons Achados" template (price badge near the
# bottom, link sticker just above it, resting against it).
DEFAULT_STICKER_Y_RATIO = 0.73
STICKER_X_RATIO = 0.5  # horizontally centered, like the template frame

# Per-story position jitter, so the whole batch does not nail the exact
# same relative pixel (an easy-to-detect robotic pattern).
#   - Y: UP only (subtracted from the target) — never goes below the base
#     y_ratio, so the sticker only moves away from the price badge, never
#     closer to it.
#   - X: symmetric (both sides of the center).
STICKER_Y_JITTER_UP = 0.015   # fraction of the height: moves up by 0..this
STICKER_X_JITTER = 0.010      # fraction of the width: +/- this


def jitter_sticker_pos(y_ratio, x_ratio=STICKER_X_RATIO):
    """(x, y) with noise: y only decreases (moves up in the artwork), x
    varies to both sides."""
    y = y_ratio - random.uniform(0.0, STICKER_Y_JITTER_UP)
    x = x_ratio + random.uniform(-STICKER_X_JITTER, STICKER_X_JITTER)
    return x, y


def log(msg):
    print(f"[post_story] {msg}", flush=True)


def do_login(playwright):
    """Opens a visible browser for manual login and saves the session."""
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://business.facebook.com/")
    log("Log into Meta Business Suite in the browser window.")
    log("After logging in (and passing any 2FA), come back here and press ENTER.")
    input(">>> Press ENTER once the login is done... ")
    context.storage_state(path=AUTH_STATE_FILE)
    log(f"Session saved to {AUTH_STATE_FILE}. You do not need to log in again.")
    browser.close()


def human_drag(page, start_x, start_y, end_x, end_y, steps=25, settle_ms=15):
    """
    Simulates a human drag: mousedown, a sequence of intermediate mousemove
    events (not a direct jump), and mouseup. This is required because the
    sticker is moved through JS mouse listeners, not the native HTML5 drag —
    a direct position "jump" may not trigger the handlers correctly.
    """
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.wait_for_timeout(80)
    for i in range(1, steps + 1):
        t = i / steps
        # simple easing so it looks less robotic
        ix = start_x + (end_x - start_x) * t
        iy = start_y + (end_y - start_y) * t
        page.mouse.move(ix, iy)
        page.wait_for_timeout(settle_ms)
    page.wait_for_timeout(100)
    page.mouse.up()


def select_page(page, page_name):
    """Selects the page/account in the 'Compartilhar em' dropdown, if needed."""
    if not page_name:
        return
    try:
        dropdown = page.get_by_text("Compartilhar em").locator("..").locator("[role='combobox'], button").first
        current_text = dropdown.inner_text()
        if page_name in current_text:
            log(f"Page '{page_name}' already selected.")
            return
        dropdown.click()
        page.get_by_text(page_name, exact=False).first.click()
        log(f"Page '{page_name}' selected.")
    except Exception as e:
        log(f"Warning: could not switch the page automatically ({e}). "
            f"Check manually that '{page_name}' is selected.")


# Meta Business Suite UI selectors/texts. Isolated here because this is what
# breaks when Meta updates the interface — last validated live in 2026-09
# (see notes in each function).
#
# NOTE: the string VALUES stay in Portuguese on purpose: they must match the
# real Meta Business Suite UI in the pt-BR locale. Only the constant names
# are in English.
TXT_CREATE_STORY = "Criar story"
TXT_ADD_MEDIA = "Adicionar foto/vídeo"
TXT_EDIT = "Editar"
TXT_CREATION_TOOLS = "Ferramentas de criação"
# The "Link" sticker moved (validated live 2026-09): the "Figurinhas" panel now
# lists "Mencionar" and "Mais figurinhas"; "Link" only shows up after clicking
# "Mais figurinhas" (it used to sit directly under a "Figurinhas" entry).
TXT_MORE_STICKERS = "Mais figurinhas"
TXT_LINK = "Link"
TXT_LINK_POPOVER = "Adicionar figurinha de link"
TXT_APPLY = "Aplicar"
TXT_SHARE = "Compartilhar"

# Legacy Facebook class for the rendered preview <img> inside the editor.
# It is the exact rectangle of the Story artwork (9:16 aspect ratio).
SEL_PREVIEW_IMG = "img._5i4g"
# The positioned sticker is a <div> with inline style using margin-left/
# margin-top (px) + position:absolute + transform:rotate(...).
SEL_STICKER = 'div[style*="margin-left"][style*="position: absolute"]'


def _count_edit_row_buttons(page):
    """How many media-row 'Editar' buttons are visible right now."""
    return page.evaluate(
        """() => [...document.querySelectorAll('div[role=button]')]
              .filter(e => e.offsetParent && e.textContent.trim() === 'Editar'
                        && e.getBoundingClientRect().x > 100).length"""
    )


def _media_still_loading(page):
    """True while Meta still shows the 'Carregando mídia' indicator."""
    try:
        return page.get_by_text("Carregando mídia", exact=False).first.is_visible()
    except Exception:
        return False


def _wait_for_n_edit_buttons(page, n, timeout_s=120, context="media processing"):
    """Waits for N media-row 'Editar' buttons, logging progress."""
    log(f"Waiting for {context} (expecting {n} media item(s) ready)...")
    deadline = time.time() + timeout_s
    last = None
    next_heartbeat = time.time() + 10
    while time.time() < deadline:
        current = _count_edit_row_buttons(page)
        loading = _media_still_loading(page)
        if current != last or time.time() >= next_heartbeat:
            extra = " (Meta still shows 'Carregando mídia')" if loading else ""
            log(f"  {current}/{n} media item(s) ready{extra}...")
            last = current
            next_heartbeat = time.time() + 10
        if current >= n and not loading:
            log(f"  {n}/{n} ready.")
            return
        page.wait_for_timeout(2000)

    shot = f"error-wait-{int(time.time())}.png"
    try:
        page.screenshot(path=shot)
    except Exception:
        pass
    raise RuntimeError(
        f"Only {last}/{n} media items became ready in {timeout_s} s. "
        f"An image may have been rejected (format/size/dimensions) or the "
        f"processing stalled on Meta's side. Screenshot: {shot}."
    )


def add_all_media(page, image_paths):
    """
    Uploads ALL images at once through a single file chooser (Meta accepts
    multiple at a time; the batch is already capped at --max-per-run,
    default 10, the platform ceiling). Then waits for all N rows to become
    ready for editing.
    """
    for p in image_paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"Image not found: {p}")

    n = len(image_paths)
    files = [str(Path(p).resolve()) for p in image_paths]
    log(f"Uploading {n} image(s) at once: "
        + ", ".join(Path(p).name for p in image_paths))
    with page.expect_file_chooser() as fc_info:
        page.get_by_text(TXT_ADD_MEDIA, exact=False).first.click()
    fc_info.value.set_files(files)
    _wait_for_n_edit_buttons(page, n, timeout_s=max(120, 30 * n))
    page.wait_for_timeout(1200)  # breathing room for the thumbnails to settle

    log(f"{n} media item(s) ready for editing.")


def _edit_buttons_by_row(page):
    """
    Lists the media-row "Editar" buttons, top to bottom (index 0 = first
    uploaded media). Ignores the stray "Editar" in the page's left corner
    (x ~= 8).
    """
    buttons = page.get_by_role("button", name=TXT_EDIT)
    rows = []
    for i in range(buttons.count()):
        b = buttons.nth(i)
        try:
            box = b.bounding_box()
        except Exception:
            box = None
        if box and box["x"] > 200 and box["width"] > 0:
            rows.append((box["y"], b))
    rows.sort(key=lambda t: t[0])
    return [b for _, b in rows]


def open_row_editor(page, row_index, total_media):
    log(f"Opening the editor for media #{row_index + 1}...")
    # After applying the sticker on one media, Meta RE-PROCESSES that row and
    # its "Editar" button becomes a skeleton for a few seconds. If we open
    # the next editor in that window, we grab the wrong button (or none).
    # Wait for all N rows to come back.
    _wait_for_n_edit_buttons(page, total_media, context="post-edit re-render")
    page.wait_for_timeout(1000)

    buttons = _edit_buttons_by_row(page)
    if row_index >= len(buttons):
        raise RuntimeError(f"Found only {len(buttons)} media 'Editar' buttons; "
                           f"expected at least {row_index + 1}.")
    btn = buttons[row_index]
    modal = page.get_by_text(TXT_CREATION_TOOLS, exact=False).first
    # Sometimes the first click does not "take" (post-processing re-render).
    for attempt in range(1, 4):
        btn.click()
        try:
            modal.wait_for(state="visible", timeout=6000)
            return
        except PWTimeout:
            log(f"Editor did not open (attempt {attempt}/3), retrying...")
            page.wait_for_timeout(1000)
    raise RuntimeError("Could not open the photo editor (the 'Editar' button). "
                       "The interface may have changed.")


def add_link_sticker(page, link_url, sticker_text=None):
    """
    Real flow (validated live in 2026-09):
      1. Click "Mais figurinhas" in the editor's "Figurinhas" panel (the "Link"
         button used to sit directly under "Figurinhas"; now it is revealed by
         "Mais figurinhas").
      2. Click the "Link" button -> opens the "Adicionar figurinha de link"
         popover.
      3. The popover has 2 <input type=text>: the URL one (no maxlength) and
         the custom-text one (maxlength=25). Neither has a placeholder or
         aria-label.
      4. Click the popover's "Aplicar" button (not the modal footer one) ->
         the sticker appears over the image and the popover closes.
    """
    log("Opening the 'Mais figurinhas' panel...")
    try:
        page.get_by_role("button", name=TXT_MORE_STICKERS, exact=True).first.click(timeout=4000)
    except PWTimeout:
        page.get_by_text(TXT_MORE_STICKERS, exact=True).first.click()
    page.wait_for_timeout(400)

    log("Clicking 'Link'...")
    try:
        page.get_by_role("button", name=TXT_LINK, exact=True).first.click(timeout=4000)
    except PWTimeout:
        # fallback: Meta does not always mark the control with role=button
        page.get_by_text(TXT_LINK, exact=True).first.click()

    # Anchor on the popover heading and walk up to the first ancestor <div>
    # that contains an <input> — that is the popover body (isolates the
    # fields and the popover's "Aplicar" button from the modal footer's
    # "Aplicar").
    heading = page.get_by_text(TXT_LINK_POPOVER, exact=True)
    heading.wait_for(state="visible", timeout=10000)
    popover = heading.locator("xpath=ancestor::div[.//input][1]")

    log("Filling in the URL...")
    url_input = popover.locator("input:not([maxlength])").first
    url_input.click()
    url_input.fill(link_url)
    page.wait_for_timeout(500)  # let the UI validate the URL (enables Aplicar)

    if sticker_text:
        log(f"Filling in the custom sticker text: {sticker_text!r}")
        popover.locator('input[maxlength="25"]').first.fill(sticker_text)
        page.wait_for_timeout(200)

    log("Confirming the sticker (the popover's Aplicar)...")
    popover.get_by_role("button", name=TXT_APPLY, exact=True).click()
    heading.wait_for(state="hidden", timeout=10000)
    page.wait_for_timeout(500)


def _preview_frame_box(page):
    """Rectangle (screen px) of the Story artwork inside the editor."""
    img = page.locator(SEL_PREVIEW_IMG).last
    img.wait_for(state="visible", timeout=10000)
    box = img.bounding_box()
    if not box:
        raise RuntimeError("Could not measure the image preview in the editor "
                           f"(selector {SEL_PREVIEW_IMG}). The interface may have changed.")
    return box


STICKER_TOLERANCE = 0.02       # acceptable error in the final y_ratio
STICKER_MAX_ADJUSTMENTS = 5    # corrective drag attempts


def _sticker_center(page):
    sticker = page.locator(SEL_STICKER).last
    sticker.wait_for(state="visible", timeout=10000)
    box = sticker.bounding_box()
    if not box:
        raise RuntimeError("Link sticker not found in the preview "
                           f"(selector {SEL_STICKER}). The interface may have changed.")
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def position_sticker(page, y_ratio, x_ratio=STICKER_X_RATIO):
    """
    Drags the link sticker to (x_ratio, y_ratio) of the rendered ARTWORK.
    The drag "slips" (the sticker moves less than the cursor), so we do
    CORRECTIVE drags: measure where it stopped, drag the remainder, repeat
    until it is within STICKER_TOLERANCE.
    """
    frame = _preview_frame_box(page)
    target_x = frame["x"] + frame["width"] * x_ratio
    target_y = frame["y"] + frame["height"] * y_ratio
    log(f"Preview artwork: {frame}")
    log(f"Sticker target: x_ratio={x_ratio:.3f} y_ratio={y_ratio:.3f}  "
        f"({target_x:.0f}, {target_y:.0f})")

    for attempt in range(1, STICKER_MAX_ADJUSTMENTS + 1):
        cx, cy = _sticker_center(page)
        got = (cy - frame["y"]) / frame["height"]
        error = y_ratio - got
        if abs(error) <= STICKER_TOLERANCE:
            log(f"Sticker at y_ratio ~= {got:.3f} (ok, {attempt - 1} adjustment(s)).")
            return
        log(f"  adjustment {attempt}: at {got:.3f}, target {y_ratio:.3f} "
            f"(error {error:+.3f}) — dragging...")
        human_drag(page, cx, cy, target_x, target_y)
        page.wait_for_timeout(300)

    cx, cy = _sticker_center(page)
    got = (cy - frame["y"]) / frame["height"]
    log(f"Sticker at y_ratio ~= {got:.3f} after {STICKER_MAX_ADJUSTMENTS} adjustments "
        f"(target {y_ratio:.3f}). Check it visually.")


def apply_and_close_editor(page):
    log("Applying the editor changes...")
    tools = page.get_by_text(TXT_CREATION_TOOLS, exact=False).first
    # At this point the sticker popover is already closed, so there is only
    # one "Aplicar" (the modal footer one).
    page.get_by_role("button", name=TXT_APPLY, exact=True).last.click()
    # The modal closes and Meta re-processes the media ("Carregando mídia...").
    tools.wait_for(state="hidden", timeout=30000)
    # Wait for the dimension to reappear on the card (media re-processed with
    # the sticker).
    page.get_by_text("1920", exact=False).first.wait_for(state="visible", timeout=60000)
    page.wait_for_timeout(500)


def open_composer(page, page_name=None):
    """Opens the Story composer and selects the page."""
    log("Opening the Story composer...")
    page.goto(BUSINESS_SUITE_URL, wait_until="domcontentloaded")
    page.wait_for_selector(f"text={TXT_CREATE_STORY}", timeout=30000)
    select_page(page, page_name)


def edit_row_media(page, row_index, total_media, link_url, y_ratio,
                   sticker_text=None, x_ratio=STICKER_X_RATIO):
    """
    Edits ONE media item already loaded in the composer (row `row_index`,
    0 = the first): opens the editor, adds the link sticker, positions it
    and applies. Publishes nothing. Every step is labeled for the error log.
    """
    steps = [
        ("open media editor",
         lambda: open_row_editor(page, row_index, total_media)),
        ("add link sticker", lambda: add_link_sticker(page, link_url, sticker_text)),
        ("position sticker", lambda: position_sticker(page, y_ratio, x_ratio)),
        ("apply and close editor", lambda: apply_and_close_editor(page)),
    ]
    for name, action in steps:
        log(f"-> step: {name}")
        try:
            action()
        except Exception as e:
            raise RuntimeError(f"failed at step '{name}': {e}") from e


# NOTE: the script NEVER clicks "Compartilhar" (Share). That action is
# always manual. It builds the stories in the composer and stops; you review
# and publish by hand.


# --------------------------------------------------------------------------
# Batch mode (--manifest) + progress tracking
# --------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sleep_with_log(seconds, reason):
    log(f"Pausing for {seconds}s ({reason})...")
    time.sleep(seconds)


def load_manifest(manifest_arg):
    """
    Accepts a path to a manifest.json OR to a .zip (with manifest.json at
    the root). Returns (manifest_dict, base_dir, manifest_bytes,
    progress_path, cleanup_fn). base_dir is the folder used to resolve image
    paths. progress_path is ALWAYS next to the original file passed in (not
    inside the zip extracted to tmp), so it survives across runs.
    """
    src = Path(manifest_arg).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"Manifest not found: {src}")

    if src.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="auto_story_"))
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        mf = tmp / "manifest.json"
        if not mf.exists():
            nested = list(tmp.glob("*/manifest.json"))
            if len(nested) == 1:
                mf = nested[0]
            else:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError("manifest.json not found at the root of the zip.")
        base_dir = mf.parent
        manifest_bytes = mf.read_bytes()
        cleanup = lambda: shutil.rmtree(tmp, ignore_errors=True)
    else:
        base_dir = src.parent
        manifest_bytes = src.read_bytes()
        cleanup = lambda: None

    progress_path = src.with_name(src.stem + PROGRESS_SUFFIX)
    manifest = json.loads(manifest_bytes)
    return manifest, base_dir, manifest_bytes, progress_path, cleanup


def validate_manifest(manifest, base_dir):
    """Raises ValueError on the first inconsistency found."""
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError(
            f"manifest version = {manifest.get('version')!r}; "
            f"this script only supports {MANIFEST_VERSION}."
        )

    stories = manifest.get("stories")
    if not isinstance(stories, list) or not stories:
        raise ValueError("'stories' missing or empty in the manifest.")

    base_resolved = base_dir.resolve()
    seen = set()

    for i, s in enumerate(stories):
        sid = s.get("id")
        if not sid or not isinstance(sid, str):
            raise ValueError(f"story #{i + 1}: 'id' field required (string).")
        if sid in seen:
            raise ValueError(f"duplicate 'id' in the manifest: {sid}")
        seen.add(sid)

        if not s.get("image"):
            raise ValueError(f"story {sid}: 'image' field required.")
        img = (base_dir / s["image"]).resolve()
        try:
            inside = img.is_relative_to(base_resolved)
        except AttributeError:  # Python < 3.9
            inside = str(img).startswith(str(base_resolved) + os.sep)
        if not inside:
            raise ValueError(f"story {sid}: 'image' points outside the manifest folder.")
        if not img.exists():
            raise ValueError(f"story {sid}: image not found: {s['image']}")

        link = s.get("link", "")
        if not isinstance(link, str) or not link.startswith("https://"):
            raise ValueError(f"story {sid}: 'link' must start with 'https://' (got {link!r}).")


def resolve_story(manifest, s, y_ratio_override=None):
    """
    Applies the manifest defaults to one 'stories' item.

    The sticker position does NOT come from the manifest: it is always
    --sticker-y-ratio (if passed) or DEFAULT_STICKER_Y_RATIO, with a
    per-story jitter (see jitter_sticker_pos).
    """
    d = manifest.get("defaults", {}) or {}
    y_ratio = y_ratio_override if y_ratio_override is not None else DEFAULT_STICKER_Y_RATIO
    x_jit, y_jit = jitter_sticker_pos(y_ratio)
    return {
        "id": s["id"],
        "image": s["image"],
        "link": s["link"],
        "sticker_text": s.get("sticker_text", d.get("sticker_text")),
        "sticker_y_ratio": float(y_jit),
        "sticker_x_ratio": float(x_jit),
    }


def manifest_hash(manifest_bytes):
    return hashlib.sha256(manifest_bytes).hexdigest()


def load_progress(progress_path, mhash):
    if progress_path.exists():
        try:
            return json.loads(progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"Warning: could not read {progress_path.name} ({e}). Restarting progress.")
    return {"manifest_hash": mhash, "published": []}


def save_progress(progress_path, data):
    """Atomic write: write to .tmp and rename."""
    tmp = progress_path.with_name(progress_path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(progress_path)


def _launch(pw, args):
    browser = pw.chromium.launch(headless=args.headless, slow_mo=50)
    context = browser.new_context(
        storage_state=AUTH_STATE_FILE, viewport={"width": 1600, "height": 1000}
    )
    return browser, context.new_page()


def run_manifest(pw, args):
    """
    Builds ALL pending stories (up to --max-per-run) in a SINGLE composer —
    one image + one link sticker at a time — and STOPS before sharing, so
    you can review everything and click 'Compartilhar' once. (Meta publishes
    every media item in the composer at once.)
    """
    manifest, base_dir, mbytes, progress_path, cleanup = load_manifest(args.manifest)
    try:
        validate_manifest(manifest, base_dir)
        mhash = manifest_hash(mbytes)

        if args.reset_progress and progress_path.exists():
            progress_path.unlink()
            log(f"{progress_path.name} removed (--reset-progress).")

        progress = load_progress(progress_path, mhash)
        if progress.get("manifest_hash") != mhash:
            log("WARNING: the manifest changed since the last run (different hash).")
            if input(">>> Resume anyway? [y/N] ").strip().lower() != "y":
                log("Aborted. Use --reset-progress to start from scratch.")
                return
            progress["manifest_hash"] = mhash

        y_ratio_used = (args.sticker_y_ratio if args.sticker_y_ratio is not None
                        else DEFAULT_STICKER_Y_RATIO)
        log(f"Sticker position: base y_ratio={y_ratio_used} "
            f"(the manifest sticker_y_ratio is ignored; "
            f"{'--sticker-y-ratio' if args.sticker_y_ratio is not None else 'default'}). "
            f"Per-story jitter: Y -0..{STICKER_Y_JITTER_UP} (up only), "
            f"X 0.5 +/-{STICKER_X_JITTER}.")

        done_ids = {p["id"] for p in progress["published"] if p.get("status") == "ok"}
        all_stories = manifest["stories"]
        total = len(all_stories)
        pending = [resolve_story(manifest, s, args.sticker_y_ratio)
                   for s in all_stories if s["id"] not in done_ids]

        if not pending:
            log(f"Batch complete — {total}/{total} already published. Nothing to do.")
            return

        batch = pending[: args.max_per_run]
        log(f"{len(done_ids)}/{total} already published. {len(pending)} pending; "
            f"this run builds {len(batch)} (--max-per-run {args.max_per_run}).")
        for i, st in enumerate(batch, 1):
            log(f"  {i}. {st['image']}  ->  {st['link']}  (sticker {st['sticker_text']!r})")

        image_paths = [str((base_dir / st["image"]).resolve()) for st in batch]

        browser, page = _launch(pw, args)
        try:
            try:
                open_composer(page, manifest.get("page"))
                add_all_media(page, image_paths)
            except Exception as e:
                shot = progress_path.with_name(f"error-upload-{int(time.time())}.png")
                try:
                    page.screenshot(path=str(shot))
                    log(f"Screenshot saved to {shot.name}")
                except Exception:
                    pass
                log(f"ERROR uploading the media: {e}")
                log("Nothing was built; nothing marked. Fix it and run again.")
                return

            built, failures = [], []
            for idx, story in enumerate(batch):
                n = idx + 1
                log("")
                log(f"=== [{n}/{len(batch)}] building story (id={story['id']}) ===")
                log(f"    image={story['image']}")
                log(f"    link={story['link']}  sticker={story['sticker_text']!r}"
                    f"  x_ratio={story['sticker_x_ratio']:.3f}"
                    f"  y_ratio={story['sticker_y_ratio']:.3f}")
                try:
                    edit_row_media(
                        page, idx, len(batch),
                        link_url=story["link"],
                        y_ratio=story["sticker_y_ratio"],
                        sticker_text=story["sticker_text"],
                        x_ratio=story["sticker_x_ratio"],
                    )
                    built.append(story)
                    log(f"    OK — sticker applied on media #{n} "
                        f"({len(built)}/{len(batch)} built so far).")
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    shot = progress_path.with_name(f"error-{story['id']}-{int(time.time())}.png")
                    try:
                        page.screenshot(path=str(shot))
                        log(f"Error screenshot saved to {shot.name}")
                    except Exception:
                        pass
                    log("ERROR building this story:")
                    log(f"    id={story['id']}  image={story['image']}  ({image_paths[idx]})")
                    log(f"    link={story['link']}  sticker={story['sticker_text']!r}")
                    log(f"    error: {e}")
                    failures.append((story, str(e)))
                    if input(">>> Keep building the rest? [y/N] ").strip().lower() != "y":
                        log("Stopped. Nothing was marked as published; "
                            "run the same command to restart the batch.")
                        return

                if n < len(batch):
                    _sleep_with_log(
                        random.randint(args.pause_min, args.pause_max),
                        "pacing between edits",
                    )
                    log(f"--> moving on to build story #{n + 1}...")

            log("=" * 60)
            log(f"Loop done. {len(built)}/{len(batch)} stories built in the composer.")
            if failures:
                log(f"{len(failures)} failed (the image is in the composer, but WITHOUT a sticker):")
                for st, err in failures:
                    log(f"    - {st['image']}: {err[:120]}")

            log("")
            log("The script does NOT share anything — that action is always manual.")
            log("Review ALL stories in the browser window "
                "(use the '>' arrow in the preview to move between them) and "
                "click 'Compartilhar' yourself when it looks right.")
            resp = input(
                ">>> Did you click 'Compartilhar' and publish? "
                "[y = mark these ids as done / n = do not mark] "
            ).strip().lower()
            published = (resp == "y")

            if published:
                for story in built:
                    progress["published"].append({
                        "id": story["id"], "at": _now_iso(), "status": "ok",
                        "image": story["image"], "link": story["link"],
                    })
                save_progress(progress_path, progress)
                ok_count = sum(1 for p in progress["published"] if p["status"] == "ok")
                log(f"Progress saved: {ok_count}/{total} published.")
                remaining = total - ok_count
                if remaining > 0:
                    log(f"{remaining} left. Run the same command to build the next batch.")
                else:
                    log(f"Batch complete: {total}/{total}.")
            else:
                log("Nothing marked as published. Run the same command to redo the batch.")
        except KeyboardInterrupt:
            log("")
            log("*** Ctrl+C — run interrupted by the user. ***")
            log("Nothing was marked as published. The browser is still open: "
                "you can finish building/publishing by hand if you want.")
        except Exception as e:
            shot = progress_path.with_name(f"error-unexpected-{int(time.time())}.png")
            try:
                page.screenshot(path=str(shot))
            except Exception:
                pass
            log("")
            log(f"*** UNEXPECTED ERROR: {type(e).__name__}: {e} ***")
            log(f"Screenshot: {shot.name}. Nothing was marked as published.")
            import traceback
            log(traceback.format_exc())
        finally:
            if not args.headless:
                try:
                    input(">>> Press ENTER to close the browser... ")
                except (KeyboardInterrupt, EOFError):
                    pass
            browser.close()
    finally:
        cleanup()


def run_single(pw, args):
    y_base = (args.sticker_y_ratio if args.sticker_y_ratio is not None
              else DEFAULT_STICKER_Y_RATIO)
    x_ratio, y_ratio = jitter_sticker_pos(y_base)
    log(f"Sticker position: base y={y_base}  ->  jittered "
        f"x_ratio={x_ratio:.3f} y_ratio={y_ratio:.3f}")
    browser, page = _launch(pw, args)
    try:
        open_composer(page, args.page)
        add_all_media(page, [args.image])
        edit_row_media(page, 0, 1, args.link, y_ratio, args.sticker_text,
                       x_ratio=x_ratio)

        log("All set. The script does NOT share — that action is manual.")
        log("Review it in the browser and click 'Compartilhar' yourself.")
        input(">>> Press ENTER to finish (the browser will close)... ")
    except Exception as e:
        try:
            page.screenshot(path=f"error-{int(time.time())}.png")
            log(f"Error screenshot saved to error-{int(time.time())}.png")
        except Exception:
            pass
        log(f"ERROR: {e}")
        log("Run without --headless to see what is happening.")
        raise
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Automates a Story with a link sticker in Meta Business Suite.")
    parser.add_argument("--login", action="store_true",
                        help="Run the manual login flow and save the session.")
    parser.add_argument("--image", help="Path to the Story image (1080x1920 template).")
    parser.add_argument("--link", help="Destination URL of the link sticker.")
    parser.add_argument("--page", default=None,
                        help="Page/account name to select in 'Compartilhar em'.")
    parser.add_argument("--sticker-text", default=None,
                        help="Custom sticker text (e.g. 'Buy now').")
    parser.add_argument("--sticker-y-ratio", type=float, default=None,
                        help=f"BASE vertical position of the sticker, a 0-1 fraction of "
                             f"the artwork height. Without a manifest: default "
                             f"{DEFAULT_STICKER_Y_RATIO}. With --manifest: overrides the "
                             f"manifest value for ALL stories. Each story gets a random "
                             f"jitter: Y moves up by 0 to {STICKER_Y_JITTER_UP} (never "
                             f"down), X varies 0.5 +/-{STICKER_X_JITTER}.")
    parser.add_argument("--headless", action="store_true",
                        help="Run without showing the browser window.")

    batch = parser.add_argument_group("batch mode (--manifest)")
    batch.add_argument("--manifest",
                       help="Path to a manifest.json OR a .zip with manifest.json at the "
                            "root (see MANIFEST_SPEC.md). Replaces --image/--link.")
    batch.add_argument("--max-per-run", type=int, default=10,
                       help="Maximum stories built in one composer per run "
                            "(default: 10 — Meta's limit).")
    batch.add_argument("--pause-min", type=int, default=4,
                       help="Minimum pause (s) between editing one media item and the "
                            "next (default: 4).")
    batch.add_argument("--pause-max", type=int, default=16,
                       help="Maximum pause (s) between editing one media item and the "
                            "next (default: 16).")
    batch.add_argument("--reset-progress", action="store_true",
                       help="Delete the .progress.json file and restart the batch from scratch.")

    args = parser.parse_args()

    with sync_playwright() as pw:
        if args.login:
            do_login(pw)
            return

        if not os.path.exists(AUTH_STATE_FILE):
            log("No saved session found. Run first: python post_story.py --login")
            sys.exit(1)

        if args.manifest:
            run_manifest(pw, args)
            return

        if not args.image or not args.link:
            parser.error("--image and --link are required (or use --manifest, or --login).")

        run_single(pw, args)


if __name__ == "__main__":
    main()
