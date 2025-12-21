#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Banner
echo -e "${CYAN}"
echo "  __  __ _                  ____ ____  __  __ "
echo " |  \/  (_) ___ _ __ ___   / ___|  _ \|  \/  |"
echo " | |\/| | |/ __| '__/ _ \ | |   | |_) | |\/| |"
echo " | |  | | | (__| | | (_) || |___|  _ <| |  | |"
echo " |_|  |_|_|\___|_|  \___/  \____|_| \_\_|  |_|"
echo -e "${NC}"
echo -e "${BOLD}Local Setup Wizard${NC}"
echo ""

# Check for Docker
check_docker() {
    echo -e "${BLUE}Checking for Docker...${NC}"
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Docker is not installed or not in PATH.${NC}"
        echo "Please install Docker Desktop from: https://www.docker.com/products/docker-desktop/"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo -e "${RED}Docker daemon is not running.${NC}"
        echo "Please start Docker Desktop and try again."
        exit 1
    fi

    echo -e "${GREEN}Docker is installed and running.${NC}"
}

# Check for docker compose
check_compose() {
    echo -e "${BLUE}Checking for Docker Compose...${NC}"
    if docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
        echo -e "${GREEN}Docker Compose (v2) is available.${NC}"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
        echo -e "${GREEN}Docker Compose (v1) is available.${NC}"
    else
        echo -e "${RED}Docker Compose is not available.${NC}"
        echo "Please ensure Docker Desktop is properly installed."
        exit 1
    fi
}

# Generate .env file
generate_env() {
    echo ""
    echo -e "${BLUE}Configuring environment...${NC}"

    # Check if .env already exists
    if [ -f .env ]; then
        echo -e "${YELLOW}An existing .env file was found.${NC}"
        read -p "Do you want to overwrite it? (y/N): " overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            echo -e "${GREEN}Keeping existing .env file.${NC}"
            return
        fi
    fi

    # Prompt for superuser credentials
    echo ""
    echo -e "${CYAN}Configure admin account:${NC}"
    read -p "Admin username [admin]: " admin_user
    admin_user=${admin_user:-admin}
    read -p "Admin email [admin@example.com]: " admin_email
    admin_email=${admin_email:-admin@example.com}
    read -s -p "Admin password [admin]: " admin_pass
    echo ""
    admin_pass=${admin_pass:-admin}

    # Prompt for Gemini API Key
    echo ""
    echo -e "${CYAN}The Gemini API is used for AI-powered lead research.${NC}"
    echo -e "Get your API key from: ${BOLD}https://aistudio.google.com/app/apikey${NC}"
    echo ""
    read -p "Enter your GEMINI_API_KEY (or press Enter to skip): " gemini_key

    # Generate .env file
    cat > .env << EOF
# Superuser
SUPERUSER_USERNAME=${admin_user}
SUPERUSER_EMAIL=${admin_email}
SUPERUSER_PASSWORD=${admin_pass}

# Django
SECRET_KEY=$(openssl rand -base64 32 2>/dev/null || echo "local-dev-secret-key-$(date +%s)")
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
ADMIN_URL=admin/
CSRF_TRUSTED_ORIGINS=

# Database - SQLite is used in DEBUG mode
DB_NAME=crm
DB_USER=crm
DB_PASSWORD=crm
DB_HOST=localhost
DB_PORT=5432

# Redis & Celery
REDIS_HOST=redis
REDIS_PORT=6379
CELERY_TASK_ALWAYS_EAGER=False

# API
API_KEY=dev-api-key

# Gemini AI
GEMINI_API_KEY=${gemini_key}

# Google SSO (optional - leave empty for local use)
GOOGLE_SSO_CLIENT_ID=
GOOGLE_SSO_CLIENT_SECRET=
GOOGLE_SSO_PROJECT_ID=
GOOGLE_SSO_SUPERUSER_LIST=
EOF

    echo -e "${GREEN}.env file generated successfully.${NC}"
}

