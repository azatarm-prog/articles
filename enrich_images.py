#!/usr/bin/env python3
"""
Article Image Enrichment Tool

Enriches Markdown articles with AI-generated 3D isometric images using
Google's Nano Banana (Gemini 2.5 Flash Image) API — single-step generation.

By default, generates only a single hero/banner image placed right after
the article title — which becomes Medium's featured image on import.
Use --max-images to add section images at H2 boundaries as well.

Usage:
    python enrich_images.py "Article.md" --api-key YOUR_KEY
    python enrich_images.py "Article.md"  # reads GOOGLE_AI_API_KEY env var
    python enrich_images.py "Article.md" --dry-run
    python enrich_images.py "Article.md" --max-images 5

Options:
    --api-key     Google AI Studio API key
    --images-dir  Directory to save generated images (default: images/)
    --max-images  Total images to generate: 1=banner only, N>1 adds section images (default: 1)
    --dry-run     Print prompts without calling APIs or writing files
    --output      Output Markdown file (default: overwrites input)
"""

import argparse
import base64
import json
import os
import re
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Section 1: Constants & Style Guide
# ---------------------------------------------------------------------------

STYLE_GUIDE = """\
3D Isometric perspective, consistent viewing angle, dimensional depth, professional corporate-grade rendering quality.
Clay-like matte surfaces with subtle reflectivity, solid blocky 3D platforms with clean edges.
Simple geometric shapes without complex details, smooth professional finishes.
Background: Dark #1E2337 or pure black.
Primary color: Neon Teal #00D4AA for primary highlights and active elements.
Secondary color: Electric Blue #0098EA for secondary highlights, connections, and data flows.
Pure White #FFFFFF for text labels and high contrast elements.
Dark grid floor with subtle line patterns and soft reflections.
Grid lines follow proper isometric perspective.
Soft volumetric lighting with atmospheric glows, clear directional lighting with realistic shadows.
Subtle halos around glowing objects.
16:9 aspect ratio."""

STYLE_PREFIX = (
    "3D isometric perspective, dark background #1E2337, clay-like matte surfaces, "
    "neon teal #00D4AA primary highlights, electric blue #0098EA secondary elements, "
    "pure white #FFFFFF text labels, dark grid floor with soft reflections, "
    "soft volumetric lighting, 16:9 aspect ratio, professional corporate-grade rendering. "
)

NANO_BANANA_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-2.5-flash-image:generateContent"
)

DEFAULT_IMAGES_DIR = "images"
DEFAULT_MAX_IMAGES = 1
RATE_LIMIT_SLEEP = 2.0


# ---------------------------------------------------------------------------
# Section 2: Markdown Parsing
# ---------------------------------------------------------------------------

@dataclass
class Section:
    heading: str
    level: int          # 1 = H1, 2 = H2
    line_index: int     # 0-based index in the split lines list
    content_preview: str
    is_banner: bool


