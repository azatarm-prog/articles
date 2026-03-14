"""
Article Image Enrichment Web Service

Flask service wrapping enrich_images.py for deployment on Railway.
Exposes HTTP endpoints to generate AI images for Markdown articles
using Nano Banana (Gemini 2.5 Flash Image) API.

Endpoints:
    GET  /health   — health check
    POST /enrich   — enrich markdown with AI-generated images
    POST /webhook  — GitHub push webhook for auto-enrichment
"""

import base64
import json
import os
import time

import requests
from flask import Flask, request, jsonify

from enrich_images import (
    parse_sections,
    select_image_positions,
    build_image_prompt,
    generate_image_nanobana,
    image_filename,
    insert_image_references,
    RATE_LIMIT_SLEEP,
    DEFAULT_MAX_IMAGES,
)

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/enrich", methods=["POST"])
def enrich():
    data = request.get_json(silent=True)
    if not data or "markdown" not in data:
        return jsonify(error="Missing 'markdown' field in request body"), 400

    md_text = data["markdown"]
    max_images = data.get("max_images", DEFAULT_MAX_IMAGES)
    api_key = data.get("api_key") or os.environ.get("GOOGLE_AI_API_KEY")

    if not api_key:
        return jsonify(error="No API key provided. Set GOOGLE_AI_API_KEY env var or pass 'api_key' in request."), 401

    sections = parse_sections(md_text)
    if not sections:
        return jsonify(error="No headings found in markdown"), 400

    selected = select_image_positions(sections, max_images)
    if not selected:
        return jsonify(markdown=md_text, images=[], image_count=0)

    article_title = next((s.heading for s in sections if s.is_banner), "article")
    images_dir = "images"
    insertions = []
    images_out = []

    for idx, section in enumerate(selected):
        fname = image_filename(article_title, idx, section.is_banner)
        caption = section.heading

        try:
            png_bytes = generate_image_nanobana(section, api_key)
            time.sleep(RATE_LIMIT_SLEEP)
        except RuntimeError as e:
            msg = str(e)
            if "[FATAL]" in msg:
                return jsonify(error=f"Google API error: {msg}"), 502
            continue

        insertions.append((section.line_index, fname, caption, section.is_banner))
        images_out.append({
            "filename": fname,
            "content_type": "image/png",
            "data_base64": base64.b64encode(png_bytes).decode(),
        })

    enriched_md = insert_image_references(md_text, insertions, images_dir)

    return jsonify(
        markdown=enriched_md,
        images=images_out,
        image_count=len(images_out),
    )


# ---------------------------------------------------------------------------
# GitHub Webhook — auto-enrich articles on push
# ---------------------------------------------------------------------------

def _github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN env var not set")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def _github_api(method, path, **kwargs):
    """Call the GitHub API."""
    repo = os.environ.get("GITHUB_REPO", "")
    url = f"https://api.github.com/repos/{repo}{path}"
    resp = getattr(requests, method)(url, headers=_github_headers(), **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else None


def _get_file_content(path, ref):
    """Fetch a file's content from GitHub."""
    data = _github_api("get", f"/contents/{path}", params={"ref": ref})
    content_b64 = data.get("content", "")
    return base64.b64decode(content_b64).decode("utf-8"), data.get("sha")


def _commit_file(path, content_bytes, message, branch, sha=None):
    """Create or update a file in the repo via GitHub API."""
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    _github_api("put", f"/contents/{path}", json=payload)


def _article_already_has_banner(md_text):
    """Check if the article already has a banner image after the H1."""
    lines = md_text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            # Check the next few lines for an image reference
            for j in range(i + 1, min(i + 5, len(lines))):
                if "![" in lines[j] and "banner" in lines[j].lower():
                    return True
                if lines[j].strip().startswith("!["):
                    return True
            return False
    return False


@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle GitHub push webhooks — auto-generate images for new/changed articles."""
    api_key = os.environ.get("GOOGLE_AI_API_KEY")
    if not api_key:
        return jsonify(error="GOOGLE_AI_API_KEY not configured"), 500

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        return jsonify(error="GITHUB_TOKEN not configured"), 500

    github_repo = os.environ.get("GITHUB_REPO")
    if not github_repo:
        return jsonify(error="GITHUB_REPO not configured"), 500

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify(error="Invalid webhook payload"), 400

    # Extract branch name from ref (e.g., "refs/heads/main" -> "main")
    ref = payload.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    # Collect modified/added markdown files (exclude README)
    md_files = set()
    for commit in payload.get("commits", []):
        for f in commit.get("added", []) + commit.get("modified", []):
            if f.lower().endswith(".md") and "readme" not in f.lower():
                md_files.add(f)
            # Also match files without extension that look like articles
            # (the Bitcoin article has no .md extension)
            if not any(f.lower().endswith(ext) for ext in [".md", ".py", ".txt", ".html", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh"]):
                # Might be a markdown article without extension — check if it has headings
                md_files.add(f)

    if not md_files:
        return jsonify(status="no articles changed", processed=0)

    results = []
    for md_file in md_files:
        try:
            md_text, file_sha = _get_file_content(md_file, ref=branch)
        except Exception as e:
            results.append({"file": md_file, "status": "error", "message": f"Could not fetch: {e}"})
            continue

        # Skip if not actually markdown (no headings)
        sections = parse_sections(md_text)
        if not sections:
            results.append({"file": md_file, "status": "skipped", "message": "No headings found"})
            continue

        # Skip if already has a banner image
        if _article_already_has_banner(md_text):
            results.append({"file": md_file, "status": "skipped", "message": "Already has banner image"})
            continue

        # Generate banner image
        selected = select_image_positions(sections, 1)
        if not selected:
            results.append({"file": md_file, "status": "skipped", "message": "No sections to image"})
            continue

        section = selected[0]
        article_title = next((s.heading for s in sections if s.is_banner), "article")
        fname = image_filename(article_title, 0, section.is_banner)

        try:
            app.logger.info(f"Generating banner for: {md_file}")
            png_bytes = generate_image_nanobana(section, api_key)
        except RuntimeError as e:
            results.append({"file": md_file, "status": "error", "message": str(e)[:200]})
            continue

        # Commit the image to the repo
        image_path = f"images/{fname}"
        try:
            _commit_file(
                image_path,
                png_bytes,
                f"Add banner image for {article_title[:50]}",
                branch,
            )
        except Exception as e:
            results.append({"file": md_file, "status": "error", "message": f"Image commit failed: {e}"})
            continue

        # Update the article markdown with the image reference
        insertions = [(section.line_index, fname, section.heading, section.is_banner)]
        enriched_md = insert_image_references(md_text, insertions, "images")

        try:
            _commit_file(
                md_file,
                enriched_md.encode("utf-8"),
                f"Add banner image reference to {md_file}",
                branch,
                sha=file_sha,
            )
        except Exception as e:
            results.append({"file": md_file, "status": "error", "message": f"Markdown commit failed: {e}"})
            continue

        results.append({"file": md_file, "status": "enriched", "image": image_path})
        time.sleep(RATE_LIMIT_SLEEP)

    return jsonify(status="done", processed=len(results), results=results)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
