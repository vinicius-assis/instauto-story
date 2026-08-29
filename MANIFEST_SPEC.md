# Export contract — Story batch

Specification of what the **web application** must generate to be consumed
by `post_story.py` in batch mode (`--manifest`).

The goal: the app assembles a package (images + a `manifest.json`), the
operator downloads it, extracts it, and runs **a single command**, reviewing
each Story before sharing.

---

## 1. Delivery format

The app must produce a **`.zip` file** containing, at the root:

```
batch-<identifier>.zip
└── (zip root)
    ├── manifest.json         ← required, always at the root
    ├── 001-white-sneaker.jpg
    ├── 002-bluetooth-earbuds.jpg
    └── 003-smart-watch.jpg
```

Rules:

- `manifest.json` sits **at the root of the zip** (not inside a subfolder).
- Images sit at the root, next to the manifest (or in a subfolder, as long
  as the path in the manifest is relative — see §3.1).
- The zip name is free, but `batch-YYYY-MM-DD.zip` or
  `batch-<campaign-id>.zip` is recommended for tracking.
- Do not include extra files (thumbnails, `.DS_Store`, etc.).

> Zip-less alternative: deliver a **folder** with the same contents. The
> script accepts both `--manifest path/to/manifest.json` and a zip.

---

## 2. `manifest.json` structure

### 2.1 Full example

```json
{
  "version": 1,
  "generated_at": "2026-08-29T14:30:00-03:00",
  "page": "bonsachados.links",
  "defaults": {
    "sticker_text": "Buy now"
  },
  "stories": [
    {
      "id": "prod-10482",
      "image": "001-white-sneaker.jpg",
      "link": "https://bonsachados.links/r/10482",
      "sticker_text": "See the deal"
    },
    {
      "id": "prod-10483",
      "image": "002-bluetooth-earbuds.jpg",
      "link": "https://bonsachados.links/r/10483"
    }
  ]
}
```

### 2.2 Minimal valid example

```json
{
  "version": 1,
  "stories": [
    { "id": "prod-10482", "image": "sneaker.jpg", "link": "https://bonsachados.links/r/10482" }
  ]
}
```

---

## 3. Fields

### 3.1 Root level

| Field | Type | Required | Description |
|---|---|:---:|---|
| `version` | integer | yes | Format version. Use `1`. Lets the script reject incompatible future formats. |
| `generated_at` | string (ISO 8601 with timezone) | no | Batch generation timestamp. Informational / logging only. |
| `page` | string | no | Page/account name to select in *"Compartilhar em"* in Meta Business Suite. Applies to every story in the batch. Omit if the account has only one page. |
| `defaults` | object | no | Default values inherited by each `stories` item that does not set them. Only field used: `sticker_text`. |
| `stories` | array of objects | yes | List of Stories to publish, **in the order** they should be processed. Must not be empty. |

### 3.2 Each `stories` item

