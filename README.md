# Quraani: automatic Quran recitation shorts

Every day this repo makes 9:16 videos (up to 45 s) and schedules them on TikTok (2 a day) and Instagram Reels (1 a day) through Buffer's free plan. Optional: connect YouTube as Buffer's third free channel and Shorts are posted too.

Each video is:
- a popular passage (`catalog/passages.json`, 109 passages) recited by a well-known reciter (`catalog/reciters.json`, 28 reciters). Clips always start and end on whole ayahs.
- the Arabic text (Uthmani script, Amiri Quran font) shown phrase by phrase in sync with the reciter (word timings from quran.com, ayah timings from mp3quran.net).
- real nature footage from a screened library of free stock clips (`catalog/footage.json`, Pexels and Mixkit). Clips with people, animals or symbols were removed, and each clip is checked again by a small detector before use. Clips are streamed from the original free CDNs, not re-hosted.
- exported as a clean H.264 MP4. Posts are marked "not AI-generated" on Buffer: the recitation and footage are real.

Total cost: R0. GitHub Actions, GitHub Pages, Buffer Free (3 channels, 10 queued posts each) and the Quran APIs are free. No card anywhere.

## How it runs

`.github/workflows/daily.yml` runs every night at 03:17 (SA time):
1. picks the next passages and reciters (no repeats), renders the videos,
2. publishes them on this repo's GitHub Pages (`gh-pages` branch, only files with posts still waiting),
3. schedules them on Buffer for the times in `config.json`, two days ahead.

## Setup (done once)

1. Buffer: TikTok + Instagram (Creator/Business) connected; a personal API key with `account:read`, `posts:read`, `posts:write`.
2. Repo secret `QURAANI_SECRETS` (Settings > Secrets and variables > Actions) containing one line: `BUFFER_API_KEY=...`
3. Actions > Quraani daily > Run workflow > `check`. Then Settings > Pages > Deploy from a branch > `gh-pages` / root. Run `check` again until all lines say OK, then run `daily` once.

## Changing things

`config.json`: posting times (`slots`, Africa/Johannesburg time), how many days ahead to schedule, and `handle` (e.g. `@calmquraan.daily`) to print a small handle on the videos.

Run locally: `pip install -r requirements.txt && python -m playwright install chromium`, then `python scripts/batch.py 5 out/batch` renders five videos with their captions.
