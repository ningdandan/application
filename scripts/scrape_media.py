#!/usr/bin/env python3
"""
Scrape 1 profile/hero photo + up to 3 work samples per artist from their
portfolio website (preferred) and/or public Instagram profile.

Usage:
  .venv/bin/pip install -r scripts/requirements.txt
  .venv/bin/python scripts/scrape_media.py              # all artists
  .venv/bin/python scripts/scrape_media.py --limit 5
  .venv/bin/python scripts/scrape_media.py --id 3
  .venv/bin/python scripts/scrape_media.py --refresh
  .venv/bin/python scripts/scrape_media.py --skip-ig    # websites only (faster)

Instagram note:
  Anonymous IG requests are usually blocked (401). To enable IG scraping,
  log in once, then re-run without --skip-ig:

    .venv/bin/instaloader --login YOUR_IG_USERNAME
    IG_USER=YOUR_IG_USERNAME .venv/bin/python scripts/scrape_media.py

Images → media/<id>/   Updates artists.json + data.js for the website.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
ARTISTS_PATH = ROOT / "artists.json"
DATA_JS_PATH = ROOT / "data.js"
MEDIA_DIR = ROOT / "media"
MANIFEST_PATH = MEDIA_DIR / "manifest.json"

# Fail fast on Instagram rate limits
os.environ.setdefault("INSTALOADER_MAX_CONNECTION_ATTEMPTS", "1")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_HOST_FRAGMENTS = (
    "google.com",
    "docs.google",
    "drive.google",
    "googleapis.com",
    "facebook.com",
    "fbcdn.net",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "tiktok.com",
    "schema.org",
)

SKIP_IMG_FRAGMENTS = (
    "/logo",
    "logo.",
    "favicon",
    "sprite",
    "placeholder",
    "1x1",
    "/pixel",
    "spacer",
    "tracking",
    "emoji",
    "badge",
    "spinner",
    "loading.gif",
    ".svg",
    "data:image/svg",
    "gravatar.com/avatar",
    "static.squarespace.com/universal",
    "static.cargo.site/assets",
    "/noise.png",
    "i.ytimg.com",
    "img.youtube.com",
)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean_portfolio_url(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    m = re.search(
        r"(https?://[^\s\)\",]+)|(www\.[^\s\)\",]+)|([a-z0-9.-]+\.[a-z]{2,}(?:/[^\s\)\",]*)?)",
        text,
        re.I,
    )
    if not m:
        return ""
    url = next(g for g in m.groups() if g)
    url = url.rstrip(".,);]'\"")
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return urlunparse(parsed)


def ig_handle(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    if "instagram.com" in text.lower():
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", text, re.I)
        return (m.group(1) if m else "").strip(".")
    m = re.search(r"@([A-Za-z0-9._]+)", text)
    if m:
        return m.group(1)
    # reject display names with spaces / invalid handles
    handle = text.lstrip("@").strip()
    if " " in handle or "/" in handle:
        return ""
    handle = handle.strip("./")
    if re.fullmatch(r"[A-Za-z0-9._]{2,30}", handle):
        return handle
    return ""


def sanitize_url(url: str) -> str:
    """Strip HTML/JSON junk that often glues onto scraped URLs."""
    url = url.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
    # cut off common trailers from embedded JSON
    for sep in ('&quot;', '"', "'", "\\", "<", ">", " ", "\n", "\r"):
        if sep in url:
            url = url.split(sep, 1)[0]
    return url.rstrip(".,);]")


def normalize_image_url(url: str) -> str:
    """Prefer a mid/large size for Squarespace / Readymag / common CDNs."""
    url = sanitize_url(url)
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if "squarespace-cdn.com" in host or "squarespace.com" in host:
        qs["format"] = "1500w"
        return urlunparse(parsed._replace(query=urlencode(qs)))

    # Readymag: full screenshots 403; _1024 variants work
    if "rmcdn.net" in host and path.endswith("_readyscr.jpg"):
        path = path.replace("_readyscr.jpg", "_readyscr_1024.jpg")
        return urlunparse(parsed._replace(path=path, query=""))

    if "images.unsplash.com" in host:
        qs["w"] = "1200"
        return urlunparse(parsed._replace(query=urlencode(qs)))

    return urlunparse(parsed._replace(path=path))


def image_fingerprint(url: str) -> str:
    """Dedupe CDN variants of the same asset."""
    parsed = urlparse(url)
    path = parsed.path
    m = re.search(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/", path, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"(Screenshot-[0-9a-f\-]+)", path, re.I)
    if m:
        return m.group(1).lower()
    base = re.sub(r"[-_]\d{2,4}w?(?=\.|$)", "", path)
    base = re.sub(r"_readyscr(_\d+)?", "", base, flags=re.I)
    return f"{parsed.netloc}{base}"


def guess_ext(url: str, content_type: str | None) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext == ".jpe":
            return ".jpg"
        if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def is_skippable_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(x in host for x in SKIP_HOST_FRAGMENTS)


def looks_like_media(url: str) -> bool:
    low = url.lower()
    if not url.startswith("http"):
        return False
    if any(x in low for x in SKIP_IMG_FRAGMENTS):
        return False
    host = urlparse(url).netloc.lower()
    if any(x in host for x in SKIP_HOST_FRAGMENTS) and "cdninstagram" not in host and "instagram" not in host:
        return False
    return True


def download_image(url: str, dest: Path, referer: str | None = None, timeout: int = 30) -> bool:
    try:
        url = normalize_image_url(url)
        headers = {}
        if referer:
            headers["Referer"] = referer
        elif urlparse(url).netloc:
            # hotlink-friendly default
            headers["Referer"] = f"https://{urlparse(url).netloc}/"
        r = SESSION.get(url, timeout=timeout, stream=True, allow_redirects=True, headers=headers)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" in ctype and not path_looks_image(url):
            return False
        data = r.content
        if len(data) < 4000:
            return False
        if not is_image_bytes(data, ctype):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Prefer magic-byte extension over URL guess (Squarespace often serves webp)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext = ".webp"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        else:
            ext = guess_ext(url, ctype)
        out = dest.with_suffix(ext)
        # remove sibling extensions if re-saving
        for other in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            sibling = dest.with_suffix(other)
            if sibling != out and sibling.exists():
                sibling.unlink(missing_ok=True)
        out.write_bytes(data)
        return True
    except Exception as e:
        print(f"    download fail: {url[:90]} ({e})")
        return False


def path_looks_image(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")) or "format=" in url.lower()


def is_image_bytes(data: bytes, ctype: str) -> bool:
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[:4] == b"GIF8":
        return True
    if b"ftypavif" in data[:32] or b"ftypmif1" in data[:32]:
        return True
    return ctype.startswith("image/")


def extract_imgs_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []

    for prop in (
        ("property", "og:image"),
        ("property", "og:image:url"),
        ("name", "twitter:image"),
        ("name", "twitter:image:src"),
    ):
        tag = soup.find("meta", attrs={prop[0]: prop[1]})
        if tag and tag.get("content"):
            found.append(urljoin(base_url, tag["content"].strip()))

    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "image_src" in rel and link.get("href"):
            found.append(urljoin(base_url, link["href"].strip()))

    for img in soup.find_all("img"):
        candidates = []
        for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-url", "data-image"):
            if img.get(attr):
                candidates.append(img.get(attr))
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            best = None
            best_w = -1
            for chunk in srcset.split(","):
                bits = chunk.strip().split()
                if not bits:
                    continue
                w = 0
                if len(bits) > 1 and bits[1].endswith("w"):
                    try:
                        w = int(bits[1][:-1])
                    except ValueError:
                        w = 0
                if w >= best_w:
                    best_w = w
                    best = bits[0]
            if best:
                candidates.append(best)
        for c in candidates:
            if c and not c.startswith("data:"):
                found.append(urljoin(base_url, c.strip()))

    for el in soup.find_all(style=True):
        for m in re.finditer(r"url\(['\"]?(.*?)['\"]?\)", el["style"]):
            u = m.group(1)
            if u and not u.startswith("data:"):
                found.append(urljoin(base_url, u.strip()))

    # Platform-specific clean extractors (avoid JSON/HTML glue)
    for m in re.finditer(r"https://images\.squarespace-cdn\.com/content/v1/[^\"'\s<>\\]+", html, re.I):
        found.append(m.group(0))
    for m in re.finditer(r"https://c-p\.rmcdn\.net/[A-Za-z0-9/_\-]+\.jpg", html, re.I):
        found.append(m.group(0))
    for m in re.finditer(r"https://freight\.cargo\.site/[^\"'\s<>\\]+", html, re.I):
        found.append(m.group(0))
    for m in re.finditer(
        r"https?://(?:cdn\.sanity\.io|uploads-ssl\.webflow\.com|images\.unsplash\.com|"
        r"miro\.medium\.com|cdn\.dribbble\.com)[^\"'\s<>\\]+",
        html,
        re.I,
    ):
        found.append(m.group(0))

    for m in re.finditer(r'https?://[^\"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"\'\s<>]*)?', html, re.I):
        found.append(m.group(0))

    seen = set()
    out = []
    for u in found:
        u = sanitize_url(u)
        if not looks_like_media(u):
            continue
        # Prefer Readymag 1024 variants — drop bare _readyscr.jpg if 1024 exists later
        fp = image_fingerprint(u)
        if fp in seen:
            # upgrade to 1024 if we already have smaller
            continue
        seen.add(fp)
        out.append(normalize_image_url(u))
    return out


def scrape_website(url: str) -> tuple[list[str], str]:
    """Returns (image_urls, final_page_url used as Referer)."""
    if not url or is_skippable_host(url):
        return [], url
    if "instagram.com" in urlparse(url).netloc.lower():
        return [], url
    try:
        r = SESSION.get(url, timeout=25, allow_redirects=True)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype.startswith("image/"):
            return [r.url], r.url
        imgs = extract_imgs_from_html(r.text, r.url)

        def score(u: str) -> int:
            s = 0
            low = u.lower()
            if any(
                x in low
                for x in (
                    "upload",
                    "wp-content",
                    "images",
                    "media",
                    "gallery",
                    "work",
                    "project",
                    "content/v1",
                    "freight.cargo",
                    "rmcdn",
                )
            ):
                s += 5
            if "readyscr_1024" in low:
                s += 8
            if "format=1500" in low or "format=1000" in low:
                s += 3
            if low.endswith((".jpg", ".jpeg", ".png", ".webp")) or "format=" in low:
                s += 2
            return s

        return sorted(imgs, key=score, reverse=True)[:16], r.url
    except Exception as e:
        print(f"    site fail {url}: {e}")
        return [], url


def scrape_instagram_web(handle: str) -> list[str]:
    if not handle:
        return []
    imgs: list[str] = []
    try:
        r = SESSION.get(f"https://www.instagram.com/{handle}/", timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            imgs.append(og["content"])
        for m in re.finditer(r'"profile_pic_url_hd":"(https:[^"]+)"', r.text):
            imgs.insert(0, m.group(1).encode("utf-8").decode("unicode_escape"))
        for m in re.finditer(r'"display_url":"(https:[^"]+)"', r.text):
            imgs.append(m.group(1).encode("utf-8").decode("unicode_escape"))
    except Exception as e:
        print(f"    ig web fail @{handle}: {e}")
    return dedupe(imgs)[:8]


def scrape_instagram_instaloader(handle: str) -> list[str]:
    try:
        import instaloader  # type: ignore
    except ImportError:
        return []

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        max_connection_attempts=1,
    )
    # Optional: reuse a browser login session saved by:
    #   .venv/bin/instaloader --login YOUR_IG_USERNAME
    session_user = os.environ.get("IG_USER", "").strip()
    if session_user:
        try:
            L.load_session_from_file(session_user)
            print(f"    ig: loaded session for {session_user}")
        except Exception as e:
            print(f"    ig: no session for {session_user} ({e})")

    try:
        profile = instaloader.Profile.from_username(L.context, handle)
        urls = [profile.profile_pic_url]
        for i, post in enumerate(profile.get_posts()):
            if i >= 5:
                break
            urls.append(post.url)
            time.sleep(0.3)
        return urls
    except Exception as e:
        print(f"    ig skip @{handle}: {e}")
        return []


def scrape_instagram(handle: str) -> list[str]:
    urls = scrape_instagram_instaloader(handle)
    if len(urls) >= 2:
        return urls
    for u in scrape_instagram_web(handle):
        if u not in urls:
            urls.append(u)
    return urls


def dedupe(seq: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for u in seq:
        if not u:
            continue
        fp = image_fingerprint(u)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(u)
    return out


def already_have(artist_dir: Path) -> dict:
    profile = None
    samples = []
    if not artist_dir.exists():
        return {"profile": None, "samples": []}
    for name in ("profile.jpg", "profile.jpeg", "profile.png", "profile.webp", "profile.gif"):
        p = artist_dir / name
        if p.exists() and p.stat().st_size > 0:
            profile = p
            break
    for i in range(1, 4):
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            p = artist_dir / f"sample_{i}{ext}"
            if p.exists() and p.stat().st_size > 0:
                samples.append(p)
                break
    return {"profile": profile, "samples": samples}


def save_slot(url: str, dest_stem: Path, referer: str | None = None) -> Path | None:
    dest = dest_stem.with_suffix(".jpg")
    if not download_image(url, dest, referer=referer):
        return None
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        p = dest_stem.with_suffix(ext)
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def gather_candidates(artist: dict, skip_ig: bool) -> tuple[list[str], list[str], dict, str | None]:
    site_imgs: list[str] = []
    ig_imgs: list[str] = []
    meta = {"site": None, "instagram": None}
    referer = None

    port = clean_portfolio_url(artist.get("portfolio") or "")
    if port:
        print(f"    site: {port}")
        meta["site"] = port
        site_imgs, referer = scrape_website(port)

    handle = ig_handle(artist.get("instagram") or "")
    if not handle and port and "instagram.com" in port:
        handle = ig_handle(port)

    need_ig = (not skip_ig) and handle and len(site_imgs) < 4
    if need_ig:
        print(f"    ig: @{handle}")
        meta["instagram"] = handle
        ig_imgs = scrape_instagram(handle)
    elif handle and not skip_ig and len(site_imgs) >= 4:
        print("    ig: skipped (site has enough images)")
        meta["instagram"] = handle

    profile_cands = dedupe((ig_imgs[:1] if ig_imgs else []) + site_imgs[:3])
    sample_cands = dedupe(site_imgs + (ig_imgs[1:] if ig_imgs else []))
    return profile_cands, sample_cands, meta, referer


def process_artist(artist: dict, refresh: bool = False, skip_ig: bool = False) -> dict:
    aid = artist["id"]
    name = artist["name"]
    artist_dir = MEDIA_DIR / str(aid)
    existing = already_have(artist_dir)

    result = {
        "id": aid,
        "name": name,
        "profile": None,
        "samples": [],
        "sources": {},
        "status": "ok",
        "error": None,
    }

    need_profile = refresh or not existing["profile"]
    need_samples = refresh or len(existing["samples"]) < 3

    if not need_profile and not need_samples:
        result["profile"] = str(existing["profile"].relative_to(ROOT)) if existing["profile"] else None
        result["samples"] = [str(p.relative_to(ROOT)) for p in existing["samples"][:3]]
        result["status"] = "cached"
        return result

    has_links = clean_portfolio_url(artist.get("portfolio") or "") or ig_handle(artist.get("instagram") or "")
    if not has_links:
        result["status"] = "no_links"
        result["error"] = "No portfolio or Instagram"
        return result

    print(f"[{aid}] {name}")
    try:
        profile_cands, sample_cands, sources, referer = gather_candidates(artist, skip_ig=skip_ig)
        result["sources"] = sources
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        return result

    artist_dir.mkdir(parents=True, exist_ok=True)

    profile_path = existing["profile"]
    used_fps = set()
    if need_profile:
        profile_path = None
        for url in profile_cands:
            saved = save_slot(url, artist_dir / "profile", referer=referer)
            if saved:
                profile_path = saved
                used_fps.add(image_fingerprint(url))
                print(f"    profile <- {url[:88]}")
                break

    sample_paths = [] if refresh else list(existing["samples"])
    if need_samples:
        # wipe bad prior samples when refreshing or replacing empty junk
        for url in sample_cands:
            if len(sample_paths) >= 3:
                break
            fp = image_fingerprint(url)
            if fp in used_fps:
                continue
            idx = len(sample_paths) + 1
            saved = save_slot(url, artist_dir / f"sample_{idx}", referer=referer)
            if not saved:
                continue
            if profile_path and saved.exists() and profile_path.exists():
                if hashlib.md5(saved.read_bytes()).hexdigest() == hashlib.md5(profile_path.read_bytes()).hexdigest():
                    saved.unlink(missing_ok=True)
                    continue
            sample_paths.append(saved)
            used_fps.add(fp)
            print(f"    sample_{idx} <- {url[:88]}")
            time.sleep(0.2)

    result["profile"] = str(profile_path.relative_to(ROOT)) if profile_path else None
    result["samples"] = [str(p.relative_to(ROOT)) for p in sample_paths[:3]]
    if not result["profile"] and not result["samples"]:
        result["status"] = "empty"
        result["error"] = "No images found"
    elif not result["profile"] or len(result["samples"]) < 3:
        result["status"] = "partial"
    return result


def update_artists_json(results: list[dict]) -> None:
    artists = json.loads(ARTISTS_PATH.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in results}
    for a in artists:
        r = by_id.get(a["id"])
        if not r:
            continue
        a["media"] = {
            "profile": r.get("profile"),
            "samples": r.get("samples") or [],
            "status": r.get("status"),
        }
    ARTISTS_PATH.write_text(json.dumps(artists, ensure_ascii=False, indent=2), encoding="utf-8")
    DATA_JS_PATH.write_text(
        "window.ARTISTS = " + json.dumps(artists, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape artist profile + work sample images")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--id", type=int, default=0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--skip-ig", action="store_true", help="Only scrape portfolio websites")
    args = parser.parse_args()

    artists = json.loads(ARTISTS_PATH.read_text(encoding="utf-8"))
    if args.id:
        artists = [a for a in artists if a["id"] == args.id]
    else:
        artists = sorted(
            artists,
            key=lambda a: (
                0 if clean_portfolio_url(a.get("portfolio") or "") else 1,
                0 if ig_handle(a.get("instagram") or "") else 1,
                a["id"],
            ),
        )
        if args.limit:
            artists = artists[: args.limit]

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for a in artists:
        results.append(process_artist(a, refresh=args.refresh, skip_ig=args.skip_ig))
        time.sleep(0.5)

    prev = {}
    if MANIFEST_PATH.exists():
        try:
            prev = {r["id"]: r for r in json.loads(MANIFEST_PATH.read_text())}
        except Exception:
            prev = {}
    for r in results:
        prev[r["id"]] = r
    all_results = [prev[k] for k in sorted(prev)]
    MANIFEST_PATH.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    update_artists_json(all_results)

    ok = sum(1 for r in results if r["status"] in ("ok", "cached", "partial"))
    empty = sum(1 for r in results if r["status"] in ("empty", "no_links", "error"))
    print(f"\nDone. processed={len(results)} with_media={ok} failed={empty}")
    print(f"Manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
