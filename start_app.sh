#!/bin/bash

# IntelliScale Application Startup Script
# This script starts Django runserver, Celery worker, and Celery beat with logging

set -e  # Exit on any error

# Configuration
PROJECT_DIR="/home/nathan/Documents/eport/intelliscale"
VENV_PATH="$PROJECT_DIR/.venv"  # Adjust this to your virtual environment path
LOG_DIR="$PROJECT_DIR/logs"
DJANGO_PORT=8000

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"
}

print_info() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $1${NC}"
}

# Function to cleanup processes on exit
cleanup() {
    print_warning "Shutting down all services..."
    
    # Kill all background jobs
    jobs -p | xargs -r kill
    
    # Kill any remaining Django/Celery processes
    pkill -f "python.*manage.py runserver" 2>/dev/null || true
    pkill -f "celery.*worker" 2>/dev/null || true
    pkill -f "celery.*beat" 2>/dev/null || true
    
    print_status "All services stopped."
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Check if virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
    print_error "Virtual environment not found at $VENV_PATH"
    print_info "Please create a virtual environment or update the VENV_PATH in this script"
    exit 1
fi

# Create logs directory
mkdir -p "$LOG_DIR"

# Change to project directory
cd "$PROJECT_DIR"

print_status "Starting IntelliScale Application..."
print_info "Project Directory: $PROJECT_DIR"
print_info "Virtual Environment: $VENV_PATH"
print_info "Logs Directory: $LOG_DIR"

# Activate virtual environment
print_status "Activating virtual environment..."
source "$VENV_PATH/bin/activate"

# Check if Django is available
if ! python -c "import django" 2>/dev/null; then
    print_error "Django not found in virtual environment"
    exit 1
fi

# Check if Celery is available
if ! python -c "import celery" 2>/dev/null; then
    print_error "Celery not found in virtual environment"
    exit 1
fi

# Run Django migrations
print_status "Running Django migrations..."
python manage.py migrate --noinput

# Collect static files (if needed)
# print_status "Collecting static files..."
# python manage.py collectstatic --noinput

# Start Django development server
print_status "Starting Django development server on port $DJANGO_PORT..."
python manage.py runserver 0.0.0.0:$DJANGO_PORT > "$LOG_DIR/django.log" 2>&1 &
DJANGO_PID=$!
print_info "Django server started with PID: $DJANGO_PID"

# Wait a moment for Django to start
sleep 2

# Start Celery worker
print_status "Starting Celery worker..."
celery -A core worker --loglevel=info > "$LOG_DIR/celery_worker.log" 2>&1 &
CELERY_WORKER_PID=$!
print_info "Celery worker started with PID: $CELERY_WORKER_PID"

# Wait a moment for worker to start
sleep 2

# Start Celery beat
print_status "Starting Celery beat scheduler..."
celery -A core beat --loglevel=info > "$LOG_DIR/celery_beat.log" 2>&1 &
CELERY_BEAT_PID=$!
print_info "Celery beat started with PID: $CELERY_BEAT_PID"

# Display startup summary
echo
print_status "=== IntelliScale Application Started Successfully ==="
print_info "Django Server: http://localhost:$DJANGO_PORT"
print_info "Django Logs: $LOG_DIR/django.log"
print_info "Celery Worker Logs: $LOG_DIR/celery_worker.log"
print_info "Celery Beat Logs: $LOG_DIR/celery_beat.log"
echo
print_info "Process IDs:"
print_info "  Django: $DJANGO_PID"
print_info "  Celery Worker: $CELERY_WORKER_PID"
print_info "  Celery Beat: $CELERY_BEAT_PID"
echo
print_warning "Press Ctrl+C to stop all services"
echo

# Function to display logs
show_logs() {
    echo
    print_status "=== LIVE LOGS (Press Ctrl+C to stop) ==="
    echo
    
    # Start tailing logs in background with labels
    (
        echo -e "${GREEN}=== DJANGO LOGS ===${NC}"
        tail -f "$LOG_DIR/django.log" | sed "s/^/[DJANGO] /"
    ) &
    
    (
        echo -e "${BLUE}=== CELERY WORKER LOGS ===${NC}"
        tail -f "$LOG_DIR/celery_worker.log" | sed "s/^/[WORKER] /"
    ) &
    
    (
        echo -e "${YELLOW}=== CELERY BEAT LOGS ===${NC}"
        tail -f "$LOG_DIR/celery_beat.log" | sed "s/^/[BEAT] /"
    ) &
    
    # Wait for interrupt
    wait
}

# Check if services are running
check_services() {
    local all_running=true
    
    if ! kill -0 $DJANGO_PID 2>/dev/null; then
        print_error "Django server is not running!"
        all_running=false
    fi
    
    if ! kill -0 $CELERY_WORKER_PID 2>/dev/null; then
        print_error "Celery worker is not running!"
        all_running=false
    fi
    
    if ! kill -0 $CELERY_BEAT_PID 2>/dev/null; then
        print_error "Celery beat is not running!"
        all_running=false
    fi
    
    if [ "$all_running" = true ]; then
        print_status "All services are running successfully!"
    else
        print_error "Some services failed to start. Check the logs for details."
        cleanup
    fi
}

# Wait a moment and check if all services started successfully
sleep 3
check_services

# Show live logs
show_logs
