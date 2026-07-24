from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

@app.route('/api/fetch', methods=['POST'])
def fetch_video():
    try:
        data = request.get_json() or {}
        url = data.get('url')

        if not url:
            return jsonify({'success': False, 'error': 'Video URL is required.'}), 400

        # Configure yt-dlp to extract progressive streams (combined video + audio)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best[ext=mp4]/best',
            'nocheckcertificate': True,
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

if __name__ == '__main__':
    app.run(port=5000)