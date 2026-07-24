import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

# Standard browser headers to bypass 403 blocks (crucial for TikTok)
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Sec-Fetch-Mode': 'navigate',
}

# Make the route catch-all so it doesn't matter if the frontend calls /api, /api/, or /api/extract
@app.route('/api', methods=['GET', 'POST'])
@app.route('/api/', methods=['GET', 'POST'])
@app.route('/api/<path:path>', methods=['GET', 'POST'])
def extract_video(path=None):
    url = None
    if request.method == 'POST':
        url = request.json.get('url')
    else:
        url = request.args.get('url')

    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400

    ydl_opts = {
        'format': 'best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'http_headers': BROWSER_HEADERS
    }

    # 1. YouTube Bypass: Use mobile clients (ios, android) to bypass web bot detection
    if 'youtube.com' in url or 'youtu.be' in url:
        ydl_opts['extractor_args'] = {'youtube': {'player_client': ['ios', 'android']}}
    
    # 2. TikTok Bypass: Add specific TikTok referer to headers
    elif 'tiktok' in url:
        ydl_opts['http_headers']['Referer'] = 'https://www.tiktok.com/'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Sometimes the direct url is in 'url', sometimes in 'formats'
            direct_url = None
            if 'url' in info:
                direct_url = info['url']
            elif 'formats' in info and len(info['formats']) > 0:
                # Get the last (usually best) format URL
                direct_url = info['formats'][-1].get('url')
            
            return jsonify({
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'url': direct_url,
                'uploader': info.get('uploader')
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
