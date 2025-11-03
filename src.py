import os
import time
import csv
import sys
from datetime import datetime, timezone
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()
#pqrswxyznef

# src.py
# Polls Spotify "currently playing" and logs plays to CSV.
# Requires: pip install spotipy
# Set environment variables: SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI
# Scope required: user-read-playback-state user-read-currently-playing



POLL_INTERVAL = int(os.environ.get("SPOTIFY_POLL_INTERVAL", "10"))  # seconds
SUMMARY_CSV = os.environ.get("SPOTIFY_SUMMARY_CSV", "spotify_summary.csv")
HISTORY_CSV = os.environ.get("SPOTIFY_HISTORY_CSV", "spotify_history.csv")
SCOPE = "user-read-playback-state user-read-currently-playing"
CACHE_PATH = os.environ.get("SPOTIFY_CACHE_PATH", ".spotify_cache")

def iso_from_ms_epoch(ms_epoch):
    return datetime.fromtimestamp(ms_epoch / 1000.0, tz=timezone.utc).isoformat()

def ensure_csv(path, fieldnames):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

def load_summary(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["track_id"]: row for row in reader}

def save_summary(path, records):
    fieldnames = ["track_id", "track_name", "artists", "album", "duration_ms", "first_played", "last_played", "play_count"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records.values():
            writer.writerow(rec)

def append_history(path, record):
    fieldnames = ["played_at", "track_id", "track_name", "artists", "album", "duration_ms"]
    ensure_csv(path, fieldnames)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(record)


def wait_or_break_if_skipped(sp, current_unique, remaining_ms, check_interval=10.0):
    """Wait up to remaining_ms milliseconds, but poll Spotify every check_interval seconds.
    If the currently playing track changes (unique != current_unique), return the new unique
    immediately so the caller can continue without waiting the full duration.

    Returns the last observed unique (either the same current_unique if nothing changed,
    or the new unique tuple (track_id, start_ms) if changed).
    """
    if remaining_ms <= 0:
        return current_unique
    end_time = time.time() + (remaining_ms / 1000.0)
    # Clamp a minimum sensible check interval
    check_interval = max(0.5, float(check_interval))
    while time.time() < end_time:
        # Sleep a short time, but not longer than remaining time
        to_sleep = min(check_interval, end_time - time.time())
        if to_sleep > 0:
            time.sleep(to_sleep)
        try:
            cur = sp.current_user_playing_track()
        except Exception as e:
            # Transient error — print and continue polling
            print("Spotify API error during wait:", str(e))
            continue

        if cur and cur.get("item"):
            timestamp = cur.get("timestamp")
            progress_ms = cur.get("progress_ms", 0)
            start_ms = (timestamp - progress_ms) if (timestamp is not None) else None
            cur_unique = (cur["item"].get("id"), start_ms)
            if cur_unique != current_unique:
                # Track changed (skipped/next/resumed different session)
                return cur_unique
        else:
            # Nothing playing anymore — treat as change so main loop will continue
            return (None, None)

    return current_unique

def get_spotify_client():
    load_dotenv()
    client_id = os.getenv('SPOTIPY_CLIENT_ID')
    client_secret = os.getenv('SPOTIPY_CLIENT_SECRET')
    redirect_uri = os.getenv('SPOTIPY_REDIRECT_URI')
    if not (client_id and client_secret and redirect_uri):
        print("Please set SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI environment variables.")
        sys.exit(1)
    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPE,
        cache_path=CACHE_PATH
    )
    return spotipy.Spotify(auth_manager=auth_manager)

