# Quraani: automatic Quran recitation shorts

Every day this repo makes four 9:16 videos (up to 45 s) and schedules them through Buffer's free plan: TikTok (4 a day), YouTube Shorts (2 a day) and Instagram Reels (1 a day).

Each video is:
- a popular passage (`catalog/passages.json`, 109 passages) recited by a well-known reciter (`catalog/reciters.json`, 28 reciters). Clips always start and end on whole ayahs.
- the Arabic text (Uthmani script, Amiri Quran font) shown phrase by phrase in sync with the reciter (word timings from quran.com, ayah timings from mp3quran.net).
- real nature footage from a screened library of free stock clips (`catalog/footage.json`, Pexels and Mixkit). Clips with people, animals or symbols were removed, and each clip is checked again by a small detector before use. Clips are streamed from the original free CDNs, not re-hosted.
- exported as a clean H.264 MP4. Posts are marked "not AI-generated" on Buffer: the recitation and footage are real.

Total cost: R0. GitHub Actions, GitHub Pages, Buffer Free (3 channels, 10 queued posts each) and the Quran APIs are free. No card anywhere.

## How it runs

`.github/workflows/daily.yml` runs every night at 03:17 (SA time), plus a safety-net run at 15:17:
1. picks the next passages and reciters (no repeats), renders the videos,
2. publishes them on this repo's GitHub Pages (`gh-pages` branch, only files with posts still waiting),
3. schedules them on Buffer for the times in `config.json`, one day ahead (Buffer Free allows 10 queued posts per channel). If a channel is connected later, a post failed or a queue was full, the next run adds the missing posts for videos that are still online.

## Setup (done once)

1. Buffer: TikTok, Instagram (Creator/Business) and YouTube connected; a personal API key with `account:read`, `posts:read`, `posts:write`.
2. Repo secret `BUFFER_API_KEY` (Settings > Secrets and variables > Actions) holding the Buffer key. Buffer keys last up to a year: when it expires, make a new one in Buffer (Settings > API) and paste it over this secret.
3. Actions > Quraani daily > Run workflow > `check`. Then Settings > Pages > Deploy from a branch > `gh-pages` / root. Run `check` again until all lines say OK, then run `daily` once.

## Changing things

`config.json`: posting times (`slots`, Africa/Johannesburg time), how many days ahead to schedule, and `handle` (e.g. `@calmquraan.daily`) to print a small handle on the videos.

Run locally: `pip install -r requirements.txt && python -m playwright install chromium`, then `python scripts/batch.py 5 out/batch` renders five videos with their captions.
