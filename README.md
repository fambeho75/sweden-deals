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

## Local preview

```bash
python3 -m http.server --directory . 8080
```

Then open [http://127.0.0.1:8080/](http://127.0.0.1:8080/).
