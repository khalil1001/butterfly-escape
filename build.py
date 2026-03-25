#!/usr/bin/env python3
"""
Butterfly Escape — Notion CMS Build Script
==========================================
Reads content from two Notion databases:
  - Blog Articles  → regenerates posts/*.html and index.html post grid
  - Store Products → regenerates store.html and {slug}.html product pages

Run locally:
  pip install -r requirements.txt
  NOTION_TOKEN=secret_xxx NOTION_ARTICLES_DB=xxx NOTION_PRODUCTS_DB=xxx python build.py

On Vercel, these env vars are set in the dashboard.
"""

import os
import re
import json
import shutil
import requests
from datetime import datetime
from notion_client import Client
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
NOTION_TOKEN      = os.environ["NOTION_TOKEN"]
ARTICLES_DB_ID    = os.environ["NOTION_ARTICLES_DB"]
PRODUCTS_DB_ID    = os.environ["NOTION_PRODUCTS_DB"]
BASE_DIR          = Path(__file__).parent
POSTS_DIR         = BASE_DIR / "posts"
PRODUCTS_DIR      = BASE_DIR / "products"
IMAGES_ARTICLES   = BASE_DIR / "images" / "articles"
IMAGES_PRODUCTS   = BASE_DIR / "images" / "products"

