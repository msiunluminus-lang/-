import os
import sys
import cv2
import ffmpeg
import numpy as np
import glob
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# 렌더 서버 내의 절대 경로 기준 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKER_FOLDER = os.path.join(BASE_DIR, "킬마크")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

# 폴더 자동 생성
for folder in [MARKER_FOLDER, UPLOAD_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

def cut_video(input_path, output_path, start_time, duration):
    try:
        # 렌더 환경에 설치될 FFmpeg 바이너리 경로 지정
        ffmpeg_bin_path = os.path.join(BASE_DIR, "ffmpeg_bin", "ffmpeg")
        if not os.path.exists(ffmpeg_bin_path):
            ffmpeg_bin_path = "ffmpeg" # 로컬 테스트용 기본 명령

        (
            ffmpeg
            .input(input_path, ss=start_time)
            .output(output_path, t=duration, c='copy')
            .run(cmd=ffmpeg_bin_path, overwrite_output=True, capture_stdout=True, capture_stderr=True)
        )
        return True
    except Exception as e:
        print(f"❌ FFmpeg 자르기 실패: {e}")
        return False

def run_analysis_backend(video_path, pre_roll=7.0, max_cool_down=5.0, min_kills=1):
    if not os.path.exists(video_path):
        return {"status": "error", "message": "업로드된 영상 파일을 찾을 수 없습니다."}

    marker_files = []
    for ext in ["*.png", "*.PNG", "*.jpg", "*.JPG", "*.jpeg"]:
        marker_files.extend(glob.glob(os.path.join(MARKER_FOLDER, ext)))
    
    if not marker_files:
        return {"status": "error", "message": "서버의 '킬마크' 폴더에 기준 이미지 파일이 없습니다."}

    templates = []
    for f in marker_files:
        try:
            img_array = np.fromfile(f, np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                templates.append(img)
        except:
            pass

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    if total_frames == 0 or fps == 0:
        return {"status": "error", "message": "영상을 읽을 수 없거나 손상된 파일입니다."}

    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    roi_x1, roi_x2 = int(frame_width * 0.25), int(frame_width * 0.75)
    roi_y1, roi_y2 = int(frame_height * 0.45), int(frame_height * 0.85)

    kill_segments = []  
    is_combat = False
    combat_start = 0
    combat_kills = 0    
    missing_duration = 0.0
    CHECK_INTERVAL = 5 
    frame_count = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        
        if frame_count % CHECK_INTERVAL == 0:
            current_time = frame_count / fps
            roi_frame = frame[roi_y1:roi_y2, roi_x1:roi_x2]
            gray_roi = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
            
            any_marker_detected = False
            for template in templates:
                res = cv2.matchTemplate(gray_roi, template, cv2.TM_CCOEFF_NORMED)
                loc = np.where(res >= 0.6)
                if len(loc[0]) > 0:
                    any_marker_detected = True
                    break
            
            if any_marker_detected:
                missing_duration = 0.0
                if not is_combat:
                    is_combat = True
                    combat_start = max(0, current_time - pre_roll) 
                    combat_kills = 1
                else:
                    if frame_count % (CHECK_INTERVAL * 6) == 0: 
                        combat_kills += 1
            
            elif is_combat:
                missing_duration += (CHECK_INTERVAL / fps)
                if missing_duration >= max_cool_down: 
                    is_combat = False
                    combat_end = current_time
                    if combat_kills >= min_kills:
                        kill_segments.append([combat_start, combat_end])

    if is_combat and combat_kills >= min_kills:
        kill_segments.append([combat_start, frame_count / fps])
    cap.release()

    highlight_list = []
    v_name_only = os.path.splitext(os.path.basename(video_path))[0]

    for i, segment in enumerate(kill_segments):
        start_seconds = int(segment[0])
        duration = int(segment[1] - segment[0])
        if duration < 3: continue
        
        minutes = start_seconds // 60
        seconds = start_seconds % 60
        timestamp_str = f"{minutes:02d}:{seconds:02d}"
        
        # 렌더 서버 내 다운로드 폴더에 하이라이트 영상 저장
        clip_name = os.path.join(UPLOAD_FOLDER, f"{v_name_only}_하이라이트_{i+1}.mp4")
        cut_video(video_path, clip_name, segment[0], duration)

        highlight_list.append({
            "id": i + 1,
            "timestamp": timestamp_str,
            "description": f"🔥 {i+1}번 매드무비 구간 생성 완료 ({duration}초)",
            "download_url": f"/download?file={v_name_only}_하이라이트_{i+1}.mp4"
        })

    return {"status": "success", "highlights": highlight_list}

# 🌐 렌더 전용 웹/API 통합 서버 라우팅
class RenderServer(BaseHTTPRequestHandler):
    def _set_headers(self, content_type='application/json'):
        self.send_response(200)
        self.send_header('Content-type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_GET(self):
        url_path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)

        # 1. 폰으로 접속했을 때 보여줄 메인 화면 (index.html 역할)
        if url_path == "/" or url_path == "/index.html":
            self._set_headers('text/html; charset=utf-8')
            html_path = os.path.join(BASE_DIR, "index.html")
            with open(html_path, "r", encoding="utf-8") as f:
                self.wfile.write(f.read().encode('utf-8'))

        # 2. 분석 결과 타임라인 요청 처리 API
        elif url_path == "/analyze":
            self._set_headers()
            v_file = query.get("file", [""])[0]
            full_path = os.path.join(UPLOAD_FOLDER, v_file)
            result = run_analysis_backend(full_path)
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

        # 3. 편집 완료된 하이라이트 영상 다운로드 기능
        elif url_path == "/download":
            f_name = query.get("file", [""])[0]
            file_path = os.path.join(UPLOAD_FOLDER, f_name)
            if os.path.exists(file_path):
                self.send_response(200)
                self.send_header('Content-type', 'video/mp4')
                self.send_header('Content-Disposition', f'attachment; filename="{f_name}"')
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "파일을 찾을 수 없습니다.")

    # 4. 스마트폰에서 업로드한 영상 파일 서버에 저장하기
    def do_POST(self):
        if self.path == "/upload":
            content_length = int(self.headers['Content-Length'])
            # 간이 멀티파트 바운더리 분리 생략, 순수 바이너리 업로드 방식 적용
            file_data = self.rfile.read(content_length)
            
            filename = self.headers.get('X-File-Name', 'uploaded_video.mp4')
            saved_path = os.path.join(UPLOAD_FOLDER, filename)
            
            with open(saved_path, "wb") as f:
                f.write(file_data)
                
            self._set_headers()
            self.wfile.write(json.dumps({"status": "success", "filename": filename}).encode('utf-8'))

if __name__ == "__main__":
    # 렌더 서버는 제공하는 환경변수 PORT를 반드시 따라야 통신이 뚫립니다.
    port = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(('', port), RenderServer)
    print(f"🚀 렌더 매드무비 서버 정상 구동 중... (Port: {port})")
    httpd.serve_forever()