def main():
    sp = get_spotify_client()
    # Prepare CSVs
    ensure_csv(HISTORY_CSV, ["played_at", "track_id", "track_name", "artists", "album", "duration_ms"])
    ensure_csv(SUMMARY_CSV, ["track_id", "track_name", "artists", "album", "duration_ms", "first_played", "last_played", "play_count"])
    summary = load_summary(SUMMARY_CSV)
    #last_play_unique is either None if uninstantiated or the last trackid in history
    last_play_unique = None if not HISTORY_CSV else list(csv.DictReader(open(HISTORY_CSV, encoding='utf-8')))[-1]["track_id"]
    # Try to initialize last_play_unique from last line of history CSV
    # try:
    #     with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
    #         reader = csv.DictReader(f)
    #         rows = list(reader)
    #         if rows:
    #             last_row = rows[-1]
    #             last_play_unique = last_row["track_id"]
    # except Exception as e:
    #     print("Error reading history CSV for last play:", str(e))
    #     last_play_unique = None

    print("Starting Spotify tracker. Poll interval:", POLL_INTERVAL, "s")
    try:
        while True:
            try:
                currently = sp.current_user_playing_track()
            except Exception as e:
                # Token refresh or transient error; print and continue
                print("Spotify API error:", str(e))
                time.sleep(POLL_INTERVAL)
                continue

            if currently and currently.get("item"):
                is_playing = currently.get("is_playing", False)
                item = currently["item"]
                track_id = item.get("id")
                track_name = item.get("name")
                artists = ", ".join(a.get("name") for a in item.get("artists", []) if a.get("name"))
                album = item.get("album", {}).get("name")
                duration_ms = item.get("duration_ms", 0)

                timestamp = currently.get("timestamp")  # ms epoch when data was fetched
                progress_ms = currently.get("progress_ms", 0)
                # start of this playback session (approx)
                start_ms = (timestamp - progress_ms) if (timestamp is not None) else None

                unique_play = track_id

                # Consider a new play only when a new unique_play is detected and the track is playing
                if is_playing and last_play_unique == unique_play:
                    # Still playing the same track as before; do nothing
                    pass
                elif is_playing and (unique_play != last_play_unique):
                    #print(last_play_unique, unique_play)
                    played_at_iso = iso_from_ms_epoch(start_ms) if start_ms else datetime.now(timezone.utc).isoformat()
                    # Append to history
                    append_history(HISTORY_CSV, {
                        "played_at": played_at_iso,
                        "track_id": track_id,
                        "track_name": track_name,
                        "artists": artists,
                        "album": album,
                        "duration_ms": duration_ms
                    })
                    # Update summary
                    rec = summary.get(track_id)
                    if rec:
                        rec["play_count"] = str(int(rec.get("play_count", "0")) + 1)
                        rec["last_played"] = played_at_iso
                        # Update descriptive fields in case they changed
                        rec["track_name"] = track_name
                        rec["artists"] = artists
                        rec["album"] = album
                        rec["duration_ms"] = str(duration_ms)
                    else:
                        summary[track_id] = {
                            "track_id": track_id,
                            "track_name": track_name,
                            "artists": artists,
                            "album": album,
                            "duration_ms": str(duration_ms),
                            "first_played": played_at_iso,
                            "last_played": played_at_iso,
                            "play_count": "1"
                        }
                    save_summary(SUMMARY_CSV, summary)
                    last_play_unique = unique_play
                    print(f"Logged: {track_name} — {artists} [{played_at_iso}] (count={summary[track_id]['play_count']})")
                    # Sleep for remaining duration if track is replayed
                    remaining_ms = duration_ms - progress_ms
                   # print("Remaining ms in track:", remaining_ms)
                    # while remaining_ms > 0:
                    #     # Wait but break early if the user skips to another track.
                    #     # This will poll Spotify every ~1s during the remaining time
                    #     # and return early if the currently playing track changes.
                    #     last_play_unique = wait_or_break_if_skipped(sp, unique_play, remaining_ms, check_interval=2.0)
                    #     progress_ms = currently.get("progress_ms", 0)
                    #     remaining_ms=duration_ms-progress_ms if progress_ms > POLL_INTERVAL*1000 else 0
                    #time.sleep((remaining_ms/2) / 1000.0)
            else:
                # Nothing playing
                pass

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Stopping tracker.")

if __name__ == "__main__":
    main()