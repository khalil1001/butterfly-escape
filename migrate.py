import os
import re
import time
from bs4 import BeautifulSoup
from notion_client import Client

# Notion setup
NOTION_TOKEN      = os.environ.get("NOTION_TOKEN")
ARTICLES_DB_ID    = os.environ.get("NOTION_ARTICLES_DB")
PRODUCTS_DB_ID    = os.environ.get("NOTION_PRODUCTS_DB")

if not NOTION_TOKEN:
    print("❌ ERROR: Please ensure NOTION_TOKEN is exported.")
    exit(1)

notion = Client(auth=NOTION_TOKEN)

def get_month_num(month_str):
    months = {
        'January': '01', 'February': '02', 'March': '03', 'April': '04',
        'May': '05', 'June': '06', 'July': '07', 'August': '08',
        'September': '09', 'October': '10', 'November': '11', 'December': '12'
    }
    return months.get(month_str, '01')

def parse_date(date_str):
    # 'October 20, 2021' -> '2021-10-20'
    try:
        parts = date_str.replace(',', '').split()
        if len(parts) == 3:
            y = parts[2]
            m = get_month_num(parts[0])
            d = parts[1].zfill(2)
            return f"{y}-{m}-{d}"
    except:
        pass
    return "2025-01-01"

def html_to_notion_blocks(html_filepath):
    """Parses a local post HTML file and creates Notion SDK block objects out of <p> and <img>."""
    blocks = []
    if not os.path.exists(html_filepath):
        return blocks
        
    with open(html_filepath, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')
        
    content_div = soup.find('div', class_='post-inner-content')
    if not content_div:
        return blocks
        
    for child in content_div.children:
        if child.name == 'p':
            text = child.get_text(strip=True)
            if text:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    }
                })
        elif child.name == 'figure':
            img = child.find('img')
            if img and img.get('src'):
                src = img['src']
                if src.startswith('http'):
                    blocks.append({
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "external",
                            "external": {"url": src}
                        }
                    })
    return blocks

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text

def normalize_tag(t):
    if not t: return ""
    t = t.strip()
    words = t.split()
    return " ".join(w.capitalize() for w in words)

# ── Blog Migration ────────────────────────────────────────────────────────────

def scrape_post_details(html_path):
    if not os.path.exists(html_path):
        return None, "", []
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
        
        hero_img = soup.select_one('.post-hero-img')
        cover_url = hero_img['src'] if hero_img and hero_img.get('src') else None
        
        hero_tag_elem = soup.select_one('.post-hero-tag')
        hero_cat = hero_tag_elem.get_text(strip=True) if hero_tag_elem else ""
        hero_cat = re.sub(r'[^\w\s]', '', hero_cat).strip().lower()
        
        tags = []
        tag_elems = soup.select('.post-tags .post-tag')
        for t in tag_elems:
            tags.append(t.get_text(strip=True))
            
        return cover_url, hero_cat, tags
    except:
        return None, "", []