notion = Client(auth=NOTION_TOKEN)


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text):
    """Simple slugifier — lowercase, hyphens, no special chars."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text


from PIL import Image
from io import BytesIO

def download_image(url, dest_dir, filename):
    """Download and compress an image from url to dest_dir/filename."""
    if not url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Always save as WebP for best compression/quality ratio
    filepath = dest_dir / f"{filename}.webp"
    
    try:
        img = None
        
        # LOCAL FALLBACK: If the URL is our own domain, check if the file exists locally first
        # This handles the transition period before the first deploy of new content.
        if "butterfly-escape.vercel.app" in url:
            local_rel_path = url.split("butterfly-escape.vercel.app/")[-1]
            local_abs_path = BASE_DIR / local_rel_path
            if local_abs_path.exists():
                img = Image.open(local_abs_path)
        
        if not img:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content))
        
        # Open image with Pillow
        # img already opened above
        
        # Convert to RGB mode (in case of RGBA/PNG or palettes)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        # Max dimension of 1600px handles high-res retina without being overkill
        img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        
        # Save heavily compressed WebP
        img.save(filepath, "webp", quality=75, optimize=True)
            
        return str(filepath.relative_to(BASE_DIR))
    except Exception as e:
        print(f"  ⚠ Failed to download/compress {url}: {e}")
        return url  # Fall back to remote URL


def get_prop(page, key, default=None):
    """Safely extract a page property value."""
    props = page.get("properties", {})
    if key not in props:
        return default
    p = props[key]
    t = p.get("type")
    if t == "title":
        parts = p.get("title", [])
        return "".join(r["plain_text"] for r in parts) or default
    if t == "rich_text":
        parts = p.get("rich_text", [])
        return "".join(r["plain_text"] for r in parts) or default
    if t == "select":
        s = p.get("select")
        return s["name"] if s else default
    if t == "multi_select":
        ms = p.get("multi_select", [])
        return ",".join(s["name"] for s in ms) if ms else default
    if t == "status":
        s = p.get("status")
        return s["name"] if s else default
    if t == "number":
        return p.get("number", default)
    if t == "date":
        d = p.get("date")
        return d["start"] if d else default
    if t == "url":
        return p.get("url", default)
    if t == "files":
        files = p.get("files", [])
        urls = []
        for f in files:
            if f["type"] == "external":
                urls.append(f["external"]["url"])
            elif f["type"] == "file":
                urls.append(f["file"]["url"])
        return urls or default
    return default


def blocks_to_html(blocks):
    """Convert Notion block list to simple HTML for article body."""
    html = ""
    for block in blocks:
        bt = block["type"]
        if bt == "paragraph":
            parts = block["paragraph"].get("rich_text", [])
            text = rich_text_to_html(parts)
            if text.strip():
                html += f'<p class="wp-block-paragraph">{text}</p>\n'
        elif bt in ("heading_1", "heading_2", "heading_3"):
            level = {"heading_1": 1, "heading_2": 2, "heading_3": 3}[bt]
            parts = block[bt].get("rich_text", [])
            text = rich_text_to_html(parts)
            html += f'<h{level+1}>{text}</h{level+1}>\n'
        elif bt == "image":
            img = block["image"]
            url = img["external"]["url"] if img["type"] == "external" else img["file"]["url"]
            caption_parts = img.get("caption", [])
            caption = "".join(r["plain_text"] for r in caption_parts)
            cap_html = f"<figcaption>{caption}</figcaption>" if caption else ""
            html += f'<figure class="post-figure"><img class="post-img" src="{url}" alt="{caption}" />{cap_html}</figure>\n'
        elif bt == "bulleted_list_item":
            parts = block["bulleted_list_item"].get("rich_text", [])
            text = rich_text_to_html(parts)
            html += f'<li>{text}</li>\n'
        elif bt == "numbered_list_item":
            parts = block["numbered_list_item"].get("rich_text", [])
            text = rich_text_to_html(parts)
            html += f'<li>{text}</li>\n'
        elif bt == "quote":
            parts = block["quote"].get("rich_text", [])
            text = rich_text_to_html(parts)
            html += f'<blockquote>{text}</blockquote>\n'
        elif bt == "divider":
            html += '<hr />\n'
    return html


def rich_text_to_html(parts):
    """Convert rich_text array to HTML string with bold/italic/links."""
    result = ""
    for part in parts:
        text = part.get("plain_text", "")
        # Escape HTML entities
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        annotations = part.get("annotations", {})
        link = part.get("href")
        if annotations.get("bold"):
            text = f"<strong>{text}</strong>"
        if annotations.get("italic"):
            text = f"<em>{text}</em>"
        if annotations.get("code"):
            text = f"<code>{text}</code>"
        if link:
            text = f'<a href="{link}" target="_blank" rel="noopener">{text}</a>'
        result += text
    return result


def get_all_blocks(page_id):
    """Fetch all blocks for a page, handling pagination."""
    blocks = []
    cursor = None
    while True:
        params = {"block_id": page_id, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = notion.blocks.children.list(**params)
        blocks.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return blocks


def query_database(db_id, filter_status=None):
    """Fetch all rows from a Notion database."""
    rows = []
    cursor = None
    filter_body = {}
    if filter_status:
        filter_body = {
            "filter": {
                "property": "Status",
                "status": {"equals": filter_status}
            }
        }
    while True:
        params = {"database_id": db_id, "page_size": 100, **filter_body}
        if cursor:
            params["start_cursor"] = cursor
        resp = notion.databases.query(**params)
        rows.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return rows


# ── HTML Partials ─────────────────────────────────────────────────────────────

NAV = """  <nav class="nav">
    <a class="nav-logo" href="{root}index.html">Butterfly<span> Escape</span></a>
    <div class="hamburger" id="hamburger"><span></span><span></span><span></span></div>
    <ul class="nav-links" id="nav-links">
      <li><a href="{root}index.html#posts">Articles</a></li>
      <li><a href="{root}store.html">Store</a></li>
      <li><a href="{root}index.html#about">About</a></li>
      <li><a href="https://www.instagram.com/butterfly__escape" target="_blank" rel="noopener" class="nav-cta">Instagram ↗</a></li>
    </ul>
  </nav>"""

NAV_SCRIPT = """<script>
  document.addEventListener('DOMContentLoaded', () => {
    const hamburger = document.getElementById('hamburger');
    const navLinks = document.getElementById('nav-links');
    const links = document.querySelectorAll('.nav-links a');
    if (hamburger && navLinks) {
      hamburger.addEventListener('click', () => {
        hamburger.classList.toggle('active');
        navLinks.classList.toggle('active');
      });
      links.forEach(link => {
        link.addEventListener('click', () => {
          hamburger.classList.remove('active');
          navLinks.classList.remove('active');
        });
      });
    }
  });
