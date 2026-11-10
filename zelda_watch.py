#!/usr/bin/env python3
"""
zelda_watch.py
Surveille le stock de la manette « Nintendo Switch 2 Pro Controller – The Legend of Zelda
(40th Anniversary) » sur les boutiques suisses et t'alerte dès qu'elle est commandable.

Alertes : Telegram, ntfy (push sur le téléphone, gratuit), notification macOS.
Navigateurs : Chromium, puis Firefox, puis WebKit si un site refuse le précédent.

Détection du stock, par ordre de fiabilité :
  1. données structurées (schema.org JSON-LD, microdata, balises meta produit)
  2. état du bouton « ajouter au panier » (actif / désactivé)
  3. texte de la fiche (FR / DE / EN / IT)
Toute alerte est confirmée par une 2e lecture avant d'être envoyée.

Usage :
    python zelda_watch.py            # tourne en boucle (toutes les INTERVAL_MIN minutes)
    python zelda_watch.py --once     # un seul passage (cron, GitHub Actions…)
    python zelda_watch.py --test     # un passage + notification de test
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
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip().strip('"').strip("'"))


load_dotenv()


def _names(var: str) -> set[str]:
    return {x.strip().lower() for x in os.environ.get(var, "").split(",") if x.strip()}


STATE_FILE = Path(os.environ.get("STATE_FILE", HERE / "zelda_state.json"))
COOKIE_DIR = Path(os.environ.get("COOKIE_DIR", HERE))         # cookies conservés entre passages
INTERVAL_MIN = float(os.environ.get("INTERVAL_MIN", "5"))     # délai entre deux passages
REMIND_MIN = float(os.environ.get("REMIND_MIN", "30"))        # rappel si toujours en stock (0 = off)
MAX_PRICE = float(os.environ.get("MAX_PRICE", "130"))         # ignore les offres plus chères (revendeurs)
MIN_PRICE = float(os.environ.get("MIN_PRICE", "50"))          # offres moins chères = services/accessoires
FAIL_ALERT_PASSES = int(os.environ.get("FAIL_ALERT_PASSES", "6"))  # alerte si un site est KO trop longtemps
BACKOFF_AFTER = int(os.environ.get("BACKOFF_AFTER", "3"))     # site bloqué N fois de suite → on ralentit
BACKOFF_EVERY = int(os.environ.get("BACKOFF_EVERY", "4"))     # … et on ne le tente plus qu'1 passage sur N
CONFIRM = os.environ.get("CONFIRM", "1") != "0"               # 2e lecture avant toute alerte
ENGINES = [e.strip() for e in os.environ.get("ENGINES", "chromium,firefox,webkit").split(",") if e.strip()]
ONLY_SITES, SKIP_SITES = _names("ONLY_SITES"), _names("SKIP_SITES")
MAX_LINKS_PER_SEARCH = 4

Q = urllib.parse.quote("zelda pro controller")

# Boutiques suisses. `urls` = fiches produit connues, `search` = page de recherche
# (le script y repère tout seul les fiches correspondantes et les mémorise).
# `aggregator=True` : comparateur de prix, alerte si une offre passe sous MAX_PRICE.
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


def site_active(site: dict) -> bool:
    n = site["name"].lower()
    if not site.get("enabled", True) or n in SKIP_SITES:
        return False
    return not ONLY_SITES or n in ONLY_SITES


# ─────────────────────────────── Détection : texte ───────────────────────────────

def norm(s: str) -> str:
    """minuscules, sans accents, ponctuation → espaces."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[\-_/|–—:;,.'’()\[\]«»\"!?]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


EXCLUDE = ("skin", "coque", "hulle", "sticker", "etui", "housse", "ladestation", "charging",
           "tasche", "sacoche", "carrying", "schutz", "protection", "tears of the kingdom", "joy con")


def is_target(text: str) -> bool:
    """Vrai si le texte (titre + URL) désigne la manette Pro Switch 2 Zelda."""
    t = norm(text)
    return ("zelda" in t
            and re.search(r"controller|manette|controleur|kontroller", t) is not None
            and re.search(r"switch ?2", t) is not None
            and not any(x in t for x in EXCLUDE))


