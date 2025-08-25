from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import requests
import re
import json
import random
import time
import asyncio
from typing import Optional, Dict, Any
import logging
from urllib.parse import urlparse, parse_qs
import os
from datetime import datetime
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Instagram Downloader API", version="2.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Free proxy list (rotating public proxies - optional)
FREE_PROXIES = [
    # These are examples - you'd need to find working free proxies
    # Most free proxies are unreliable, so we'll primarily use direct requests
]

# Keep-alive configuration for Render
KEEP_ALIVE_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # Set this to your Render app URL

# User agents for rotation
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
    headers: Optional[Dict[str, str]] = None

# Rate limiting
request_timestamps = []
MAX_REQUESTS_PER_MINUTE = 10

def get_random_user_agent():
    """Get a random user agent"""
    return random.choice(USER_AGENTS)

def get_proxy_config():
    """Get proxy configuration for requests (using free proxies if available)"""
    if FREE_PROXIES:
        proxy = random.choice(FREE_PROXIES)
        return {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}"
        }
    return None  # No proxy - direct connection



def get_headers():
    """Get randomized headers"""
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }

async def rate_limit_check():
    """Check and enforce rate limiting"""
    global request_timestamps
    current_time = time.time()
    
    # Remove timestamps older than 1 minute
    request_timestamps = [ts for ts in request_timestamps if current_time - ts < 60]
    
    if len(request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment.")
    
    request_timestamps.append(current_time)

async def add_random_delay():
    """Add random delay between requests"""
    delay = random.uniform(2, 5)  # 2-5 seconds
    await asyncio.sleep(delay)

def make_request_with_retry(url: str, max_retries: int = 3):
    """Make HTTP request with retry logic and user agent rotation"""
    for attempt in range(max_retries):
        try:
            headers = get_headers()
            proxies = get_proxy_config()
            
            logger.info(f"Attempt {attempt + 1}: Making request to {url}")
            
            response = requests.get(
                url,
                headers=headers,
                proxies=proxies,
                timeout=30,
                allow_redirects=True
            )
            
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                logger.warning(f"Rate limited on attempt {attempt + 1}")
                time.sleep(random.uniform(10, 20))  # Wait longer for rate limits
            else:
                logger.warning(f"HTTP {response.status_code} on attempt {attempt + 1}")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed on attempt {attempt + 1}: {str(e)}")
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
                logger.info("Keep-alive ping sent")
            except Exception as e:
                logger.error(f"Keep-alive ping failed: {e}")
    
    # Start keep-alive in background thread
    thread = threading.Thread(target=ping_self, daemon=True)
    thread.start()
    logger.info("Keep-alive service started")

def extract_instagram_data(html_content: str, url: str):
    """Extract Instagram media data from HTML"""
    try:
        # Look for JSON data in script tags
        json_pattern = r'window\._sharedData\s*=\s*({.+?});'
        match = re.search(json_pattern, html_content)
        
        if match:
            shared_data = json.loads(match.group(1))
            # Process shared data...
            
        # Alternative: Look for newer Instagram data structure
        json_pattern2 = r'"require"\s*:\s*\[\s*\[\s*"PolarisPostActionLoadPostQueryResource".*?({.+?})\s*\]'
        match2 = re.search(json_pattern2, html_content, re.DOTALL)
        
        if match2:
            # Process newer data structure...
            pass
            
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
        logger.error(f"Error extracting Instagram data: {str(e)}")
        return {'type': 'unknown', 'media_urls': [], 'success': False}

@app.get("/")
async def root():
    return {"message": "Instagram Downloader API v2.0", "status": "active"}

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "features": ["rate_limiting", "user_agent_rotation", "keep_alive", "retry_logic"],
        "keep_alive_enabled": bool(KEEP_ALIVE_URL),
        "uptime": "Service is running"
    }

@app.post("/api/proxy")
async def proxy_request(request: ProxyRequest):
    """Proxy endpoint for client-side requests"""
    await rate_limit_check()
    await add_random_delay()
    
    try:
        headers = get_headers()
        proxies = get_proxy_config()
        
        # Add custom headers if provided
        if request.headers:
            headers.update(request.headers)
        
        response = requests.request(
            method=request.method,
            url=request.url,
            headers=headers,
            proxies=proxies,
            timeout=30
        )
        
        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": response.text,
            "success": response.status_code == 200
        }
        
    except Exception as e:
        logger.error(f"Proxy request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Proxy request failed: {str(e)}")

@app.post("/api/download")
async def download_instagram_content(request: DownloadRequest):
    """Download Instagram content with enhanced proxy support"""
    await rate_limit_check()
    await add_random_delay()
    
    try:
        # Validate Instagram URL
        if not re.match(r'https?://(www\.)?instagram\.com/', request.url):
            raise HTTPException(status_code=400, detail="Invalid Instagram URL")
        
        # Make request with proxy and retry logic
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
        logger.error(f"Download failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.get("/api/download-file")
async def download_file(url: str):
    """Download media file with retry support"""
    await rate_limit_check()
    
    try:
        headers = get_headers()
        proxies = get_proxy_config()
        
        response = requests.get(
            url,
            headers=headers,
            proxies=proxies,
            timeout=30,
            stream=True
        )
        
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
        logger.error(f"File download failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File download failed: {str(e)}")

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("Starting Instagram Downloader API v2.0")
    keep_alive()  # Start keep-alive service
    logger.info("Keep-alive service initialized")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)