</script>"""

FOOTER_POST = """  <footer class="footer">
    <p>© 2025 Butterfly Escape · <a href="../index.html">← All Articles</a> · <a href="https://www.instagram.com/butterfly__escape" target="_blank">Instagram</a></p>
  </footer>"""

FOOTER_ROOT = """  <footer class="footer">
    <p>© 2025 Butterfly Escape · Made with 🦋 · <a href="https://www.instagram.com/butterfly__escape" target="_blank">Instagram</a></p>
  </footer>"""


# ── Post Page Builder ─────────────────────────────────────────────────────────

def build_post_page(article, body_html):
    title     = article["title"]
    date_str  = article["date"]
    tag       = article["category"]
    cover_img = article["cover_img"]
    slug      = article["slug"]
    tags      = [t.strip() for t in article.get("tags", "").split(",") if t.strip()]

    # Format date
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = date_obj.strftime("%B %-d, %Y")
    except Exception:
        date_display = date_str or ""

    tags_html = "".join(f'<span class="post-tag">{t}</span>' for t in tags)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} — Butterfly Escape</title>
  <meta name="description" content="{article.get('excerpt', '')}" />
  <link rel="stylesheet" href="../css/style.css" />
  <link rel="icon" href="../favicon.svg" type="image/svg+xml" />
  <link rel="icon" href="../favicon.png" type="image/png" />
  <script src="https://unpkg.com/commentbox.io/dist/commentBox.min.js"></script>
  <script>
    document.addEventListener('DOMContentLoaded', function() {{
      commentBox('5724160068157440-proj', {{
        textColor: '#4A4A4A',
        subtextColor: '#7D7D7D',
        backgroundColor: 'transparent',
        buttonColor: '#C9788A'
      }});
    }});
  </script>
</head>

<body>
{NAV.format(root="../")}
  <div class="post-hero">
    <img class="post-hero-img" src="../{cover_img}" alt="{title}" />
    <div class="post-hero-overlay">
      <div class="post-hero-content">
        <span class="post-hero-tag">{tag}</span>
        <h1 class="post-hero-title">{title}</h1>
        <p class="post-hero-meta">{date_display}</p>
      </div>
    </div>
  </div>
  <div class="post-container">
    <a class="post-back" href="../index.html">← Back to all articles</a>
    <div class="post-inner-content">
{body_html}
    </div>
    <div class="post-tags">
      {tags_html}
    </div>
    
    <!-- Social Section -->
    <div class="post-social">
      <hr class="social-divider">
      
      <div class="social-header">
        <h2 class="social-title">Join the Conversation</h2>
        <p class="social-subtitle">Liked this article? Let me know with a clap or a comment below! 🦋</p>
      </div>

      <div class="commentbox"></div>
    </div>
  </div>
{FOOTER_POST}
{NAV_SCRIPT}
</body>
</html>
"""


# ── Product Page Builder ──────────────────────────────────────────────────────

def build_product_page(product):
    title    = product["name"]
    price    = f"{product.get('currency', '€')}{int(product.get('price', 0))}"
    material = product.get("material", "")
    desc     = product.get("description", "")
    link     = product.get("stripe_link", "#")
    images   = product.get("local_images", [])
    cover    = images[0] if images else ""

    thumbs_html = ""
    for i, img in enumerate(images):
        active = "active" if i == 0 else ""
        thumbs_html += f'          <img class="thumb {active}" src="../{img}" onclick="changeImage(\'../{img}\', this)">\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} — Butterfly Escape</title>
  <link rel="stylesheet" href="../css/style.css" />
  <link rel="icon" href="../favicon.svg" type="image/svg+xml" />
  <link rel="icon" href="../favicon.png" type="image/png" />
  <script src="https://unpkg.com/commentbox.io/dist/commentBox.min.js"></script>
  <script>
    document.addEventListener('DOMContentLoaded', function() {{
      commentBox('5724160068157440-proj', {{
        textColor: '#4A4A4A',
        subtextColor: '#7D7D7D',
        backgroundColor: 'transparent',
        buttonColor: '#C9788A'
      }});
    }});
  </script>
</head>

