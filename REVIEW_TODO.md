# Review findings to fix before the 0.8.0 release

> Temporary working file. **Delete it in the last commit before opening the PR.**

Branch `fix/audit-followups`, base `abecbe5` (7 commits ahead of `master`). An adversarial
review of `git diff master...HEAD` confirmed the 37 findings below (several are duplicates
across areas) and refuted 4. Each was reproduced with the offline harness
(`tests/conftest.py` `home` fixture, `tests/test_sync.py` `FakeWorld`, the local HTTP server in
`tests/test_spotify_rate_limit.py`).

## Rules
- Offline only: no real Spotify / YT Music / Last.fm calls, never open `.env`, `browser.json`
  or token caches. No secrets in commits; run `git status` before every commit.
- Every fix gets a regression test that fails on `abecbe5` and passes after (check it).
- Gate: `pip install ruff==0.17.1 && ruff check .` clean, `pytest -q` green (333 passed,
  2 skipped at `abecbe5`), `python playlist_sync.py --help` works.
- New test files start with the repo's header docstring (purpose, Author: David Malko,
  Date, Version: 0.8.0). Code, comments and docs in English; comments explain why.
- Update `README.md` and the `CHANGELOG.md` `[0.8.0]` section for every behaviour change;
  version stays `0.8.0` (single source: `playlist_sync/__init__.py`).
