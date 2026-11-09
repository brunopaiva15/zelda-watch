#!/usr/bin/env python3
"""
zelda_watch.py
Surveille le stock de la manette « Nintendo Switch 2 Pro Controller – The Legend of Zelda
(40th Anniversary) » sur les boutiques suisses et t'alerte dès qu'elle est commandable.

Alertes : Telegram, ntfy (push sur le téléphone, gratuit), notification macOS.
Navigateur : Chromium headless via Playwright (les shops suisses chargent tout en JS).

Usage :
    python zelda_watch.py            # tourne en boucle (toutes les INTERVAL_MIN minutes)
    python zelda_watch.py --once     # un seul passage (cron, GitHub Actions…)
    python zelda_watch.py --test     # un passage détaillé + notification de test
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ─────────────────────────────── Config ───────────────────────────────

def load_dotenv(path: Path = HERE / ".env") -> None:
    """Charge un fichier .env (KEY=VALUE) sans dépendance externe."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()

STATE_FILE = Path(os.environ.get("STATE_FILE", HERE / "zelda_state.json"))
INTERVAL_MIN = float(os.environ.get("INTERVAL_MIN", "5"))     # délai entre deux passages
REMIND_MIN = float(os.environ.get("REMIND_MIN", "30"))        # rappel si toujours en stock (0 = off)
FAIL_ALERT_PASSES = int(os.environ.get("FAIL_ALERT_PASSES", "12"))  # alerte si un site est KO trop longtemps
MAX_LINKS_PER_SEARCH = 4

Q = urllib.parse.quote("zelda pro controller")

# Boutiques suisses. `urls` = fiches produit connues, `search` = page de recherche
# (le script y repère tout seul les fiches correspondantes et les mémorise).
# `aggregator=True` : comparateur, une seule offre en stock suffit pour alerter.
SITES = [
    dict(name="Galaxus",
         urls=["https://www.galaxus.ch/fr/s1/product/nintendo-manette-switch-2-pro-edition-the-legend-of-zelda-switch-2-manette-76622992"],
         search=f"https://www.galaxus.ch/fr/search?q={Q}"),
    dict(name="Digitec",
         urls=["https://www.digitec.ch/fr/s1/product/nintendo-manette-switch-2-pro-edition-the-legend-of-zelda-switch-2-manette-76622992"]),
    dict(name="Interdiscount",
         urls=["https://www.interdiscount.ch/fr/product/nintendo-switch-2-pro-the-legend-of-zelda-edition-manette-0014566961"],
         search=f"https://www.interdiscount.ch/fr/search?search={Q}"),
    dict(name="MediaMarkt",
         urls=["https://www.mediamarkt.ch/fr/product/_nintendo-pro-the-legend-of-zelda-edition-zum-40-jubilaum-aufsteller-controller-grungoldschwarz-fur-nintendo-switch-2-2295233.html"],
         search=f"https://www.mediamarkt.ch/fr/search.html?query={Q}"),
    dict(name="Brack",         search=f"https://www.brack.ch/search?query={Q}"),
    dict(name="Fnac",
         urls=["https://www.fr.fnac.ch/Manette-Nintendo-Pro-Controller-pour-Nintendo-Switch-2-Edition-40eme-anniversaire-The-Legend-of-Zelda/a21424358"],
         search=f"https://www.fr.fnac.ch/SearchResult/ResultList.aspx?Search={Q}"),
    dict(name="Nintendo Store",
         urls=["https://store.nintendo.com/fr-ch/manette-pro-nintendo-switch2-edition-40e-anniversaire-de-the-legend-of-zelda-support-dexposition-000000000010019437"]),
    dict(name="Mix-Image",
         urls=["https://mix-image.ch/produit/manette-pro-nintendo-switch-2-edition-40e-anniversaire-the-legend-of-zelda/"]),
    dict(name="Manor",         search=f"https://www.manor.ch/fr/search?text={Q}"),
    dict(name="World of Games",
         urls=["https://www.wog.ch/fr/index.cfm/details/product/256985%2DNintendo%2DSwitch%2D2%2DPro%2DController%2DThe%2DLegend%2Dof%2DZelda%2DOcarina%2Dof%2DTime"]),
    dict(name="Toppreise", aggregator=True,
         urls=["https://www.toppreise.ch/comparison-prix/Manettes-Nintendo-Switch-2/NINTENDO-Manette-Switch-2-Pro-edition-The-Legend-of-p879583"]),
    # Revente entre particuliers (souvent au-dessus du prix) — désactivé par défaut
    dict(name="Ricardo",       search=f"https://www.ricardo.ch/fr/s/{Q}", enabled=False),
]


# ─────────────────────────────── Détection ───────────────────────────────