<body class="product-page">
{NAV.format(root="../")}
  <main class="product-detail-container">
    <a href="../store.html" class="post-back">← Back to Store</a>
    <div class="product-layout">
      <div class="product-gallery">
        <div class="main-image-wrap">
          <img id="main-product-img" src="../{cover}" alt="{title}">
        </div>
        <div class="thumbnail-list">
{thumbs_html}        </div>
      </div>
      <div class="product-info-panel">
        <h1 class="product-heading">{title}</h1>
        <p class="product-price">{price}</p>
        <div class="product-desc"><p>{desc}</p></div>
        <ul class="product-specs">
          <li><strong>Dimensions:</strong> {material}</li>
          <li><strong>Shipping:</strong> Ships worldwide within 3–5 business days.</li>
        </ul>
        <a href="{link}" class="btn-buy btn-buy-large" target="_blank" rel="noopener">Buy Now — Checkout securely with Stripe</a>
      </div>
    </div>
  <div class="post-container" style="margin-top: 2rem;">
    <div class="post-social">
      <hr class="social-divider">
      <div class="social-header">
        <h2 class="social-title">Join the Conversation</h2>
        <p class="social-subtitle">Liked this product? Let me know with a comment below! 🦋</p>
      </div>
      <div class="commentbox"></div>
    </div>
  </div>
  </main>

