# Quraani: automatic Quran recitation shorts

Every day this repo makes three 9:16 videos (up to 45 s) and schedules them through Buffer's free plan: Instagram Reels (3 a day: 08:15, 13:15, 19:45), YouTube Shorts (2 a day: 16:15, 19:45) and TikTok (2 a day: 12:15, 20:45). On Fridays a longer video (up to 3 min) takes the place of the 13:15 / 16:15 / 20:45 short.

Each video is:
- a popular passage (`catalog/passages.json`, 118 passages; the Friday video comes from `catalog/long_passages.json`, 22 longer passages and full short surahs) recited by a well-known reciter (`catalog/reciters.json`, 28 reciters). Clips always start and end on whole ayahs.
- opened by a hook title: the passage's key line (e.g. "Do not despair of the mercy of Allah") large at the top for the first 3 seconds, then the surah and reciter names fade in (the reciter in Arabic and English). It is also on the cover.
- the Arabic text (Uthmani script, Amiri Quran font) shown phrase by phrase in sync with the reciter (word timings from quran.com, ayah timings from mp3quran.net), with the English meaning (Saheeh International) underneath, split to follow each phrase. The text is kept small and the background shade light so the scenery leads; the shade gets a little stronger only on busy footage like leaves.
- real nature footage from a screened library of free stock clips (`catalog/footage.json`, Pexels and Mixkit), grouped by mood: mountains above the clouds, glaciers, waterfalls, turquoise lakes, coastlines, canyons, dunes, night skies and more. Every clip was hand-picked and checked frame by frame (2 per second) by a person/animal detector; clips with people, animals, buildings or symbols were left out. Each clip has a `score` (3 = breathtaking, 2 = strong, 1 = calm): higher scores are picked more often and each video opens with its strongest shot. Clips are streamed from the original free CDNs, not re-hosted.
- exported as a clean H.264 MP4. Posts are marked "not AI-generated" on Buffer: the recitation and footage are real.

Total cost: R0. GitHub Actions, GitHub Pages, Buffer Free (3 channels, 10 queued posts each), the YouTube Data API and the Quran APIs are free. No card anywhere.

## How it runs

`.github/workflows/daily.yml` runs every night at 03:17 (SA time), plus a safety-net run at 15:17:
1. picks the next passages and reciters (no repeats), renders the videos,
2. publishes them on this repo's GitHub Pages (`gh-pages` branch, only files with posts still waiting),
3. schedules them on Buffer for the times in `config.json`, one day ahead (Buffer Free allows 10 queued posts per channel). If a channel is connected later, a post failed or a queue was full, the next run adds the missing posts for videos that are still online.
4. learns from the numbers: it reads every sent post's stats into `state/metrics.json` (YouTube straight from YouTube with a free API key; Instagram and TikTok only if the Buffer key has `insights:read`, which needs a paid Buffer plan), compares each post with a typical post of the same age on the same platform, and gently favours the passage themes, reciters and footage moods that do better (weights between 0.6 and 1.8, so nothing is ever dropped). `state/stats.md` is a readable summary, refreshed every run.
5. follows what is popular: once a week it searches YouTube for the most viewed Quran Shorts of the last 30 days and finds the surah in each title, and searches once per reciter to rank our reciters by how their recent Shorts do on other channels (the reciter leaderboard). The most watched surahs and reciters get a small boost (weights 1.0 to 1.4, so our own stats still lead). `state/trends.md` lists what it found.

## Setup (done once)

1. Buffer: TikTok, Instagram (Creator/Business) and YouTube connected; a personal API key with `account:read`, `posts:read` and `posts:write` (plus `insights:read` on a paid plan, for Instagram and TikTok stats).
2. Repo secret `BUFFER_API_KEY` (Settings > Secrets and variables > Actions) holding the Buffer key. Buffer keys last up to a year: when it expires, make a new one in Buffer (Settings > API) and paste it over this secret.
3. YouTube API key (free, no card), for YouTube stats and trends:
   1. Sign in at https://console.cloud.google.com with a Google account. Accept the terms if asked. If it asks you to start a free trial or add a card, skip it: this API does not need billing.
   2. Top bar > project picker > New project > name it `quraani` > Create, then select it.
   3. Search bar > "YouTube Data API v3" > Enable.
   4. APIs & Services > Credentials > Create credentials > API key. Copy the key.
   5. On the new key: Edit > API restrictions > Restrict key > tick "YouTube Data API v3" > Save.
   6. Repo Settings > Secrets and variables > Actions > New repository secret: name `YOUTUBE_API_KEY`, value the key.
   7. If `state/stats.md` stays empty for YouTube after two days, put your YouTube handle in `config.json` as `"youtube_channel": "@yourhandle"` so the job can find the videos itself.
4. Actions > Quraani daily > Run workflow > `check`. Then Settings > Pages > Deploy from a branch > `gh-pages` / root. Run `check` again until all lines say OK, then run `daily` once.

## Changing things

`config.json`: posting times (`slots`, Africa/Johannesburg time), how many days ahead to schedule, `handle` (e.g. `@calmquraan.daily`) printed small on the videos, `english` (true/false) for the translation line, `hook_title` (true/false) for the opening title, `learn_from_stats` (true/false) for the stats step, `learn_from_trends` (true/false) for the weekly YouTube trends step, `youtube_channel` (optional, e.g. `@yourhandle`) to find our YouTube videos when Buffer gives no link, the `L` slot (`"long": true`, `"weekdays": [4]` = Fridays, Monday is 0, `"replaces": "B"` = posts in place of slot B that day) for the longer video, and `platform_plan` to pause a platform or limit it to some slots from a date on (used to warm up TikTok: paused, then 1, then 2 posts a day).

After a style change, Actions > Quraani daily > Run workflow > `rerender` redoes the videos that are already scheduled, under the same links. `new-footage` does the same with fresh clips from the library (useful after adding footage).

Run locally: `pip install -r requirements.txt && python -m playwright install chromium`, then `python scripts/batch.py 5 out/batch` renders five videos with their captions.
