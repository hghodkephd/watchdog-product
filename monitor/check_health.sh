#!/bin/bash
# Govee Monitor - System Health Check Script
# Displays status of all services and system resources

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "  Govee Monitor Health Check"
echo "  $(date)"
echo "=========================================="
echo ""

# Function to check if service is running
check_service() {
    local service=$1
    if systemctl is-active --quiet "$service"; then
        echo -e "${GREEN}✓ $service is running${NC}"
        return 0
    else
        echo -e "${RED}✗ $service is NOT running${NC}"
        return 1
    fi
}

# Check services
echo -e "${BLUE}Service Status:${NC}"
check_service govee-monitor.service
check_service govee-dashboard.service
echo ""

# Check if services are enabled for boot
echo -e "${BLUE}Boot Configuration:${NC}"
if systemctl is-enabled --quiet govee-monitor.service; then
    echo -e "${GREEN}✓ BLE monitoring will start on boot${NC}"
else
    echo -e "${YELLOW}⚠ BLE monitoring NOT enabled for boot${NC}"
fi

if systemctl is-enabled --quiet govee-dashboard.service; then
    echo -e "${GREEN}✓ Dashboard will start on boot${NC}"
else
    echo -e "${YELLOW}⚠ Dashboard NOT enabled for boot${NC}"
fi
echo ""

# Check database
echo -e "${BLUE}Database Status:${NC}"
DB_PATH="$HOME/govee-monitor/data/data.sqlite3"
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    RECORD_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM readings;" 2>/dev/null || echo "N/A")
    LAST_READING=$(sqlite3 "$DB_PATH" "SELECT datetime(MAX(ts), 'unixepoch', 'localtime') FROM readings;" 2>/dev/null || echo "N/A")
    
    echo -e "${GREEN}✓ Database exists${NC}"
    echo "  Location: $DB_PATH"
    echo "  Size: $DB_SIZE"
    echo "  Total readings: $RECORD_COUNT"
    echo "  Last reading: $LAST_READING"
    
    # Check if data is recent (within last 10 minutes)
    if [ "$LAST_READING" != "N/A" ]; then
        LAST_TS=$(sqlite3 "$DB_PATH" "SELECT MAX(ts) FROM readings;" 2>/dev/null)
        CURRENT_TS=$(date +%s)
        AGE=$((CURRENT_TS - ${LAST_TS%.*}))
        
        if [ $AGE -lt 600 ]; then
            echo -e "${GREEN}  ✓ Data is fresh (${AGE}s old)${NC}"
        else
            echo -e "${YELLOW}  ⚠ Data may be stale (${AGE}s old)${NC}"
        fi
    fi
else
    echo -e "${RED}✗ Database not found at $DB_PATH${NC}"
fi
echo ""

# Check network access
echo -e "${BLUE}Network Status:${NC}"
IP_ADDR=$(hostname -I | awk '{print $1}')
echo "  Local IP: $IP_ADDR"
echo "  Dashboard URL: http://$IP_ADDR:8501"

# Check if port 8501 is listening
if netstat -tln 2>/dev/null | grep -q ":8501 " || ss -tln 2>/dev/null | grep -q ":8501 "; then
    echo -e "${GREEN}  ✓ Port 8501 is listening${NC}"
else
    echo -e "${RED}  ✗ Port 8501 is NOT listening${NC}"
fi
echo ""

# Check system resources
echo -e "${BLUE}System Resources:${NC}"
CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)
MEM_INFO=$(free -h | grep Mem)
MEM_USED=$(echo "$MEM_INFO" | awk '{print $3}')
MEM_TOTAL=$(echo "$MEM_INFO" | awk '{print $2}')
MEM_PERCENT=$(free | grep Mem | awk '{printf "%.1f", $3/$2 * 100.0}')

echo "  CPU Usage: ${CPU_USAGE}%"
echo "  Memory: $MEM_USED / $MEM_TOTAL (${MEM_PERCENT}%)"

# Check disk space
DISK_INFO=$(df -h "$HOME" | tail -1)
DISK_USED=$(echo "$DISK_INFO" | awk '{print $3}')
DISK_TOTAL=$(echo "$DISK_INFO" | awk '{print $2}')
DISK_PERCENT=$(echo "$DISK_INFO" | awk '{print $5}' | cut -d'%' -f1)

