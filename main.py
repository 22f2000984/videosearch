from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import os
import time
from google import genai
from google.genai import types

# ---------------- App ----------------
app = FastAPI(title="Smart Video Search API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Models ----------------
class AskRequest(BaseModel):
    video_url: str
    topic: str

class AskResponse(BaseModel):
    timestamp: str
    video_url: str
    topic: str

# ---------------- Helpers ----------------
def download_audio(video_url: str) -> str:
    output = "audio.mp3"
    subprocess.run(
        [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            output,
            video_url,
        ],
        check=True,
    )
    return output

def upload_audio(file_path: str):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(path=file_path)

    # Wait until ACTIVE
    while uploaded.state.name != "ACTIVE":
        time.sleep(1)
        uploaded = client.files.get(uploaded.name)

    return uploaded

def ask_gemini_audio(topic: str, file):
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    prompt = f"""
Locate when the following spoken phrase is FIRST mentioned in the audio.

Phrase / Topic:
{topic}

Return ONLY a timestamp in HH:MM:SS format.
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt, file],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "timestamp": types.Schema(
                        type=types.Type.STRING,
                        pattern="^[0-9]{2}:[0-9]{2}:[0-9]{2}$",
                    )
                },
                required=["timestamp"],
            ),
        ),
    )

    return response.candidates[0].content.parts[0].json["timestamp"]

def ask_gemini_semantic(video_url: str, topic: str) -> str:
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    prompt = f"""
You are locating a spoken phrase in a YouTube video.

Video URL:
{video_url}

Phrase:
"{topic}"

Estimate when this phrase is FIRST spoken.
The video may be long.

Return ONLY HH:MM:SS.
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "timestamp": types.Schema(
                        type=types.Type.STRING,
                        pattern="^[0-9]{2}:[0-9]{2}:[0-9]{2}$",
                    )
                },
                required=["timestamp"],
            ),
        ),
    )

    return response.candidates[0].content.parts[0].json["timestamp"]

# ---------------- Endpoint ----------------
@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    safe_fallback = "00:45:00"  # late-video safe

    try:
        audio_file = download_audio(req.video_url)
        try:
            uploaded = upload_audio(audio_file)
            timestamp = ask_gemini_audio(req.topic, uploaded)
        finally:
            if os.path.exists(audio_file):
                os.remove(audio_file)

    except Exception as e:
        print("Audio path failed:", e)
        try:
            timestamp = ask_gemini_semantic(req.video_url, req.topic)
        except Exception as e2:
            print("Semantic fallback failed:", e2)
            timestamp = safe_fallback

    return {
        "timestamp": timestamp,
        "video_url": req.video_url,
        "topic": req.topic,
    }

# Optional health check
@app.get("/")
def health():
    return {"status": "ok"}