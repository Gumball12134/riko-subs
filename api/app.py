from flask import Flask, request, jsonify, Response, redirect, render_template
import requests, os, json, hashlib, time, hmac, base64
from bs4 import BeautifulSoup

app = Flask(__name__)
DATA_FILE = "videos.json"
COMMENTS_FILE = "comments.json"
SECRET_KEY = b"X8Cz6vvz0A9TDLxe1bRgPlmTVBkpjRbUtKvXp+MUGy0="

# تحميل البيانات
def load_data():
    if not os.path.exists(DATA_FILE): return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_comments():
    if not os.path.exists(COMMENTS_FILE): return {}
    with open(COMMENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_comments(data):
    with open(COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def generate_hash(original_url):
    return hashlib.sha256(original_url.encode()).hexdigest()

def upload_to_kraken(file_stream, filename):
    url = "https://hs10.krakencloud.net/_uploader/gallery/upload"
    files = [('files[]', (filename, file_stream, 'application/octet-stream'))]
    headers = {
        'User-Agent': "Mozilla/5.0",
        'Accept': "application/json",
        'origin': "https://krakenfiles.com",
        'referer': "https://krakenfiles.com/"
    }
    res = requests.post(url, files=files, headers=headers)
    j = res.json()
    if "files" in j and j["files"]:
        info = j["files"][0]
        if info.get("error") == "":
            return {"status": "success", "original_url": "https://krakenfiles.com" + info["url"]}
    return {"status": "error", "message": "فشل في الرفع"}

def extract_direct_video_url(view_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(view_url, headers=headers)
    if res.status_code != 200: return None
    soup = BeautifulSoup(res.text, "html.parser")
    tag = soup.find("video")
    if tag and tag.has_attr("data-src-url"):
        return tag["data-src-url"]
    return None

def generate_download_token(video_hash, expire_in_seconds=600):
    expire_at = int(time.time()) + expire_in_seconds
    data = f"{video_hash}:{expire_at}"
    sig = hmac.new(SECRET_KEY, data.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(data.encode() + b"." + sig).decode()

def verify_download_token(token):
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
        data, sig = decoded.rsplit(b".", 1)
        expected = hmac.new(SECRET_KEY, data, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected): return None
        video_hash, expire_at = data.decode().split(":")
        if int(time.time()) > int(expire_at): return None
        return video_hash
    except: return None

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "ارفع ملف"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "الملف فارغ"}), 400
    result = upload_to_kraken(file.stream, file.filename)
    if result["status"] == "success":
        original_url = result["original_url"]
        video_hash = generate_hash(original_url)
        data = load_data()
        data[video_hash] = {"original_url": original_url}
        save_data(data)
        page_link = request.host_url.rstrip("/") + f"/files/{video_hash}"
        return jsonify({"status": "success", "page_url": page_link})
    return jsonify(result), 500

@app.route("/files/<video_hash>")
def video_page(video_hash):
    data = load_data()
    if video_hash not in data:
        return "❌ الفيديو غير موجود", 404
    comments_data = load_comments()
    comments = comments_data.get(video_hash, [])
    stream_url = request.host_url.rstrip("/") + f"/files/{video_hash}/video.mp4"
    token = generate_download_token(video_hash)
    download_url = request.host_url.rstrip("/") + f"/files/{video_hash}/download?token={token}"
    return render_template("video.html", stream_url=stream_url, download_url=download_url, comments=comments, video_hash=video_hash)

@app.route("/files/<video_hash>/video.mp4")
def stream_video(video_hash):
    data = load_data()
    if video_hash not in data:
        return "❌ لم يتم العثور على الفيديو", 404
    view_url = data[video_hash]["original_url"]
    direct_url = extract_direct_video_url(view_url)
    if not direct_url:
        return "❌ فشل في استخراج رابط الفيديو", 404
    def generate():
        with requests.get(direct_url, stream=True) as r:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk: yield chunk
    return Response(generate(), content_type="video/mp4")

@app.route("/files/<video_hash>/download")
def download_video(video_hash):
    token = request.args.get("token", "")
    valid = verify_download_token(token)
    if not valid or valid != video_hash:
        return "❌ رابط غير صالح", 403
    data = load_data()
    if video_hash not in data:
        return "❌ لم يتم العثور على الفيديو", 404
    view_url = data[video_hash]["original_url"]
    direct_url = extract_direct_video_url(view_url)
    if not direct_url:
        return "❌ فشل في التحميل", 500
    return redirect(direct_url)

@app.route("/files/<video_hash>/comment", methods=["POST"])
def add_comment(video_hash):
    name = request.form.get("name", "").strip()
    msg = request.form.get("message", "").strip()
    delete_code = request.form.get("delete_code", "").strip()
    if not name or not msg or not delete_code:
        return "❌ جميع الحقول مطلوبة", 400
    comments = load_comments()
    if video_hash not in comments:
        comments[video_hash] = []
    comments[video_hash].append({
        "name": name,
        "message": msg,
        "delete_code": delete_code
    })
    save_comments(comments)
    return redirect(f"/files/{video_hash}")

@app.route("/files/<video_hash>/delete_comment", methods=["POST"])
def delete_comment(video_hash):
    code = request.form.get("code", "").strip()
    target_msg = request.form.get("comment_message", "").strip()
    comments = load_comments()
    if video_hash in comments:
        comments[video_hash] = [
            c for c in comments[video_hash]
            if not (c.get("message") == target_msg and c.get("delete_code") == code)
        ]
        save_comments(comments)
    return redirect(f"/files/{video_hash}")
