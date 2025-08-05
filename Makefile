#!/usr/bin/env make

.PHONY: help install lint format test test-unit test-e2e clean docker-build docker-up docker-down

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies
	pip install -r requirements.txt
	pip install ruff black pytest

lint: ## Run linting
	ruff check app/ tests/ --show-source

format: ## Format code
	black app/ tests/
	ruff check app/ tests/ --fix

format-check: ## Check code formatting
	black --check app/ tests/
	ruff check app/ tests/

test: ## Run all tests
	pytest tests/ -v

test-unit: ## Run unit tests only
	pytest tests/test_rules.py -v

test-e2e: ## Run e2e tests only
	pytest tests/test_e2e.py -v

clean: ## Clean up temporary files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type f -name ".coverage" -delete

docker-build: ## Build Docker image
	docker build -t fraud-mev-monitor:latest .

docker-up: ## Start all services with Docker Compose
	docker compose up --build

docker-down: ## Stop all services
	docker compose down

docker-logs: ## View service logs
	docker compose logs -f

dev-setup: install ## Set up development environment
	@echo "Development environment ready!"
	@echo "Run 'make docker-up' to start all services"
	@echo "Run 'make test' to run tests"