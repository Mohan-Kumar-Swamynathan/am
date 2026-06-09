import os, sys, pickle, base64, traceback

print("=== YouTube Playlist Creator ===")

# Check env
b64 = os.environ.get("YOUTUBE_TOKEN_BASE64","")
cs  = os.environ.get("CLIENT_SECRETS_BASE64","")
print(f"YOUTUBE_TOKEN_BASE64: {'set (' + str(len(b64)) + ' chars)' if b64 else 'NOT SET'}")
print(f"CLIENT_SECRETS_BASE64: {'set' if cs else 'NOT SET'}")
print(f"youtube_token.pickle: {'exists' if os.path.exists('youtube_token.pickle') else 'missing'}")

try:
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    print("✅ google libraries imported")
except ImportError as e:
    print(f"❌ import failed: {e}"); sys.exit(1)

# Load credentials
creds = None
if b64:
    try:
        data = base64.b64decode(b64)
        creds = pickle.loads(data)
        print(f"✅ Credentials loaded from env (valid={creds.valid}, expired={creds.expired})")
    except Exception as e:
        print(f"❌ Env decode failed: {e}")

if not creds and os.path.exists("youtube_token.pickle"):
    try:
        with open("youtube_token.pickle","rb") as f: creds = pickle.load(f)
        print(f"✅ Credentials loaded from file (valid={creds.valid})")
    except Exception as e:
        print(f"❌ File load failed: {e}")

if not creds:
    print("❌ No credentials — cannot proceed"); sys.exit(1)

if not creds.valid:
    if creds.expired and creds.refresh_token:
        print("Refreshing expired token...")
        try:
            creds.refresh(Request())
            print(f"✅ Token refreshed (now valid={creds.valid})")
        except Exception as e:
            print(f"❌ Refresh failed: {e}"); sys.exit(1)
    else:
        print(f"❌ Token invalid, expired={creds.expired}, has_refresh={bool(creds.refresh_token)}")
        sys.exit(1)

# Build YouTube service
try:
    yt = build("youtube","v3",credentials=creds)
    print("✅ YouTube service built")
except Exception as e:
    print(f"❌ Service build failed: {e}"); sys.exit(1)

# Get channel info
try:
    ch = yt.channels().list(part="snippet",mine=True).execute()
    if ch.get("items"):
        print(f"✅ Channel: {ch['items'][0]['snippet']['title']}")
    else:
        print("⚠️ No channel found for this account")
except Exception as e:
    print(f"❌ Channel fetch failed: {e}"); traceback.print_exc(); sys.exit(1)

# Check existing playlists
try:
    existing = yt.playlists().list(part="snippet",mine=True,maxResults=50).execute()
    print(f"Found {len(existing.get('items',[]))} existing playlists:")
    for item in existing.get("items",[]):
        print(f"  - '{item['snippet']['title']}' ({item['id']})")
        title = item["snippet"]["title"]
        if any(kw in title for kw in ["தூக்கம்","sleep","Sleep","meditation","Meditation"]):
            pid = item["id"]
            print(f"✅ Found sleep playlist: '{title}' → {pid}")
            with open("sleep_playlist_id.txt","w") as f: f.write(pid)
            sys.exit(0)
except Exception as e:
    print(f"❌ Playlist list failed: {e}"); traceback.print_exc(); sys.exit(1)

# Create new playlist
try:
    resp = yt.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": "ஆழ்ந்த தூக்கம் — Tamil Sleep & Meditation Music",
                "description": (
                    "தமிழ் தியான இசை — Solfeggio frequencies, binaural beats, "
                    "deity & nature sounds.\n\n"
                    "174Hz • 285Hz • 528Hz • 639Hz • 741Hz • 852Hz\n"
                    "முருகன் • சிவன் • விநாயகர் frequencies\n"
                    "Rain • River • Forest soundscapes\n\n"
                    "New video daily. Use headphones for binaural effect.\n"
                    "Subscribe: @aalayamani"
                ),
                "defaultLanguage": "ta",
            },
            "status": {"privacyStatus": "public"}
        }
    ).execute()
    pid = resp["id"]
    print(f"✅ Created: '{resp['snippet']['title']}'")
    print(f"✅ Playlist ID: {pid}")
    print(f"✅ URL: https://www.youtube.com/playlist?list={pid}")
    with open("sleep_playlist_id.txt","w") as f: f.write(pid)
except Exception as e:
    print(f"❌ Playlist creation failed: {e}"); traceback.print_exc(); sys.exit(1)
