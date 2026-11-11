# zelda-watch

Alerte dès que la **manette Pro Switch 2 – The Legend of Zelda (40th Anniversary)** est de retour en stock dans une boutique suisse.

**Boutiques surveillées :** Galaxus, Digitec, Interdiscount, MediaMarkt, Brack, Fnac, Nintendo Store (CH), Mix-Image, Manor, World of Games, et Toppreise (comparateur, attrape les boutiques qui ne sont pas dans la liste). Ricardo est prévu mais désactivé (revendeurs).

## Comment il lit le stock

Pour chaque fiche, le script ouvre la page dans un vrai navigateur et croise trois sources, de la plus fiable à la moins fiable :

1. **Données structurées** que les shops publient pour Google : schema.org (JSON-LD), microdata, balises meta produit. Les offres annexes à moins de `MIN_PRICE` (garantie, livraison à 4.95…) sont ignorées.
2. **Le bouton panier** : actif ou désactivé.
3. **Le texte de la zone d'achat** (prix + bouton + dispo) en FR/DE/EN/IT, puis du reste de la fiche. Les « en stock » des produits similaires ou du pied de page ne comptent pas.

Chaque statut affiche ce qui l'a décidé, par exemple `schema.org OutOfStock · CHF 109.95` ou `zone d'achat « en stock »`. Avant toute alerte, la fiche est **relue une 2e fois** 15–25 s plus tard : une alerte n'est envoyée que si les deux lectures concordent. Il n'alerte qu'au **changement** (rupture → dispo), avec un rappel toutes les 30 min tant que c'est en stock.

| Statut | Signification | Alerte |
|---|---|---|
| ✅ EN STOCK | commandable | oui |
| 🟡 PRÉCOMMANDE | réservable | oui |
| ⚠️ À VÉRIFIER | sources contradictoires (ex. schema.org dit dispo, la page dit rupture) | oui |
| 💸 trop cher | dispo mais au-dessus de `MAX_PRICE` (130 CHF par défaut) | non |
| ❌ rupture / ❔ inconnu / · pas listée | rien à faire | non |
| 🚫 bloqué / 💥 erreur | le site refuse le robot (le log dit lequel : DataDome, Cloudflare, Akamai…) | après 6 échecs, au plus 1×/72 h par site, jamais pour Interdiscount et Fnac |
| ⏸ en pause | site bloqué plusieurs fois de suite, retenté moins souvent | non |

## Quand un site bloque (403)

- Le script réessaie avec **Chromium, puis Firefox, puis WebKit**, et retient pour chaque site le navigateur qui a marché.
- Il laisse quelques secondes au site pour terminer sa vérification automatique, puis relit la page.
- Il **garde les cookies** entre deux passages, comme un navigateur normal.
- Si un site bloque 3 fois de suite, il ne le retente plus qu'1 passage sur 4, pour ne pas se faire bannir plus fort.
- Après 6 échecs, tu reçois « ⚠️ Site non surveillé », au plus une fois tous les 3 jours par site. Rien pour les sites de `MANUAL_SITES` (Interdiscount, Fnac), déjà connus comme bloqués. Le retour à la normale n'est noté que dans le log.

Le script ne contourne pas les captchas. Un site qui refuse toutes les IP de datacenter (GitHub, serveurs cloud) ne passera jamais depuis là : la solution est de le surveiller depuis une connexion de particulier, voir « Répartir entre GitHub et ton Mac ».

---

## 1. Installer (Mac, Linux, Windows)

```bash
cd zelda-watch
python3 -m venv .venv && source .venv/bin/activate     # Windows : .venv\Scripts\activate
pip install playwright
python -m playwright install chromium firefox webkit
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

### Répartir entre GitHub et ton Mac

Interdiscount et Fnac sont protégés par DataDome, qui refuse les IP de serveurs comme GitHub. Depuis une connexion de particulier, à un rythme normal, ils passent beaucoup mieux.

1. Sur ton Mac, dans le dossier du repo : `./setup_mac.sh`
   - installe tout, fait un passage de test (tu vois tout de suite si Interdiscount et Fnac répondent), puis lance la surveillance en arrière-plan ;
   - le Mac ne surveille que ces deux sites (`./setup_mac.sh tout` pour toutes les boutiques) ;
   - il ne se met pas en veille tant qu'il est sur secteur.
2. Sur GitHub : Settings → Secrets and variables → Actions → onglet **Variables** → New repository variable : `SKIP_SITES` = `Interdiscount,Fnac`. GitHub arrête alors de les tenter.

Arrêter : `./setup_mac.sh stop`. Log : `tail -f zelda.log`.

### Sur un PC Windows

Même principe que le Mac, avec une tâche planifiée Windows.

1. Récupère le repo : `git clone https://github.com/brunopaiva15/zelda-watch.git`, ou sur GitHub **Code → Download ZIP** puis décompresse.
2. Dans le dossier, clic droit sur `setup_windows.ps1` → **Exécuter avec PowerShell**. Ou dans un terminal ouvert dans le dossier :
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1        # Interdiscount + Fnac
   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 tout   # toutes les boutiques
   ```
3. Si aucune notification n'est configurée, le Bloc-notes s'ouvre sur `.env` : remplis ntfy ou Telegram, enregistre, relance.

Le script installe Python et Playwright dans le dossier, fait un passage de test, puis crée la tâche `zelda-watch` qui tourne sans fenêtre et redémarre à chaque ouverture de session. Le PC ne se met pas en veille tant qu'elle tourne.

- Log en direct : `Get-Content .\zelda.log -Wait -Tail 30`
- Arrêter : `powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 stop`

### Sur un serveur Debian / Ubuntu

```bash
git clone https://github.com/brunopaiva15/zelda-watch.git && cd zelda-watch
cp .env.example .env && nano .env     # ntfy et/ou Telegram
./setup_linux.sh test                 # teste seulement Interdiscount + Fnac depuis ce serveur
./setup_linux.sh                      # service 24/7 pour Interdiscount + Fnac
./setup_linux.sh tout                 # … ou pour toutes les boutiques
```

Le service systemd `zelda-watch` redémarre tout seul (plantage, reboot). Log : `journalctl -u zelda-watch -f`. Arrêter : `./setup_linux.sh stop`.

Un serveur hébergé chez toi (IP de particulier) a plus de chances de passer DataDome qu'un VPS (IP de datacenter, bloquée comme GitHub).

### Filet de sécurité : l'alerte de prix Toppreise

Sur la fiche Toppreise de la manette, le bouton **« Ajouter une alerte de prix »** t'envoie un e-mail dès qu'une boutique référencée la propose sous le prix choisi (ex. 130 CHF). C'est officiel et ça ne dépend ni de GitHub ni de ton Mac.

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
