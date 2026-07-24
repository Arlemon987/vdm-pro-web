from flask import Flask, request, jsonify, Response, stream_with_context, redirect
from flask_cors import CORS
import yt_dlp
import os
import re
import tempfile
import base64
import requests

app = Flask(__name__)
CORS(app)

@app.route('/', methods=['GET'])
def home():
    """
    Serves the static index.html or health status to guarantee 200 OK on root route.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(base_dir, 'public', 'index.html')
        if os.path.exists(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                return Response(f.read(), mimetype='text/html')
    except Exception as e:
        print(f"Error serving homepage: {e}")
    return jsonify({'status': 'online', 'message': 'IDM Pro Serverless Engine API'}), 200

def setup_cookies():
    """
    Writes cookies from environment variable to a temporary file for yt-dlp.
    Supports raw Netscape format or Base64 encoded Netscape cookies.
    """
    cookie_content = os.getenv('YT_COOKIES', '').strip()
    
    if not cookie_content:
        # Check base64 encoded env var
        b64_content = os.getenv('YT_COOKIES_BASE64', '').strip()
        if b64_content:
            try:
                cookie_content = base64.b64decode(b64_content).decode('utf-8')
            except Exception:
                pass

    if cookie_content:
        try:
            temp_cookie_file = os.path.join(tempfile.gettempdir(), 'yt_cookies.txt')
            with open(temp_cookie_file, 'w', encoding='utf-8') as f:
                f.write(cookie_content)
            return temp_cookie_file
        except Exception as e:
            print(f"Failed to write cookie file: {e}")
    return None

def get_ydl_options(cookie_path=None, quality='best'):
    """
    Generates yt-dlp configurations tailored for YouTube bot-bypassing and TikTok extractor fixes.
    """
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

    # Quality format rules
    format_rule = 'best[ext=mp4]/best'
    if quality == 'audio':
        format_rule = 'bestaudio/best'
    elif quality == 'hd':
        format_rule = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif quality == 'sd':
        format_rule = 'worstvideo[ext=mp4]+worstaudio/worst[ext=mp4]/worst'

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': format_rule,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'geo_bypass': True,
        'http_headers': {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
        },
        # Client rotation to bypass YouTube bot blocks & TikTok extraction errors
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'ios', 'android', 'web_embedded'],
                'skip': ['hls', 'dash']
            },
            'tiktok': {
                'app_version': ['34.0.0'],
                'manifest_app_version': ['34.0.0']
            }
        }
    }

    if cookie_path and os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path

    return ydl_opts

@app.route('/api/fetch', methods=['POST'])
def fetch_video():
    try:
        data = request.get_json() or {}
        url = data.get('url', '').strip()
        quality = data.get('quality', 'best')

        if not url:
            return jsonify({'success': False, 'error': 'Video URL is required.'}), 400

        cookie_path = setup_cookies()
        ydl_opts = get_ydl_options(cookie_path, quality)

        # Primary extraction attempt
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as primary_error:
            # Fallback with simpler format and direct android client fallback
            fallback_opts = ydl_opts.copy()
            fallback_opts['extractor_args'] = {
                'youtube': {
                    'player_client': ['android', 'ios'],
                }
            }
            fallback_opts['format'] = 'best'
            
            with yt_dlp.YoutubeDL(fallback_opts) as ydl_fallback:
                info = ydl_fallback.extract_info(url, download=False)

        if 'entries' in info and len(info['entries']) > 0:
            info = info['entries'][0]

        title = info.get('title', 'Video Media')
        thumbnail = info.get('thumbnail') or (info.get('thumbnails', [{}])[-1].get('url') if info.get('thumbnails') else '')
        duration = info.get('duration', 0)
        ext = info.get('ext', 'mp4')
        uploader = info.get('uploader', info.get('uploader_id', 'Unknown Creator'))
        views = info.get('view_count', 0)

        download_url = info.get('url', '')
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')

        return jsonify({
            'success': True,
            'title': title,
            'safe_title': safe_title,
            'thumbnail': thumbnail,
            'duration': duration,
            'uploader': uploader,
            'views': views,
            'download_url': download_url,
            'ext': ext,
            'proxy_url': f"/api/stream?url={requests.utils.quote(download_url)}&filename={safe_title}.{ext}" if download_url else None
        })

    except Exception as e:
        error_msg = str(e)
        
        if "Sign in to confirm you're not a bot" in error_msg:
            error_msg = "YouTube Bot Block detected. Please set the YT_COOKIES environment variable in your Vercel Dashboard."
        elif "Video not available" in error_msg or "status code 0" in error_msg:
            error_msg = "TikTok Provider blocked server request. Try refreshing or updating cookies."

        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/stream', methods=['GET'])
def stream_media():
    """
    Proxies video data stream with correct Referer/User-Agent headers to prevent 
    TikTok/YouTube CDN hotlink blocks and ERR_INVALID_RESPONSE errors on Vercel.
    """
    target_url = request.args.get('url')
    filename = request.args.get('filename', 'video.mp4')

    if not target_url:
        return jsonify({'error': 'Missing media URL'}), 400

    try:
        # Sanitize filename for HTTP header standards (RFC 5987 compliance)
        safe_filename = re.sub(r'[^\w\.-]', '_', filename).strip('_') or 'video.mp4'

        # Set domain-appropriate Referer header to pass TikTok and YouTube CDN checks
        referer = 'https://www.tiktok.com/' if any(d in target_url for d in ['tiktok', 'byteoversea', 'ibyteimg']) else 'https://www.youtube.com/'

        req_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Referer': referer,
            'Accept': '*/*'
        }

        # Issue stream request to target CDN
        r = requests.get(target_url, headers=req_headers, stream=True, timeout=12)

        if r.status_code != 200:
            # Fallback directly to CDN link if proxying returns non-200
            return redirect(target_url)

        # Generator function for streaming response chunks
        def generate():
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        content_type = r.headers.get('Content-Type', 'video/mp4')

        # Exclude Content-Length header to prevent HTTP chunked transfer conflicts on Vercel
        res_headers = {
            'Content-Type': content_type,
            'Content-Disposition': f'attachment; filename="{safe_filename}"',
            'Access-Control-Allow-Origin': '*'
        }

        return Response(stream_with_context(generate()), headers=res_headers, status=200)

    except Exception as e:
        print(f"Proxy stream exception: {e}")
        return redirect(target_url)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

# ... existing code ...
def setup_cookies(custom_cookie_str=None):
    """
    Writes cookies from request payload or environment variables to a temporary file for yt-dlp.
    Supports raw Netscape format or Base64 encoded Netscape cookies.
    """
    cookie_content = (custom_cookie_str or '').strip()
    
    if not cookie_content:
        cookie_content = os.getenv('YT_COOKIES', '').strip()
    
    if not cookie_content:
        # Check base64 encoded env var
        b64_content = os.getenv('YT_COOKIES_BASE64', '').strip()
        if b64_content:
            try:
                cookie_content = base64.b64decode(b64_content).decode('utf-8')
            except Exception:
                pass

    if cookie_content:
        try:
            temp_cookie_file = os.path.join(tempfile.gettempdir(), 'yt_cookies.txt')
            with open(temp_cookie_file, 'w', encoding='utf-8') as f:
                f.write(cookie_content)
            return temp_cookie_file
        except Exception as e:
            print(f"Failed to write cookie file: {e}")
    return None

def get_ydl_options(cookie_path=None, quality='best'):
    """
    Generates yt-dlp configurations tailored for YouTube bot-bypassing and TikTok extractor fixes.
    """
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

    format_rule = 'best[ext=mp4]/best'
    if quality == 'audio':
        format_rule = 'bestaudio/best'
    elif quality == 'hd':
        format_rule = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif quality == 'sd':
        format_rule = 'worstvideo[ext=mp4]+worstaudio/worst[ext=mp4]/worst'

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': format_rule,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'geo_bypass': True,
        'http_headers': {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
        },
        # Rotate TV/Android VR/iOS signatures to bypass datacenter IP bot blocks
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_embedded', 'android_vr', 'ios', 'mweb', 'web_embedded'],
                'skip': ['hls', 'dash']
            },
            'tiktok': {
                'app_version': ['34.0.0'],
                'manifest_app_version': ['34.0.0']
            }
        }
    }

    if cookie_path and os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path

    return ydl_opts

@app.route('/api/fetch', methods=['POST'])
def fetch_video():
    try:
        data = request.get_json() or {}
        url = data.get('url', '').strip()
        quality = data.get('quality', 'best')
        custom_cookies = data.get('cookies', '').strip()

        if not url:
            return jsonify({'success': False, 'error': 'Video URL is required.'}), 400

        cookie_path = setup_cookies(custom_cookies)
        ydl_opts = get_ydl_options(cookie_path, quality)

        # Primary extraction attempt
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as primary_error:
            # Fallback with tv_embedded and android_creator client fallback
            fallback_opts = ydl_opts.copy()
            fallback_opts['extractor_args'] = {
                'youtube': {
                    'player_client': ['tv_embedded', 'android_creator', 'mweb', 'android'],
                }
            }
            fallback_opts['format'] = 'best'
            
            with yt_dlp.YoutubeDL(fallback_opts) as ydl_fallback:
                info = ydl_fallback.extract_info(url, download=False)

        if 'entries' in info and len(info['entries']) > 0:
            info = info['entries'][0]

        title = info.get('title', 'Video Media')
        thumbnail = info.get('thumbnail') or (info.get('thumbnails', [{}])[-1].get('url') if info.get('thumbnails') else '')
        duration = info.get('duration', 0)
        ext = info.get('ext', 'mp4')
        uploader = info.get('uploader', info.get('uploader_id', 'Unknown Creator'))
        views = info.get('view_count', 0)

        download_url = info.get('url', '')
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')

        return jsonify({
            'success': True,
            'title': title,
            'safe_title': safe_title,
            'thumbnail': thumbnail,
            'duration': duration,
            'uploader': uploader,
            'views': views,
            'download_url': download_url,
            'ext': ext,
            'proxy_url': f"/api/stream?url={requests.utils.quote(download_url)}&filename={safe_title}.{ext}" if download_url else None
        })

    except Exception as e:
        error_msg = str(e)
        
        if "Sign in to confirm you're not a bot" in error_msg:
            error_msg = "YouTube Bot Block detected. Click 'Bypass Bot Fix' at top right to paste your YouTube cookies."
        elif "Video not available" in error_msg or "status code 0" in error_msg:
            error_msg = "TikTok Provider blocked server request. Try refreshing or updating cookies in the Bypass Bot Fix menu."

        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/stream', methods=['GET'])
def stream_media():
    """
    Serves a lightweight HTML download bridge with no-referrer policy to bypass 
    Vercel 4.5MB serverless response limits and prevent ERR_INVALID_RESPONSE.
    """
    target_url = request.args.get('url')
    filename = request.args.get('filename', 'video.mp4')

    if not target_url:
        return jsonify({'error': 'Missing media URL'}), 400

    safe_filename = re.sub(r'[^\w\.-]', '_', filename).strip('_') or 'video.mp4'

    # Return HTML download trigger bridge with no-referrer
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="referrer" content="no-referrer">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Downloading {safe_filename}...</title>
  <style>
    body {{
      background-color: #080d1a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      text-align: center;
    }}
    .card {{
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid rgba(255, 255, 255, 0.1);
      padding: 2rem;
      border-radius: 1rem;
      max-width: 480px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    a.btn {{
      display: inline-block;
      margin-top: 1rem;
      padding: 0.75rem 1.5rem;
      background: #06b6d4;
      color: #0f172a;
      font-weight: bold;
      text-decoration: none;
      border-radius: 0.5rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h2 style="margin-top:0;">⚡ Preparing Download...</h2>
    <p style="color:#94a3b8;font-size:14px;">Your download for <strong>{safe_filename}</strong> will start automatically without referrer blocks.</p>
    <a id="downloadLink" class="btn" href="{target_url}" download="{safe_filename}" rel="noreferrer" referrerpolicy="no-referrer">Click to Download Directly</a>
  </div>
  <script>
    window.onload = function() {{
      const link = document.getElementById('downloadLink');
      link.click();
    }};
  </script>
</body>
</html>"""

    return Response(html_content, mimetype='text/html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
