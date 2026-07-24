from flask import Flask, request, jsonify, Response, stream_with_context
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
    Proxies video data stream to prevent cross-origin media blocks and CDNs blocking direct downloads.
    """
    target_url = request.args.get('url')
    filename = request.args.get('filename', 'video.mp4')

    if not target_url:
        return jsonify({'error': 'Missing media URL'}), 400

    try:
        req_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        
        r = requests.get(target_url, headers=req_headers, stream=True, timeout=15)
        
        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        headers = {
            'Content-Type': r.headers.get('Content-Type', 'video/mp4'),
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': r.headers.get('Content-Length', '')
        }

        return Response(stream_with_context(generate()), headers=headers, status=r.status_code)

    except Exception as e:
        return jsonify({'error': f'Failed to stream media: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
