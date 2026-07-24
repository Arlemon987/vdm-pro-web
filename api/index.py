from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import yt_dlp
import requests

app = Flask(__name__)
CORS(app)

@app.route('/api/fetch', methods=['POST'])
def fetch_video():
    try:
        data = request.get_json() or {}
        url = data.get('url')

        if not url:
            return jsonify({'success': False, 'error': 'Video URL is required.'}), 400

        # Configure yt-dlp to bypass YouTube bot checks on Vercel servers
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best[ext=mp4]/best',
            'nocheckcertificate': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'android', 'mweb']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            title = info.get('title', 'Video')
            thumbnail = info.get('thumbnail', '')
            duration = info.get('duration', 0)
            download_url = info.get('url', '')
            ext = info.get('ext', 'mp4')

            return jsonify({
                'success': True,
                'title': title,
                'thumbnail': thumbnail,
                'duration': duration,
                'download_url': download_url,
                'ext': ext
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/proxy', methods=['GET'])
def proxy_stream():
    """Proxies media streams to bypass TikTok/YouTube 403 Forbidden hotlink blocks."""
    media_url = request.args.get('url')
    if not media_url:
        return "Missing media URL", 400

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Referer': 'https://www.tiktok.com/' if any(k in media_url for k in ['tiktok', 'v19', 'musically', 'byteoversea']) else 'https://www.youtube.com/'
    }

    try:
        r = requests.get(media_url, headers=headers, stream=True)
        
        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        response = Response(generate(), content_type=r.headers.get('content-type', 'video/mp4'))
        response.headers['Content-Disposition'] = 'attachment; filename="video.mp4"'
        return response
    except Exception as e:
        return f"Proxy download error: {str(e)}", 500


if __name__ == '__main__':
    app.run(port=5000)
