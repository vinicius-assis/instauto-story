# post_story.py — automated Story with a link sticker (Meta Business Suite)

A [Playwright](https://playwright.dev/python/) script that automates the
creation of Stories in Meta Business Suite, dropping a **link sticker** just
above the price badge, calibrated for the 1080x1920 image template used by
Bons Achados.

- **What it does:** uploads an image (or a batch of images), opens the
  Story editor, adds a link sticker with your URL, drags it to a calibrated
  position, clicks *"Compartilhar"* (Share) to publish it, and — in batch
  mode — repeats automatically for the next batch of up to 10 until
  everything in the manifest is published.
- **Publishing is automatic.** The script clicks *"Compartilhar"* itself, for
  every batch, with no manual confirmation step. There is no flag to fall
  back to a review-only mode; run a small batch without `--headless` first
  to confirm the flow before trusting it with a large one.

---

## Table of contents

- [Warning: Terms of Service](#warning-terms-of-service)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Install](#install-once)
- [First run: login](#first-run-login)
- [Single mode (one image + one link)](#single-mode-one-image--one-link)
- [Batch mode (`--manifest`)](#batch-mode---manifest)
- [Calibrating the sticker position](#calibrating-the-sticker-position)
- [Progress tracking / resume](#progress-tracking--resume)
- [Troubleshooting](#troubleshooting)

---

## Warning: Terms of Service

This automates the Meta Business Suite **web interface** — it does not use
Meta's official API. UI automation outside the official channels is against
Meta's Terms of Service, and there is a real risk of feature restriction or
account suspension if the usage is flagged as automated.

Mitigations built into the script:

- Realistic delays between actions (`slow_mo`, human-like drag, random
  4–16 s pauses between edits, and a longer 30–90 s pause between batches).
- Per-story position **jitter** so the sticker does not land on the exact
  same relative pixel across a whole batch.

Because the script now clicks *"Compartilhar"* itself and loops
unattended across batches, the account-suspension risk above is higher
than a review-then-publish-by-hand flow. Keep batches small, watch the
first run without `--headless`, and use it sparingly.

---

## How it works

The script drives a real Chromium window through Playwright. The end-to-end
flow was walked through live on a real account (2026-08) and the selectors
were fixed against the actual DOM.

```
open_composer ──> add_all_media ──> for each media row: edit_row_media ──> click_share ──> next batch (if any)
                                        │
                                        ├─ open_row_editor      (click the row's "Editar")
                                        ├─ add_link_sticker      ("Figurinhas" -> "Link" -> popover)
                                        ├─ position_sticker      (corrective drags to the target y)
                                        └─ apply_and_close_editor (modal footer "Aplicar")
```

Key mechanics:

| Step | Detail |
|---|---|
| **Upload** | The *"Adicionar foto/vídeo"* button opens a **native file chooser**; Playwright intercepts it with `expect_file_chooser` (no OS popup). Multiple images can be sent at once. The script then waits for every media row's *"Editar"* button to appear and for the *"Carregando mídia"* indicator to clear before touching anything. |
| **Media rows** | Each uploaded image becomes a row with its own *"Editar"* button. The script edits them top to bottom (`_edit_buttons_by_row` filters out the stray corner button by x-position). |
| **Link sticker** | *"Figurinhas"* → *"Link"* button → the *"Adicionar figurinha de link"* popover, which has two `<input>` fields: URL (no `maxlength`) and sticker text (`maxlength=25`). The **popover's** *"Aplicar"* button (not the modal footer one) creates the sticker. |
| **Positioning** | The sticker is a `<div>` with `margin-left/margin-top` (px) + `position:absolute`, moved via **JS mouse listeners** (not HTML5 drag). `human_drag` does `mousedown → many mousemoves → mouseup`. The drag "slips" (the sticker travels ~2/3 of the cursor), so `position_sticker` measures where it stopped and does **corrective drags** until it is within `STICKER_TOLERANCE` (0.02) of the target, relative to the rendered `<img._5i4g>` artwork rectangle. |
| **Apply** | The modal footer *"Aplicar"* closes the editor; Meta re-processes the media (the row briefly turns into a skeleton — the script waits for all rows to come back before editing the next one). |
| **Publish** | **Automatic.** `click_share` clicks *"Compartilhar"*, which publishes every media item in the composer at once, then waits for the composer to reset (media rows drop to 0) before the script marks that batch as published and moves on. |

**UI text selectors** live together near the top of `post_story.py`
(`TXT_CREATE_STORY`, `TXT_ADD_MEDIA`, `TXT_STICKERS`, …). Their string
**values are Portuguese on purpose** — they must match the real pt-BR Meta
Business Suite UI. If Meta changes the interface, this is the first place to
look.

---

## Repository layout

| File | Committed? | Purpose |
|---|:---:|---|
| `post_story.py` | yes | The whole tool. |
| `README.md` | yes | This file. |
| `MANIFEST_SPEC.md` | yes | The batch manifest contract (what a producing app must generate). |
| `requirements.txt` | yes | `playwright>=1.40.0`. |
| `.gitignore` | yes | Excludes the files below. |
| `auth_state.json` | **no** | Your authenticated session cookies (Facebook / Instagram / Meta). Treat it like a password. Generated by `--login`. |
| `*.progress.json` | **no** | Per-batch resume state, written next to the manifest. |
| `error-*.png` | **no** | Screenshots dropped on failure. |
| `venv/` | **no** | Virtualenv. |

---

## Install (once)

```bash
cd auto-story
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Requires Python 3.8+ (3.9+ recommended).

---

## First run: login

```bash
python post_story.py --login
```

This opens a visible Chromium window. Log into Meta Business Suite normally
(username/password, 2FA if enabled). Then return to the terminal and press
ENTER — the session is saved to `auth_state.json` and later runs reuse it.

> **Do not share `auth_state.json`.** It holds your authenticated session,
> equivalent to being logged into your account. It is already in
> `.gitignore`.

---

## Single mode (one image + one link)

```bash
python post_story.py --image path/to/image.jpg --link "https://yoursite.com/product"
```

**Expected input:**

- `--image` — a local image file. Use the **1080x1920 (9:16)** template;
  other aspect ratios upload fine but break the sticker calibration.
- `--link` — must start with `https://`.

The script uploads the image, adds the link sticker, drags it to the
calibrated position, and clicks *"Compartilhar"* itself — the story is
published by the time the script finishes.

### All flags

| Flag | Default | Description |
|---|---|---|
| `--login` | — | Run the login flow and save the session, then exit. |
| `--image PATH` | — | Story image (single mode). Required unless `--manifest`/`--login`. |
| `--link URL` | — | Destination URL of the sticker (`https://`). Required unless `--manifest`/`--login`. |
| `--page NAME` | — | Page/account to select in *"Compartilhar em"* if the account has more than one. |
| `--sticker-text TEXT` | Meta default | Custom text shown on the sticker (e.g. `"Buy now"`). Max 25 chars (the field's `maxlength`). |
| `--sticker-y-ratio N` | `0.73` | Base vertical position of the sticker center, as a `0–1` fraction of the artwork height (`0` = top, `1` = bottom). See [Calibration](#calibrating-the-sticker-position). |
| `--headless` | off | Run without a visible window. Only use once the flow is fully calibrated. |

Batch-only flags are listed [below](#batch-mode---manifest).

---

## Batch mode (`--manifest`)

Feed a package of several images plus a `manifest.json` (full contract in
[`MANIFEST_SPEC.md`](MANIFEST_SPEC.md)):

```bash
python post_story.py --manifest batch-2026-08-29.zip
```

Accepts either a `.zip` (with `manifest.json` at the root) or a direct path
to a `manifest.json`.

**Minimal `manifest.json`:**

```json
{
  "version": 1,
  "stories": [
    { "id": "prod-10482", "image": "001-shoe.jpg", "link": "https://bonsachados.links/r/10482" }
  ]
}
```

**What the script does:**

1. Validates the **entire** manifest before opening the browser (version,
   unique `id`s, images exist and stay inside the manifest folder, links
   are `https://`).
2. Opens **one composer** and uploads the next pending batch of images (up
   to `--max-per-run`, default 10 — Meta's limit).
3. Edits **one media at a time**: opens that image's editor, adds the link
   sticker, positions it (corrective drags), applies.
4. Clicks *"Compartilhar"* automatically, waits for confirmation the
   composer published (media rows reset to 0), and records that batch's
   `id`s as done in `<name>.progress.json`.
5. If there is still pending work, pauses (30–90 s by default) and repeats
   from step 2 with the next batch of up to 10 — in the **same run** —
   until the whole manifest is published or a failure stops it.

If a media fails mid-batch, that image stays in the composer without a
sticker; the script asks whether to continue building the rest of that
batch. Whatever did build is still shared and marked as published; the run
then stops so you can fix the failure before resuming.

### Batch-mode flags

| Flag | Default | Description |
|---|---|---|
| `--manifest PATH` | — | `manifest.json` or a `.zip` containing it. Replaces `--image`/`--link`. |
| `--max-per-run N` | `10` | Max media items built (and shared) per composer batch (Meta's ceiling). |
| `--pause-min N` / `--pause-max N` | `4` / `16` | Random pause (s) between editing one media and the next, within a batch. |
| `--batch-pause-min N` / `--batch-pause-max N` | `30` / `90` | Random pause (s) after sharing one batch before opening the composer for the next one. |
| `--sticker-y-ratio N` | `0.73` | Base sticker position for **all** stories (the manifest value, if any, is ignored). |
| `--reset-progress` | — | Delete `<name>.progress.json` and restart the batch from scratch. |

### Batches larger than 10

Meta's composer accepts at most **10** media per publish. For a manifest of
30, a single run of the script builds 10, clicks *"Compartilhar"*, pauses,
builds the next 10, shares, pauses, builds the last 10, shares — all
automatically, in one run. It is all one manifest — the producing app does
not need to split anything, and you don't need to re-run the command
between batches. If the run is interrupted partway, running the same
command again resumes from the next pending batch.

### Simple manual loop (alternative to `--manifest`)

```bash
for pair in "product1.jpg|https://site.com/product1" "product2.jpg|https://site.com/product2"; do
  IFS='|' read -r img link <<< "$pair"
  python post_story.py --image "$img" --link "$link"
done
```

---

## Calibrating the sticker position

`--sticker-y-ratio` is the fraction of the artwork height where the
**center** of the sticker should sit. The sticker must land **just above**
the price badge, with some clearance.

The default `0.73` was validated on the Omo test artwork (badge `"R$ 80,18"`
at `y ≈ 0.80`). On your first real run:

1. Run **without** `--headless`.
2. Watch where the sticker lands in the preview.
3. To move it **down**, increase the value (e.g. `0.77`); to move it
   **up**, decrease it (e.g. `0.70`).
4. Repeat until it rests snug above the badge.

For a different template you can compute it:

```
y_ratio = (badge_top_px - clearance - sticker_half_height) / total_height
```

with `clearance` ≈ 40–80 px on a 1920 px-tall image.

> **Automatic jitter:** on top of the base value, each story gets a random
> vertical offset **upward only** (0 to `0.015` of the height ≈ 0–29 px)
> and a symmetric horizontal one (`0.5 ± 0.010` of the width ≈ ±11 px), so
> positions are not identical across the batch. The clearance above the
> badge should assume the worst case: the sticker never goes **below** the
> base value, only above it.

Once calibrated, the value should work for every image that follows the
exact same frame template.

---

## Progress tracking / resume

In batch mode the script writes `<manifest-name>.progress.json` next to the
manifest (or the zip):

```json
{
  "manifest_hash": "sha256 of manifest.json",
  "published": [
    { "id": "prod-10482", "at": "2026-08-29T15:10:22-03:00", "status": "ok",
      "image": "001-shoe.jpg", "link": "https://bonsachados.links/r/10482" }
  ]
}
```

- Each batch is built, shared automatically, and only then are its `id`s
  marked as `"ok"` — a batch is only recorded as published once
  `click_share` confirms the composer actually reset.
- On the next run (or the next automatic batch within the same run) it
  skips every `id` already marked `"ok"` and builds the next one.
- If `manifest.json` changed since the last run (different
  `manifest_hash`), it warns and asks before resuming.
- `--reset-progress` deletes the file and starts over.

This is why each story's `id` must be **stable**: if the producing app
regenerates the manifest with different `id`s for the same products, the
script no longer recognizes what was published and redoes everything.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `No saved session found` | Run `python post_story.py --login` first. |
| `Timeout` waiting for a page element | Meta changed the UI. Run **without** `--headless` to see where it stalled, then adjust the matching `TXT_*` / `SEL_*` constant in `post_story.py`. |
| `Only X/N media items became ready` | An image was rejected (wrong format/size/dimensions) or Meta's processing stalled. Check the `error-wait-*.png` screenshot. |
| Sticker lands on/over the badge | Decrease `--sticker-y-ratio`. Remember the jitter only moves it **up** from the base, so leave clearance. |
| Editor won't open (`Could not open the photo editor`) | Post-processing re-render race; the script retries 3×. If it persists, the *"Editar"* selector changed. |
| Login cookies leaked to git | `auth_state.json` is in `.gitignore`. If it was ever committed, rotate the session (`--login` again) and scrub history. |

When the Meta interface changes, the fix is almost always a one-line edit
to a selector constant near the top of `post_story.py`.
