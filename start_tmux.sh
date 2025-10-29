#!/bin/bash

# IntelliScale Startup Script with Tmux - Shows each service in separate panes
# Requires tmux to be installed: sudo apt install tmux

PROJECT_DIR="/home/nathan/Desktop/intelliscale"
VENV_PATH="$PROJECT_DIR/.venv"
SESSION_NAME="intelliscale"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# Check if tmux is installed
if ! command -v tmux &> /dev/null; then
    echo -e "${RED}Error: tmux is not installed. Install it with: sudo apt install tmux${NC}"
    exit 1
fi

# Check if session already exists
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "${YELLOW}Session '$SESSION_NAME' already exists. Killing it...${NC}"
    tmux kill-session -t "$SESSION_NAME"
fi

echo -e "${GREEN}Starting IntelliScale in tmux session: $SESSION_NAME${NC}"
echo -e "${BLUE}Project Directory: $PROJECT_DIR${NC}"

# Create new tmux session and split it into panes
tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_DIR"

# Split the window into 3 panes
tmux split-window -h -t "$SESSION_NAME"
tmux split-window -v -t "$SESSION_NAME:0.0"

# Set up each pane
echo -e "${YELLOW}Setting up panes...${NC}"

# Pane 0: Django runserver
tmux send-keys -t "$SESSION_NAME:0.0" "cd $PROJECT_DIR" Enter
tmux send-keys -t "$SESSION_NAME:0.0" "source $VENV_PATH/bin/activate" Enter
tmux send-keys -t "$SESSION_NAME:0.0" "echo 'Running Django migrations...'" Enter
tmux send-keys -t "$SESSION_NAME:0.0" "python manage.py migrate --noinput" Enter
tmux send-keys -t "$SESSION_NAME:0.0" "echo 'Starting Django Server...'" Enter
tmux send-keys -t "$SESSION_NAME:0.0" "python manage.py runserver 0.0.0.0:8000" Enter

# Pane 1: Celery worker
tmux send-keys -t "$SESSION_NAME:0.1" "cd $PROJECT_DIR" Enter
tmux send-keys -t "$SESSION_NAME:0.1" "source $VENV_PATH/bin/activate" Enter
tmux send-keys -t "$SESSION_NAME:0.1" "echo 'Starting Celery Worker...'" Enter
tmux send-keys -t "$SESSION_NAME:0.1" "celery -A core worker --loglevel=info" Enter

# Pane 2: Celery beat
tmux send-keys -t "$SESSION_NAME:0.2" "cd $PROJECT_DIR" Enter
tmux send-keys -t "$SESSION_NAME:0.2" "source $VENV_PATH/bin/activate" Enter
tmux send-keys -t "$SESSION_NAME:0.2" "echo 'Starting Celery Beat...'" Enter
tmux send-keys -t "$SESSION_NAME:0.2" "celery -A core beat --loglevel=info" Enter

# Set pane titles
tmux select-pane -t "$SESSION_NAME:0.0" -T "Django Server"
tmux select-pane -t "$SESSION_NAME:0.1" -T "Celery Worker"
tmux select-pane -t "$SESSION_NAME:0.2" -T "Celery Beat"

# Customize status bar to show pane numbers
tmux set-option -t "$SESSION_NAME" status-right "Pane: #P | %H:%M"

echo -e "${GREEN}=== IntelliScale Started Successfully ===${NC}"
echo -e "${BLUE}Django Server: http://localhost:8000${NC}"
echo
echo -e "${YELLOW}Tmux Commands:${NC}"
echo -e "  Attach to session: ${GREEN}tmux attach -t $SESSION_NAME${NC}"
echo -e "  Switch panes: ${GREEN}Ctrl+B + Arrow Keys${NC}"
echo -e "  Detach session: ${GREEN}Ctrl+B + D${NC}"
echo -e "  Kill session: ${GREEN}tmux kill-session -t $SESSION_NAME${NC}"
echo
echo -e "${YELLOW}Attaching to session...${NC}"

# Attach to the session
tmux attach -t "$SESSION_NAME"
