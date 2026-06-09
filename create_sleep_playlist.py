import os, sys, pickle, base64

b64 = os.environ.get("YOUTUBE_TOKEN_BASE64","")
if not b64:
    print("No YOUTUBE_TOKEN_BASE64 — skipping"); sys.exit(0)

if os.path.exists("sleep_playlist_id.txt"):
    pid = open("sleep_playlist_id.txt").read().strip()
    if pid:
        print(f"Playlist already exists: {pid}")
        sys.exit(0)

try:
    creds = pickle.loads(base64.b64decode(b64))
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    yt = build("youtube", "v3", credentials=creds)

    # Check channel
    ch = yt.channels().list(part="snippet", mine=True).execute()
    if ch.get("items"):
        print(f"Channel: {ch['items'][0]['snippet']['title']}")

    # Check existing playlists
    resp = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    for item in resp.get("items", []):
        t = item["snippet"]["title"]
        if any(kw in t for kw in ["தூக்கம்", "sleep", "Sleep", "meditation", "Meditation"]):
            pid = item["id"]
            print(f"Found existing: {t} -> {pid}")
            open("sleep_playlist_id.txt", "w").write(pid)
            sys.exit(0)

    # Create new
    r = yt.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": "ஆழ்ந்த தூக்கம் — Tamil Sleep & Meditation Music",
                "description": (
                    "தமிழ் தியான இசை — Solfeggio frequencies, binaural beats, nature sounds.\n"
                    "174Hz 285Hz 396Hz 417Hz 528Hz 639Hz 741Hz 852Hz 963Hz\n"
                    "முருகன் சிவன் விநாயகர் frequencies\n"
                    "Use headphones for binaural effect. New video daily.\n"
                    "Subscribe: @aalayamani"
                ),
                "defaultLanguage": "ta",
            },
            "status": {"privacyStatus": "public"}
        }
    ).execute()

    pid = r["id"]
    print(f"Created: {r['snippet']['title']}")
    print(f"Playlist ID: {pid}")
    print(f"URL: https://www.youtube.com/playlist?list={pid}")
    open("sleep_playlist_id.txt", "w").write(pid)

except Exception as e:
    print(f"Error: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