{FOOTER_ROOT}
  <script>
    function changeImage(src, el) {{
      document.getElementById('main-product-img').src = src;
      document.querySelectorAll('.thumb').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    }}
  </script>
{NAV_SCRIPT}
</body>
</html>
"""


# ── Index Page Card Builder ───────────────────────────────────────────────────

def build_post_card(article):
    title    = article["title"]
    slug     = article["slug"]
    cover    = article.get("cover_img", "")
    excerpt  = article.get("excerpt", "")
    date_str = article.get("date", "")
    category = article.get("category", "")
    tag_str  = article.get("tag", category)
    data_cat = slugify(category)

    # Split tags and create multiple pills
    tags_list = [t.strip() for t in tag_str.split(",") if t.strip()]
    if not tags_list and category:
        tags_list = [category]
        
    tag_display = "".join(f'<span class="card-tag">{t}</span>' for t in tags_list)

    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = date_obj.strftime("%B %d, %Y")
    except Exception:
        date_display = date_str

    return f"""      <article class="card" data-category="{data_cat}" onclick="location.href='posts/{slug}.html'">
        <div class="card-image-wrap">
          <img class="card-image" src="{cover}" alt="{title}" loading="lazy" />
          <div class="card-tags">
            {tag_display}
          </div>
        </div>
        <div class="card-body">
          <p class="card-date">{date_display}</p>
          <h2 class="card-title">{title}</h2>
          <p class="card-excerpt">{excerpt}</p>
          <div class="card-footer">
            <span class="card-read-more">Read more →</span>
          </div>
        </div>
      </article>"""


def build_store_card(product):
    name   = product["name"]
    slug   = product["slug"]
    price  = f"{product.get('currency', '€')}{int(product.get('price', 0))}"
    mat    = product.get("material", "")
    status = product.get("status", "Active")
    images = product.get("local_images", [])
    main   = images[0] if len(images) > 0 else ""
    hover  = images[1] if len(images) > 1 else ""
    
    if status == "Active":
        link_href = f"products/{slug}.html"
        btn_text  = "View Details"
    else:
        link_href = "#"
        btn_text  = "Coming Soon"

    hover_img = f'\n          <img class="img-hover" loading="lazy" src="{hover}" alt="{name} - Alternate">' if hover else ""

    return f"""      <article class="product-card">
        <a href="{link_href}" class="product-image-wrap">
          <img class="img-main" loading="lazy" src="{main}" alt="{name}">{hover_img}
        </a>
        <div class="product-info">
          <a href="{link_href}"><h2 class="product-title">{name}</h2></a>
          <p class="product-details">{mat}</p>
          <div class="product-action">
            <span class="product-price">{price}</span>
            <a href="{link_href}" class="btn-buy">{btn_text}</a>
          </div>
        </div>
      </article>"""


# ── Index.html Updater ────────────────────────────────────────────────────────

def update_index_html(cards_html):
    """Replace the posts-grid content in index.html with new cards."""
    index_path = BASE_DIR / "index.html"
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace content between the posts-grid div tags
    pattern = r'(<div class="posts-grid" id="postsGrid">)(.*?)(</div><!-- /posts-grid -->\s*</section>)'
    replacement = f'\\1\n\n{cards_html}\n\n    \\3'
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("  ✓ Updated index.html post grid")


def update_store_html(store_cards_html):
    """Replace the product-grid content in store.html with new cards."""
    store_path = BASE_DIR / "store.html"
    with open(store_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r'(<div class="product-grid">)(.*?)(</div>\s*\n\s*</main>)'
    replacement = f'\\1\n\n{store_cards_html}\n\n    \\3'
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    with open(store_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("  ✓ Updated store.html product grid")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🦋 Butterfly Escape — Notion Build Script")
    print("=" * 45)

    # ── 1. Fetch and build Articles ──────────────────
    print("\n📰 Fetching Published articles from Notion...")
    article_rows = query_database(ARTICLES_DB_ID, filter_status="Published")
    print(f"   Found {len(article_rows)} published articles.")

    articles = []
    post_cards = []

    for row in article_rows:
        page_id = row["id"]
        title   = get_prop(row, "Title") or "Untitled"
        slug    = get_prop(row, "Slug") or slugify(title)
        date    = get_prop(row, "Date") or ""
        cat     = get_prop(row, "Category") or ""
        excerpt = get_prop(row, "Excerpt") or ""
        tag     = get_prop(row, "Tag") or cat
        cover_urls = get_prop(row, "Cover Image") or []

        print(f"   → Processing: {title}")

        # Download cover image
        cover_img = None
        if cover_urls:
            cover_img = download_image(cover_urls[0], IMAGES_ARTICLES, slug)

        article = {
            "title": title, "slug": slug, "date": date,
            "category": cat, "tag": tag, "excerpt": excerpt,
            "cover_img": cover_img or "",
            "tags": get_prop(row, "Tags") or cat
        }
        articles.append(article)
        
        # Ensure posts directory exists
        POSTS_DIR.mkdir(parents=True, exist_ok=True)

        # Fetch body blocks and build post page
        blocks  = get_all_blocks(page_id)
        body_html = blocks_to_html(blocks)
        post_html = build_post_page(article, body_html)

        post_path = POSTS_DIR / f"{slug}.html"
        with open(post_path, "w", encoding="utf-8") as f:
            f.write(post_html)
        print(f"     ✓ Written posts/{slug}.html")

    # Sort articles by date (newest first)
    # Date is YYYY-MM-DD, so reverse alphabetical sort works perfectly
    articles.sort(key=lambda x: x["date"], reverse=True)

    # Generate cards from sorted list
    for article in articles:
        post_cards.append(build_post_card(article))

    # Update index.html
    update_index_html("\n\n".join(post_cards))

    # ── 2. Fetch and build Products ──────────────────
    print("\n🛍️  Fetching products from Notion...")
    product_rows = query_database(PRODUCTS_DB_ID)
    # Filter Active and Coming Soon (exclude Hidden)
    product_rows = [r for r in product_rows if get_prop(r, "Status") not in (None, "Hidden")]
    print(f"   Found {len(product_rows)} products.")

    store_cards = []

    for row in product_rows:
        name     = get_prop(row, "Name") or "Untitled"
        slug     = get_prop(row, "Slug") or slugify(name)
        status   = get_prop(row, "Status") or "Active"
        price    = get_prop(row, "Price") or 0
        currency = get_prop(row, "Currency") or "€"
        material = get_prop(row, "Material") or ""
        desc     = get_prop(row, "Description") or ""
        stripe   = get_prop(row, "Stripe Link") or "#"
        img_urls = get_prop(row, "Images") or []

        print(f"   → Processing: {name} [{status}]")

        # Download all product images
        local_images = []
        for i, url in enumerate(img_urls):
            local_path = download_image(url, IMAGES_PRODUCTS, f"{slug}-{i+1}")
            if local_path:
                local_images.append(local_path)

        product = {
            "name": name, "slug": slug, "status": status,
            "price": price, "currency": currency,
            "material": material, "description": desc,
            "stripe_link": stripe, "local_images": local_images
        }

        # Build product detail page (only for Active products)
        if status == "Active":
            PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
            prod_html = build_product_page(product)
            prod_path = PRODUCTS_DIR / f"{slug}.html"
            with open(prod_path, "w", encoding="utf-8") as f:
                f.write(prod_html)
            print(f"     ✓ Written products/{slug}.html")

        store_cards.append(build_store_card(product))

    # Update store.html
    update_store_html("\n\n".join(store_cards))

    print("\n✅ Build complete!")


if __name__ == "__main__":
    main()
