FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    libsodium-dev \
    libopus-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Tạo thư mục lưu trữ playlist và file nhạc upload
RUN mkdir -p /app/playlists /app/temp_audio

# Mount volumes để dữ liệu playlist tồn tại khi restart
VOLUME ["/app/playlists", "/app/temp_audio"]

CMD ["python", "bot.py"]
