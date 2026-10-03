FROM python:3.12-slim
# ffmpeg = 音视频；libreoffice = Office 文档；7zip = rar/iso 等；fonts = 中文文档转 PDF 不乱码
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg 7zip libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV MINIBOX_HOST=0.0.0.0 MINIBOX_PORT=8765 \
    MINIBOX_LIBRARY=/files MINIBOX_DATA=/data
VOLUME ["/files", "/data"]
EXPOSE 8765
CMD ["python", "app.py", "--no-browser"]
