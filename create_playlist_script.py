import os, sys, pickle, base64

try:
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
except ImportError:
    print("ERROR: google-api-python-client not installed")
    sys.exit(1)

# Load credentials
creds = None
b64 = os.environ.get("YOUTUBE_TOKEN_BASE64","")
if b64:
    try:
        creds = pickle.loads(base64.b64decode(b64))
        print("Loaded credentials from env")
    except Exception as e:
        print(f"Env decode failed: {e}")

if not creds and os.path.exists("youtube_token.pickle"):
    with open("youtube_token.pickle","rb") as f:
        creds = pickle.load(f)
    print("Loaded credentials from file")

if not creds:
    print("ERROR: No YouTube credentials found")
    sys.exit(1)

if not creds.valid:
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        print("Refreshed credentials")
    else:
        print("ERROR: Credentials invalid and cannot refresh")
        sys.exit(1)

yt = build("youtube","v3",credentials=creds)

# Check channel info
ch = yt.channels().list(part="snippet",mine=True).execute()
if ch.get("items"):
    print(f"Channel: {ch['items'][0]['snippet']['title']}")

# Check if playlist already exists
existing = yt.playlists().list(part="snippet",mine=True,maxResults=50).execute()
for item in existing.get("items",[]):
    title = item["snippet"]["title"]
    if any(kw in title for kw in ["தூக்கம்","sleep","Sleep","meditation","Meditation","tookam"]):
        pid = item["id"]
        print(f"Found existing: '{title}' -> {pid}")
        with open("sleep_playlist_id.txt","w") as f: f.write(pid)
        sys.exit(0)

# Create new playlist
resp = yt.playlists().insert(
    part="snippet,status",
    body={
        "snippet": {
            "title": "ஆழ்ந்த தூக்கம் — Tamil Sleep & Meditation Music",
            "description": (
                "தமிழ் தியான இசை — Solfeggio frequencies, binaural beats, "
                "deity & nature sounds.\n\n"
                "174Hz • 285Hz • 396Hz • 417Hz • 528Hz • 639Hz • 741Hz • 852Hz • 963Hz\n"
                "முருகன் • சிவன் • விநாயகர் frequencies\n"
                "Rain • River • Forest soundscapes\n\n"
                "New video added daily. Use headphones for binaural effect.\n"
                "Subscribe: @aalayamani"
            ),
            "defaultLanguage": "ta",
        },
        "status": {"privacyStatus": "public"}
    }
).execute()

pid = resp["id"]
print(f"Created playlist: '{resp['snippet']['title']}'")
print(f"Playlist ID: {pid}")
print(f"URL: https://www.youtube.com/playlist?list={pid}")
with open("sleep_playlist_id.txt","w") as f: f.write(pid)