def norm(s: str) -> str:
    """minuscules, sans accents, ponctuation → espaces."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[\-_/|–—:;,.'’()\[\]«»\"]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


EXCLUDE = ("skin", "coque", "hulle", "sticker", "etui", "housse", "ladestation", "charging",
           "tasche", "sacoche", "carrying", "schutz", "protection", "tears of the kingdom", "joy con")


def is_target(text: str) -> bool:
    """Vrai si le texte (titre + URL) désigne la manette Pro Switch 2 Zelda."""
    t = norm(text)
    return ("zelda" in t
            and ("controller" in t or "manette" in t)
            and re.search(r"switch ?2", t) is not None
            and not any(x in t for x in EXCLUDE))


NEG = [r"rupture de stock", r"\bepuise", r"\bindisponible", r"\b(pas|plus|non) (encore )?disponible",
       r"\b(pas|plus) en stock", r"ausverkauft",
       r"\b((momentan|derzeit|zurzeit|aktuell) )?nicht (mehr )?(an|auf) lager",
       r"\b((momentan|derzeit|zurzeit|aktuell) )?nicht (mehr )?(verfugbar|lieferbar|erhaltlich|bestellbar)",
       r"\b(momentan|derzeit|zurzeit|aktuell) nicht\b", r"\bout of stock", r"\bsold out",
       r"\bcurrently unavailable", r"\bno longer available", r"\bnot available"]
STRONG = [r"\ben stock\b", r"\b(an|auf) lager\b", r"\blieferbar\b", r"\bin stock\b",
          r"\blivre (demain|apres demain|entre|le |d ici|sous)", r"\bgeliefert (morgen|ubermorgen|zwischen|am |bis)",
          r"\bdelivered (tomorrow|between|on )", r"\babholbereit\b", r"\bversandbereit\b",
          r"\bpret pour (le )?retrait", r"\bdisponible immediatement", r"\bexpedie (sous|demain|aujourd)"]
PREORDER = [r"\bprecommand", r"\bvorbestell", r"\bpre ?order"]
CART = [r"ajouter au panier", r"in den warenkorb", r"add to (cart|basket)", r"acheter maintenant",
        r"jetzt kaufen", r"aggiungi al carrello"]
BLOCK = ["access denied", "just a moment", "attention required", "captcha", "are you a robot",
         "pardon our interruption", "request unsuccessful", "verify you are human", "zugriff verweigert"]

NEG_RE = re.compile("|".join(NEG))
ALERT = {"in", "preorder", "maybe"}
LABEL = {"in": "✅ EN STOCK", "preorder": "🟡 PRÉCOMMANDE", "maybe": "⚠️ À VÉRIFIER",
         "out": "❌ rupture", "unknown": "❔ inconnu", "absent": "· pas listée",
         "blocked": "🚫 bloqué", "error": "💥 erreur"}


def classify_text(text: str, aggregator: bool = False) -> str:
    t = norm(text)
    neg = NEG_RE.search(t) is not None
    cleaned = NEG_RE.sub(" ", t)   # « pas en stock » ne doit pas compter comme « en stock »
    strong = any(re.search(p, cleaned) for p in STRONG)
    pre = any(re.search(p, cleaned) for p in PREORDER)
    cart = any(re.search(p, cleaned) for p in CART)
    if aggregator:
        return "maybe" if strong else ("out" if neg else "unknown")
    if strong:
        return "maybe" if neg else "in"
    if neg:
        return "out"
    if pre:
        return "preorder"
    if cart:
        return "maybe"
    return "unknown"


LD_MAP = {"instock": "in", "limitedavailability": "in", "onlineonly": "in", "instoreonly": "in",
          "preorder": "preorder", "presale": "preorder", "backorder": "preorder",
          "outofstock": "out", "soldout": "out", "discontinued": "out"}


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def classify_jsonld(blocks: list[str]) -> str | None:
    """Lit schema.org Product → offers.availability (signal le plus fiable quand il existe)."""
    products = []
    for raw in blocks:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for d in _walk(data):
            typ = d.get("@type")
            typs = typ if isinstance(typ, list) else [typ]
            if "Product" in typs:
                products.append(d)
    if not products:
        return None
    matching = [p for p in products if is_target(str(p.get("name", "")))]
    pool = matching or (products if len(products) == 1 else [])
    found = set()
    for p in pool:
        for d in _walk(p.get("offers", [])):
            av = str(d.get("availability", "")).rsplit("/", 1)[-1].lower()
            if av in LD_MAP:
                found.add(LD_MAP[av])
    for s in ("in", "preorder", "out"):
        if s in found:
            return s
    return None


def combine(ld: str | None, txt: str) -> str:
    if ld is None:
        return txt
    if ld == "out":
        return "maybe" if txt == "in" else "out"
    if txt == "out":
        return "maybe"
    return "in" if txt == "in" else ld


# ─────────────────────────────── Navigateur ───────────────────────────────

JS_REGION = """() => {
  const h = document.querySelector('h1');
  let el = h;
  for (let i = 0; el && el.parentElement && i < 8; i++) {
    el = el.parentElement;
    if ((el.innerText || '').length > 2500) break;
  }
  const body = document.body ? (document.body.innerText || '') : '';
  return { title: document.title || '', h1: h ? h.innerText : '',
           region: ((el || document.body || {}).innerText || body).slice(0, 8000),
           body: body.slice(0, 20000) };
}"""

JS_LINKS = """() => Array.from(document.querySelectorAll('a[href]')).map(a => [
  a.href, (a.innerText || '') + ' ' + (a.getAttribute('title') || '') + ' ' + (a.getAttribute('aria-label') || '')
])"""


def make_browser(p):
    headless = os.environ.get("HEADLESS", "1") != "0"
    browser = p.chromium.launch(headless=headless,
                                args=["--disable-blink-features=AutomationControlled"])
    major = browser.version.split(".")[0]
    ua = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
    ctx = browser.new_context(locale="fr-CH", timezone_id="Europe/Zurich", user_agent=ua,
                              viewport={"width": 1366, "height": 900},
                              extra_http_headers={"Accept-Language": "fr-CH,fr;q=0.9,de-CH;q=0.8,en;q=0.7"})
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font")
              else r.continue_())
    return browser, ctx


def open_page(ctx, url: str):
    page = ctx.new_page()
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    return page, (resp.status if resp else 0)


def looks_blocked(code: int, title: str, body: str) -> bool:
    if code in (401, 403, 429, 503):
        return True
    t = (title + " " + body[:1500]).lower()
    return any(b in t for b in BLOCK)


def clean_url(u: str) -> str:
    p = urllib.parse.urlsplit(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith(("utm_", "gclid", "fbclid", "ref"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))


def discover(ctx, search_url: str) -> tuple[list[str], str]:
    """Ouvre la page de recherche et renvoie les liens vers la manette."""
    page, code = open_page(ctx, search_url)
    try:
        info = page.evaluate(JS_REGION)
        if looks_blocked(code, info["title"], info["body"]):
            return [], "blocked"
        if code >= 400:
            return [], f"http {code}"
        host = urllib.parse.urlsplit(search_url).netloc.replace("www.", "")
        out = []
        for href, text in page.evaluate(JS_LINKS):
            if host not in urllib.parse.urlsplit(href).netloc:
                continue
            if not is_target(text + " " + urllib.parse.unquote(href)):
                continue
            u = clean_url(href)
            if u != clean_url(search_url) and u not in out:
                out.append(u)
            if len(out) >= MAX_LINKS_PER_SEARCH:
                break
        return out, "ok"
    finally:
        page.close()


def check_product(ctx, url: str, aggregator: bool) -> tuple[str, str]:
    page, code = open_page(ctx, url)
    try:
        info = page.evaluate(JS_REGION)
        if looks_blocked(code, info["title"], info["body"]):
            return "blocked", f"HTTP {code}"
        if code == 404 or code == 410:
            return "absent", "fiche supprimée"
        if code >= 400:
            return "error", f"HTTP {code}"
        name = (info["h1"] or info["title"]).strip().replace("\n", " ")[:90]
        if aggregator:
            return classify_text(info["body"], aggregator=True), name
        ld = classify_jsonld(page.locator('script[type="application/ld+json"]').all_text_contents())
        return combine(ld, classify_text(info["region"])), name
    finally:
        page.close()


# ─────────────────────────────── Notifications ───────────────────────────────

def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=20).read()


def notify(title: str, message: str, url: str | None = None) -> None:
    log(f"🔔 {title} — {message} {url or ''}")
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            text = f"<b>{title}</b>\n{message}" + (f"\n{url}" if url else "")
            _post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode(),
                  {"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as e:
            log(f"   Telegram KO : {e}")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            payload = {"topic": topic, "title": title, "message": message, "priority": 5,
                       "tags": ["video_game"]}
            if url:
                payload["click"] = url
            _post(os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
                  json.dumps(payload).encode(), {"Content-Type": "application/json"})
        except Exception as e:
            log(f"   ntfy KO : {e}")
    if platform.system() == "Darwin" and os.environ.get("MACOS_NOTIF", "1") != "0":
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(message)}" with title "{esc(title)}" sound name "Glass"'],
                       capture_output=True)


# ─────────────────────────────── Boucle ───────────────────────────────

def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    for k in ("items", "discovered", "fails"):
        s.setdefault(k, {})
    s.setdefault("fail_alerted", [])
    return s


def save_state(s: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def run_pass(state: dict, verbose: bool = False) -> list[tuple]:
    from playwright.sync_api import sync_playwright

    rows = []
    with sync_playwright() as p:
        browser, ctx = make_browser(p)
        try:
            for site in SITES:
                if not site.get("enabled", True):
                    continue
                name, agg = site["name"], site.get("aggregator", False)
                site_ok = False

                if site.get("search"):
                    try:
                        found, st = discover(ctx, site["search"])
                        site_ok = st == "ok"
                        known = state["discovered"].setdefault(name, [])
                        for u in found:
                            if u not in known:
                                known.append(u)
                                log(f"{name} : nouvelle fiche repérée → {u}")
                        if verbose:
                            log(f"{name} : recherche {st}, {len(found)} fiche(s)")
                    except Exception as e:
                        log(f"{name} : recherche en erreur ({type(e).__name__}: {str(e)[:120]})")
                    time.sleep(random.uniform(1.5, 4))

                urls = list(dict.fromkeys(site.get("urls", []) + state["discovered"].get(name, [])))
                if not urls:
                    rows.append((name, "absent", "aucune fiche trouvée", site.get("search")))

                for url in urls:
                    try:
                        status, info = check_product(ctx, url, agg)
                    except Exception as e:
                        status, info = "error", f"{type(e).__name__}: {str(e)[:100]}"
                    if status not in ("error", "blocked"):
                        site_ok = True
                    rows.append((name, status, info, url))
                    handle_status(state, name, url, status, info)
                    time.sleep(random.uniform(1.5, 4))

                handle_health(state, name, site_ok)
        finally:
            browser.close()
    save_state(state)
    return rows


def handle_status(state: dict, site: str, url: str, status: str, info: str) -> None:
    now = time.time()
    prev = state["items"].get(url, {})
    item = {"site": site, "status": status, "info": info, "checked": now,
            "notified": prev.get("notified", 0)}
    newly = status in ALERT and prev.get("status") not in ALERT
    remind = (status in ("in", "preorder") and REMIND_MIN > 0
              and prev.get("status") in ALERT and now - prev.get("notified", 0) >= REMIND_MIN * 60)
    if newly or remind:
        title = {"in": "🎮 Manette Zelda EN STOCK",
                 "preorder": "🎮 Manette Zelda en PRÉCOMMANDE",
                 "maybe": "🎮 Manette Zelda peut-être dispo"}[status]
        notify(("🔁 " if remind else "") + title, f"{site} — {info}", url)
        item["notified"] = now
    elif prev.get("status") in ALERT and status not in ALERT:
        log(f"{site} : de nouveau {LABEL.get(status, status)}")
    state["items"][url] = item


def handle_health(state: dict, site: str, ok: bool) -> None:
    if ok:
        state["fails"][site] = 0
        if site in state["fail_alerted"]:
            state["fail_alerted"].remove(site)
            notify("✅ Surveillance rétablie", f"{site} répond à nouveau.")
        return
    n = state["fails"].get(site, 0) + 1
    state["fails"][site] = n
    if n >= FAIL_ALERT_PASSES and site not in state["fail_alerted"]:
        state["fail_alerted"].append(site)
        notify("⚠️ Site non surveillé", f"{site} bloque ou plante depuis {n} passages — vérifie-le à la main.")


def print_table(rows: list[tuple]) -> None:
    print()
    for site, status, info, url in rows:
        print(f"  {site:<15} {LABEL.get(status, status):<16} {info[:60]}")
        if url:
            print(f"  {'':<15} {url}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Alerte stock manette Pro Switch 2 Zelda (Suisse)")
    ap.add_argument("--once", action="store_true", help="un seul passage puis quitte")
    ap.add_argument("--test", action="store_true", help="passage détaillé + notification de test")
    ap.add_argument("--interval", type=float, default=INTERVAL_MIN, help="minutes entre deux passages")
    args = ap.parse_args()

    state = load_state()
    if args.test:
        notify("🧪 Test zelda_watch", "Si tu lis ceci, les notifications fonctionnent.")
        print_table(run_pass(state, verbose=True))
        return
    if args.once:
        rows = run_pass(state)
        log("Passage terminé : " + ", ".join(f"{r[0]}={r[1]}" for r in rows))
        return

    log(f"Surveillance lancée — {sum(s.get('enabled', True) for s in SITES)} sites, "
        f"toutes les ~{args.interval:g} min. Ctrl+C pour arrêter.")
    while True:
        try:
            rows = run_pass(state)
            summary = {}
            for r in rows:
                summary[r[1]] = summary.get(r[1], 0) + 1
            log("Passage OK : " + ", ".join(f"{LABEL.get(k, k)} ×{v}" for k, v in summary.items()))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"Passage en erreur : {type(e).__name__}: {e}")
        time.sleep(args.interval * 60 * random.uniform(0.8, 1.2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arrêt.")
        sys.exit(0)
