FROM python:3.11-slim

# Ngăn Python tạo file .pyc và đảm bảo log hiển thị ngay lập tức
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cài đặt FFmpeg và các thư viện cần thiết cho âm thanh
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    libsodium-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc
WORKDIR /app

# Copy requirements và cài đặt dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn vào container
COPY . .

# Chạy bot
CMD ["python", "bot.py"]
