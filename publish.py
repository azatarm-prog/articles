#!/usr/bin/env python3
"""
Medium Article Publisher — Markdown to Beautiful HTML for Medium Import

Converts Markdown articles into professional, Medium-import-ready HTML pages.
The output HTML is hosted via GitHub Pages, then imported into Medium using
Medium's "Import a Story" feature (no API token required).

Usage:
    python publish.py "My Article.md" [options]

Options:
    --title       Override the article title (default: extracted from H1)
    --author      Author name (default: "Author")
    --date        Publication date (default: extracted from content or today)
    --description Short description for meta tags
    --output      Output directory (default: docs/)
    --images-dir  Directory containing images (default: images/)
    --canonical   Canonical URL for the original article
    --tags        Comma-separated tags (for meta keywords)
    --draft       Open in browser for preview instead of just generating

Example:
    python publish.py "Bitcoin is a Symbiotic Organism.md" \\
        --author "Azat Arm" \\
        --tags "Bitcoin,BSV,Biology,Technology" \\
        --description "Bitcoin as a living symbiotic organism"
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path

try:
    import markdown
    HAS_MARKDOWN_LIB = True
except ImportError:
    HAS_MARKDOWN_LIB = False


# ---------------------------------------------------------------------------
# Markdown → HTML conversion (pure-Python fallback if `markdown` not installed)
# ---------------------------------------------------------------------------

def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_inline(text: str) -> str:
    """Convert inline Markdown: bold, italic, links, inline code, images."""
    # Images: ![alt](src "title")  or  ![alt](src)
    text = re.sub(
        r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)',
        lambda m: (
            f'<figure><img src="{m.group(2)}" alt="{m.group(1)}">'
            f'<figcaption>{m.group(3) or m.group(1)}</figcaption></figure>'
        ),
        text,
    )
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # Bold + italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Footnote references  [1](#...) → superscript
    text = re.sub(
        r'\[(\d+)\]\(#[^)]*\)',
        r'<sup>[\1]</sup>',
        text,
    )
    # Anchor-style back-references [↩](#...) [↩2](#...)
    text = re.sub(r'\[↩\d*\]\(#[^)]*\)', '', text)
    return text


def _markdown_to_html_fallback(md_text: str) -> str:
    """Minimal Markdown→HTML converter (no external deps)."""
    lines = md_text.split('\n')
    html_parts: list[str] = []
    in_table = False
    in_blockquote = False
    in_ul = False
    in_ol = False
    in_code = False
    code_buf: list[str] = []
    table_rows: list[str] = []
    bq_buf: list[str] = []

    def flush_blockquote():
        nonlocal in_blockquote, bq_buf
        if in_blockquote:
            content = ' '.join(bq_buf)
            html_parts.append(f'<blockquote><p>{_convert_inline(content)}</p></blockquote>')
            bq_buf = []
            in_blockquote = False

    def flush_table():
        nonlocal in_table, table_rows
        if in_table and table_rows:
            header = table_rows[0]
            body = table_rows[1:]  # skip separator
            out = '<div class="table-wrapper"><table><thead><tr>'
            for cell in header:
                out += f'<th>{_convert_inline(cell)}</th>'
            out += '</tr></thead><tbody>'
            for row in body:
                if all(c in '-| ' for c in '|'.join(row)):
                    continue  # separator row
                out += '<tr>'
                for cell in row:
                    out += f'<td>{_convert_inline(cell)}</td>'
                out += '</tr>'
            out += '</tbody></table></div>'
            html_parts.append(out)
            table_rows = []
            in_table = False

    def flush_list():
        nonlocal in_ul, in_ol
        if in_ul:
            html_parts.append('</ul>')
            in_ul = False
        if in_ol:
            html_parts.append('</ol>')
            in_ol = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Fenced code blocks
        if line.strip().startswith('```'):
            if in_code:
                html_parts.append(f'<pre><code>{chr(10).join(code_buf)}</code></pre>')
                code_buf = []
                in_code = False
            else:
                flush_blockquote()
                flush_table()
                flush_list()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(_escape_html(line))
            i += 1
            continue

        stripped = line.strip()

        # Blank line
        if not stripped:
            flush_blockquote()
            flush_table()
            flush_list()
            i += 1
            continue

        # Horizontal rule
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            flush_blockquote()
            flush_table()
            flush_list()
            html_parts.append('<hr>')
            i += 1
            continue

        # Headings
        hm = re.match(r'^(#{1,6})\s+(.*)', stripped)
        if hm:
            flush_blockquote()
            flush_table()
            flush_list()
            level = len(hm.group(1))
            text = _convert_inline(hm.group(2))
            html_parts.append(f'<h{level}>{text}</h{level}>')
            i += 1
            continue

        # Table rows
        if '|' in stripped and stripped.startswith('|'):
            flush_blockquote()
            flush_list()
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(cells)
            i += 1
            continue
        else:
            flush_table()

        # Blockquote
        if stripped.startswith('>'):
            flush_table()
            flush_list()
            content = stripped.lstrip('>').strip()
            if not in_blockquote:
                in_blockquote = True
                bq_buf = []
            bq_buf.append(content)
            i += 1
            continue
        else:
            flush_blockquote()

        # Unordered list
        um = re.match(r'^[-*+]\s+(.*)', stripped)
        if um:
            flush_table()
            flush_blockquote()
            if not in_ul:
                flush_list()
                html_parts.append('<ul>')
                in_ul = True
            html_parts.append(f'<li>{_convert_inline(um.group(1))}</li>')
            i += 1
            continue

        # Ordered list
        om = re.match(r'^\d+\.\s+(.*)', stripped)
        if om:
            flush_table()
            flush_blockquote()
            if not in_ol:
                flush_list()
                html_parts.append('<ol>')
                in_ol = True
            html_parts.append(f'<li>{_convert_inline(om.group(1))}</li>')
            i += 1
            continue

        flush_list()

        # Regular paragraph
        html_parts.append(f'<p>{_convert_inline(stripped)}</p>')
        i += 1

    # Flush remaining
    flush_blockquote()
    flush_table()
    flush_list()
    if in_code:
        html_parts.append(f'<pre><code>{chr(10).join(code_buf)}</code></pre>')

    return '\n'.join(html_parts)


def _strip_metadata_lines(md_text: str) -> str:
    """Remove raw Date/Keywords lines that will be shown in the article meta block."""
    lines = md_text.split('\n')
    filtered = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^\*\*Date:\*\*', stripped):
            continue
        if re.match(r'^\*\*Keywords?:\*\*', stripped):
            continue
        filtered.append(line)
    return '\n'.join(filtered)


def _merge_consecutive_lists(html: str) -> str:
    """Merge consecutive <ol> elements into a single list (fixes footnotes split by blank lines)."""
    html = re.sub(r'</ol>\s*<ol>', '', html)
    return html


def _tables_to_prose(html: str) -> str:
    """Convert HTML tables to plain prose paragraphs for Medium compatibility."""
    def _convert_table(match):
        table_html = match.group(0)
        headers = re.findall(r'<th>(.*?)</th>', table_html)
        rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
        sentences = []
        for row in rows:
            cells = re.findall(r'<td>(.*?)</td>', row)
            if not cells:
                continue
            label = re.sub(r'</?strong>', '', cells[0])
            parts = []
            for i, cell in enumerate(cells[1:], 1):
                header = headers[i] if i < len(headers) else f'Column {i}'
                parts.append(f'{header}: {cell}')
            sentences.append(
                f'<p><strong>{label}.</strong> ' + '. '.join(parts) + '</p>'
            )
        return '\n'.join(sentences)

    return re.sub(
        r'<div class="table-wrapper"><table>.*?</table></div>',
        _convert_table,
        html,
        flags=re.DOTALL,
    )


def _remove_hr_before_headings(html: str) -> str:
    """Remove <hr> tags immediately before <h2> (causes extra spacing on Medium)."""
    return re.sub(r'<hr>\s*\n?(<h2>)', r'\1', html)


def _insert_toc(html: str) -> str:
    """Insert a Table of Contents after the Abstract section."""
    headings = re.findall(r'<h[23]>(\d+\.\s+.+?)</h[23]>', html)
    if not headings:
        return html
    toc_lines = ['<h2>Table of Contents</h2>']
    for h in headings:
        num = re.match(r'(\d+)\.', h)
        if num:
            toc_lines.append(
                f'<p><strong>{num.group(1)}.</strong> {h[len(num.group(0)):].strip()}</p>'
            )
    toc_block = '\n'.join(toc_lines)
    # Insert after the Abstract paragraph (first </p> after <h2>Abstract</h2>)
    html = re.sub(
        r'(<h2>Abstract</h2>\s*<p>.*?</p>)',
        r'\1\n' + toc_block,
        html,
        count=1,
        flags=re.DOTALL,
    )
    return html


def _numbered_h2_to_h3(html: str) -> str:
    """Convert numbered section <h2> to <h3> to reduce spacing on Medium."""
    return re.sub(r'<h2>(\d+\.[^<]*)</h2>', r'<h3>\1</h3>', html)


def convert_markdown(md_text: str) -> str:
    """Convert Markdown to HTML, using python-markdown if available."""
    md_text = _strip_metadata_lines(md_text)
    if HAS_MARKDOWN_LIB:
        html = markdown.markdown(
            md_text,
            extensions=['tables', 'fenced_code', 'footnotes', 'toc', 'attr_list'],
        )
    else:
        html = _markdown_to_html_fallback(md_text)
    html = _merge_consecutive_lists(html)
    html = _tables_to_prose(html)
    html = _remove_hr_before_headings(html)
    html = _insert_toc(html)
    html = _numbered_h2_to_h3(html)
    return html


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def extract_metadata(md_text: str):
    """Pull title, date, keywords from the Markdown content."""
    title = None
    date = None
    keywords = None

    # Title from first H1
    m = re.search(r'^#\s+(.+)', md_text, re.MULTILINE)
    if m:
        title = m.group(1).strip()

    # Date
    m = re.search(r'\*\*Date:\*\*\s*(.+)', md_text)
    if m:
        date = m.group(1).strip()

    # Keywords
    m = re.search(r'\*\*Keywords?:\*\*\s*(.+)', md_text)
    if m:
        keywords = m.group(1).strip()

    return title, date, keywords


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------

def process_images(html: str, images_dir: str, output_dir: str) -> str:
    """Copy local images to output dir and update src paths."""
    img_pattern = re.compile(r'<img\s+[^>]*src="([^"]+)"', re.IGNORECASE)

    def replace_src(match):
        src = match.group(1)
        full = match.group(0)
        # Skip URLs
        if src.startswith(('http://', 'https://', '//')):
            return full
        # Try to find the local file
        local_path = None
        for candidate in [
            Path(src),
            Path(images_dir) / Path(src).name,
            Path(images_dir) / src,
        ]:
            if candidate.exists():
                local_path = candidate
                break
        if local_path:
            dest = Path(output_dir) / 'images' / local_path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dest)
            return full.replace(src, f'images/{local_path.name}')
        return full

    return img_pattern.sub(replace_src, html)


# ---------------------------------------------------------------------------
# HTML template — professional, Medium-import-optimized
# ---------------------------------------------------------------------------

def build_html_page(
    body_html: str,
    title: str,
    author: str,
    date: str,
    description: str,
    canonical: str,
    keywords: str,
) -> str:
    """Wrap article HTML in a beautiful, complete HTML page."""
    return textwrap.dedent(f"""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <meta name="author" content="{author}">
        <meta name="description" content="{description}">
        <meta name="keywords" content="{keywords}">
        <meta name="date" content="{date}">
        {f'<link rel="canonical" href="{canonical}">' if canonical else ''}

        <!-- Open Graph for Medium import & social sharing -->
        <meta property="og:title" content="{title}">
        <meta property="og:description" content="{description}">
        <meta property="og:type" content="article">
        <meta property="article:published_time" content="{date}">
        <meta property="article:author" content="{author}">

        <!-- Professional typography -->
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,400;0,700;0,900;1,300;1,400&family=Source+Sans+Pro:wght@400;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">

        <style>
            /* ── Reset & Base ────────────────────────────────── */
            *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

            html {{
                font-size: 18px;
                scroll-behavior: smooth;
                -webkit-font-smoothing: antialiased;
                -moz-osx-font-smoothing: grayscale;
            }}

            body {{
                font-family: 'Merriweather', Georgia, 'Times New Roman', serif;
                line-height: 1.8;
                color: #1a1a1a;
                background: #fdfdfd;
            }}

            /* ── Article Container ───────────────────────────── */
            article {{
                max-width: 740px;
                margin: 0 auto;
                padding: 60px 24px 80px;
            }}

            /* ── Typography ──────────────────────────────────── */
            h1 {{
                font-family: 'Source Sans Pro', 'Helvetica Neue', Arial, sans-serif;
                font-size: 2.6rem;
                font-weight: 900;
                line-height: 1.15;
                color: #0a0a0a;
                margin-bottom: 0.3em;
                letter-spacing: -0.02em;
            }}

            h2 {{
                font-family: 'Source Sans Pro', 'Helvetica Neue', Arial, sans-serif;
                font-size: 1.65rem;
                font-weight: 700;
                line-height: 1.3;
                color: #0a0a0a;
                margin-top: 2.4em;
                margin-bottom: 0.6em;
                padding-bottom: 0.3em;
                border-bottom: 2px solid #e8e8e8;
            }}

            h3 {{
                font-family: 'Source Sans Pro', 'Helvetica Neue', Arial, sans-serif;
                font-size: 1.3rem;
                font-weight: 700;
                line-height: 1.4;
                color: #1a1a1a;
                margin-top: 2em;
                margin-bottom: 0.5em;
            }}

            h4, h5, h6 {{
                font-family: 'Source Sans Pro', sans-serif;
                font-weight: 700;
                margin-top: 1.6em;
                margin-bottom: 0.4em;
            }}

            p {{
                margin-bottom: 1.4em;
                font-size: 1rem;
                color: #2a2a2a;
            }}

            /* ── Article Meta ────────────────────────────────── */
            .article-meta {{
                font-family: 'Source Sans Pro', sans-serif;
                color: #777;
                font-size: 0.9rem;
                margin-bottom: 2em;
                padding-bottom: 1.5em;
                border-bottom: 1px solid #eee;
            }}

            .article-meta .author {{
                font-weight: 600;
                color: #333;
            }}

            .article-meta .date {{
                margin-left: 0.5em;
            }}

            .article-meta .tags {{
                margin-top: 0.5em;
                display: flex;
                flex-wrap: wrap;
                gap: 0.4em;
            }}

            .article-meta .tag {{
                background: #f0f0f0;
                padding: 0.15em 0.6em;
                border-radius: 3px;
                font-size: 0.8rem;
                color: #555;
            }}

            /* ── Links ───────────────────────────────────────── */
            a {{
                color: #1a8917;
                text-decoration: none;
                border-bottom: 1px solid transparent;
                transition: border-color 0.2s;
            }}

            a:hover {{
                border-bottom-color: #1a8917;
            }}

            /* ── Blockquotes ─────────────────────────────────── */
            blockquote {{
                margin: 1.8em 0;
                padding: 1.2em 1.5em;
                border-left: 4px solid #1a8917;
                background: #f8faf8;
                border-radius: 0 6px 6px 0;
                font-style: italic;
                color: #3a3a3a;
            }}

            blockquote p {{
                margin-bottom: 0.6em;
            }}

            blockquote p:last-child {{
                margin-bottom: 0;
            }}

            /* ── Tables ──────────────────────────────────────── */
            .table-wrapper {{
                overflow-x: auto;
                margin: 1.8em 0;
                border-radius: 8px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.08);
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                font-family: 'Source Sans Pro', sans-serif;
                font-size: 0.92rem;
            }}

            thead {{
                background: #1a8917;
                color: #fff;
            }}

            th {{
                padding: 12px 16px;
                text-align: left;
                font-weight: 600;
                letter-spacing: 0.02em;
            }}

            td {{
                padding: 11px 16px;
                border-bottom: 1px solid #eee;
                vertical-align: top;
            }}

            tbody tr:nth-child(even) {{
                background: #fafafa;
            }}

            tbody tr:hover {{
                background: #f0f7f0;
            }}

            /* ── Styled Grid (Medium-compatible table alternative) ── */
            .styled-grid {{
                margin: 1.8em 0;
            }}

            .grid-row {{
                padding: 1em 1.2em;
                margin-bottom: 0.6em;
                background: #f9faf9;
                border-left: 3px solid #1a8917;
                border-radius: 0 6px 6px 0;
            }}

            .grid-row:nth-child(even) {{
                background: #f4f5f4;
            }}

            .grid-label {{
                font-family: 'Source Sans Pro', sans-serif;
                font-weight: 700;
                font-size: 1.05rem;
                color: #0a0a0a;
                margin-bottom: 0.4em;
            }}

            .grid-field {{
                font-size: 0.95rem;
                color: #2a2a2a;
                margin-bottom: 0.3em;
                line-height: 1.6;
            }}

            .grid-field .field-name {{
                font-family: 'Source Sans Pro', sans-serif;
                font-weight: 600;
                color: #555;
            }}

            /* ── Code ────────────────────────────────────────── */
            code {{
                font-family: 'Fira Code', 'Consolas', monospace;
                background: #f4f4f4;
                padding: 0.15em 0.4em;
                border-radius: 3px;
                font-size: 0.88em;
                color: #c7254e;
            }}

            pre {{
                margin: 1.6em 0;
                padding: 1.2em 1.4em;
                background: #282c34;
                color: #abb2bf;
                border-radius: 8px;
                overflow-x: auto;
                line-height: 1.5;
            }}

            pre code {{
                background: none;
                padding: 0;
                color: inherit;
                font-size: 0.88rem;
            }}

            /* ── Images & Figures ────────────────────────────── */
            figure {{
                margin: 2em 0;
                text-align: center;
            }}

            figure img {{
                max-width: 100%;
                height: auto;
                border-radius: 6px;
                box-shadow: 0 2px 12px rgba(0,0,0,0.1);
            }}

            figcaption {{
                font-family: 'Source Sans Pro', sans-serif;
                font-size: 0.85rem;
                color: #888;
                margin-top: 0.6em;
                font-style: italic;
            }}

            img {{
                max-width: 100%;
                height: auto;
                border-radius: 6px;
            }}

            /* ── Horizontal Rule ─────────────────────────────── */
            hr {{
                border: none;
                height: 1px;
                background: linear-gradient(to right, transparent, #ccc, transparent);
                margin: 3em 0;
            }}

            /* ── Lists ───────────────────────────────────────── */
            ul, ol {{
                margin: 1em 0 1.4em 1.5em;
            }}

            li {{
                margin-bottom: 0.5em;
            }}

            /* ── Superscript (footnotes) ─────────────────────── */
            sup {{
                font-size: 0.7em;
                line-height: 0;
                vertical-align: super;
            }}

            sup a {{
                color: #1a8917;
                font-weight: 600;
            }}

            /* ── Footnotes section ───────────────────────────── */
            .footnotes {{
                margin-top: 3em;
                padding-top: 1.5em;
                border-top: 2px solid #eee;
                font-size: 0.88rem;
                color: #555;
            }}

            .footnotes ol {{
                padding-left: 1.2em;
            }}

            .footnotes li {{
                margin-bottom: 0.8em;
            }}

            /* ── Strong emphasis ─────────────────────────────── */
            strong {{
                font-weight: 700;
                color: #0a0a0a;
            }}

            /* ── Print styles ────────────────────────────────── */
            @media print {{
                body {{ font-size: 12pt; }}
                article {{ max-width: 100%; padding: 0; }}
                pre {{ white-space: pre-wrap; }}
            }}

            /* ── Mobile ──────────────────────────────────────── */
            @media (max-width: 600px) {{
                html {{ font-size: 16px; }}
                h1 {{ font-size: 2rem; }}
                h2 {{ font-size: 1.4rem; }}
                article {{ padding: 30px 16px 50px; }}
                blockquote {{ padding: 0.8em 1em; }}
            }}
        </style>
    </head>
    <body>
        <article>
            {body_html}
        </article>
    </body>
    </html>
    """)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Turn a title into a URL-friendly slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    return text.strip('-')[:80]


def inject_article_meta(html: str, author: str, date: str, keywords: str) -> str:
    """Add an article-meta block right after the first <h1>."""
    tags_html = ''
    if keywords:
        tag_list = [k.strip() for k in keywords.split(',')]
        tags_html = '<div class="tags">' + ''.join(
            f'<span class="tag">{t}</span>' for t in tag_list[:8]
        ) + '</div>'

    meta_block = (
        f'<div class="article-meta">'
        f'<span class="author">{author}</span>'
        f'<span class="date"> &middot; {date}</span>'
        f'{tags_html}'
        f'</div>'
    )

    # Insert after first closing </h1>
    return html.replace('</h1>', f'</h1>\n{meta_block}', 1)


def publish(args):
    """Main publish pipeline."""
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    md_text = input_path.read_text(encoding='utf-8')

    # Extract metadata
    auto_title, auto_date, auto_keywords = extract_metadata(md_text)
    title = args.title or auto_title or input_path.stem
    date = args.date or auto_date or datetime.now().strftime('%B %d, %Y')
    keywords = args.tags or auto_keywords or ''
    description = args.description or (title[:160] if title else '')
    author = args.author or 'Author'
    canonical = args.canonical or ''

    # Convert
    body_html = convert_markdown(md_text)

    # Inject article meta
    body_html = inject_article_meta(body_html, author, date, keywords)

    # Process images
    output_dir = args.output or 'docs'
    images_dir = args.images_dir or 'images'
    body_html = process_images(body_html, images_dir, output_dir)

    # Build full page
    full_html = build_html_page(
        body_html=body_html,
        title=title,
        author=author,
        date=date,
        description=description,
        canonical=canonical,
        keywords=keywords,
    )

    # Write output
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(title)
    out_file = out_dir / f'{slug}.html'
    out_file.write_text(full_html, encoding='utf-8')

    # Also write/update index.html that lists all articles
    _update_index(out_dir, title, slug, date, description)

    print(f"\n{'='*60}")
    print(f"  Article published successfully!")
    print(f"{'='*60}")
    print(f"  Output:  {out_file}")
    print(f"  Title:   {title}")
    print(f"  Author:  {author}")
    print(f"  Date:    {date}")
    print(f"{'='*60}")
    print(f"\n  Next steps:")
    print(f"  1. git add docs/ && git commit && git push")
    print(f"  2. Enable GitHub Pages (Settings → Pages → Source: /docs)")
    print(f"  3. Go to medium.com/me/stories → 'Import a story'")
    print(f"     Paste: https://<username>.github.io/articles/{slug}.html")
    print(f"  4. Review the draft on Medium and hit Publish!\n")

    return str(out_file)


def _update_index(out_dir: Path, title: str, slug: str, date: str, description: str):
    """Create/update an index.html listing all published articles."""
    index_path = out_dir / 'index.html'

    # Collect existing entries
    entries: list[dict] = []
    if index_path.exists():
        existing = index_path.read_text(encoding='utf-8')
        for m in re.finditer(
            r'<a href="([^"]+\.html)"[^>]*>([^<]+)</a>\s*<span[^>]*>([^<]*)</span>',
            existing,
        ):
            entries.append({
                'slug': m.group(1).replace('.html', ''),
                'title': m.group(2),
                'date': m.group(3),
            })

    # Add/update current article
    found = False
    for e in entries:
        if e['slug'] == slug:
            e['title'] = title
            e['date'] = date
            found = True
    if not found:
        entries.append({'slug': slug, 'title': title, 'date': date})

    items_html = '\n'.join(
        f'            <li>'
        f'<a href="{e["slug"]}.html" class="article-link">{e["title"]}</a> '
        f'<span class="article-date">{e["date"]}</span>'
        f'</li>'
        for e in entries
    )

    index_html = textwrap.dedent(f"""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Articles</title>
        <link href="https://fonts.googleapis.com/css2?family=Source+Sans+Pro:wght@400;600;700&display=swap" rel="stylesheet">
        <style>
            body {{
                font-family: 'Source Sans Pro', sans-serif;
                max-width: 740px;
                margin: 60px auto;
                padding: 0 24px;
                color: #1a1a1a;
                background: #fdfdfd;
            }}
            h1 {{
                font-size: 2rem;
                margin-bottom: 0.5em;
            }}
            .subtitle {{
                color: #777;
                margin-bottom: 2em;
                font-size: 1.1rem;
            }}
            ul {{ list-style: none; padding: 0; }}
            li {{
                padding: 1em 0;
                border-bottom: 1px solid #eee;
            }}
            .article-link {{
                font-size: 1.2rem;
                font-weight: 600;
                color: #1a8917;
                text-decoration: none;
            }}
            .article-link:hover {{ text-decoration: underline; }}
            .article-date {{
                color: #999;
                font-size: 0.9rem;
                margin-left: 0.5em;
            }}
        </style>
    </head>
    <body>
        <h1>Articles</h1>
        <p class="subtitle">Published articles — ready for Medium import</p>
        <ul>
    {items_html}
        </ul>
    </body>
    </html>
    """)

    index_path.write_text(index_html, encoding='utf-8')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Convert Markdown articles to beautiful HTML for Medium import.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Example:
              python publish.py "My Article.md" --author "Jane Doe" --tags "AI,Tech"

            Workflow:
              1. Run this script to generate HTML in docs/
              2. Push to GitHub and enable GitHub Pages
              3. Import the URL into Medium via "Import a Story"
        """),
    )
    parser.add_argument('input', help='Path to the Markdown (.md) file')
    parser.add_argument('--title', help='Article title (default: from H1)')
    parser.add_argument('--author', help='Author name', default='Author')
    parser.add_argument('--date', help='Publication date (default: from content)')
    parser.add_argument('--description', help='Short description for meta tags')
    parser.add_argument('--output', help='Output directory (default: docs/)', default='docs')
    parser.add_argument('--images-dir', help='Local images directory', default='images')
    parser.add_argument('--canonical', help='Canonical URL for the article')
    parser.add_argument('--tags', help='Comma-separated tags/keywords')
    parser.add_argument('--draft', action='store_true', help='Open preview in browser')

    args = parser.parse_args()
    out_file = publish(args)

    if args.draft:
        import webbrowser
        webbrowser.open(f'file://{os.path.abspath(out_file)}')


if __name__ == '__main__':
    main()
