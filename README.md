# zelda-watch

Alerte dès que la **manette Pro Switch 2 – The Legend of Zelda (40th Anniversary)** est de retour en stock dans une boutique suisse.

**Boutiques surveillées :** Galaxus, Digitec, Interdiscount, MediaMarkt, Brack, Fnac, Nintendo Store (CH), Mix-Image, Manor, World of Games, et Toppreise (comparateur, attrape les boutiques qui ne sont pas dans la liste). Ricardo est prévu mais désactivé (revendeurs).

Pour chaque boutique, le script ouvre la page de recherche dans un vrai Chromium headless, repère la fiche de la manette, puis lit sa disponibilité (données schema.org en priorité, sinon le texte de la page en FR/DE/EN). Il n'alerte qu'au **changement** (rupture → dispo), avec un rappel toutes les 30 min tant que c'est en stock.

| Statut | Signification | Alerte |
|---|---|---|
| ✅ EN STOCK | commandable | oui |
| 🟡 PRÉCOMMANDE | réservable | oui |
| ⚠️ À VÉRIFIER | signaux contradictoires (ex. livraison KO mais retrait en magasin OK) | oui |
| ❌ rupture / · pas listée | rien à faire | non |
| 🚫 bloqué / 💥 erreur | le site refuse le robot | alerte « site non surveillé » après ~1 h |

---

## 1. Installer (Mac, Linux, Windows)

```bash
cd zelda-watch
python3 -m venv .venv && source .venv/bin/activate     # Windows : .venv\Scripts\activate
pip install playwright
python -m playwright install chromium
cp .env.example .env                                    # puis remplis-le
```

## 2. Choisir où recevoir les alertes (au moins une)

**ntfy — le plus rapide (2 min, gratuit, push sur le téléphone)**
1. Installe l'app **ntfy** (iOS / Android).
2. « Subscribe to topic » → invente un nom introuvable, ex. `bruno-zelda-7k2q9x`.
3. Dans `.env` : `NTFY_TOPIC=bruno-zelda-7k2q9x`

**Telegram**
1. Dans Telegram, écris à **@BotFather** → `/newbot` → récupère le token.
2. Envoie « salut » à ton nouveau bot.
3. Ouvre `https://api.telegram.org/bot<TOKEN>/getUpdates` → copie `"chat":{"id": ...}`.
4. Dans `.env` : `TELEGRAM_BOT_TOKEN=...` et `TELEGRAM_CHAT_ID=...`

**macOS** : notification + son automatiques si le script tourne sur un Mac.

> Pas de vrai SMS : ça passe par un service payant (Twilio…). ntfy/Telegram font sonner le téléphone pareil, gratuitement.

## 3. Tester

```bash
python zelda_watch.py --test
```

Envoie une notification de test, puis affiche l'état de chaque boutique. Si une boutique sort `🚫 bloqué` ou `· pas listée` alors que tu vois la manette chez elle à la main, colle l'URL de sa fiche dans `urls=[...]` de cette boutique (en haut du script, liste `SITES`).

## 4. Le laisser tourner

**Option A — sur ton Mac (le plus fiable)**
```bash
caffeinate -i python zelda_watch.py
```
Garde le Mac éveillé tant que le terminal est ouvert. Pour que ça démarre tout seul, voir le plist launchd plus bas.

**Option B — n'importe quelle machine Linux allumée en permanence** (Raspberry Pi, VPS, serveur Infomaniak…)
```bash
nohup python zelda_watch.py >> zelda.log 2>&1 &
```

**Option C — GitHub Actions (rien à laisser allumé)**
1. Crée un repo **public** (minutes illimitées ; en privé, 2000 min/mois ne suffisent pas) et pousse le dossier tel quel, `.github/workflows/zelda-watch.yml` compris.
2. Settings → Secrets and variables → Actions → ajoute `NTFY_TOPIC` et/ou `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`. Les secrets restent privés même si le repo est public.
3. Onglet Actions → « Run workflow » pour tester.

Limites : GitHub déclenche avec 5–20 min de retard, et les IP de datacenter se font plus souvent bloquer par MediaMarkt/Interdiscount. Bien pour dépanner, moins bien qu'un Mac ou un Pi.

### Démarrage auto sur Mac (launchd)

Crée `~/Library/LaunchAgents/ch.vergasta.zeldawatch.plist` (remplace les chemins) :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ch.vergasta.zeldawatch</string>
  <key>ProgramArguments</key><array>
    <string>/Users/TOI/zelda-watch/.venv/bin/python</string>
    <string>/Users/TOI/zelda-watch/zelda_watch.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/TOI/zelda-watch</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/TOI/zelda-watch/zelda.log</string>
  <key>StandardErrorPath</key><string>/Users/TOI/zelda-watch/zelda.log</string>
</dict></plist>
```
```bash
launchctl load ~/Library/LaunchAgents/ch.vergasta.zeldawatch.plist   # démarrer
launchctl unload ~/Library/LaunchAgents/ch.vergasta.zeldawatch.plist # arrêter
```

---

## Ajouter / corriger une boutique

Dans `SITES` en haut de `zelda_watch.py` :
```python
dict(name="MaBoutique", search="https://www.maboutique.ch/search?q=zelda%20pro%20controller"),
dict(name="AutreShop",  urls=["https://www.autreshop.ch/produit/manette-zelda-123"]),
```
Les fiches trouvées sont mémorisées dans `zelda_state.json` : même si la recherche d'un site casse plus tard, la fiche reste surveillée.

## Bon à savoir

- Le script ne fait que lire les pages, à un rythme humain (≈ 5 min, pauses aléatoires). Il n'achète rien.
- Les URL de recherche de Brack et Manor n'ont pas pu être vérifiées en direct : `--test` te dit tout de suite lesquelles répondent.
- Supprime `zelda_state.json` pour repartir de zéro.