NEG = [r"rupture de stock", r"\bepuise", r"\bindisponible", r"\b(pas|plus|non) (encore )?disponible",
       r"\b(pas|plus) en stock", r"\bstock epuise", r"\bproduit (epuise|indisponible)",
       r"\bm avertir (de|du|quand|lorsque)", r"\bme prevenir (de|du|quand|lorsque)",
       r"\bdisponible prochainement", r"\bbientot (de retour|disponible)", r"\bplus commercialise",
       r"ausverkauft", r"\bvergriffen",
       r"\b((momentan|derzeit|zurzeit|aktuell) )?nicht (mehr )?(an|auf) lager",
       r"\b((momentan|derzeit|zurzeit|aktuell) )?nicht (mehr )?(verfugbar|lieferbar|erhaltlich|bestellbar)",
       r"\b(momentan|derzeit|zurzeit|aktuell) nicht\b", r"\bkein liefertermin", r"\bliefertermin unbekannt",
       r"\bbenachrichtigen,? (sobald|wenn)",
       r"\bout of stock", r"\bsold out", r"\bcurrently unavailable", r"\bno longer available",
       r"\bnot available", r"\bnotify me when", r"\bnon disponibile", r"\besaurito"]
STRONG = [r"\ben stock\b", r"\b(an|auf) lager\b", r"\blieferbar\b", r"\bin stock\b",
          r"\b\d+ (pieces?|stuck|articles?) (en stock|an lager|disponibles?)",
          r"\blivre (demain|apres demain|entre|le |d ici|sous|des)", r"\blivraison (demain|des demain|en \d)",
          r"\bgeliefert (morgen|ubermorgen|zwischen|am |bis)", r"\blieferung (morgen|in \d)",
          r"\bdelivered (tomorrow|between|on )", r"\babholbereit\b", r"\bversandbereit\b",
          r"\bpret pour (le )?retrait", r"\bdisponible immediatement", r"\bexpedie (sous|demain|aujourd)",
          r"\bsofort verfugbar", r"\bdisponibile\b"]
PREORDER = [r"\bprecommand", r"\bvorbestell", r"\bpre ?order", r"\bprenotazion"]
CART_RE = re.compile(r"ajouter au panier|dans le panier|in den warenkorb|add to (cart|basket)|"
                     r"acheter maintenant|acheter|jetzt kaufen|kaufen|aggiungi al carrello|"
                     r"precommander|vorbestellen|pre ?order", re.I)

NEG_RE = re.compile("|".join(NEG))
ALERT = {"in", "preorder", "maybe"}
LABEL = {"in": "✅ EN STOCK", "preorder": "🟡 PRÉCOMMANDE", "maybe": "⚠️ À VÉRIFIER",
         "out": "❌ rupture", "pricey": "💸 trop cher", "unknown": "❔ inconnu",
         "absent": "· pas listée", "blocked": "🚫 bloqué", "error": "💥 erreur",
         "paused": "⏸ en pause"}