def migrate_articles():
    print("\n📰 Starting Blog Migration to Notion...")
    
    with open("old_index.html", "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
        
    posts_grid = soup.find(id="postsGrid")
    if not posts_grid:
        print("❌ Could not find postsGrid in old_index.html")
        return
        
    cards = posts_grid.find_all('article', class_='card')
    print(f"Found {len(cards)} articles to migrate.")
    
    success_count = 0
    for i, card in enumerate(cards, 1):
        title = card.find('h2', class_='card-title').get_text(strip=True)
        date_str = card.find('p', class_='card-date').get_text(strip=True)
        iso_date = parse_date(date_str)
        excerpt = card.find('p', class_='card-excerpt').get_text(strip=True)
        
        data_cats = card.get('data-category', '').split()
        onclick = card.get('onclick', '')
        match = re.search(r"location.href='(posts/[^']+)'", onclick)
        html_path = match.group(1) if match else ""
        
        print(f"[{i}/{len(cards)}] Processing: {title}...")
        
        cover_url, hero_cat, tags = scrape_post_details(html_path)
        
        final_cats = set(c.lower() for c in data_cats if c)
        if hero_cat: final_cats.add(hero_cat.lower())
        notion_cats = [{"name": c} for c in sorted(list(final_cats))]
        
        cleaned_tags = sorted(list(set(normalize_tag(t) for t in tags if t)))
        children_blocks = []
        if html_path:
            children_blocks = html_to_notion_blocks(html_path)
        
        try:
            slug = slugify(title)
            properties = {
                "Title": {"title": [{"text": {"content": title}}]},
                "Status": {"status": {"name": "Published"}},
                "Date": {"date": {"start": iso_date}},
                "Excerpt": {"rich_text": [{"text": {"content": excerpt}}]},
                "Slug": {"rich_text": [{"text": {"content": slug}}]},
                "Category": {"multi_select": notion_cats}
            }
            if cleaned_tags:
                tag_str = ", ".join(cleaned_tags)
                properties["Tags"] = {"rich_text": [{"text": {"content": tag_str}}]}
            
            payload = {
                "parent": {"database_id": ARTICLES_DB_ID},
                "properties": properties,
                "children": children_blocks
            }
            if cover_url and cover_url.startswith('http'):
                properties["Cover Image"] = {"files": [{"name": "Cover", "type": "external", "external": {"url": cover_url}}]}
                payload["cover"] = {"type": "external", "external": {"url": cover_url}}
                
            notion.pages.create(**payload)
            success_count += 1
            print(f"  ✓ Uploaded: {len(notion_cats)} cats, {len(cleaned_tags)} tags.")
        except Exception as e:
            print(f"  ⚠ Failed to upload '{title}': {e}")
            
    print(f"✅ Blog Migration complete! {success_count} articles added.")

# ── Store Migration ───────────────────────────────────────────────────────────

def scrape_product_details(html_path):
    if not os.path.exists(html_path):
        return None
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
            
        title = soup.select_one('.product-heading').get_text(strip=True)
        price_str = soup.select_one('.product-price').get_text(strip=True)
        price = re.sub(r'[^\d]', '', price_str)
        
        desc = soup.select_one('.product-desc p').get_text(strip=True)
        
        specs = []
        for li in soup.select('.product-specs li'):
            specs.append(li.get_text(strip=True))
        material = specs[0] if specs else ""
        
        stripe_link = soup.select_one('.btn-buy-large')['href'] if soup.select_one('.btn-buy-large') else ""
        
        # Images
        image_urls = []
        # Main image
        main_img = soup.select_one('#main-product-img')
        if main_img and main_img.get('src'):
            image_urls.append(main_img['src'])
            
        # Thumbnails
        for img in soup.select('.thumbnail-list img'):
            src = img.get('src')
            if src and src not in image_urls:
                image_urls.append(src)
                
        return {
            "name": title,
            "price": int(price) if price else 0,
            "description": desc,
            "material": material,
            "stripe_link": stripe_link,
            "images": image_urls
        }
    except Exception as e:
        print(f"  ⚠ Error scraping {html_path}: {e}")
        return None

def migrate_products():
    if not PRODUCTS_DB_ID:
        print("⏭ Skipping Store Migration (NOTION_PRODUCTS_DB not set)")
        return

    print("\n🛍️ Starting Store Migration to Notion...")
    product_files = [f for f in os.listdir("products") if f.endswith(".html")]
    
    success_count = 0
    for filename in product_files:
        path = os.path.join("products", filename)
        print(f"Processing: {filename}...")
        data = scrape_product_details(path)
        if not data: continue
        
        try:
            slug = slugify(data["name"])
            
            # Format images for Notion (must be absolute URLs or files)
            # Since these are local paths in the HTML like '../images/...' 
            # we need to skip them unless they are full URLs, or the user uploads them manually.
            # But the build script expects images to be in Notion.
            # For migration, we'll try to guess the public URL if possible, 
            # but usually people just upload them manually to Notion.
            # I'll add them as 'external' if they look like URLs.
            notion_images = []
            for url in data["images"]:
                # Convert relative to 'absolute' for the sake of the record
                if url.startswith('../'):
                    url = "https://butterfly-escape.vercel.app/" + url.replace('../', '')
                
                notion_images.append({
                    "name": os.path.basename(url),
                    "type": "external",
                    "external": {"url": url}
                })

            properties = {
                "Name": {"title": [{"text": {"content": data["name"]}}]},
                "Slug": {"rich_text": [{"text": {"content": slug}}]},
                "Status": {"status": {"name": "Active"}},
                "Price": {"number": data["price"]},
                "Material": {"rich_text": [{"text": {"content": data["material"]}}]},
                "Description": {"rich_text": [{"text": {"content": data["description"]}}]},
                "Stripe Link": {"url": data["stripe_link"]}
            }
            if notion_images:
                properties["Images"] = {"files": notion_images}
            
            notion.pages.create(
                parent={"database_id": PRODUCTS_DB_ID},
                properties=properties
            )
            success_count += 1
            print(f"  ✓ Uploaded: {data['name']}")
            
        except Exception as e:
            print(f"  ⚠ Failed to upload '{data['name']}': {e}")
            
    print(f"✅ Store Migration complete! {success_count} products added.")

if __name__ == "__main__":
    import sys
    
    # Run articles by default
    if len(sys.argv) > 1 and sys.argv[1] == "store":
        migrate_products()
    elif len(sys.argv) > 1 and sys.argv[1] == "all":
        migrate_articles()
        migrate_products()
    else:
        # Default behavior: run articles
        print("Usage: python migrate.py [articles|store|all]")
        migrate_articles()
