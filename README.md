# Sweden Deals

A static front end for Deal Hunter’s daily Swedish shop deals: NetOnNet, Elgiganten, Inet, POWER, Webhallen, Kjell & Company, and more.

**Live:** [https://fambeho75.github.io/sweden-deals/](https://fambeho75.github.io/sweden-deals/)

## Data

Deal Hunter writes the day’s feed as plain JSON, with product images, to a folder on the build machine. `scripts/sync_from_feed.py` copies that feed into this repository (`deals.json` and `images/`). A daily routine runs the script and publishes the update.

```bash
python3 scripts/sync_from_feed.py
# or: python3 scripts/sync_from_feed.py /path/to/feed
```

The script refuses to overwrite `deals.json` if the incoming feed is missing, invalid, or has an empty deals list.

## Prischeck (tidigare pris)

`scripts/pricecheck.py` jämför varje deals "tidigare pris" med butikens verkliga prishistorik på Prisjakt och lägger till ett `pricecheck`-objekt per deal i `deals.json`:

- `inflated` – röd etikett **Uppblåst ordinariepris**: det angivna priset är mer än 3 % över butikens lägsta pris de 30 dagarna före rean (prisinformationslagen). Kräver säker produktmatchning, full historik för hela fönstret och samma variant; annars blir det `suspect`.
- `raised_before_sale` – gul etikett **Höjt före rean**: lagligt, men butiken höjde priset mer än 30 dagar före rean från ungefär dagens pris (t.ex. "Var 349 kr i oktober · verklig rabatt ca 3 %").
- `ok` – stämmer; bara en liten länk "Prishistorik".
- `suspect` / `unknown` – diskret "Ej verifierad". Inga priser gissas.

Skriptet avslutar alltid med kod 0 så att publiceringen fungerar även om Prisjakt inte svarar (då blir allt `unknown`). Resultat cachas per deal och dag i `scripts/.pricecheck-cache.json`.

Kör det **efter** `sync_from_feed.py` (som skriver om `deals.json` från flödet):

```bash
python3 scripts/sync_from_feed.py /workspace/sweden-deals-feed
python3 scripts/pricecheck.py deals.json
```

Tester: `node tests/ui-test.js`

## Local preview

```bash
python3 -m http.server --directory . 8080
```

Then open [http://127.0.0.1:8080/](http://127.0.0.1:8080/).