# Start services
start_services() {
    echo ""
    echo -e "${BLUE}Building and starting services...${NC}"
    echo ""
    echo -e "This will build and start:"
    echo -e "  ${CYAN}- Redis${NC}  (message broker)"
    echo -e "  ${CYAN}- Web${NC}    (Django application)"
    echo -e "  ${CYAN}- Celery${NC} (background task worker)"
    echo -e "  ${CYAN}- Beat${NC}   (periodic task scheduler)"
    echo ""
    echo -e "${YELLOW}First run may take a few minutes to build the image...${NC}"
    echo ""

    $COMPOSE_CMD -f compose.local.yaml up --build -d

    echo ""
    echo -e "${GREEN}All services started successfully!${NC}"
    echo ""
    echo -e "${BOLD}Access the application:${NC}"
    echo -e "  ${CYAN}Admin Panel:${NC} http://localhost:8000/admin/"
    echo -e "  ${CYAN}API Docs:${NC}    http://localhost:8000/api/docs"
    echo ""
    echo -e "${BOLD}Login credentials:${NC}"
    echo -e "  Username: ${CYAN}${admin_user:-admin}${NC}"
    echo -e "  Password: ${CYAN}(as configured)${NC}"
    echo ""
    echo -e "${BOLD}Useful commands:${NC}"
    echo -e "  View logs:     ${CYAN}./local_wizard.sh logs${NC}"
    echo -e "  Stop:          ${CYAN}./local_wizard.sh stop${NC}"
    echo -e "  Restart:       ${CYAN}./local_wizard.sh restart${NC}"
    echo ""

    # Show container status
    echo -e "${BOLD}Container status:${NC}"
    $COMPOSE_CMD -f compose.local.yaml ps
}

# Stop services
stop_services() {
    echo -e "${BLUE}Stopping services...${NC}"
    $COMPOSE_CMD -f compose.local.yaml down
    echo -e "${GREEN}All services stopped.${NC}"
}

# Show logs
show_logs() {
    $COMPOSE_CMD -f compose.local.yaml logs -f
}

# Show status
show_status() {
    $COMPOSE_CMD -f compose.local.yaml ps
}

# Reset everything
reset_all() {
    echo -e "${YELLOW}This will stop all services, remove containers, volumes, and images.${NC}"
    read -p "Are you sure? (y/N): " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Removing everything...${NC}"
        $COMPOSE_CMD -f compose.local.yaml down -v --rmi local
        echo -e "${GREEN}Reset complete. Run './local_wizard.sh start' to start fresh.${NC}"
    else
        echo "Reset cancelled."
    fi
}

# Main menu
main_menu() {
    echo ""
    echo -e "${BOLD}What would you like to do?${NC}"
    echo ""
    echo "  1) Start (first-time setup)"
    echo "  2) Stop"
    echo "  3) Restart"
    echo "  4) View logs"
    echo "  5) Status"
    echo "  6) Reset (remove everything)"
    echo "  7) Exit"
    echo ""
    read -p "Enter your choice [1-7]: " choice

    case $choice in
        1)
            generate_env
            start_services
            ;;
        2)
            stop_services
            ;;
        3)
            echo -e "${BLUE}Restarting services...${NC}"
            $COMPOSE_CMD -f compose.local.yaml restart
            echo -e "${GREEN}Services restarted.${NC}"
            ;;
        4)
            show_logs
            ;;
        5)
            show_status
            ;;
        6)
            reset_all
            ;;
        7)
            echo -e "${GREEN}Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice. Please try again.${NC}"
            main_menu
            ;;
    esac
}

# Run checks
check_docker
check_compose

# Handle command line arguments
case "${1:-}" in
    start)
        generate_env
        start_services
        ;;
    stop)
        stop_services
        ;;
    logs)
        show_logs
        ;;
    restart)
        $COMPOSE_CMD -f compose.local.yaml restart
        ;;
    status)
        show_status
        ;;
    reset)
        reset_all
        ;;
    *)
        main_menu
        ;;
esac
