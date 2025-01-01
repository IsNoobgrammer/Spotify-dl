import httpx
import random,json,asyncio,urllib

async def fetch_json_response(base_url: str, query: str):
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
    ]

    # Encode the query for URL compatibility
    encoded_query = urllib.parse.quote(query)
    full_url = f"{base_url}?url={encoded_query}"

    headers = {
        "User-Agent": random.choice(user_agents)
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(full_url, headers=headers)
        return response.json()

# Define base URL and query
base_url = "https://api.fabdl.com/spotify/get"
query = "https://open.spotify.com/playlist/3BsMFFxm7YdzX0CpnyVCCD"

# Fetch JSON response
resp= asyncio.run(fetch_json_response(base_url, query))
Playlist=f"{resp["result"]["name"]} - {resp["result"]["owner"]}"
tracks=resp["result"]["tracks"]


import os
import re
import base64
import asyncio
import httpx
from tqdm.asyncio import tqdm
from pyDes import des, PAD_PKCS5, ECB
import yt_dlp
from moviepy.audio.io.AudioFileClip import AudioFileClip
import nest_asyncio
nest_asyncio.apply()


error_songs=[]

# Initialize DES cipher
des_cipher = des(b"38346591", ECB, b"\0\0\0\0\0\0\0\0", pad=None, padmode=PAD_PKCS5)
semaphore = asyncio.Semaphore(8)  # Limit concurrent downloads to 5

async def ensure_output_dir(output_dir):
    """Ensure the output directory exists."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

async def get_jio_link(session, track):
    """Fetch the JioSaavn link for the given track."""
    name = f'{track["name"]} By {track["artists"]} Official'
    uri = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&query={name}&_format=json&_marker=0&ctx=wap6dot0"
    
    try:
        response = await session.get(uri)
        response.raise_for_status()
        resp_json = response.json().get("songs", {}).get("data")
        if resp_json:
            data = [
                (i["title"], i["more_info"]["primary_artists"], i["url"])
                for i in resp_json
            ]
            for j in data:
                if track["name"] in j[0] and j[1] in track["artists"]:
                    return j[2]
        return None
    except Exception as e:
        print(f"Error in get_jio_link: {e}")
        return None

async def download_and_convert_jio_song(session, track, download_url, output_dir):
    """Download JioSaavn song as temp file, convert to MP3, and save."""
    try:
        temp_file = os.path.join(output_dir, f"{track['name']}_temp.mp4")
        output_file = os.path.join(output_dir, f"{track['name']} - {track['artists']}.mp3")
        
        async with session.stream("GET", download_url) as response:
            response.raise_for_status()
            with open(temp_file, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
        
        # Convert MP4 to MP3
        audio = AudioFileClip(temp_file)
        audio.write_audiofile(output_file,logger=None)
        await asyncio.sleep(2)
        audio.close()
        
        # Clean up temp file
        os.remove(temp_file)
        print(f"\nDownloaded {output_file}")
    except Exception as e:
        error_songs.append(track)
        print(f"Error processing {track['name']} from JioSaavn: {e}")
        return None

async def get_dl_link(session, track, output_dir):
    """Fetch download link and metadata for JioSaavn or fallback to YouTube."""

    check=os.path.join(output_dir, f"{track['name']} - {track['artists']}.mp3")
    if os.path.exists(check):
        print(f"\n{track['name']} already exists:")
        return None

    # uri = await get_jio_link(session, track)
    uri=False
    if uri:
        try:
            id = re.findall(r"song\/.*\/(.*)", uri)[0]
            url = f'https://www.jiosaavn.com/api.php?__call=webapi.get&api_version=4&_format=json&_marker=0&ctx=wap6dot0&token={id}&type=song'
            song_resp = await session.get(url)
            song_resp.raise_for_status()
            song_data = song_resp.json().get("songs", [{}])[0]
            enc_url = song_data["more_info"]["encrypted_media_url"]
            enc = base64.b64decode(enc_url.strip())
            dec_url = des_cipher.decrypt(enc, padmode=PAD_PKCS5).decode("utf-8")
            download_url = (
                dec_url.replace("_96.mp4", "_320.mp4")
                if song_data["more_info"].get("320kbps")
                else dec_url.replace("_96.mp4", "_160.mp4")
            )
            return await download_and_convert_jio_song(session, track, download_url, output_dir)
        except Exception as e:
            print(f"Error in get_dl_link: {e}")
            return None
    else:
        # Fallback to YouTube
        return await download_yt_song(track, output_dir)

async def download_yt_song(track, output_dir):
    """Download YouTube song as audio."""
    async with semaphore:
        try:
            name = f'{track["name"]} By {track["artists"]} Official'
            url = f'https://www.youtube.com/results?search_query={name}'
            async with httpx.AsyncClient() as session:
                response = await session.get(url)
                response.raise_for_status()
                video_id = re.findall(r'videoId\":\"([^"]+)', response.text)[0]
            
            yt_url = f"https://www.youtube.com/watch?v={video_id}"
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(output_dir, f"{track['name']} - {track['artists']}"),
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320',
                    },
                    {
                        'key': 'FFmpegMetadata',
                    },
                ],
                'postprocessor_args': [
                    '-metadata', f'title={track["name"]}',
                    '-metadata', f'artist={track["artists"]}',
                ],
                'quiet': True,
                'noprogress': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([yt_url])
            await asyncio.sleep(13)  # Wait for 12 seconds
            print(f"\nDownloaded {name}")
        except Exception as e:
            error_songs.append(track)
            print(f"Error downloading {track['name']} from YouTube: {e}")
            return None

async def main(tracks, output_dir):
    """Main function to process a list of tracks."""
    await ensure_output_dir(output_dir)
    
    # Create progress bar
    progress_bar = tqdm(total=len(tracks), desc="Downloading Songs")
    
    async with httpx.AsyncClient() as session:
        tasks = []
        for track in tracks:
            task = asyncio.create_task(get_dl_link(session, track, output_dir))
            task.add_done_callback(lambda _: progress_bar.update(1))
            tasks.append(task)
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks)
    
    progress_bar.close()
    import pickle
    with open("error_songs.bin", "wb+") as f:
        pickle.dump(error_songs, f)

        




asyncio.run(main(tracks[:], Playlist))

asyncio.run(main(tracks[:], Playlist))

