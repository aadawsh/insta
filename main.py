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
import instaloader
from urllib.parse import urlparse
import tempfile
import shutil

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

# Initialize Instaloader
def get_instaloader():
    """Get configured Instaloader instance"""
    L = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        storyitem_metadata_txt_pattern="",
        max_connection_attempts=3,
        request_timeout=30
    )
    
    # Set user agent
    L.context.user_agent = random.choice(USER_AGENTS)
    return L

def extract_shortcode_from_url(url: str) -> str:
    """Extract Instagram shortcode from URL"""
    patterns = [
        r'/p/([A-Za-z0-9_-]+)',
        r'/reel/([A-Za-z0-9_-]+)',
        r'/reels/([A-Za-z0-9_-]+)',
        r'/tv/([A-Za-z0-9_-]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    raise ValueError("Could not extract shortcode from URL")

def extract_username_from_url(url: str) -> str:
    """Extract Instagram username from profile URL"""
    # Remove trailing slash and split
    clean_url = url.rstrip('/')
    parts = clean_url.split('/')
    
    # Find the username part - look for instagram.com
    for i, part in enumerate(parts):
        if 'instagram.com' in part and i + 1 < len(parts):
            username = parts[i + 1]
            # Remove query parameters and clean up
            username = username.split('?')[0].split('#')[0]
            # Skip common non-username paths
            if username not in ['p', 'reel', 'reels', 'tv', 'stories', 'explore', 'accounts']:
                return username
    
    # Alternative approach - regex
    import re
    match = re.search(r'instagram\.com/([^/?#]+)', url)
    if match:
        username = match.group(1)
        if username not in ['p', 'reel', 'reels', 'tv', 'stories', 'explore', 'accounts']:
            return username
    
    raise ValueError(f"Could not extract username from URL: {url}")

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
        print(f"Extracting data from URL: {url}")
        print(f"HTML content length: {len(html_content)}")
        
        # Updated extraction patterns for Instagram's current structure
        patterns = {
            'video_url': [
                r'"video_url":"([^"]+)"',
                r'"videoUrl":"([^"]+)"',
                r'video_url":"([^"]+)"',
                r'"src":"([^"]*\.mp4[^"]*)"',
                r'"playback_url":"([^"]+)"',
                r'"video_dash_manifest":"([^"]+)"',
                r'videoUrl&quot;:&quot;([^&]+)&quot;',
                r'"video_versions":\[{"url":"([^"]+)"'
            ],
            'display_url': [
                r'"display_url":"([^"]+)"',
                r'"displayUrl":"([^"]+)"',
                r'display_url":"([^"]+)"',
                r'"src":"([^"]*\.jpg[^"]*)"',
                r'"src":"([^"]*\.jpeg[^"]*)"',
                r'"image_versions2":{"candidates":\[{"url":"([^"]+)"',
                r'displayUrl&quot;:&quot;([^&]+)&quot;',
                r'"display_resources":\[{"src":"([^"]+)"'
            ],
            'profile_pic': [
                r'"profile_pic_url_hd":"([^"]+)"',
                r'"profilePicUrlHd":"([^"]+)"',
                r'profile_pic_url_hd":"([^"]+)"',
                r'"hd_profile_pic_url_info":{"url":"([^"]+)"',
                r'"hd_profile_pic_versions":\[{"url":"([^"]+)"'
            ]
        }
        
        video_urls = []
        image_urls = []
        profile_urls = []
        
        # Try all video patterns
        for pattern in patterns['video_url']:
            matches = re.findall(pattern, html_content)
            video_urls.extend(matches)
        
        # Try all image patterns
        for pattern in patterns['display_url']:
            matches = re.findall(pattern, html_content)
            image_urls.extend(matches)
        
        # Try all profile patterns
        for pattern in patterns['profile_pic']:
            matches = re.findall(pattern, html_content)
            profile_urls.extend(matches)
        
        # Clean URLs (remove escape characters and duplicates)
        def clean_urls(urls):
            cleaned = []
            for url in urls:
                clean_url = url.replace('\\u0026', '&').replace('\\/', '/').replace('\\u003d', '=')
                if clean_url not in cleaned and ('instagram' in clean_url or 'fbcdn' in clean_url):
                    cleaned.append(clean_url)
            return cleaned
        
        video_urls = clean_urls(video_urls)
        image_urls = clean_urls(image_urls)
        profile_urls = clean_urls(profile_urls)
        
        print(f"Found {len(video_urls)} video URLs, {len(image_urls)} image URLs, {len(profile_urls)} profile URLs")
        
        # Debug: Show first few characters of HTML to understand structure
        if len(html_content) > 1000:
            sample = html_content[:2000]
            if 'window._sharedData' in sample:
                print("Found window._sharedData in HTML")
            if 'application/json' in sample:
                print("Found JSON data in HTML")
            if 'instagram.com' in sample:
                print("Instagram domain found in HTML")
        
        # Debug: Print first few URLs found
        if video_urls:
            print(f"Sample video URL: {video_urls[0][:100]}...")
        if image_urls:
            print(f"Sample image URL: {image_urls[0][:100]}...")
        if profile_urls:
            print(f"Sample profile URL: {profile_urls[0][:100]}...")
        
        # Determine content type and select appropriate URLs
        if '/reel/' in url or '/reels/' in url:
            content_type = 'reel'
            media_urls = video_urls if video_urls else image_urls
        elif '/p/' in url:
            content_type = 'post'
            # For posts, prioritize images but include videos
            media_urls = image_urls + video_urls
        elif '/stories/' in url:
            content_type = 'story'
            media_urls = video_urls + image_urls
        else:
            content_type = 'profile'
            media_urls = profile_urls
        
        # Remove duplicates while preserving order
        unique_urls = []
        for url in media_urls:
            if url not in unique_urls:
                unique_urls.append(url)
        
        media_urls = unique_urls[:10]  # Limit to 10 URLs
        
        print(f"Final result: type={content_type}, urls={len(media_urls)}")
        
        return {
            'type': content_type,
            'media_urls': media_urls,
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

@app.get("/api/test-download")
async def test_download():
    """Test endpoint to verify download functionality"""
    test_url = "https://www.instagram.com/reel/DKw9uUCSuko"
    try:
        # Test with Instaloader
        L = get_instaloader()
        shortcode = extract_shortcode_from_url(test_url)
        print(f"Testing with shortcode: {shortcode}")
        
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        result = {
            "test_url": test_url,
            "shortcode": shortcode,
            "is_video": getattr(post, 'is_video', False),
            "success": True
        }
        
        if post.is_video and hasattr(post, 'video_url'):
            result["video_url"] = post.video_url
        
        if hasattr(post, 'url'):
            result["display_url"] = post.url
            
        return result
        
    except Exception as e:
        print(f"Test failed: {str(e)}")
        return {
            "test_url": test_url,
            "error": str(e),
            "success": False
        }

@app.post("/api/download")
async def download_instagram_content(request: DownloadRequest):
    """Download Instagram content - try Instaloader first, fallback to regex"""
    try:
        print(f"Received download request for: {request.url}")
        
        # Validate Instagram URL
        if not re.match(r'https?://(www\.)?instagram\.com/', request.url):
            raise HTTPException(status_code=400, detail="Invalid Instagram URL")
        
        # Try Instaloader first for posts/reels
        if '/p/' in request.url or '/reel/' in request.url or '/tv/' in request.url:
            try:
                print("Trying Instaloader method...")
                L = get_instaloader()
                shortcode = extract_shortcode_from_url(request.url)
                print(f"Extracted shortcode: {shortcode}")
                
                # Simple approach - just get the post
                post = instaloader.Post.from_shortcode(L.context, shortcode)
                
                media_urls = []
                content_type = 'reel' if '/reel/' in request.url else 'post'
                
                # Get primary media URL
                if post.is_video and post.video_url:
                    media_urls.append(post.video_url)
                elif post.url:
                    media_urls.append(post.url)
                
                if media_urls:
                    print(f"Instaloader success: found {len(media_urls)} URLs")
                    return {
                        "success": True,
                        "type": content_type,
                        "media_urls": media_urls,
                        "download_count": len(media_urls),
                        "message": f"Found {len(media_urls)} media files (Instaloader)"
                    }
                else:
                    print("Instaloader found no URLs, trying fallback")
                    
            except Exception as e:
                print(f"Instaloader failed: {str(e)}")
            
            # Fallback to regex extraction
            print("Using fallback extraction method")
            return await fallback_extraction(request.url)
        
        elif '/stories/' in request.url:
            # Story - use fallback method
            return await fallback_extraction(request.url)
        
        else:
            # Profile picture - try Instaloader
            try:
                username = extract_username_from_url(request.url)
                print(f"Extracted username: {username}")
                
                L = get_instaloader()
                profile = instaloader.Profile.from_username(L.context, username)
                
                return {
                    "success": True,
                    "type": "profile",
                    "media_urls": [profile.profile_pic_url],
                    "download_count": 1,
                    "message": "Found profile picture"
                }
                
            except Exception as e:
                print(f"Profile download failed: {str(e)}")
                # Fallback for profile
                return await fallback_extraction(request.url)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Download failed: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

async def fallback_extraction(url: str):
    """Fallback to regex extraction if Instaloader fails"""
    try:
        print("Using fallback extraction method")
        
        # Try different approaches
        methods = [
            ("Standard request", lambda: make_request_with_retry(url)),
            ("Mobile user agent", lambda: make_mobile_request(url)),
            ("Embed request", lambda: make_embed_request(url))
        ]
        
        for method_name, method_func in methods:
            try:
                print(f"Trying {method_name}")
                response = method_func()
                media_data = extract_instagram_data(response.text, url)
                
                if media_data['success']:
                    return {
                        "success": True,
                        "type": media_data['type'],
                        "media_urls": media_data['media_urls'],
                        "download_count": len(media_data['media_urls']),
                        "message": f"Found {len(media_data['media_urls'])} media files ({method_name})"
                    }
            except Exception as e:
                print(f"{method_name} failed: {str(e)}")
                continue
        
        raise HTTPException(status_code=404, detail="Could not extract media from Instagram post with any method")
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Fallback extraction failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"All extraction methods failed: {str(e)}")

def make_mobile_request(url: str):
    """Make request with mobile user agent"""
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    if response.status_code == 200:
        return response
    else:
        raise Exception(f"Mobile request failed: {response.status_code}")

def make_embed_request(url: str):
    """Try to get embed version of the content"""
    # Convert regular URL to embed URL
    if '/p/' in url:
        embed_url = url.replace('/p/', '/p/').rstrip('/') + '/embed/'
    elif '/reel/' in url:
        embed_url = url.replace('/reel/', '/p/').rstrip('/') + '/embed/'
    else:
        raise Exception("Cannot create embed URL for this type")
    
    headers = get_headers()
    response = requests.get(embed_url, headers=headers, timeout=30, allow_redirects=True)
    if response.status_code == 200:
        return response
    else:
        raise Exception(f"Embed request failed: {response.status_code}")

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