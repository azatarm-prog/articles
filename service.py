"""
Article Image Enrichment Web Service

Flask service wrapping enrich_images.py for deployment on Railway.
Exposes HTTP endpoints to generate AI images for Markdown articles
using Google Gemini + Imagen APIs.

Endpoints:
    GET  /health  — health check
    POST /enrich  — enrich markdown with AI-generated images
"""

import base64
import os
import time

from flask import Flask, request, jsonify

from enrich_images import (
    parse_sections,
    select_image_positions,
    generate_image_prompt,
    generate_image,
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
            prompt = generate_image_prompt(section, api_key)
            time.sleep(RATE_LIMIT_SLEEP)

            png_bytes = generate_image(prompt, api_key)
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
