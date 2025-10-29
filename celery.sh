#!/bin/bash

# Configuration variables
PROJECT_ROOT=$(pwd)
VIRTUAL_ENV_PATH="/home/nathan/Desktop/intelliscale/.venv"
APP_MODULE="core.celery"
#
# PID and log file paths (create a logs directory first)
PIDFILE_WORKER="$PROJECT_ROOT/celery_worker.pid"
PIDFILE_BEAT="$PROJECT_ROOT/celery_beat.pid"
LOGFILE_WORKER="$PROJECT_ROOT/celery_worker.log"
LOGFILE_BEAT="$PROJECT_ROOT/celery_beat.log"

start() {
    echo "Starting Redis server and Celery services..."
    # Start Redis (requires sudo)
    sudo systemctl start redis-server
    
    # Activate the virtual environment
    source "$VIRTUAL_ENV_PATH/bin/activate"

    # Start the worker
    nohup celery -A "$APP_MODULE" worker --loglevel=info --pidfile="$PIDFILE_WORKER" --logfile="$LOGFILE_WORKER" &
    # Start the beat scheduler
    nohup celery -A "$APP_MODULE" beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler --pidfile="$PIDFILE_BEAT" --logfile="$LOGFILE_BEAT" &
    echo "All services started."
}

stop() {
    echo "Stopping Redis server and Celery services..."
    # Stop the worker
    if [ -f "$PIDFILE_WORKER" ]; then
        kill $(cat "$PIDFILE_WORKER")
        rm "$PIDFILE_WORKER"
        echo "Celery worker stopped."
    else
        echo "Celery worker not running."
    fi

    # Stop the beat scheduler
    if [ -f "$PIDFILE_BEAT" ]; then
        kill $(cat "$PIDFILE_BEAT")
        rm "$PIDFILE_BEAT"
        echo "Celery beat stopped."
    else
        echo "Celery beat not running."
    fi

    # Stop Redis (requires sudo)
    sudo systemctl stop redis-server
    echo "All services stopped."
}

restart() {
    stop
    sleep 3
    start
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        exit 1
        ;;
esac

exit 0