def _first(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return None


def classify_text(text: str) -> tuple[str, str]:
    """→ (statut, raison). La raison cite le mot qui a décidé, pour déboguer."""
    t = norm(text)
    neg = _first(NEG, t)
    cleaned = NEG_RE.sub(" ", t)   # « pas en stock » ne doit pas compter comme « en stock »
    strong = _first(STRONG, cleaned)
    pre = _first(PREORDER, cleaned)
    if strong and neg:
        return "maybe", f"texte « {strong} » mais aussi « {neg} »"
    if strong:
        return "in", f"texte « {strong} »"
    if neg:
        return "out", f"texte « {neg} »"
    if pre:
        return "preorder", f"texte « {pre} »"
    return "unknown", "texte sans indice de stock"


# ─────────────────────────────── Détection : données structurées ───────────────────────────────

LD_MAP = {"instock": "in", "limitedavailability": "in", "onlineonly": "in", "instoreonly": "in",
          "preorder": "preorder", "presale": "preorder", "backorder": "preorder",
          "outofstock": "out", "soldout": "out", "discontinued": "out",
          # balises meta produit (Open Graph / Facebook)
          "in stock": "in", "available for order": "in",
          "out of stock": "out", "oos": "out", "pending": "preorder"}


def map_availability(v) -> tuple[str | None, str]:
    raw = str(v or "").strip()
    key = raw.rsplit("/", 1)[-1].lower().replace("_", " ").strip()
    key2 = key.replace(" ", "")
    return (LD_MAP.get(key) or LD_MAP.get(key2)), raw.rsplit("/", 1)[-1]


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_price(v) -> float | None:
    try:
        s = str(v).replace("'", "").replace("’", "").replace(" ", "").replace(" ", "")
        s = re.sub(r"[^\d.,]", "", s).replace(",", ".")
        if s.count(".") > 1:
            head, tail = s.rsplit(".", 1)
            s = head.replace(".", "") + "." + tail
        return float(s) if s else None
    except Exception:
        return None


def pick_offer(offers: list[tuple]) -> tuple[str | None, str, float | None]:
    """offers = [(statut, brut, prix)]. Ignore les offres annexes (garantie, livraison à 4.95…)."""
    if not offers:
        return None, "", None
    plausible = [o for o in offers if o[2] is None or o[2] >= MIN_PRICE]
    offers = plausible or offers
    prices = [o[2] for o in offers if o[2]]
    price = min(prices) if prices else None
    for wanted in ("in", "preorder", "out"):
        for st, raw, pr in offers:
            if st == wanted:
                return st, raw, (pr or price)
    return None, "", price


def classify_jsonld(blocks: list[str]) -> tuple[str | None, str, float | None]:
    """schema.org Product (JSON-LD) → (statut, valeur brute, prix)."""
    products = []
    for raw in blocks:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for d in _walk(data):
            typ = d.get("@type")
            typs = typ if isinstance(typ, list) else [typ]
            if "Product" in typs or "ProductGroup" in typs:
                products.append(d)
    if not products:
        return None, "", None
    matching = [p for p in products if is_target(str(p.get("name", "")))]
    pool = matching or (products if len(products) == 1 else [])
    offers = []
    for p in pool:
        for d in _walk(p.get("offers", [])):
            st, raw = map_availability(d.get("availability"))
            price = None
            for k in ("price", "lowPrice"):
                if d.get(k) not in (None, ""):
                    price = parse_price(d.get(k))
                    break
            if st or price:
                offers.append((st, raw, price))
    return pick_offer(offers)


def classify_meta(signals: dict) -> tuple[str | None, str, float | None]:
    """microdata itemprop=availability + meta product:availability."""
    offers = []
    for v in signals.get("availability", []):
        st, raw = map_availability(v)
        if st:
            offers.append((st, raw, None))
    price = None
    for v in signals.get("prices", []):
        pr = parse_price(v)
        if pr and pr >= MIN_PRICE:
            price = pr if price is None else min(price, pr)
    st, raw, _ = pick_offer(offers)
    return st, raw, price


def classify_buttons(buttons: list[dict]) -> tuple[str | None, str]:
    """Boutons panier : actif → achetable, tous désactivés → non achetable."""
    carts = [b for b in buttons if CART_RE.search(b.get("text", "") or "")]
    if not carts:
        return None, ""
    enabled = [b for b in carts if not b.get("disabled")]
    if enabled:
        label = (enabled[0]["text"] or "").strip()[:30]
        return ("preorder" if re.search(r"pr[ée] ?command|vorbestell|pre ?order", label, re.I)
                else "in"), f"bouton « {label} » actif"
    return "out", f"bouton « {(carts[0]['text'] or '').strip()[:30]} » désactivé"


def decide(struct: tuple, button: tuple, text: tuple) -> tuple[str, str]:
    """Combine les trois sources. struct=(statut, brut, prix, source) button=(statut, raison) text=(statut, raison)."""
    s, s_raw, _, s_src = struct
    b, b_why = button
    t, t_why = text
    if s:
        tag = f"{s_src} {s_raw}"
        if s == "out":
            if t == "in" and b in ("in", "preorder"):
                return "maybe", f"{tag} mais {t_why} et {b_why}"
            return "out", tag
        # struct dit dispo / précommande
        if t == "out" and b in (None, "out"):
            return "maybe", f"{tag} mais {t_why}"
        if b == "out" and t != "in":
            return "maybe", f"{tag} mais {b_why}"
        return ("in" if t == "in" and s == "preorder" else s), tag + (f" + {t_why}" if t == "in" else "")
    # Pas de données structurées : texte + bouton
    if t == "in":
        return ("maybe", f"{t_why} mais {b_why}") if b == "out" else ("in", t_why)
    if t == "out":
        return "out", t_why
    if t == "maybe":
        return "maybe", t_why
    if t == "preorder":
        return ("out", f"{t_why} mais {b_why}") if b == "out" else ("preorder", t_why)
    if b == "out":
        return "out", b_why
    if b == "preorder":
        return "preorder", b_why
    if b:   # bouton actif seul : trop faible pour alerter (certains shops laissent commander sans stock)
        return "unknown", b_why + ", aucun indice de stock"
    return "unknown", "aucun indice de stock"


def text_signal(info: dict) -> tuple[str, str]:
    """Texte de la zone d'achat (prix + bouton) d'abord : évite les « en stock » des produits similaires."""
    box = classify_text(info.get("buybox", "")) if info.get("buybox") else ("unknown", "")
    if box[0] != "unknown":
        return box[0], box[1].replace("texte", "zone d'achat", 1)
    return classify_text(info.get("region", ""))


PRICE_RE = re.compile(r"(?:à partir de|a partir de|ab|from|dès|des)\s*CHF\s*([\d'’]+(?:[.,]\d{1,2}|[.,]-)?)", re.I)
# Tout ce qui suit ces titres concerne d'AUTRES produits. (« Variantes de produit » et « Autres produits »
# ne servent pas de coupure : ce sont aussi des onglets placés AVANT la liste des offres.)
OTHER_PRODUCTS_RE = re.compile(r"produits alternatifs|d.autres internautes|alternative produkte|"
                               r"andere nutzer interessieren|alternative products", re.I)
NO_OFFER_RE = re.compile(r"aucune offre|\b0 offres?\b|keine angebote|\b0 angebote?\b|no offers|\b0 offers?\b")


def toppreise_status(title: str, body: str) -> tuple[str, str]:
    """Toppreise : on ne regarde que le produit lui-même, jamais les « produits alternatifs ».
    Le titre de l'onglet contient « à partir de CHF X » quand il y a des offres."""
    m = PRICE_RE.search(title)
    if not m:
        cut = OTHER_PRODUCTS_RE.search(body)
        own = body[:cut.start()] if cut else body[:3000]
        if NO_OFFER_RE.search(norm(own)):
            return "out", "aucune offre"
        m = PRICE_RE.search(own)
        if not m:
            return "unknown", "prix introuvable"
    price = parse_price(m.group(1).rstrip("-"))
    if price is None:
        return "unknown", "prix illisible"
    if price < MIN_PRICE:
        return "unknown", f"prix CHF {price:.2f} suspect (< {MIN_PRICE:.0f}), ignoré"
    if price <= MAX_PRICE:
        return "in", f"offre dès CHF {price:.2f} (≤ {MAX_PRICE:.0f})"
    return "pricey", f"seule offre dès CHF {price:.2f} (> {MAX_PRICE:.0f})"


# ─────────────────────────────── Blocages ───────────────────────────────

BLOCK_TEXT = ["access denied", "just a moment", "attention required", "captcha", "are you a robot",
              "pardon our interruption", "request unsuccessful", "verify you are human",
              "zugriff verweigert", "please enable js", "enable javascript and cookies",
              "checking your browser", "unusual traffic", "bot detection", "403 forbidden",
              "forbidden", "request blocked", "you have been blocked"]


def block_vendor(headers: dict, html_head: str) -> str:
    """Devine l'anti-bot en place, pour un message clair dans le log."""
    h = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    blob = " ".join(f"{k}:{v}" for k, v in h.items()) + " " + (html_head or "").lower()
    if "datadome" in blob or "captcha-delivery" in blob:
        return "DataDome"
    if "cf-ray" in h or "cloudflare" in h.get("server", "") or "challenges.cloudflare" in blob:
        return "Cloudflare"
    if "akamai" in blob or "_abck" in blob or "bm_sz" in blob or "edgesuite" in blob:
        return "Akamai"
    if "perimeterx" in blob or "_px" in h.get("set-cookie", "") or "px-captcha" in blob:
        return "PerimeterX"
    if "incapsula" in blob or "imperva" in blob or "x-iinfo" in h:
        return "Imperva"
    if "awselb" in blob or "x-amzn-waf" in blob:
        return "AWS WAF"
    return "anti-bot"


def looks_blocked(code: int, title: str, body: str) -> bool:
    """code=0 : relecture après attente, on ne juge plus que le contenu."""
    if code in (401, 403, 429):
        return True
    if code == 0 and len(body.strip()) < 500:
        return True
    t = (title + " " + body[:1500]).lower()
    if code == 503 and len(body) < 3000:
        return True
    return len(body) < 4000 and any(b in t for b in BLOCK_TEXT)


# ─────────────────────────────── Navigateurs ───────────────────────────────

JS_SIGNALS = """() => {
  const h = document.querySelector('h1');
  let el = h;
  for (let i = 0; el && el.parentElement && i < 8; i++) {
    el = el.parentElement;
    if ((el.innerText || '').length > 2500) break;
  }
  const body = document.body ? (document.body.innerText || '') : '';
  const region = el || document.body;
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const av = [], prices = [];
  document.querySelectorAll('[itemprop="availability"]').forEach(e =>
      av.push(e.getAttribute('href') || e.getAttribute('content') || e.textContent || ''));
  document.querySelectorAll('meta[property="product:availability"], meta[property="og:availability"], meta[name="availability"]')
      .forEach(e => av.push(e.getAttribute('content') || ''));
  document.querySelectorAll('[itemprop="price"], meta[property="product:price:amount"], meta[property="og:price:amount"]')
      .forEach(e => prices.push(e.getAttribute('content') || e.textContent || ''));
  // Zone d'achat : bloc autour du bouton panier situé après le titre (sinon autour du titre)
  const actRe = /ajouter au panier|dans le panier|in den warenkorb|add to (cart|basket)|pr[ée] ?command|vorbestell|acheter|jetzt kaufen|aggiungi al carrello|commander|bestellen/i;
  let anchor = null;
  for (const b of document.querySelectorAll('button, a[role="button"], input[type="submit"], a[class*="cart" i], a[class*="basket" i]')) {
    const tx = ((b.innerText || b.value || b.getAttribute('aria-label') || '') + '').trim();
    if (!tx || tx.length > 60 || !actRe.test(tx) || !vis(b)) continue;
    if (h && (h.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_PRECEDING)) continue;  // bouton avant le titre = en-tête du site
    anchor = b; break;
  }
  let box = anchor || h;
  for (let i = 0; box && box.parentElement && i < 12 && (box.innerText || '').length < 350; i++) box = box.parentElement;
  const buybox = box ? (box.innerText || '').slice(0, 3000) : '';
  const buttons = [];
  (region || document).querySelectorAll('button, a[role="button"], input[type="submit"], [data-test*="cart" i], [data-testid*="cart" i]')
    .forEach(b => {
      if (!vis(b) || buttons.length > 40) return;
      const text = ((b.innerText || b.value || b.getAttribute('aria-label') || b.getAttribute('title') || '') + '').trim();
      if (!text || text.length > 60) return;
      const disabled = b.disabled || b.getAttribute('aria-disabled') === 'true' ||
                       /disabled|inactive|unavailable|soldout|sold-out/i.test(b.className || '');
      buttons.push({ text, disabled });
    });
  return { title: document.title || '', h1: h ? h.innerText : '',
           region: ((region || {}).innerText || body).slice(0, 8000), buybox,
           body: body.slice(0, 20000), head: document.documentElement.outerHTML.slice(0, 4000),
           availability: av.slice(0, 10), prices: prices.slice(0, 10), buttons };
}"""

JS_LINKS = """() => Array.from(document.querySelectorAll('a[href]')).map(a => [
  a.href, (a.innerText || '') + ' ' + (a.getAttribute('title') || '') + ' ' + (a.getAttribute('aria-label') || '')
])"""


class Engines:
    """Lance les navigateurs à la demande, un contexte par moteur, cookies conservés sur disque."""

    def __init__(self, p):
        self.p, self.ctx, self.browsers, self.dead = p, {}, [], set()

    def cookie_file(self, name: str) -> Path:
        return COOKIE_DIR / f"zelda_cookies_{name}.json"

    def get(self, name: str):
        if name in self.ctx or name in self.dead:
            return self.ctx.get(name)
        headless = os.environ.get("HEADLESS", "1") != "0"
        common = dict(locale="fr-CH", timezone_id="Europe/Zurich",
                      viewport={"width": 1366, "height": 900},
                      extra_http_headers={"Accept-Language": "fr-CH,fr;q=0.9,de-CH;q=0.8,en;q=0.7"})
        cf = self.cookie_file(name)
        if cf.exists():
            common["storage_state"] = str(cf)
        try:
            if name == "chromium":
                args = ["--disable-blink-features=AutomationControlled"]
                try:   # « vrai » Chromium en headless (plus proche d'un Chrome normal que headless-shell)
                    browser = self.p.chromium.launch(channel="chromium", headless=headless, args=args)
                except Exception:
                    browser = self.p.chromium.launch(headless=headless, args=args)
                major = browser.version.split(".")[0]
                ua = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
                ctx = self._new_context(browser, cf, user_agent=ua, **common)
                ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            elif name == "firefox":
                browser = self.p.firefox.launch(headless=headless)
                ctx = self._new_context(browser, cf, **common)
            elif name == "webkit":
                browser = self.p.webkit.launch(headless=headless)
                ctx = self._new_context(browser, cf, **common)
            else:
                raise ValueError(name)
        except Exception as e:
            log(f"Moteur {name} indisponible ({short(e)})")
            self.dead.add(name)
            return None
        ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font")
                  else r.continue_())
        self.browsers.append(browser)
        self.ctx[name] = ctx
        return ctx

    @staticmethod
    def _new_context(browser, cookie_file: Path, **kw):
        try:
            return browser.new_context(**kw)
        except Exception:
            kw.pop("storage_state", None)          # fichier de cookies corrompu → on repart à zéro
            cookie_file.unlink(missing_ok=True)
            return browser.new_context(**kw)

    def close(self):
        for name, ctx in self.ctx.items():
            try:
                ctx.storage_state(path=str(self.cookie_file(name)))
            except Exception:
                pass
        for b in self.browsers:
            try:
                b.close()
            except Exception:
                pass


