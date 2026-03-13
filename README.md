# Medium Article Publisher

A zero-dependency Python tool that converts Markdown articles into beautiful, professional HTML pages optimized for Medium's "Import a Story" feature.

**No Medium API token required.**

## How It Works

```
Markdown Article → publish.py → Beautiful HTML → GitHub Pages → Medium Import
```

1. **Write** your article in Markdown (with images, tables, footnotes, etc.)
2. **Run** `publish.py` to generate a professional HTML page in `docs/`
3. **Push** to GitHub — GitHub Pages serves the HTML
4. **Import** the URL into Medium via "Import a Story" (takes 10 seconds)

## Quick Start

```bash
# Generate HTML from your Markdown article
python3 publish.py "My Article.md" --author "Your Name" --tags "Tag1,Tag2,Tag3"

# Commit and push
git add docs/ && git commit -m "Publish article" && git push

# Then go to: medium.com/me/stories → "Import a story" → paste your GitHub Pages URL
```

## Usage

```
python3 publish.py <article.md> [options]

Options:
  --title       Override the article title (default: extracted from first H1)
  --author      Author name (default: "Author")
  --date        Publication date (default: extracted from content or today)
  --description Short description for meta tags and social sharing
  --output      Output directory (default: docs/)
  --images-dir  Directory containing local images (default: images/)
  --canonical   Canonical URL for the original article
  --tags        Comma-separated tags/keywords
  --draft       Open preview in browser after generating
```

## Features

- **Professional typography** — Merriweather serif body, Source Sans Pro headings, Fira Code for code
- **Beautiful tables** — styled with green headers, hover effects, zebra striping
- **Blockquotes** — green-bordered, subtle background
- **Images** — responsive with captions via `![caption](image.jpg)`
- **Footnotes** — superscript references rendered cleanly
- **Code blocks** — dark theme with syntax-friendly monospace font
- **Meta tags** — Open Graph, canonical URL, publication date (Medium reads these on import)
- **Mobile responsive** — looks great on all screen sizes
- **Zero dependencies** — works with just Python 3 standard library (optionally uses `python-markdown` if installed)
- **Auto-index** — generates an `index.html` listing all published articles

## Adding Images

Place images in the `images/` directory and reference them in your Markdown:

```markdown
![A beautiful diagram of Bitcoin's network](images/bitcoin-network.png)
```

The tool will:
- Copy local images to `docs/images/`
- Wrap them in `<figure>` tags with captions
- Medium will automatically import images from the hosted HTML

For web-hosted images, just use the full URL:

```markdown
![Diagram](https://example.com/my-image.png)
```

## GitHub Pages Setup

1. Push `docs/` to your repository
2. Go to **Settings → Pages**
3. Set Source to **Deploy from a branch**
4. Select your branch and folder: `/ docs`
5. Your articles will be live at `https://<username>.github.io/<repo>/`

## Medium Import Steps

1. Go to [medium.com/me/stories](https://medium.com/me/stories)
2. Click **"Import a story"**
3. Paste your GitHub Pages article URL
4. Review the imported draft — add any finishing touches
5. Hit **Publish**

Medium will automatically:
- Set the canonical URL to your GitHub Pages link
- Backdate to the original publication date
- Import images from the HTML