def _strip_inline(text: str) -> str:
    """Remove Markdown inline markup for clean text preview."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text.strip()


def parse_sections(md_text: str) -> list:
    """Parse Markdown into Section objects (H1 banner + H2 sections)."""
    lines = md_text.split('\n')
    sections = []
    banner_found = False

    for i, line in enumerate(lines):
        m = re.match(r'^(#{1,2})\s+(.*)', line)
        if not m:
            continue
        level = len(m.group(1))
        heading = _strip_inline(m.group(2))

        if level > 2:
            continue

        # Build content preview from subsequent non-heading lines
        preview_parts = []
        j = i + 1
        while j < len(lines) and len(' '.join(preview_parts)) < 300:
            l = lines[j].strip()
            j += 1
            if not l:
                continue
            if re.match(r'^#{1,6}\s', l):
                break
            if l.startswith('|') or l.startswith('```'):
                continue
            preview_parts.append(_strip_inline(l))
        content_preview = ' '.join(preview_parts)[:300]

        is_banner = (level == 1 and not banner_found)
        if is_banner:
            banner_found = True

        sections.append(Section(
            heading=heading,
            level=level,
            line_index=i,
            content_preview=content_preview,
            is_banner=is_banner,
        ))

    return sections


def select_image_positions(sections: list, max_images: int) -> list:
    """
    Select which sections get images.
    max_images=1: banner only.
    max_images>1: banner + evenly-spaced H2 sections.
    """
    if max_images <= 0:
        return []

    banner = next((s for s in sections if s.is_banner), None)
    h2_sections = [s for s in sections if s.level == 2]

    selected = []
    if banner:
        selected.append(banner)

    remaining_slots = max_images - len(selected)
    if remaining_slots > 0 and h2_sections:
        if remaining_slots >= len(h2_sections):
            selected.extend(h2_sections)
        else:
            # Evenly spaced selection across H2 sections
            stride = len(h2_sections) / remaining_slots
            indices = {round(stride * k) for k in range(remaining_slots)}
            indices = {min(i, len(h2_sections) - 1) for i in indices}
            for i in sorted(indices):
                selected.append(h2_sections[i])

    return selected


# ---------------------------------------------------------------------------
# Section 3: Slug & Naming Utilities
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Turn a title into a URL/filename-friendly slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    return text.strip('-')[:60]


def image_filename(article_title: str, section_index: int, is_banner: bool) -> str:
    base = slugify(article_title)
    if is_banner:
        return f"{base}-00-banner.png"
    return f"{base}-{section_index:02d}.png"


# ---------------------------------------------------------------------------
# Section 4: API Calls (Nano Banana — Gemini 2.5 Flash Image)
# ---------------------------------------------------------------------------

def _api_post(url: str, api_key: str, payload: dict) -> dict:
    """Raw HTTP POST to a Google AI endpoint."""
    import urllib.request
    import urllib.error

    full_url = f"{url}?key={api_key}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        full_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        if e.code == 403:
            raise RuntimeError(
                "[FATAL] HTTP 403 Forbidden — the API key was rejected.\n"
                "  Possible causes:\n"
                "  1. The 'Generative Language API' is not enabled for this key's Google Cloud project.\n"
                "     → Enable it at: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com\n"
                "  2. The key has HTTP referrer or IP restrictions.\n"
                "     → Remove restrictions at: https://console.cloud.google.com/apis/credentials\n"
                "  3. The key was created for a different Google service (e.g. Maps, Vision).\n"
                "     → Create a new key at: https://aistudio.google.com/app/apikey\n"
                f"  Raw response: {error_body[:200]}"
            ) from e
        raise RuntimeError(f"HTTP {e.code}: {error_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e


def _api_post_with_retry(url: str, api_key: str, payload: dict) -> dict:
    """POST with one retry on 429 rate-limit."""
    try:
        return _api_post(url, api_key, payload)
    except RuntimeError as e:
        if "429" in str(e):
            print("    Rate limited. Waiting 15s before retry...", file=sys.stderr)
            time.sleep(15)
            return _api_post(url, api_key, payload)
        raise


def build_image_prompt(section: Section) -> str:
    """Craft a detailed, style-guide-strict image prompt for a section.

    Analyzes the section heading and content to build a rich visual
    description with specific 3D isometric objects, compositions, and
    metaphors — all strictly within the style guide palette and format.
    """
    heading = section.heading.lower()
    preview = section.content_preview.lower()

    # Map conceptual themes to specific 3D isometric visual elements
    visual_elements = []

    # Bitcoin / cryptocurrency motifs
    if any(w in heading + preview for w in ["bitcoin", "btc", "bsv", "cryptocurrency", "coin"]):
        visual_elements.append(
            "a luminous Bitcoin coin rendered as a thick 3D disc with the B symbol "
            "embossed on top, glowing neon teal #00D4AA, hovering above the platform"
        )

    # DNA / genetics / protocol
    if any(w in heading + preview for w in ["dna", "genetic", "protocol", "code", "blueprint"]):
        visual_elements.append(
            "a glowing double helix made of translucent teal #00D4AA and electric blue #0098EA "
            "interlocking blocks rising from the grid floor, each strand composed of small "
            "code-block segments connected by luminous data bridges"
        )

    # Network / nodes / distributed
    if any(w in heading + preview for w in ["network", "node", "distributed", "peer", "p2p"]):
        visual_elements.append(
            "a constellation of interconnected cube-shaped nodes arranged in a decentralized "
            "mesh, connected by glowing electric blue #0098EA data lines with small pulsing "
            "packets traveling along them"
        )

    # Mining / proof of work / energy / metabolism
    if any(w in heading + preview for w in ["mining", "proof-of-work", "energy", "metabol", "hash"]):
        visual_elements.append(
            "a cluster of blocky 3D mining rigs on stepped platforms with emanating "
            "heat waves rendered as teal energy arcs, feeding into a central processing "
            "tower that outputs glowing golden blocks"
        )

    # Evolution / life / organism / symbiosis
    if any(w in heading + preview for w in ["life", "organism", "evolut", "symbio", "alive", "biological"]):
        visual_elements.append(
            "an organic-mechanical hybrid form — a tree-like structure with circuit-board "
            "branches bearing glowing teal leaf-nodes, its roots extending into a digital "
            "substrate grid, symbolizing digital life"
        )

    # Fork / divergence / species
    if any(w in heading + preview for w in ["fork", "diverge", "species", "split", "segwit"]):
        visual_elements.append(
            "a central blockchain column that splits into two diverging paths — one path "
            "glowing healthy teal #00D4AA continuing straight, the other curving away in "
            "dimmer blue, with small broken chain links at the split point"
        )

    # Scaling / growth / blocks
    if any(w in heading + preview for w in ["scal", "growth", "block size", "capacity", "unbounded"]):
        visual_elements.append(
            "a series of progressively larger 3D blocks ascending like stairs, each block "
            "larger than the last, with data streams in electric blue #0098EA flowing into "
            "them, the largest block glowing brightest teal at the top"
        )

    # Ledger / record / transaction
    if any(w in heading + preview for w in ["ledger", "record", "transaction", "chain"]):
        visual_elements.append(
            "a chain of interlocked 3D blocks stretching into the distance on the grid floor, "
            "each block semi-transparent showing tiny transaction records inside, connected "
            "by glowing teal hash-links"
        )

    # Security / defense / integrity
    if any(w in heading + preview for w in ["secur", "defen", "integr", "protect", "immutab"]):
        visual_elements.append(
            "a fortified 3D shield or vault structure with layered hexagonal armor plates "
            "in dark tones, surrounded by orbiting lock icons glowing teal #00D4AA, with "
            "a bright keyhole emitting white light"
        )

    # Homeostasis / difficulty / stability
    if any(w in heading + preview for w in ["homeosta", "difficult", "stabil", "equilibr", "balance"]):
        visual_elements.append(
            "a balanced scale or gyroscope mechanism on a central pedestal, with glowing "
            "teal measurement dials and self-adjusting counterweights, emanating a calm "
            "blue aura of stability"
        )

    # Default fallback — generic tech/article visual
    if not visual_elements:
        visual_elements.append(
            "a central elevated 3D platform with an abstract geometric sculpture representing "
            "the article's theme, surrounded by floating data panels and holographic displays "
            "showing key concepts, all glowing in teal and blue accents"
        )

    # Build the full prompt
    scene_description = "; ".join(visual_elements)

    prompt = (
        f"{STYLE_PREFIX}"
        f"Scene depicting the concept '{section.heading}': {scene_description}. "
        f"The scene sits on a dark isometric grid floor with subtle reflections. "
        f"Soft volumetric lighting casts atmospheric glows with subtle halos around "
        f"the brightest elements. Clean, professional composition with dimensional depth. "
        f"No text, no watermarks, no UI elements — pure 3D isometric illustration."
    )

    return prompt


def generate_image_nanobana(section: Section, api_key: str) -> bytes:
    """Generate a PNG image using Nano Banana (Gemini 2.5 Flash Image).

    Single API call that both understands the content and generates the image
    directly, replacing the old two-step Gemini+Imagen approach.
    """
    prompt = build_image_prompt(section)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9"},
        },
    }

    response = _api_post_with_retry(NANO_BANANA_ENDPOINT, api_key, payload)

    try:
        parts = response["candidates"][0]["content"]["parts"]
        for part in parts:
            if "inlineData" in part:
                b64_data = part["inlineData"]["data"]
                return base64.b64decode(b64_data)
        raise RuntimeError(f"No image data in Nano Banana response: {list(parts[0].keys()) if parts else 'empty'}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Nano Banana response: {json.dumps(response)[:300]}") from e


# ---------------------------------------------------------------------------
# Section 5: Enrichment Pipeline
# ---------------------------------------------------------------------------

def save_image(png_bytes: bytes, filename: str, images_dir: str) -> str:
    """Save PNG bytes to the images directory. Returns path string."""
    out_dir = Path(images_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_bytes(png_bytes)
    return str(out_path)


def _already_has_image(lines: list, line_index: int, filename: str) -> bool:
    """Check if an image reference for this file already exists near line_index."""
    search_start = max(0, line_index - 1)
    search_end = min(len(lines), line_index + 6)
    for line in lines[search_start:search_end]:
        if filename in line:
            return True
    return False


def insert_image_references(md_text: str, insertions: list, images_dir: str) -> str:
    """
    Insert image references into Markdown text.
    insertions: list of (line_index, filename, caption, is_banner)
    Images are inserted on the line AFTER the heading (banner or section).
    """
    lines = md_text.split('\n')
    offset = 0
    for line_index, filename, caption, is_banner in sorted(insertions, key=lambda x: x[0]):
        # Skip if already present
        if _already_has_image(lines, line_index + offset, filename):
            continue
        img_ref = f"\n![{caption}]({images_dir}/{filename})\n"
        insert_at = line_index + 1 + offset  # after the heading line
        lines.insert(insert_at, img_ref)
        offset += 1
    return '\n'.join(lines)


def enrich(args) -> None:
    """Main enrichment pipeline."""
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    md_text = input_path.read_text(encoding="utf-8")

    # Parse and select sections
    sections = parse_sections(md_text)
    if not sections:
        print("Error: No headings found in article.", file=sys.stderr)
        sys.exit(1)

    selected = select_image_positions(sections, args.max_images)
    if not selected:
        print("Nothing to generate (max-images=0).")
        return

    api_key = args.api_key or os.environ.get("GOOGLE_AI_API_KEY")
    if not api_key and not args.dry_run:
        print(
            "Error: No API key provided.\n"
            "  Use --api-key KEY  or  export GOOGLE_AI_API_KEY=KEY",
            file=sys.stderr,
        )
        sys.exit(1)

    images_dir = args.images_dir
    article_title = next((s.heading for s in sections if s.is_banner), input_path.stem)

    print(f"\nEnriching: {input_path.name}")
    print(f"Images: {len(selected)} total  |  Output dir: {images_dir}/")
    if args.dry_run:
        print("(DRY RUN — no API calls, no files written)\n")

    insertions = []  # (line_index, filename, caption, is_banner)

    for idx, section in enumerate(selected):
        fname = image_filename(article_title, idx, section.is_banner)
        caption = section.heading
        label = "banner" if section.is_banner else f"section: {section.heading[:50]}"

        print(f"\n  [{idx + 1}/{len(selected)}] {label}")

        if args.dry_run:
            prompt = build_image_prompt(section)
            print(f"    Prompt: {prompt[:200]}...")
            print(f"    Would save to: {images_dir}/{fname}")
            continue

        # Check idempotency — skip if image file already exists
        out_path = Path(images_dir) / fname
        if out_path.exists():
            print(f"    Already exists, skipping API call: {out_path}")
            insertions.append((section.line_index, fname, caption, section.is_banner))
            continue

        try:
            print("    Generating image via Nano Banana...")
            png_bytes = generate_image_nanobana(section, api_key)
            time.sleep(RATE_LIMIT_SLEEP)

            save_image(png_bytes, fname, images_dir)
            print(f"    Saved: {images_dir}/{fname}  ({len(png_bytes):,} bytes)")

            insertions.append((section.line_index, fname, caption, section.is_banner))

        except RuntimeError as e:
            msg = str(e)
            if "[FATAL]" in msg:
                print(f"\nFatal API error: {msg}", file=sys.stderr)
                sys.exit(1)
            print(f"    WARNING: Skipped '{section.heading}': {msg}", file=sys.stderr)

    if args.dry_run:
        print("\nDry run complete. No files written.")
        return

    if not insertions:
        print("\nNo images generated. Article not modified.")
        return

    # Insert image references into Markdown
    enriched_md = insert_image_references(md_text, insertions, images_dir)

    out_path = Path(args.output) if args.output else input_path
    out_path.write_text(enriched_md, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"  Enrichment complete!")
    print(f"{'=' * 60}")
    print(f"  Article:  {out_path}")
    print(f"  Images:   {len(insertions)} generated in {images_dir}/")
    print(f"{'=' * 60}")
    print(f"\n  Next step:")
    print(f"    python publish.py \"{input_path}\" --author \"Your Name\"\n")


# ---------------------------------------------------------------------------
# Section 6: CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Enrich a Markdown article with AI-generated 3D isometric images "
            "using Nano Banana (Gemini 2.5 Flash Image) API."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Add hero/banner image only (default)
              python enrich_images.py "Article.md" --api-key YOUR_KEY

              # Add banner + 4 section images
              python enrich_images.py "Article.md" --api-key YOUR_KEY --max-images 5

              # Preview prompts without generating images
              python enrich_images.py "Article.md" --dry-run

              # Write enriched output to a new file (preserves original)
              python enrich_images.py "Article.md" --api-key KEY --output "Article-enriched.md"

            Workflow:
              1. Run this script to enrich the Markdown with image references
              2. Run publish.py to generate HTML with images for Medium import
        """),
    )
    parser.add_argument("input", help="Path to the Markdown article file")
    parser.add_argument("--api-key", help="Google AI Studio API key")
    parser.add_argument(
        "--images-dir",
        default=DEFAULT_IMAGES_DIR,
        help=f"Directory to save generated images (default: {DEFAULT_IMAGES_DIR}/)",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=DEFAULT_MAX_IMAGES,
        help=(
            f"Total images to generate. 1=banner only (default). "
            f"N>1 adds section images at H2 boundaries."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate prompts (via Gemini if key is set) but do not create images or modify files",
    )
    parser.add_argument(
        "--output",
        help="Output Markdown file path (default: overwrites input file)",
    )

    args = parser.parse_args()
    enrich(args)


if __name__ == "__main__":
    main()
