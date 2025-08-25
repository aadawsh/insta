from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import instaloader
import os
import re
import shutil
from pathlib import Path
import tempfile
from typing import Optional, List
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Instagram Downloader API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create downloads directory
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

class DownloadRequest(BaseModel):
    url: str
    content_type: str = "auto"

class DownloadResponse(BaseModel):
    success: bool
    message: str
    files: Optional[List[str]] = None
    content_type: Optional[str] = None

def extract_username_from_url(url: str) -> Optional[str]:
    """Extract Instagram username from URL"""
    patterns = [
        r'instagram\.com/([^/?]+)/?$',
        r'instagram\.com/([^/?]+)/?(?:\?.*)?$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            # Filter out common non-username paths
            if username not in ['p', 'reel', 'stories', 'tv', 'explore', 'accounts', 'direct']:
                return username
    return None

def extract_shortcode_from_url(url: str) -> Optional[str]:
    """Extract shortcode from Instagram post/reel URL"""
    patterns = [
        r'instagram\.com/p/([^/?]+)',
        r'instagram\.com/reel/([^/?]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def detect_content_type(url: str) -> str:
    """Auto-detect content type from URL"""
    if '/p/' in url:
        return 'post'
    elif '/reel/' in url:
        return 'reel'
    elif '/stories/' in url:
        return 'story'
    elif re.search(r'instagram\.com/[^/]+/?$', url):
        return 'profile'
    return 'post'  # default

def clean_filename(filename: str) -> str:
    """Clean filename for safe storage"""
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

@app.post("/api/download", response_model=DownloadResponse)
async def download_content(request: DownloadRequest):
    """Download Instagram content"""
    try:
        url = request.url.strip()
        content_type = request.content_type
        
        if content_type == "auto":
            content_type = detect_content_type(url)
        
        logger.info(f"Downloading {content_type} from {url}")
        
        # Create instaloader instance
        L = instaloader.Instaloader(
            download_videos=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )
        
        # Create temporary directory for this download
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            try:
                if content_type == 'profile':
                    username = extract_username_from_url(url)
                    if not username:
                        return DownloadResponse(
                            success=False,
                            message="Could not extract username from URL"
                        )
                    
                    try:
                        profile = instaloader.Profile.from_username(L.context, username)
                        
                        # Download profile picture
                        profile_pic_url = profile.profile_pic_url
                        filename = f"{username}_profile_pic.jpg"
                        
                        # Download the profile picture using requests
                        import requests
                        response = requests.get(profile_pic_url)
                        if response.status_code == 200:
                            with open(temp_path / filename, 'wb') as f:
                                f.write(response.content)
                        
                        # Move to downloads directory
                        final_path = DOWNLOADS_DIR / filename
                        shutil.move(temp_path / filename, final_path)
                        
                        return DownloadResponse(
                            success=True,
                            message=f"Profile picture downloaded successfully",
                            files=[filename],
                            content_type=content_type
                        )
                        
                    except instaloader.exceptions.ProfileNotExistsException:
                        return DownloadResponse(
                            success=False,
                            message="Profile does not exist or is private"
                        )
                
                elif content_type in ['post', 'reel']:
                    shortcode = extract_shortcode_from_url(url)
                    if not shortcode:
                        return DownloadResponse(
                            success=False,
                            message="Could not extract shortcode from URL"
                        )
                    
                    try:
                        post = instaloader.Post.from_shortcode(L.context, shortcode)
                        
                        # Check if profile is private
                        if post.owner_profile.is_private:
                            return DownloadResponse(
                                success=False,
                                message="Post data unavailable for private profile"
                            )
                        
                        # Download post
                        L.download_post(post, target=temp_path)
                        
                        # Find downloaded files
                        downloaded_files = []
                        # Look for all files in temp directory
                        for file_path in temp_path.iterdir():
                            if file_path.is_file() and not file_path.name.startswith('.'):
                                clean_name = clean_filename(file_path.name)
                                final_path = DOWNLOADS_DIR / clean_name
                                shutil.copy2(file_path, final_path)
                                downloaded_files.append(clean_name)
                                logger.info(f"Downloaded file: {clean_name}")
                        
                        if not downloaded_files:
                            return DownloadResponse(
                                success=False,
                                message="No files were downloaded"
                            )
                        
                        return DownloadResponse(
                            success=True,
                            message=f"{content_type.title()} downloaded successfully",
                            files=downloaded_files,
                            content_type=content_type
                        )
                        
                    except instaloader.exceptions.PostChangedException:
                        return DownloadResponse(
                            success=False,
                            message="Post has been deleted or is no longer available"
                        )
                    except instaloader.exceptions.PrivateProfileNotFollowedException:
                        return DownloadResponse(
                            success=False,
                            message="Post data unavailable for private profile"
                        )
                
                elif content_type == 'story':
                    return DownloadResponse(
                        success=False,
                        message="Story download requires authentication and is not supported in this demo"
                    )
                
                else:
                    return DownloadResponse(
                        success=False,
                        message=f"Unsupported content type: {content_type}"
                    )
                    
            except instaloader.exceptions.InstaloaderException as e:
                logger.error(f"Instaloader error: {str(e)}")
                return DownloadResponse(
                    success=False,
                    message=f"Download failed: {str(e)}"
                )
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                return DownloadResponse(
                    success=False,
                    message="An unexpected error occurred during download"
                )
                
    except Exception as e:
        logger.error(f"Request processing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/download-file/{filename}")
async def download_file(filename: str):
    """Download a specific file"""
    file_path = DOWNLOADS_DIR / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/octet-stream'
    )

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "message": "Instagram Downloader API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)