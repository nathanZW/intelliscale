#!/bin/bash

# Simple IntelliScale Startup Script - Shows logs in terminal
# Use this for development when you want to see logs directly

PROJECT_DIR=$(pwd)  #Uses current directory automatically than it being hardcoded for a specific directory just in case another dev hops onto the project
VENV_PATH="$PROJECT_DIR/.venv"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}Starting IntelliScale Application...${NC}"

# Change to project directory
cd "$PROJECT_DIR"

# Activate virtual environment
source "$VENV_PATH/bin/activate"

# Run migrations
echo -e "${YELLOW}Running migrations...${NC}"
python manage.py migrate --noinput

# Collect static assets for WhiteNoise
echo -e "${YELLOW}Collecting static files...${NC}"
python manage.py collectstatic --noinput

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    jobs -p | xargs -r kill
    pkill -f "python.*manage.py runserver" 2>/dev/null || true
    pkill -f "celery.*worker" 2>/dev/null || true
    pkill -f "celery.*beat" 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

echo -e "${GREEN}Starting services...${NC}"
echo -e "${BLUE}Django: http://localhost:8000${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
echo

# Start all services with output
{
    echo "=== DJANGO RUNSERVER ==="
    python manage.py runserver 0.0.0.0:8000 &
    
    echo "=== CELERY WORKER ==="
    celery -A core worker --loglevel=info &
    
    echo "=== CELERY BEAT ==="
    celery -A core beat --loglevel=info &
    
    wait
}