- Then: open a PR to `master`, get CI green. **Ask David before merging, and ask for the final
  OK on the exact version before tagging `v0.8.0`** (`publish.yml` ships to PyPI through
  Trusted Publishing on the tag; its gates need tag == `__version__` == a `## [0.8.0]`
  CHANGELOG section, so set that section's date to the release day).

## Decisions already taken (follow these)
- **#1 (critical)**: removals (`gone` in `cmd_sync`) must be gated on the source the enriched
  CSV was last synced from, not only on the latest snapshot. Record the `snapshot_id` with
  every enriched-CSV write by `cmd_sync` (small JSON file in `data/`; add it to the
  `tests/conftest.py` redirects). `gone = []` when the recorded source differs from the
  current one. No record (data from 0.7 or `import-csv`): fall back to today's rule (a
  snapshot of this source must exist). Rewrite
  `tests/test_sync_engine_fixes.py::test_source_switch_after_snapshot_still_removes_nothing`
  (#10) so it really switches the source via `.env` and runs `snapshot` before the sync.
- **#2/#13/#35**: a rate limit / network / Spotify error during the final push or removal
  saves everything, then exits 1 (README promises it). Update
  `tests/test_sync.py::test_rate_limited_push_loses_nothing` to expect `SystemExit(1)`.
  A rate limit or Ctrl+C during matching keeps exiting 0.
- **#3/#14**: the Ctrl+C handler's push must catch `RateLimitError`,
  `requests.exceptions.RequestException` and `spotipy.SpotifyBaseException` (covers
  `SpotifyOauthError`, which is not a `SpotifyException`), and the cache + CSV saves must run
  whatever the push raised (`finally`).
- **#4**: `restored_unpushed` only from cache keys still in `current`; drop stale entries.
- **#5**: keep the match cache: remove the "(To start fresh, delete: ...)" hint, write the cache
  atomically (temp file + `os.replace`), and on an unreadable cache warn loudly, rename it to
  `match_cache.json.bad` and tell the user to run `repush`.
- **#7**: after a failed or raised removal keep only the gone rows whose URI is still in the
  playlist (re-read it best-effort; if the re-read fails, keep them all).
- **#12**: wrap the Step 8 Spotify backfill in `try/except (RateLimitError, RequestException,
  SpotifyBaseException)`; log, skip the rest of the Spotify enrichment, continue to the CSV,
  review, snapshot and cache writes.
- **#17**: `get_tracks_batch` / `get_artists_batch`: a header-less 429 (spotipy's "Max Retries",
  i.e. 5xx) or 5xx stops without the 61 s sleep and reports what was not fetched; `enricher`
  sets `*_attempted` only for fetched tracks or a definitive 403/404.
  `add_saved_tracks` / `remove_saved_tracks` get the same treatment as part of G3.
- **#19**: `search_track` turns `SpotifyOauthError` into `SearchUnavailable`; `dispatch`
  catches `spotipy.SpotifyBaseException` with a clear "log in again / try later" message,
  exit 1. Use `SpotifyBaseException` in the new except tuples (cmd_sync, retry-unmatched,
  sync-likes, `_require_editable_playlist`).
- **#21**: likes removal skips URIs another current (or held-back, or just matched) like still
  maps to, and prints "Kept on Spotify: N ...", like `_remove_deleted`.
- **#26**: every current like missing from `likes_map` gets a row (URI from the playlist CSV
  when known, else unattempted so `--retry-unmatched` searches it); an un-like with no known
  URI is searched once; `_safe_csv_write` returns whether the real path was written and
  `sync-likes` skips `save_likes_snapshot` when it fell back. Add a migration note.
- **#27**: removals prefer the URI sync-likes saved (`likes_map`), playlist CSV only as fallback.
- **#28**: the menu's forced re-run unsaves only the held-back fingerprints the user confirmed;
  if the fresh diff removes more, hold back again.
- **#29**: every `unlink` of a report file (`review.csv`, `unmatched.csv`,
  `likes_unmatched.csv`) catches `OSError` and warns instead of crashing.
- **#30/#32**: `_tracks_from_csv` sniffs `;` vs `,` like `yt_transfer.read_import_file`, raises
  `CsvFormatError` when `title`/`artist` columns are missing (name the columns found) and for
  undecodable files (suggest saving as UTF-8). **#31**: the menu CSV import reads the CSV
  before archiving anything.
- **#34**: `_guard`'s "review" path only when the two artist names share no script (e.g. sets of
  `unicodedata.name(ch).split()[0]` for alphabetic chars are disjoint); add a reject test with
  a same-duration cover (Yesterday, Boyz II Men 123 s vs The Beatles 125 s).
- **#36**: reword README/CHANGELOG matching claims (no "never accepted"); also list in
  `review.csv` matches where only the bare-title comparison passed (remix / part-2 cases).
- **#37**: add `MANIFEST.in` with `prune tests` (sdist without the test suite) and assert the
  sdist layout in `tests/test_packaging.py`.
- **#33**: fix the CHANGELOG JSON-copy wording to what `to_json_dict` really does.

## Refuted (do not fix)
- sync-engine: No guard against mass removal: a truncated or empty YT fetch removes the missing tracks from Spotify and deletes their rows and enrichment from the CSV
- spotify-client: _spotify_session re-sends non-idempotent POSTs (playlist add, playlist create) after a 5xx
- spotify-client: A non-JSON 2xx crashes search_track (AttributeError) and makes get_playlist_tracks report an empty playlist
- likes-import-csv-models: Mass-unsave guard does not fire on an empty YT Music answer for libraries of 50 likes or fewer

## Confirmed findings by fix group

### G1 sync engine (cmd_sync, retry-unmatched, match cache)

**#1 [critical] Changing the source in .env and then running `snapshot` makes the next sync remove every track of the old source from Spotify (regression)** (`playlist_sync/cli.py:491`)

- Scenario: 1) sync YT playlist A: 5 tracks go to Spotify. 2) A CLI user, or a menu user who edits .env by hand, sets YTMUSIC_PLAYLIST_ID to playlist B, which has 3 other tracks. 3) They run `snapshot` (menu 11, 'record the YT playlist as it is now'). cmd_snapshot writes latest.json tagged with B's ID. 4) sync: _snapshot_for(B) now returns that snapshot, so `previous` is not None, while playlist_enriched.csv still holds A's rows. `gone` = every row of A with a URI, and _remove_deleted removes all 5 A tracks from Spotify. Their rows are then dropped from the CSV, so no record of the URIs is left. master removes nothing in the same flow (diff.removed = snapshot(B) - current(B) = empty). This breaks the documented rule: a source switch counts as a first run and removes nothing.
- Fix: Gate removals on the source the enriched CSV was last synced from, not on the latest snapshot. For example, cmd_sync writes snapshot_id to data/enriched_source.txt (or into the snapshot it saves, marked as written by sync) together with the CSV, and sets gone = [] when that recorded source is missing or differs from the current snapshot_id. As defence in depth, cmd_snapshot refuses (exit 1) when latest_snapshot_playlist_id() differs from the configured ID and ENRICHED_CSV exists, pointing to menu C (which archives) or a --force that archives first. Add a real switch-then-`snapshot`-then-sync regression test.

**#2 [medium] A rate limit or network or Spotify error during the final push or removal exits 0, which contradicts the README and CHANGELOG** (`playlist_sync/cli.py:755`)

- Scenario: A cron or Jenkins run matches 5 tracks, and add_tracks_to_playlist raises RateLimitError (long Retry-After), or a ConnectionError or SpotifyException occurs while pushing or removing. The except block saves the cache and the CSV, then `return`s, so the process exits 0 although nothing was added. The README (lines 301-304) says sync 'exits with code 1 when ... matched tracks could not be added or removed tracks could not be taken off Spotify'. The CHANGELOG ('Failed pushes and removals are no longer silent ... It exits 1') says the same. tests/test_sync.py:199 test_rate_limited_push_loses_nothing even relies on exit 0, because it calls sync() without expecting SystemExit.
- Fix: In that except block, call sys.exit(1) after _save_match_cache and write_enriched_csv (the menu's run_command already catches SystemExit). Keep the message. Change test_rate_limited_push_loses_nothing to wrap the first sync() in pytest.raises(SystemExit) and assert code 1.

**#3 [medium] Ctrl+C handler: a rate limit or network error during its push escapes with a traceback, so the CSV and match cache are never saved although 'Progress saved' was already printed** (`playlist_sync/cli.py:724`)

- Scenario: The user presses Ctrl+C during matching, typically because Spotify or the network seems stuck. The handler prints 'Stopped. Progress saved (N matched)', then calls _push_and_mark inside `try/except KeyboardInterrupt` only. If get_playlist_tracks raises requests.ConnectionError, or add_tracks_to_playlist raises RateLimitError, the exception leaves cmd_sync before _save_match_cache and write_enriched_csv run. The result is a traceback, no CSV written and no cache (the cache is only saved at every 25th entry). Every match of the run is lost and searched again on the next run.
- Fix: In the Ctrl+C branch, catch (KeyboardInterrupt, RateLimitError, requests.exceptions.RequestException, spotipy.SpotifyException) around _push_and_mark and print the 'next sync adds the rest' note. Or move _save_match_cache plus write_enriched_csv(rows_to_keep(include_removed=True)) into a `finally` so they run whatever the push raises.

**#4 [medium] Unpushed cache matches are pushed even for tracks no longer in the source: a killed run plus a YT deletion leaves the deleted track on Spotify for good** (`playlist_sync/cli.py:593`)

- Scenario: A long first sync of 30 tracks (or new tracks on an existing sync) is SIGTERM-killed by a cron or Jenkins timeout after the periodic save of 25 cache entries, so no CSV is written. Before the next run, the user deletes song 3 on YT Music. restored_unpushed is built from every cache entry without checking `current`, so song 3's URI is pushed to Spotify. Its row never reaches the enriched CSV (it is not in current), so it is never in `gone` and is never removed.
- Fix: Build restored_unpushed only from cache keys still in the source: [e['spotify_uri'] for fp, e in match_cache.items() if fp in current_fps and e.get('matched') and e.get('spotify_uri') and not e.get('pushed')]. Drop the other unpushed entries from match_cache before it is saved. Rows of gone tracks that reached the CSV are still handled by `gone` when a snapshot exists.

**#5 [medium] Matched-but-unpushed tracks exist only in match_cache.json; deleting it as the tool advises, or a corrupt cache, means they never reach Spotify while later syncs say 'Sync complete!'** (`playlist_sync/cli.py:184`)

- Scenario: A push is rate-limited (or retry-unmatched's push fails). The CSV records the 5 tracks as matched (spotify_uri set) and only the cache marks them unpushed. The next sync prints '(To start fresh, delete: ...match_cache.json)'. The user deletes it, or the file is corrupt: _load_match_cache silently returns {} on JSONDecodeError, and the write is not atomic. From then on the 5 rows are already_matched, are never in restored_unpushed and are never pushed. Every sync ends with 'Sync complete!' and an empty playlist; only a manual repush fixes it. This contradicts the 'Nothing is lost: the next sync adds the matched tracks' message.
- Fix: Drop the 'delete to start fresh' hint (or say that it also requires repush). On an unreadable cache, print a loud warning and rename it to match_cache.json.bad. Write the cache atomically (temp file plus os.replace). The robust fix is to keep the pushed state in the enriched CSV (for example, leave spotify_uri unset or add a pushed flag until the push succeeds), or to make sync add the already_matched URIs that _missing_from_playlist reports missing.

**#7 [low] After a failed removal all gone rows are kept, so a track that was in fact removed and is later re-added on YT is never pushed back** (`playlist_sync/cli.py:794`)

- Scenario: Song 1 is removed on YT. On the next sync, Spotify applies the removal but the response times out (ReadTimeout reaches the except at line 755), or one batch of a removal of more than 100 tracks fails (removal_failed). Either way rows_to_keep(include_removed=True) keeps song 1's row with its URI, although the URI is no longer on Spotify. The user re-adds song 1 on YT before the next successful sync. It is now already_matched, is not in matched_results or restored_unpushed, and is never pushed. Spotify lacks it permanently and sync reports success. master dropped the gone rows on the normal path, so the re-add was matched and pushed again.
- Fix: After a failed or raised removal, keep only the gone rows whose URI is still in the playlist: re-read it with get_playlist_tracks, or have remove_tracks_from_playlist return the set of removed URIs and drop those rows.

**#10 [low] test_source_switch_after_snapshot_still_removes_nothing never runs `snapshot`, so it does not test what its name says** (`tests/test_sync_engine_fixes.py:75`)

- Scenario: The test only runs sync() and then sync(from_csv=...). That is the same flow as tests/test_sync.py::test_csv_sync_after_yt_sync_removes_nothing, and it never calls run('snapshot'). The case its name describes, a source switch followed by `snapshot`, is not covered, and that case removes every old track (finding 1).
- Fix: Rewrite the test: sync(); cfg.save_setting('YTMUSIC_PLAYLIST_ID', other_id); world.yt = [other tracks]; run('snapshot'); sync(); assert world.removed == []. It fails until finding 1 is fixed. Keep the YT->CSV case under its existing test.

**#12 [medium] Long 429 or network error in the Step 8 metadata/genre backfill crashes sync after the push; CSV, snapshot and cache are not saved** (`playlist_sync/cli.py:769`)

- Scenario: The playlist push and removals succeed, then backfill_track_metadata or backfill_artist_genres calls get_tracks_batch. Spotify answers /tracks with a 429 and Retry-After: 7200. get_tracks_batch calls _handle_rate_limit, which raises RateLimitError. On master this could not happen: spotipy's session slept the wait and reported a header-less 429, so the wait defaulted to 60. Step 8 sits outside the try/except at lines 748-762, so the RateLimitError (or a requests ConnectionError) escapes cmd_sync as a traceback. playlist_enriched.csv, the snapshot and the match cache (written only every 25 entries) are not written for this run. The next run searches every match since the last checkpoint again; in the test, all 5 were searched again.
- Fix: Wrap the Step 8 Spotify backfill calls in try/except (RateLimitError, requests.exceptions.RequestException, spotipy.SpotifyException). Log, skip the rest of the Spotify enrichment, and continue to the CSV, review, snapshot and cache writes. Do not set the *_attempted flags for tracks that were never fetched.

**#13 [medium] sync exits 0 when the final push is stopped by a rate limit or network error (README promises exit 1)** (`playlist_sync/cli.py:755`)

- Scenario: Matching finishes, then add_tracks_to_playlist hits a long 429 (RateLimitError) or a ConnectionError or ReadTimeout. The except at line 755 prints 'Spotify stopped accepting changes', saves state and returns, so the exit code is 0. README says: 'sync exits with code 1 ... when matched tracks could not be added or removed tracks could not be taken off Spotify' (only a rate limit or Ctrl+C during matching exits 0), and CHANGELOG says the same. Cron or Jenkins sees success although no matched track and no removal reached Spotify. The partial-failure path (lines 858-866) exits 1, so the more complete failure is the one reported as success. tests/test_sync.py::test_rate_limited_push_loses_nothing calls sync() without expecting SystemExit, so the suite currently locks in the exit 0.
- Fix: In that except block, after _save_match_cache and write_enriched_csv, print the message and sys.exit(1); dry runs never reach it. Wrap the first sync() in test_rate_limited_push_loses_nothing in pytest.raises(SystemExit) and assert code == 1.

**#14 [medium] Ctrl+C during matching then a push error: traceback, and the 'Progress saved' it just printed is not saved** (`playlist_sync/cli.py:724`)

- Scenario: The user presses Ctrl+C during matching, typically because the network is hanging. The handler prints 'Stopped. Progress saved (N matched)' and then calls _push_and_mark inside a try that catches only KeyboardInterrupt. get_playlist_tracks or the add can then raise requests ConnectionError, or RateLimitError on a long 429. The exception escapes before lines 737-740, so the match cache and playlist_enriched.csv are never written. Every match since the last 25-entry checkpoint is lost, although the message said progress was saved.
- Fix: Save the match cache and CSV (rows_to_keep(include_removed=True)) before attempting the push in the interrupt handler. Also catch (RateLimitError, requests.exceptions.RequestException, spotipy.SpotifyException) next to KeyboardInterrupt around that _push_and_mark call and print that the next sync adds the rest.

**#35 [medium] sync exits 0 when the final push/removal fails with a network error or a long rate limit, contradicting the documented exit 1** (`playlist_sync/cli.py:756`)

- Scenario: Matching finishes. Then add_tracks_to_playlist or remove_tracks_from_playlist raises requests ConnectionError/ReadTimeout (_with_rate_limit_retry only catches SpotifyException) or RateLimitError. cmd_sync's except at cli.py:753-762 prints 'Spotify stopped accepting changes', saves the cache and returns, so the process exits 0 and nothing was added. README.md:301-305 says 'sync exits with code 1 ... when matched tracks could not be added or removed tracks could not be taken off Spotify'. It exempts only 'a rate limit or Ctrl+C during matching'. CHANGELOG says 'Failed pushes and removals are no longer silent ... It exits 1 when tracks could not be added or removed'. cron/Jenkins see success. retry-unmatched exits 1 for exactly these exceptions (cli.py:928-945), so the two commands disagree. The data is kept (the next sync pushes it); only the exit status is wrong.
- Fix: In the except block at cli.py:755-762, call sys.exit(1) after _save_match_cache / write_enriched_csv (the menu already catches SystemExit). Wrap the call in pytest.raises(SystemExit) inside test_rate_limited_push_loses_nothing, and add an exit-code assertion for ConnectionError on the push and on the removal. If a push-phase rate limit is meant to exit 0, state that in the README exit-code paragraph and the CHANGELOG instead, but a network error must still exit 1.

### G2 Spotify client, enricher flags, repush, dispatch

**#6 [medium] repush adds a Spotify track twice when two CSV rows share its URI** (`playlist_sync/cli.py:1158`)

- Scenario: Two YT entries match one Spotify track (the ME-M-3 case that sync now adds once), or the YT playlist has the same video twice. The user recreates the Spotify playlist and runs `repush`, which the sync error message itself recommends. desired_uris is not deduplicated, so to_add holds the URI twice and add_tracks_to_playlist adds 2 copies. This contradicts the docstring's 'Idempotent' claim. The next repush then reports duplicates and asks for --replace.
- Fix: desired_uris = list(dict.fromkeys(t.spotify_uri for t in matched if t.spotify_uri)), and use that for to_add (the --replace path too). Or reuse _missing_from_playlist, which already deduplicates.

**#8 [low] `repush --replace` ignores a failed wipe and adds everything again, doubling the playlist** (`playlist_sync/cli.py:1152`)

- Scenario: The user runs `repush --replace` to clean duplicates. remove_tracks_from_playlist fails for some or all batches (403/5xx, or still rate-limited after 3 waits) and returns less than requested. The return value is discarded, current_uri_counts is reset to {} and every desired URI is added again. The playlist ends up with 2 copies of every track, and the run still exits 0.
- Fix: removed = remove_tracks_from_playlist(...). If removed < len(current_uri_counts), print an error and sys.exit(1) before adding anything. Or re-read the playlist after the wipe and compute to_add from what is actually left.

**#9 [low] repush exits 0 when tracks could not be added, although sync's error message sends users to repush** (`playlist_sync/cli.py:1176`)

- Scenario: sync fails to push and tells the user 'Sync again, or run repush'. repush then adds 0 of 5 tracks, prints '(5 failed - check the log for details.)' and returns normally with exit 0, so cron or Jenkins cannot tell. This is inconsistent with the branch's new exit-1-on-push-failure rule for sync and retry-unmatched.
- Fix: After the failure message in cmd_repush, call sys.exit(1) when added < len(to_add).

**#15 [medium] repush --replace ignores a failed removal and re-adds everything (more duplicates); repush exits 0 on failures** (`playlist_sync/cli.py:1152`)

- Scenario: A playlist has duplicates and the user runs `repush --replace` to clean them. remove_tracks_from_playlist fails for a batch (for example a 5xx after retries, reported as a header-less 429) and returns fewer than requested. Its return value is ignored, and current_uri_counts is cleared anyway, so all desired URIs are added on top of the entries still there. In the test the playlist went from 7 entries (5 unique) to 12, and the output said 'Done! Added 5/5'. Separately, when adds fail ('Added 0/5 ... 5 failed') repush still exits 0. A long 429 (RateLimitError) or a requests exception from either call is a raw traceback, which with --replace can leave the playlist half-wiped.
- Fix: In --replace, compare the removed count with len(current_uri_counts). On a shortfall, re-read the playlist with get_playlist_tracks and rebuild current_uri_counts before computing to_add, or abort with exit 1. sys.exit(1) when added < len(to_add). Catch RateLimitError and requests.exceptions.RequestException around the remove and add calls, print a clear 'run repush again later' message and exit 1.

**#17 [low] Header-less 429 (5xx after retries) still sleeps 61 s per batch in metadata/genre/likes calls; an outage stalls sync for hours and writes off the backfill** (`playlist_sync/spotify_client.py:314`)

- Scenario: The branch added _is_real_rate_limit but did not use it in get_tracks_batch, get_artists_batch (line 362) or add_saved_tracks (line 547). During a Spotify outage each /tracks batch makes 4 requests, gets spotipy's header-less 'Max Retries' 429, sleeps 61 s in _handle_rate_limit, retries and fails again: about 65 s per 50 tracks. A 3,000-track first sync runs get_tracks_batch twice (metadata, then genres) plus get_artists_batch, which comes to about 2-2.5 h of sleeping after the push. backfill_track_metadata and backfill_artist_genres then set spotify_metadata_attempted / spotify_genres_attempted on every track, so that data is never fetched again.
- Fix: In get_tracks_batch and get_artists_batch, treat a header-less 429 or a 5xx as a server failure: no 61 s sleep, stop the loop, and report the batch as incomplete, for example by returning the unfetched IDs. In enricher, set *_attempted only for tracks that were fetched or got a definitive 403/404.

**#19 [low] Token refresh failure mid-run (SpotifyOauthError) escapes search_track and every cli handler** (`playlist_sync/spotify_client.py:228`)

- Scenario: Access tokens last 1 h, so a long sync refreshes the token mid-run in spotipy's _auth_headers, inside sp.search. If accounts.spotify.com answers 5xx or 429, spotipy raises SpotifyOauthError. That is a SpotifyBaseException, not a SpotifyException, and the token session has no retries. search_track catches only SpotifyException and RequestException. cmd_sync's loop catches RateLimitError, SearchUnavailable and KeyboardInterrupt, and its push handler catches RateLimitError, RequestException and SpotifyException. The run therefore dies with a traceback: the CSV is not written and matches since the last 25-entry checkpoint are lost. sync-likes and retry-unmatched behave the same way.
- Fix: In search_track, catch spotipy.oauth2.SpotifyOauthError and raise SearchUnavailable (or a dedicated auth error). Add it to cmd_sync's push except and to sync-likes and retry-unmatched, so progress is saved and the user gets a clear 're-run / log in again' message.

### G3 sync-likes (+ menu action_likes, add/remove_saved_tracks)

**#11 [high] sync-likes records a failed Liked Songs save as done; those likes never reach Spotify** (`playlist_sync/cli.py:1428`)

- Scenario: A sync-likes run matches 3 new likes, but current_user_saved_tracks_add fails (for example a Spotify 5xx after urllib3's retries, which spotipy reports as a 429 with no headers). add_saved_tracks logs the error and returns 0. cmd_sync_likes ignores that count: it prints 'Saved 0 tracks' and then 'Likes sync complete!', writes likes_enriched.csv with the 3 rows marked has_spotify_match, saves the likes snapshot containing them, and exits 0. On the next run the likes are no longer in diff.added (they are in the snapshot), and `pending` skips them (they have a match), so they are never saved. The same happens with --retry-unmatched. The user's YT likes are permanently missing on Spotify with no error.
- Fix: Have add_saved_tracks return the set of IDs it failed to save. In cmd_sync_likes, clear spotify_uri/match fields on those likes in likes_map, or keep them out of the saved snapshot, so the next run's diff.added or pending picks them up again. Print the failure and sys.exit(1) instead of 'Likes sync complete!'.

**#16 [medium] sync-likes: a failed unsave is never retried; the track stays in Spotify Liked Songs** (`playlist_sync/cli.py:1464`)

- Scenario: A song is un-liked on YT Music. remove_saved_tracks fails for its batch: a 502, or any 429, since remove_saved_tracks has no rate-limit handling. The run prints 'Unsaved 0 tracks' and still saves the likes snapshot without that song, then reports 'Likes sync complete!' with exit 0. The next run's diff no longer lists it as removed, so it is never unsaved.
- Fix: Have remove_saved_tracks return the IDs it failed to unsave. Keep those tracks in the saved snapshot and in likes_enriched.csv (as with held_back) so the next diff lists them as removed again, then exit 1. Route the delete through _with_rate_limit_retry as the playlist writes do.

**#18 [medium] One failed search throws away every match of a sync-likes run (no cache), exit 0** (`playlist_sync/cli.py:1421`)

- Scenario: The branch made search_track raise SearchUnavailable for 5xx and network errors. A single ReadTimeout is not retried, because read=False. cmd_sync tolerates up to 4 failures in a row, but cmd_sync_likes aborts at the first one with 'Nothing was changed; re-run later.' and exits 0. Nothing from the run is saved: no likes CSV, no snapshot, and likes have no match cache. A first sync-likes of thousands of likes (hours of searches) that hits one transient timeout near the end starts over from zero, and repeats that on every run that hits a timeout.
- Fix: Handle SearchUnavailable per track as cmd_sync does: skip the track with match_attempted unset and abort only after 5 failures in a row. On abort, save or push what matched. At minimum, write those likes into likes_map/likes_enriched.csv and leave the stopped ones out of the snapshot so they are retried.

**#20 [low] sync-likes crashes on a long 429 or network error while saving/unsaving; the run's matches are discarded** (`playlist_sync/cli.py:1428`)

- Scenario: add_saved_tracks calls _handle_rate_limit on a real 429 with Retry-After > 120 and raises RateLimitError. Its requests calls can also raise ReadTimeout or ConnectionError. cmd_sync_likes catches neither around steps 6 and 7, so the command ends in a traceback. Nothing from step 8 is written, and every like searched in the run is searched again next time. remove_saved_tracks has the same exposure to requests exceptions.
- Fix: Wrap steps 6 and 7 in try/except (RateLimitError, requests.exceptions.RequestException, spotipy.SpotifyException). Persist the matched likes, with the unsaved additions kept out of the snapshot so they are retried, print a clear message and exit 1.

**#21 [critical] sync-likes unsaves a Spotify track that another still-liked YT Music entry maps to** (`playlist_sync/cli.py:1436`)

- Scenario: The user has liked both the song and the music-video version of a track on YT Music (vid1 and vid2), and both match spotify:track:SHARED. They un-like only vid1. Step 7 looks up vid1's URI and unsaves SHARED, so a song still liked on YT Music disappears from Spotify Liked Songs. No later run puts it back: vid2 is 'unchanged' and its row already has a URI. The branch added exactly this protection for playlists (_remove_deleted / still_wanted) but not for likes.
- Fix: In Step 7, build still_wanted = {URI of every current like}: likes_map[fp].spotify_uri, else fingerprint_to_uri[fp], for each fp in current + held_back, plus this run's matched_likes. Remove only the deduped removal URIs that are not in still_wanted, and print 'Kept on Spotify: N track(s) another liked entry still matches', the same way _remove_deleted does.

**#22 [high] A long rate limit or one failed search during sync-likes matching throws away the whole run; a big library never gets past it** (`playlist_sync/cli.py:1418`)

- Scenario: First sync-likes run on a large library (no more 10,000 cap). The new session sends a long 429 straight back as RateLimitError (before this branch, urllib3 slept it out and the run went on). The except handler returns before anything is persisted: matches made this run, Spotify saves (even URIs reused for free), likes_enriched.csv and the snapshot are all skipped. The next run searches the same likes from the start and hits the ban at the same point, so no like is ever saved, and every run exits 0. A single SearchUnavailable (one 5xx after retries) also discards the run; cmd_sync tolerates up to 5 failures in a row and keeps a resume cache.
- Fix: On RateLimitError or KeyboardInterrupt, break out of the matching loop instead of returning. Save the URIs matched or reused so far, merge their rows into likes_map and likes_enriched.csv, and save a likes snapshot of previous + processed likes (leave the unprocessed added likes out, so the next run still sees them as added). Keep held-back and unapplied removals as well. Treat a single SearchUnavailable the way cmd_sync does: skip the track without setting match_attempted, and stop only after 5 in a row.

**#23 [high] A failed Liked Songs save batch is recorded as saved and never retried (exit 0)** (`playlist_sync/cli.py:1428`)

- Scenario: Spotify answers a 5xx (after urllib3's retries), or a 429 retry fails, while current_user_saved_tracks_add runs. add_saved_tracks logs it and returns a lower count. cmd_sync_likes ignores that count: it writes every matched row with its URI to likes_enriched.csv, saves the snapshot and prints 'Likes sync complete!'. The next runs see the likes as unchanged and matched ('Nothing to do.'), so they are never saved to Spotify. The branch fixed the same pattern for playlist pushes but not for likes.
- Fix: Have add_saved_tracks return the ids it could not save, and use _with_rate_limit_retry per batch, as playlist writes do. In cmd_sync_likes, leave the likes whose save failed out of the saved snapshot so the next run sees them as added again (their URIs can stay in likes_enriched.csv for free reuse). Print an error after the summary and sys.exit(1) instead of 'Likes sync complete!'.

**#24 [medium] A failed unsave is dropped from the snapshot and CSV, so it is never retried** (`playlist_sync/cli.py:1440`)

- Scenario: The user un-likes a song on YT Music. remove_saved_tracks gets a SpotifyException: any 5xx, or even a short 429, because it has no rate-limit retry at all. It logs the error and returns 0. cmd_sync_likes prints 'Unsaved 0 tracks' and 'Likes sync complete!', exits 0, and removes the like from both likes_enriched.csv and the snapshot. Later runs see no difference, so the song stays in Spotify Liked Songs forever.
- Fix: Wrap each delete batch in _with_rate_limit_retry and return the ids that failed. Keep the failed likes' rows in likes_enriched.csv and in the saved snapshot, the way held_back keeps them, so the next run unsaves them again. Exit 1 after the summary.

**#25 [medium] A long 429 while saving likes escapes cmd_sync_likes as a RateLimitError traceback** (`playlist_sync/cli.py:1426`)

- Scenario: With the branch's new session, a 429 whose Retry-After is over 120 s reaches add_saved_tracks, where _handle_rate_limit raises RateLimitError. Steps 6-7 of cmd_sync_likes catch nothing, and neither do dispatch and main, so the CLI (cron/Jenkins) dies with a Python traceback instead of a clean message. Nothing is persisted, so later runs redo the work. requests ConnectionError/Timeout during the save or unsave crash the same way.
- Fix: Wrap Steps 6-7 in try/except (RateLimitError, requests.exceptions.RequestException, spotipy.SpotifyException), as cmd_sync does around its push. Print a clear 'Spotify stopped accepting changes; re-run later' message, save the matched rows to likes_enriched.csv (so the next run reuses the URIs without a search), keep the old snapshot and sys.exit(1).

**#26 [medium] Likes missing from likes_enriched.csv are never recovered (0.7 upgrade, locked CSV), so un-liking them is not mirrored** (`playlist_sync/cli.py:1358`)

- Scenario: 0.7 overwrote likes_enriched.csv with only the last run's new likes. After upgrading, every older like is in the snapshot but has no row: it is never in diff.added, and pending only looks at fingerprints already in likes_map. Such a like is never searched or reused and never gets a row, even with --retry-unmatched. When the user un-likes it, Step 7 prints 'no Spotify match -- skipping' and the song stays saved on Spotify. Older unmatched likes are never retried either. This contradicts the CHANGELOG ('so un-liking an older song is mirrored'). A like_enriched.csv that is open in Excel during a run causes the same loss: _safe_csv_write writes likes_enriched_HHMMSS.csv while the snapshot is still updated.
- Fix: 1) When an un-liked like has no known URI, search it once at that point (one search per un-like) instead of skipping it. 2) Each run, add a row for every current like missing from likes_map: take a URI from fingerprint_to_uri when there is one, otherwise record the like with match_attempted unset so --retry-unmatched can search it. 3) Have _safe_csv_write return whether it wrote the real path, and skip save_likes_snapshot when the likes CSV went to a fallback file. Add a migration note.

**#27 [medium] Unsave uses the playlist sync's URI rather than the one sync-likes actually saved** (`playlist_sync/cli.py:1352`)

- Scenario: sync-likes searched vid1 itself and saved spotify:track:A, and likes_enriched.csv records A. Later the playlist sync matched the same video separately to spotify:track:B (another release; the 0.8 matcher and changing search results make this possible). fingerprint_to_uri is filled from likes_existing first and then main_existing, so the playlist's B overwrites A. When the user un-likes vid1, sync-likes unsaves B, which it never saved (it may be a track the user liked on Spotify themselves), and A stays in Liked Songs.
- Fix: For removals, use likes_map[fp].spotify_uri first and fall back to the main CSV's URI only when the likes row has none. For example, build a separate removal map from main_existing + likes_existing (likes last, so they win) and keep the current order for reusing URIs on adds.

**#28 [medium] Menu: after a held-back warning, the yes answer unsaves whatever a fresh fetch lacks, not the N tracks shown** (`playlist_sync/menu.py:1014`)

- Scenario: YT Music returns a truncated answer: 100 of 200 likes are missing, and the guard holds them back. The menu asks 'Unsave those 100 track(s) from Spotify Liked Songs anyway?' and the user says yes. run_command then starts sync-likes --force, which fetches the likes again and unsaves everything missing from that new answer, with no guard. If the second answer is also truncated, which is the failure the guard exists for, many more still-liked tracks are unsaved than the user confirmed.
- Fix: Store the held-back fingerprints on args (args.held_back_fps) and have the menu's forced run receive them. In cmd_sync_likes with forced fingerprints, unsave only removed likes in that set, and hold back again (or ask again) when the fresh diff removes more than was confirmed.

### G4 CSV reading/writing, menu CSV import, packaging

**#29 [medium] Deleting review.csv / likes_unmatched.csv while it is open crashes sync with PermissionError** (`playlist_sync/csv_manager.py:40`)

- Scenario: review.csv is meant to be opened in a spreadsheet. The user fixes the last fuzzy matches on YT Music and runs sync while review.csv is still open in Excel. write_review_csv([]) calls path.unlink(), which raises PermissionError [WinError 32] on Windows. The crash comes after the enriched CSV is written and before unmatched.csv, the snapshot, the debug stats, match-cache handling, the summary and the exit-code logic, so cron sees a traceback. The same happens in sync-likes at cli.py:1463 (LIKES_UNMATCHED_CSV.unlink()) when --retry-unmatched (which the menu offers) matches the last failures while likes_unmatched.csv is open: the snapshot is not saved. _safe_csv_write handles a locked file only for writes, not for this delete.
- Fix: Wrap both unlinks in try/except OSError: log it and print 'WARNING: review.csv is open in another program and is out of date; close it' (likewise for likes_unmatched.csv), then continue, mirroring _safe_csv_write.

**#30 [critical] A ';'-delimited (Excel EU locale) source CSV imports silently as blank tracks** (`playlist_sync/csv_manager.py:69`)

- Scenario: _tracks_from_csv always parses with ','. A CSV re-saved by Excel in a ';' locale becomes one column named 'title;artist;duration'. from_csv_row then builds tracks with empty title and artist, and import-csv prints 'Loaded N tracks', writes them to playlist_enriched.csv and exits 0. With import-csv --force, the real sync data is archived and replaced by these blank rows. yt_transfer.read_import_file already detects ';' for import-yt, but this reader does not, and missing title/artist columns are never reported.
- Fix: In _tracks_from_csv, sniff the delimiter from the header line, as read_import_file does (';' when it has more ';' than ','). After reading, raise CsvFormatError when the 'title' or 'artist' column is missing, naming the columns that were found. Also refuse rows with a blank title and artist. That covers renamed headers too, and it stops both import-csv and sync --from-csv before anything is archived or removed.

**#31 [low] Menu CSV import archives the sync data before checking that the CSV can be read** (`playlist_sync/menu.py:1086`)

- Scenario: Menu 'Import it as the starting point', yes to 'Import anyway?'. action_csv calls archive_playlist_data() first and only then run_command('import-csv'). If the CSV has a bad cell, import-csv fails with the CsvFormatError message after playlist_enriched.csv, the snapshots and the match cache were already moved to data/archive. The folder is left with no sync data, so the next sync runs as a first run (searches everything again and tracks no removals) unless the user moves the files back by hand. The CLI --force path reads the CSV before archiving; the menu does not.
- Fix: In action_csv, drop the menu's own archive_playlist_data call and use run_command('import-csv', force=True) after the user confirms, so cmd_import_csv validates the CSV first and archives only after that.

**#32 [low] A non-UTF-8 (Excel ANSI) CSV still crashes with a raw UnicodeDecodeError traceback** (`playlist_sync/csv_manager.py:69`)

- Scenario: A source CSV saved by Excel in cp1252 or cp1251 (e.g. 'Beyonce' with an accent, or Cyrillic titles). pd.read_csv(encoding='utf-8-sig') raises UnicodeDecodeError outside the per-row try, so it is never turned into CsvFormatError. It escapes dispatch for import-csv, sync --from-csv and status, which contradicts the CHANGELOG ('a bad cell names the file and row instead of a traceback'). A pandas ParserError on a malformed file escapes the same way.
- Fix: Wrap pd.read_csv in try/except (UnicodeDecodeError, pd.errors.ParserError) and raise CsvFormatError(f"{path.name} is not a UTF-8 CSV ({e}); save it as 'CSV UTF-8'"), or retry in the system code page the way read_import_file falls back.

**#33 [low] Docs say the JSON copy has null for every empty CSV cell; flags are false and text stays ""** (`CHANGELOG.md:15`)

- Scenario: CHANGELOG, the to_json_dict docstring and the write_tracks_json docstring all say the JSON has 'null where the CSV is empty' / 'null for empty cells'. In fact only numeric columns and 'explicit' become null. Empty flag cells become false, and empty text cells stay "" (an unmatched track has spotify_uri "" and album ""). A consumer filtering `.spotify_uri == null` to find unmatched tracks gets nothing.
- Fix: Reword the four places: 'numbers and true/false; null for an empty number or an unknown explicit flag; empty text stays ""; an empty flag is false'.

**#37 [low] sdist ships tests/test_*.py without tests/conftest.py and the files they read, so the suite fails when run from the sdist** (`pyproject.toml:1`)

- Scenario: setuptools' default sdist includes tests/test*.py but not tests/conftest.py (the autouse 'home' fixture), playlist_sync.py, CHANGELOG.md, SETUP.md, CONTRIBUTING.md, .github/ or .env.example. Anyone who runs pytest from the PyPI sdist (distro packagers, pip-audit rebuilds) gets 122 errors and 3 failures. The wheel itself is clean: only playlist_sync/*.py plus dist-info; no tests, data, .env or browser.json.
- Fix: Add a MANIFEST.in. The simplest is 'prune tests', which ships no tests in the sdist and also removes the risk of tests running against the real per-user folder. Alternatively, 'graft tests' plus 'include playlist_sync.py CHANGELOG.md SETUP.md CONTRIBUTING.md .env.example' and 'recursive-include .github *.yml', so the shipped suite runs as a source checkout. Add a check to the CI packaging test that lists the sdist and asserts the chosen layout.

### G5 matcher guards

**#34 [medium] Artist guard's 'review' path accepts ANY other artist (not only another alphabet): same-title covers are matched and pushed to Spotify** (`playlist_sync/matcher.py:127`)

- Scenario: A YT Music track whose original is not in the Spotify results (region-locked, missing from Spotify, or just not in the top 10). Spotify returns a same-title song by an unrelated Latin-script artist, and the two durations are within 5 s. _guard sees the artist similarity is below 0.6, then returns 'review' only because the title is >= 0.9 similar and the duration is within 5 s. It never checks that the artist names are in different alphabets. The cover is accepted at confidence 0.74 and added to the Spotify playlist; it is only listed in review.csv. sync-likes uses the same matcher, so the cover is saved to Spotify Liked Songs, and review.csv does not cover likes at all (write_review_csv is only called from cmd_sync). README.md:286-293 says a same-title song by another artist 'is never accepted', and CHANGELOG [0.8.0] says only 'an artist written in another alphabet' gets through. Both statements contradict the code. The repo test that 'rejects' Hello/Lionel Richie passes only because of its 45 s gap; at 292 s the same candidate is accepted.
- Fix: In _guard, return 'review' only when the two artist names share no script, for example with a helper that collects unicodedata.name(c).split()[0] for each alphabetic character (LATIN, CYRILLIC, CJK, HIRAGANA, ...) and checks that the sets for track.artist and the candidate's joined artist names are disjoint. Otherwise return 'reject'. Add a reject test with a same-duration cover (Yesterday by Boyz II Men at 123 s against The Beatles at 125 s), and keep test_transliterated_artist_matches_but_goes_to_review passing.

**#36 [low] README claims 'another song by the same artist ... is never accepted', but a sequel/part title passes the 60 % floor at high confidence, without a review.csv entry** (`README.md:286`)

- Scenario: The YT track is 'The Unforgiven II' by Metallica (396 s), and the original is missing from the Spotify results. 'The Unforgiven' by Metallica (387 s) is 0.90 title-similar and 9 s apart, so every guard passes. It is accepted at confidence 0.88, above REVIEW_CONFIDENCE, so it is not even listed in review.csv. The same applies to 'Part 1'/'Part 2' or 'X'/'X Remix' pairs within 15 s, because _bare_title strips ' - X Remix'. When the right track is in the results it still wins (the raw title similarity scores higher), but the README's absolute 'never accepted' is false.
- Fix: Reword README.md:285-286 and the matching CHANGELOG bullet so they describe the actual checks: a candidate is rejected unless the title is at least 60 % similar, the durations are within 15 s and an artist matches. Drop 'never'. Optionally, send a match to review.csv when only the bare-title comparison passed (raw title similarity lower than the bare one), so suffix-only and remix matches get a manual check.
