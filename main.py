from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import requests
import re
import json
import random
import time
import os
from datetime import datetime
import threading

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Keep-alive configuration for Render
KEEP_ALIVE_URL = os.getenv("RENDER_EXTERNAL_URL", "")

# Enhanced user agents for better success rate
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
]

# Request models
class DownloadRequest(BaseModel):
    url: str
    download_type: str = "auto"

class ProxyRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}

def get_headers():
    """Get enhanced headers with rotation"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }

def make_request_with_retry(url: str, max_retries: int = 3):
    """Make HTTP request with retry logic and user agent rotation"""
    for attempt in range(max_retries):
        try:
            headers = get_headers()
            print(f"Attempt {attempt + 1}: Making request to {url}")
            
            # Add random delay between attempts
            if attempt > 0:
                delay = random.uniform(2, 5)
                time.sleep(delay)
            
            response = requests.get(
                url,
                headers=headers,
                timeout=30,
                allow_redirects=True
            )
            
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                print(f"Rate limited on attempt {attempt + 1}")
                time.sleep(random.uniform(10, 20))  # Wait longer for rate limits
            else:
                print(f"HTTP {response.status_code} on attempt {attempt + 1}")
                
        except requests.exceptions.RequestException as e:
            print(f"Request failed on attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(random.uniform(3, 7))
    
    raise HTTPException(status_code=503, detail="Failed to fetch content after multiple attempts")

def keep_alive():
    """Keep the Render service alive by making periodic requests"""
    if not KEEP_ALIVE_URL:
        return
        
    def ping_self():
        while True:
            try:
                time.sleep(840)  # 14 minutes (before 15-minute timeout)
                requests.get(f"{KEEP_ALIVE_URL}/api/health", timeout=10)
                print("Keep-alive ping sent")
            except Exception as e:
                print(f"Keep-alive ping failed: {e}")
    
    # Start keep-alive in background thread
    thread = threading.Thread(target=ping_self, daemon=True)
    thread.start()
    print("Keep-alive service started")

def extract_instagram_data(html_content: str, url: str):
    """Extract Instagram media data from HTML"""
    try:
        # Extract media URLs using regex patterns
        video_pattern = r'"video_url":"([^"]+)"'
        image_pattern = r'"display_url":"([^"]+)"'
        
        video_urls = re.findall(video_pattern, html_content)
        image_urls = re.findall(image_pattern, html_content)
        
        # Clean URLs (remove escape characters)
        video_urls = [url.replace('\\u0026', '&').replace('\\/', '/') for url in video_urls]
        image_urls = [url.replace('\\u0026', '&').replace('\\/', '/') for url in image_urls]
        
        # Determine content type
        if '/reel/' in url or '/reels/' in url:
            content_type = 'reel'
            media_urls = video_urls if video_urls else image_urls
        elif '/p/' in url:
            content_type = 'post'
            media_urls = image_urls + video_urls
        else:
            content_type = 'profile'
            # For profile pictures, look for profile image URLs
            profile_pattern = r'"profile_pic_url_hd":"([^"]+)"'
            profile_urls = re.findall(profile_pattern, html_content)
            media_urls = [url.replace('\\u0026', '&').replace('\\/', '/') for url in profile_urls]
        
        return {
            'type': content_type,
            'media_urls': media_urls[:10],  # Limit to 10 URLs
            'success': len(media_urls) > 0
        }
        
    except Exception as e:
        print(f"Error extracting Instagram data: {str(e)}")
        return {'type': 'unknown', 'media_urls': [], 'success': False}

@app.get("/")
async def root():
    return {"message": "Instagram Downloader API", "status": "active"}

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "keep_alive_enabled": bool(KEEP_ALIVE_URL),
    }

@app.post("/api/download")
async def download_instagram_content(request: DownloadRequest):
    """Download Instagram content"""
    try:
        print(f"Received download request for: {request.url}")
        
        # Validate Instagram URL
        if not re.match(r'https?://(www\.)?instagram\.com/', request.url):
            raise HTTPException(status_code=400, detail="Invalid Instagram URL")
        
        # Make request with retry logic
        response = make_request_with_retry(request.url)
        
        # Extract media data
        media_data = extract_instagram_data(response.text, request.url)
        
        if not media_data['success']:
            raise HTTPException(status_code=404, detail="Could not extract media from Instagram post")
        
        return {
            "success": True,
            "type": media_data['type'],
            "media_urls": media_data['media_urls'],
            "download_count": len(media_data['media_urls']),
            "message": f"Found {len(media_data['media_urls'])} media files"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Download failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.post("/api/proxy")
async def proxy_request(request: ProxyRequest):
    """Proxy endpoint for client-side requests"""
    try:
        print(f"Received proxy request for: {request.url}")
        headers = get_headers()
        
        # Add custom headers if provided
        if request.headers:
            headers.update(request.headers)
        
        response = requests.request(
            method=request.method,
            url=request.url,
            headers=headers,
            timeout=30
        )
        
        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": response.text,
            "success": response.status_code == 200
        }
        
    except Exception as e:
        print(f"Proxy request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Proxy request failed: {str(e)}")

@app.get("/api/download-file")
async def download_file(url: str):
    """Download media file"""
    try:
        headers = get_headers()
        response = requests.get(url, headers=headers, timeout=30, stream=True)
        
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Media file not found")
        
        # Get content type and filename
        content_type = response.headers.get('content-type', 'application/octet-stream')
        filename = f"instagram_media_{int(time.time())}"
        
        if 'video' in content_type:
            filename += '.mp4'
        elif 'image' in content_type:
            filename += '.jpg'
        
        def generate():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        
        return StreamingResponse(
            generate(),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        print(f"File download failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File download failed: {str(e)}")

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    print("Starting Instagram Downloader API")
    keep_alive()  # Start keep-alive service
    print("Keep-alive service initialized")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)