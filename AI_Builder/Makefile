# AI Builder Version 2 Makefile
# Easy commands for development and deployment

.PHONY: help build run stop clean test dev prod logs shell

# Default target
help:
	@echo "AI Builder Version 2 - Available Commands:"
	@echo ""
	@echo "Development:"
	@echo "  make dev          - Run in development mode with hot reload"
	@echo "  make run          - Run the application locally"
	@echo "  make test          - Run API tests"
	@echo ""
	@echo "Docker:"
	@echo "  make build        - Build Docker image"
	@echo "  make prod         - Run in production mode with Docker Compose"
	@echo "  make stop         - Stop all containers"
	@echo "  make clean        - Clean up Docker resources"
	@echo ""
	@echo "Utilities:"
	@echo "  make logs         - Show container logs"
	@echo "  make shell        - Open shell in running container"
	@echo "  make install      - Install Python dependencies"
	@echo ""

# Development commands
dev:
	@echo "🚀 Starting development server..."
	python main_fastapi.py

run:
	@echo "🚀 Starting application..."
	python main_fastapi.py

test:
	@echo "🧪 Running API tests..."
	python test_api.py

install:
	@echo "📦 Installing dependencies..."
	pip install -r requirements.txt

# Docker commands
build:
	@echo "🔨 Building Docker image..."
	docker build -t ai-builder-v2 .

prod:
	@echo "🚀 Starting production environment..."
	docker-compose --profile production up -d

stop:
	@echo "🛑 Stopping containers..."
	docker-compose down

clean:
	@echo "🧹 Cleaning up Docker resources..."
	docker-compose down -v
	docker system prune -f
	docker image prune -f

logs:
	@echo "📋 Showing container logs..."
	docker-compose logs -f ai-builder

shell:
	@echo "🐚 Opening shell in container..."
	docker-compose exec ai-builder bash

# Health check
health:
	@echo "🏥 Checking application health..."
	curl -f http://localhost:8000/health || echo "❌ Application is not running"

# Quick start for development
quick-start: install dev

# Quick start for production
quick-prod: build prod

# Full cleanup and rebuild
rebuild: clean build

# Show running containers
status:
	@echo "📊 Container status:"
	docker-compose ps