| Field | Type | Required | Description |
|---|---|:---:|---|
| `id` | string | **yes** | Unique, **stable** identifier for the story (e.g. SKU, database id). Used for progress tracking (§8): it is the key that tells the script what has already been published. Do not reuse an `id` for another product, and do not change an item's `id` between runs. |
| `image` | string | yes | Image file path **relative to `manifest.json`**. E.g. `"001-sneaker.jpg"` or `"imgs/001-sneaker.jpg"`. No absolute paths, no `../`. |
| `link` | string (URL) | yes | Destination URL of the link sticker. Must start with `https://`. See §5. |
| `sticker_text` | string | no | Text shown on the sticker. If omitted, uses `defaults.sticker_text`; if that is also absent, Meta uses its own default text. Max 25 characters (the field's `maxlength`). |

**Do not send `sticker_y_ratio`** (neither per story nor in `defaults`):
the script ignores it. The vertical position always comes from
`--sticker-y-ratio` (command line) or the default `0.73`, with a per-story
jitter — see §4.

Unknown fields are **ignored** by the script (the app can add metadata
without breaking it), but do not send what is not used.

---

## 4. Image requirements

| Requirement | Value |
|---|---|
| Dimensions | **1080 x 1920 px** (9:16). Other aspect ratios are accepted by Meta but break the sticker calibration. |
| Format | `.jpg` / `.jpeg` (preferred) or `.png` |
| File size | ≤ 8 MB per image |
| Color space | sRGB |
| File name | ASCII, no spaces. Use `-` as the separator. Zero-padded numeric prefix (`001-`, `002-`) to keep the order readable. |

On the sticker's vertical position: it is the fraction of the artwork
height where the **center** of the sticker sits. The sticker must stop
**just above** the price badge, with clearance. On the Omo test artwork
(badge at `y ≈ 0.80`), `0.73` gave a good result. This is set by the
operator via `--sticker-y-ratio` (or the default `0.73`) — **it does not
come from the manifest**. If different artworks have the badge at different
heights, the operator computes:

```
y_ratio = (badge_top_px - clearance - sticker_half_height) / total_height
```

with `clearance` ≈ 40–80 px on a 1920 px image.

> **Automatic jitter:** on top of the base value (default `0.73` or
> `--sticker-y-ratio`), the script draws a per-story vertical offset
> **upward only** (0 to 0.015 of the height ≈ 0–29 px) and a symmetric
> horizontal one (0.5 ± 0.010 of the width ≈ ±11 px), so positions are not
> identical across the whole batch. The clearance above the badge must
> therefore assume the worst case: the sticker never goes below the base
> value, only above it.

---

## 5. Rules for `link`

- Must be `https://` (the script rejects `http://` and schemeless strings).
- May contain a query string and UTMs — nothing beyond valid-URL escaping
  is needed. E.g.
  `https://bonsachados.links/r/10482?utm_source=ig&utm_medium=story`
- A short **redirect link of your own** (`/r/<id>`) is recommended over the
  raw affiliate link: it stays short on the sticker, lets you change the
  destination later, and gives click metrics.
- Maximum length: 2000 characters.

---

## 6. Validations the app should do before generating the zip

Fail the export (with a clear message) if:

1. `stories` is empty.
2. An item has no `id`, or two items share the same `id`.
3. An `image` references a file that will not be included in the package.
4. A `link` is not a valid `https://` URL.
5. An image is not 1080x1920 (or, at minimum, emit a warning).

`post_story.py` re-runs all of these validations when it loads the manifest
and aborts before opening the browser if any fail.

---

## 7. Suggested endpoint (optional)

If you prefer a direct download instead of the operator assembling the
package:

```
GET /stories/batch/{campaign_id}/export
Accept: application/zip

200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename="batch-2026-08-29.zip"
<zip binary>
```

Errors:

```
409 Conflict   { "error": "batch_already_exported", "exported_at": "..." }
422            { "error": "validation", "details": [ "story 3: invalid link" ] }
```

Mark the batch as "exported" in the database after the `200`, so the
operator does not publish the same batch twice.

---

## 8. Progress tracking (resume) — how the script uses the manifest

This **requires nothing from the app** beyond a unique, stable `id` on each
story. It is documented here so implementers understand why `id` is
required.

When you run `python post_story.py --manifest batch.zip` (or
`.../manifest.json`), the script creates/updates a
**`<name>.progress.json`** file next to the manifest (or the zip):

```json
{
  "manifest_hash": "sha256 of manifest.json",
  "published": [
    { "id": "prod-10482", "at": "2026-08-29T15:10:22-03:00", "status": "ok",
      "image": "001-white-sneaker.jpg", "link": "https://bonsachados.links/r/10482" }
  ]
}
```

Behavior:

- The batch is **all-or-nothing**: the script builds every media in one
  composer and only marks the `id`s as `status: "ok"` after you confirm in
  the terminal that you clicked *"Compartilhar"* (Meta publishes them all
  at once).
- On the next run it skips every `id` with `status: "ok"` and builds the
  next batch.
- If `manifest.json` changed since the last run (different `manifest_hash`),
  the script warns and asks for confirmation before resuming.
- `--reset-progress` deletes `<name>.progress.json` and starts from scratch.

This is why `id` must be **stable**: if the app generates a new manifest
with different `id`s for the same products, the script cannot recognize
what was already published and redoes everything.

---

## 9. Batches with more than 10 stories

The Meta Business Suite composer accepts **at most 10 media items** per
publish. The script builds a batch of up to 10 (`--max-per-run`), stops for
you to review and share, and on the next command builds the next 10.

| Mechanism | Default | Flag |
|---|---|---|
| Media per composer / run | 10 | `--max-per-run` |
| Pause between editing one media and the next | random 4–16 s | `--pause-min` / `--pause-max` |

Flow for a batch of 30: run → builds 10 → you check and click
*"Compartilhar"* → confirm in the terminal; run again → builds the next 10;
repeat 3×. The app does not need to do anything — it is all a single
manifest.

---

## 10. Contract summary (TL;DR for implementers)

1. Generate a `.zip` with `manifest.json` at the root + the `.jpg`
   1080x1920 images.
2. `manifest.json` = `{ version: 1, page?, defaults?, stories: [...] }`.
3. Each story = `{ id (required, unique, stable), image (relative path),
   link (https), sticker_text? }`.
4. `id`, `image` and `link` are required; everything else has a default.
5. Validate before exporting (§6). Array order = order the media enter the
   composer.
6. The script tracks progress/resume on its own via `id` (§8) — just do not
   change a product's `id` between exports.