def short(e: Exception) -> str:
    msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}: {msg[:110]}"


class Blocked(Exception):
    pass


def load(ctx, url: str) -> tuple:
    """Charge une page. Laisse au site le temps de finir sa vérification JS automatique s'il en fait une.
    Renvoie (page, code, infos) ou lève Blocked."""
    page = ctx.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        code = resp.status if resp else 0
        headers = {}
        try:
            headers = resp.all_headers() if resp else {}
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        info = page.evaluate(JS_SIGNALS)
        if looks_blocked(code, info["title"], info["body"]):
            # Certaines protections laissent passer un vrai navigateur après quelques secondes
            page.wait_for_timeout(8000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            info = page.evaluate(JS_SIGNALS)
            if looks_blocked(0, info["title"], info["body"]):
                raise Blocked(f"{block_vendor(headers, info['head'])} · HTTP {code}")
        # Fiche dont le stock se charge après coup : on relit une fois si rien n'est trouvé
        if not info["availability"] and not NEG_RE.search(norm(info["region"])) \
                and not re.search("|".join(STRONG), norm(info["region"])):
            page.wait_for_timeout(3000)
            info = page.evaluate(JS_SIGNALS)
        return page, code, info
    except Exception:
        page.close()
        raise


def clean_url(u: str) -> str:
    p = urllib.parse.urlsplit(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith(("utm_", "gclid", "fbclid", "ref"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))


def discover(ctx, search_url: str) -> tuple[str, str, list[str]]:
    """Page de recherche → (statut, détail, liens vers la manette)."""
    try:
        page, code, _ = load(ctx, search_url)
    except Blocked as b:
        return "blocked", str(b), []
    try:
        if code >= 400:
            return "error", f"HTTP {code}", []
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
        return "ok", f"{len(out)} fiche(s)", out
    finally:
        page.close()


def check_product(ctx, url: str, aggregator: bool) -> tuple[str, str]:
    """Fiche produit → (statut, détail)."""
    try:
        page, code, info = load(ctx, url)
    except Blocked as b:
        return "blocked", str(b)
    try:
        if code in (404, 410):
            return "absent", "fiche supprimée"
        if code >= 400:
            return "error", f"HTTP {code}"
        if aggregator:
            return toppreise_status(info["title"], info["body"])

        ld = classify_jsonld(page.locator('script[type="application/ld+json"]').all_text_contents())
        meta = classify_meta(info)
        struct = (*ld, "schema.org") if ld[0] else (*meta, "microdata") if meta[0] else (None, "", None, "")
        price = ld[2] or meta[2]
        status, why = decide(struct, classify_buttons(info["buttons"]), text_signal(info))
        if price:
            why += f" · CHF {price:.2f}"
            if status in ALERT and price > MAX_PRICE:
                return "pricey", why
        return status, why
    finally:
        page.close()


def with_fallback(engines: Engines, fn, url: str, *args, prefer: str | None = None):
    """Essaie chaque moteur (celui qui a marché la dernière fois en premier)."""
    order = ([prefer] if prefer in ENGINES else []) + [e for e in ENGINES if e != prefer]
    last = None
    for name in order:
        ctx = engines.get(name)
        if ctx is None:
            continue
        try:
            res = fn(ctx, url, *args)
        except Exception as e:
            res = ("error", short(e)) + (([],) if fn is discover else ())
        tagged = res[:1] + (f"[{name}] {res[1]}",) + res[2:]
        if res[0] not in ("blocked", "error"):
            return tagged, name
        last = tagged
        time.sleep(random.uniform(2, 5))
    return (last or (("error", "aucun moteur", []) if fn is discover else ("error", "aucun moteur"))), None


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
    for k in ("items", "discovered", "fails", "engine"):
        s.setdefault(k, {})
    s.setdefault("fail_alerted", [])
    s.setdefault("passes", 0)
    # Oublie les fiches découvertes par une recherche qui n'existe plus dans la config
    with_search = {x["name"] for x in SITES if x.get("search")}
    for name in list(s["discovered"]):
        if name not in with_search:
            del s["discovered"][name]
    return s


def save_state(s: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def run_pass(state: dict) -> list[tuple]:
    from playwright.sync_api import sync_playwright

    state["passes"] += 1
    rows = []
    with sync_playwright() as p:
        engines = Engines(p)
        try:
            for site in SITES:
                if not site_active(site):
                    continue
                name, agg = site["name"], site.get("aggregator", False)
                host = urllib.parse.urlsplit((site.get("urls") or [site.get("search")])[0]).netloc

                # Site qui bloque en continu : on le laisse respirer (évite de se faire bannir plus fort)
                fails = state["fails"].get(name, 0)
                if fails >= BACKOFF_AFTER and state["passes"] % BACKOFF_EVERY:
                    rows.append((name, "paused", f"bloqué {fails}× de suite, retenté 1 passage sur {BACKOFF_EVERY}", ""))
                    continue

                site_ok = False
                prefer = state["engine"].get(host)

                if site.get("search"):
                    (st, detail, found), used = with_fallback(engines, discover, site["search"], prefer=prefer)
                    if used:
                        site_ok, prefer = True, used
                        state["engine"][host] = used
                    else:
                        log(f"{name} : recherche {LABEL.get(st, st)} {detail}")
                    known = state["discovered"].setdefault(name, [])
                    for u in found:
                        if u not in known and u not in site.get("urls", []):
                            known.append(u)
                            log(f"{name} : nouvelle fiche repérée → {u}")
                    time.sleep(random.uniform(1.5, 4))

                urls = list(dict.fromkeys(site.get("urls", []) + state["discovered"].get(name, [])))
                if not urls:
                    rows.append((name, "absent", "aucune fiche trouvée", ""))

                for url in urls:
                    (status, info), used = with_fallback(engines, check_product, url, agg, prefer=prefer)
                    if used:
                        site_ok, prefer = True, used
                        state["engine"][host] = used
                    prev = state["items"].get(url, {}).get("status")
                    # Confirmation : une alerte nouvelle doit tenir sur une 2e lecture
                    if CONFIRM and status in ALERT and prev not in ALERT and used:
                        time.sleep(random.uniform(15, 25))
                        (status2, info2), _ = with_fallback(engines, check_product, url, agg, prefer=used)
                        if status2 not in ALERT:
                            log(f"{name} : alerte non confirmée ({info} → {info2})")
                            status, info = status2, info2
                        else:
                            status, info = status2, info2
                    rows.append((name, status, info, url))
                    handle_status(state, name, url, status, info)
                    time.sleep(random.uniform(1.5, 4))

                handle_health(state, name, site_ok)
        finally:
            engines.close()
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
        notify("⚠️ Site non surveillé",
               f"{site} bloque le robot depuis {n} tentatives. Lance le script depuis ton Mac "
               f"(ONLY_SITES={site}) ou vérifie à la main.")


def print_table(rows: list[tuple]) -> None:
    print(flush=True)
    for site, status, info, url in rows:
        print(f"  {site:<15} {LABEL.get(status, status):<16} {info[:120]}")
        if url:
            print(f"  {'':<15} {url}")
    print(flush=True)


def keep_awake() -> None:
    """Windows : empêche la mise en veille automatique tant que le script tourne (KEEP_AWAKE=0 pour désactiver).
    Sur Mac, c'est caffeinate (setup_mac.sh) qui s'en charge."""
    if platform.system() != "Windows" or os.environ.get("KEEP_AWAKE", "1") == "0":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception as e:
        log(f"Impossible d'empêcher la veille ({short(e)})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Alerte stock manette Pro Switch 2 Zelda (Suisse)")
    ap.add_argument("--once", action="store_true", help="un seul passage puis quitte")
    ap.add_argument("--test", action="store_true", help="passage + notification de test")
    ap.add_argument("--interval", type=float, default=INTERVAL_MIN, help="minutes entre deux passages")
    ap.add_argument("--log", metavar="FICHIER", help="écrit la sortie dans ce fichier (tâche Windows en arrière-plan)")
    args = ap.parse_args()

    if args.log:   # pythonw.exe n'a pas de console : tout part dans le fichier, en UTF-8 (emojis compris)
        f = open(args.log, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f

    state = load_state()
    if args.test:
        notify("🧪 Test zelda_watch", "Si tu lis ceci, les notifications fonctionnent.")
    if args.test or args.once:
        print_table(run_pass(state))
        return

    active = [s["name"] for s in SITES if site_active(s)]
    log(f"Surveillance lancée — {len(active)} sites ({', '.join(active)}), "
        f"toutes les ~{args.interval:g} min. Ctrl+C pour arrêter.")
    keep_awake()
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
            log(f"Passage en erreur : {short(e)}")
        time.sleep(args.interval * 60 * random.uniform(0.8, 1.2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arrêt.")
        sys.exit(0)
