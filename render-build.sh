#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# 렌더 리눅스 서버에 FFmpeg 수동 심기
mkdir -p ffmpeg_bin
cd ffmpeg_bin
wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xf ffmpeg-release-amd64-static.tar.xz --strip-components=1
export PATH=$PATH:$(pwd)
cd ..