#!/bin/bash

# Tên container của bot trong docker-compose.yml
CONTAINER_NAME="discord-music-bot"
DISCORD_GATEWAY="gateway.discord.gg"

echo "=================================================="
echo " 🔍 ĐANG KIỂM TRA TRẠNG THÁI DISCORD MUSIC BOT "
echo "=================================================="

# 1. Kiểm tra container có đang chạy không
RUNNING=$(docker inspect -f '{{.State.Running}}' $CONTAINER_NAME 2>/dev/null)

if [ "$RUNNING" = "true" ]; then
    echo -e "🟢 Trạng thái Container: ĐANG CHẠY (Running)"
    
    # Lấy thời gian chạy của container
    STARTED_AT=$(docker inspect -f '{{.State.StartedAt}}' $CONTAINER_NAME)
    echo "   Khởi chạy lúc: $STARTED_AT"
    
    # Lấy tài nguyên tiêu thụ (CPU/RAM)
    STATS=$(docker stats --no-stream --format "CPU: {{.CPUPerc}} | RAM: {{.MemUsage}}" $CONTAINER_NAME)
    echo "   Tài nguyên sử dụng: $STATS"
else
    echo -e "🔴 Trạng thái Container: ĐÃ DỪNG (Stopped) hoặc CHƯA TẠO"
fi

echo "--------------------------------------------------"

# 2. Kiểm tra Ping kết nối từ Server tới Discord Gateway
echo "📶 Đang đo ping tới Discord Gateway ($DISCORD_GATEWAY)..."
PING_RES=$(ping -c 4 $DISCORD_GATEWAY 2>/dev/null)

if [ $? -eq 0 ]; then
    # Lấy dòng kết quả ping trung bình (rtt min/avg/max/mdev)
    RTT=$(echo "$PING_RES" | tail -n 1)
    echo "   Kết nối mạng: TỐT"
    echo "   Chi tiết: $RTT"
else
    echo "   ❌ Lỗi: Không thể kết nối tới Discord Gateway (Mất mạng hoặc bị chặn)"
fi

echo "--------------------------------------------------"

# 3. Hiển thị 5 dòng logs mới nhất của Bot
if [ "$RUNNING" = "true" ]; then
    echo "📋 5 dòng logs mới nhất của bot:"
    echo "----------------------------------------"
    docker logs --tail 5 $CONTAINER_NAME
    echo "----------------------------------------"
fi

echo "=================================================="