echo "  Disk: $DISK_USED / $DISK_TOTAL (${DISK_PERCENT}%)"

# Warn if disk is >90% full
if [ "$DISK_PERCENT" -gt 90 ]; then
    echo -e "${RED}  ⚠ Warning: Disk usage is high! Consider archiving old data.${NC}"
fi

# Warn if memory is >80% full
if (( $(echo "$MEM_PERCENT > 80" | bc -l) )); then
    echo -e "${YELLOW}  ⚠ Warning: Memory usage is high!${NC}"
fi
echo ""

# Check Bluetooth
echo -e "${BLUE}Bluetooth Status:${NC}"
if command -v bluetoothctl &> /dev/null; then
    BT_STATUS=$(bluetoothctl show 2>/dev/null | grep "Powered" | awk '{print $2}')
    if [ "$BT_STATUS" = "yes" ]; then
        echo -e "${GREEN}✓ Bluetooth is powered on${NC}"
    else
        echo -e "${RED}✗ Bluetooth is powered off${NC}"
        echo "  Run: sudo bluetoothctl power on"
    fi
else
    echo -e "${YELLOW}⚠ bluetoothctl not found${NC}"
fi
echo ""

# Check recent logs for errors
echo -e "${BLUE}Recent Errors (last 10 lines):${NC}"
BLE_ERRORS=$(journalctl -u govee-monitor.service --since "1 hour ago" -p err -n 10 --no-pager 2>/dev/null | tail -5)
DASH_ERRORS=$(journalctl -u govee-dashboard.service --since "1 hour ago" -p err -n 10 --no-pager 2>/dev/null | tail -5)

if [ -z "$BLE_ERRORS" ] && [ -z "$DASH_ERRORS" ]; then
    echo -e "${GREEN}✓ No recent errors${NC}"
else
    if [ ! -z "$BLE_ERRORS" ]; then
        echo -e "${RED}BLE Monitor errors:${NC}"
        echo "$BLE_ERRORS"
    fi
    if [ ! -z "$DASH_ERRORS" ]; then
        echo -e "${RED}Dashboard errors:${NC}"
        echo "$DASH_ERRORS"
    fi
fi
echo ""

# Check process PIDs
echo -e "${BLUE}Process Information:${NC}"
BLE_PID=$(systemctl show -p MainPID govee-monitor.service 2>/dev/null | cut -d'=' -f2)
DASH_PID=$(systemctl show -p MainPID govee-dashboard.service 2>/dev/null | cut -d'=' -f2)

if [ "$BLE_PID" != "0" ]; then
    echo "  BLE Monitor PID: $BLE_PID"
    if ps -p "$BLE_PID" > /dev/null 2>&1; then
        BLE_CPU=$(ps -p "$BLE_PID" -o %cpu= 2>/dev/null | xargs)
        BLE_MEM=$(ps -p "$BLE_PID" -o %mem= 2>/dev/null | xargs)
        echo "    CPU: ${BLE_CPU}%  Memory: ${BLE_MEM}%"
    fi
fi

if [ "$DASH_PID" != "0" ]; then
    echo "  Dashboard PID: $DASH_PID"
    if ps -p "$DASH_PID" > /dev/null 2>&1; then
        DASH_CPU=$(ps -p "$DASH_PID" -o %cpu= 2>/dev/null | xargs)
        DASH_MEM=$(ps -p "$DASH_PID" -o %mem= 2>/dev/null | xargs)
        echo "    CPU: ${DASH_CPU}%  Memory: ${DASH_MEM}%"
    fi
fi
echo ""

# Summary
echo "=========================================="
echo -e "${BLUE}Quick Actions:${NC}"
echo "View live logs:"
echo "  sudo journalctl -u govee-monitor.service -f"
echo ""
echo "Restart services:"
echo "  sudo systemctl restart govee-monitor.service"
echo "  sudo systemctl restart govee-dashboard.service"
echo ""
echo "View detailed status:"
echo "  sudo systemctl status govee-monitor.service"
echo ""
echo "=========================================="
