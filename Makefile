PYTHON ?= python3
APP_NAME ?= EbookRenamer
ENTRY ?= rename_books_by_meta.py
DIST_DIR ?= dist
BUILD_DIR ?= build
ASSETS_DIR ?= assets
ICON_PNG ?= $(ASSETS_DIR)/icon.png
ICON_ICO ?= $(ASSETS_DIR)/icon.ico
ICON_ICNS ?= $(ASSETS_DIR)/icon.icns
MAC_BUNDLE ?= $(DIST_DIR)/$(APP_NAME)
DIR ?= .
APP_TITLE ?= Ebook Renamer
RUN_ARGS ?=

# Docker images commonly used for cross-platform PyInstaller builds.
PYI_LINUX_IMAGE ?= cdrx/pyinstaller-linux:python3
PYI_WINDOWS_IMAGE ?= cdrx/pyinstaller-windows:python3
DOCKER_PLATFORM ?= linux/amd64
PYI_LINUX_IMAGE_NEW ?= python:3.12-slim
PYI_CACHE_DIR ?= .pyinstaller-cache
GH_WINDOWS_REPO ?= etng/EbookRenamer
GH_WINDOWS_WORKFLOW ?= build.yml
GH_WINDOWS_ARTIFACT ?= EbookRenamer-windows

.PHONY: help icon deps clean clean-build clean-dist \
	ensure-pyinstaller build-macos build-linux build-windows build-windows-remote build-all package-release release relase \
	run-cli run-tui run-gui

help:
	@echo "Targets:"
	@echo "  make icon          Generate default app icons (png/ico/icns when possible)"
	@echo "  make deps          Install local build dependencies"
	@echo "  make build-macos   Build macOS app locally with PyInstaller"
	@echo "  make build-linux   Build Linux binary via Docker (cdrx/pyinstaller-linux)"
	@echo "  make build-windows Build Windows binary via Docker (cdrx/pyinstaller-windows)"
	@echo "  make build-windows-remote Build Windows .exe via GitHub Actions"
	@echo "  make build-all     Build all targets"
	@echo "  make package-release Archive existing build outputs into release/"
	@echo "  make release       Build all targets and collect release archives"
	@echo "  make relase        Alias of make release"
	@echo "  make run-cli       Run script in CLI preview mode"
	@echo "  make run-tui       Run script in TUI preview mode"
	@echo "  make run-gui       Run script in GUI mode"
	@echo "  make clean         Remove build artifacts"

icon:
	$(PYTHON) tools/generate_icon.py --out-dir $(ASSETS_DIR) --app-name "$(APP_NAME)"

deps:
	$(PYTHON) -m pip install --user --upgrade pip pyinstaller

ensure-pyinstaller:
	@$(PYTHON) -c "import PyInstaller" >/dev/null 2>&1 || ( \
		echo "[INFO] PyInstaller not found, installing..."; \
		$(PYTHON) -m pip install --user pyinstaller || \
		$(PYTHON) -m pip install --break-system-packages --user pyinstaller \
	)

build-macos: icon ensure-pyinstaller
	@ICON_ARG=""; \
	if [ -f "$(PWD)/$(ICON_ICNS)" ]; then ICON_ARG="--icon $(PWD)/$(ICON_ICNS)"; fi; \
	PYINSTALLER_CONFIG_DIR="$(PWD)/$(PYI_CACHE_DIR)" $(PYTHON) -m PyInstaller --noconfirm --clean \
		--name "$(APP_NAME)" \
		--windowed \
		--onedir \
		--distpath "$(DIST_DIR)/macos" \
		--workpath "$(BUILD_DIR)/macos" \
		--specpath "$(BUILD_DIR)/macos" \
		--add-data "$(PWD)/locales:locales" \
		$$ICON_ARG \
		"$(ENTRY)"

build-linux: icon
	docker run --rm --platform "$(DOCKER_PLATFORM)" --entrypoint /bin/sh -v "$(PWD):/src/" $(PYI_LINUX_IMAGE_NEW) -c "\
		set -e; \
		apt-get update >/dev/null; \
		apt-get install -y --no-install-recommends binutils >/dev/null; \
		cd /src; \
		python -m pip install -q --no-cache-dir pyinstaller; \
		pyinstaller --noconfirm --clean \
			--name '$(APP_NAME)' \
			--onefile \
			--distpath '$(DIST_DIR)/linux' \
			--workpath '$(BUILD_DIR)/linux' \
			--specpath '$(BUILD_DIR)/linux' \
			--add-data '/src/locales:locales' \
			'$(ENTRY)'"

build-windows: icon
	@mkdir -p "$(DIST_DIR)/windows"
	@docker run --rm --platform "$(DOCKER_PLATFORM)" -v "$(PWD):/src/" $(PYI_WINDOWS_IMAGE) "\
		pip install -U pyinstaller && \
		pyinstaller --noconfirm --clean \
			--name '$(APP_NAME)' \
			--windowed \
			--onefile \
			--distpath '$(DIST_DIR)/windows' \
			--workpath '$(BUILD_DIR)/windows' \
			--specpath '$(BUILD_DIR)/windows' \
			--add-data '/src/locales;locales' \
			--icon '$(ICON_ICO)' \
			'$(ENTRY)'" \
	|| ( \
		echo "[WARN] Native Windows .exe build failed; fallback to remote GitHub Actions build."; \
		$(MAKE) build-windows-remote; \
	)

build-windows-remote:
	@gh workflow run "$(GH_WINDOWS_WORKFLOW)" --repo "$(GH_WINDOWS_REPO)"
	@RUN_ID="$$(gh run list --repo "$(GH_WINDOWS_REPO)" --workflow "$(GH_WINDOWS_WORKFLOW)" --limit 1 --json databaseId --jq '.[0].databaseId')"; \
	echo "[INFO] Waiting for remote Windows build run $$RUN_ID ..."; \
	gh run watch "$$RUN_ID" --repo "$(GH_WINDOWS_REPO)" --exit-status; \
	rm -rf /tmp/gh-artifacts-windows; \
	mkdir -p /tmp/gh-artifacts-windows; \
	gh run download "$$RUN_ID" --repo "$(GH_WINDOWS_REPO)" --name "$(GH_WINDOWS_ARTIFACT)" --dir /tmp/gh-artifacts-windows; \
	mkdir -p "$(DIST_DIR)/windows"; \
	cp /tmp/gh-artifacts-windows/EbookRenamer.exe "$(DIST_DIR)/windows/$(APP_NAME).exe"; \
	echo "[INFO] Downloaded Windows exe to $(DIST_DIR)/windows/$(APP_NAME).exe"

build-all: build-macos build-linux build-windows

package-release:
	@mkdir -p release
	@if [ -d "$(DIST_DIR)/macos/$(APP_NAME).app" ]; then \
		tar -C "$(DIST_DIR)/macos" -czf "release/$(APP_NAME)-macos.tar.gz" "$(APP_NAME).app"; \
		echo "Created release/$(APP_NAME)-macos.tar.gz"; \
	elif [ -d "$(DIST_DIR)/macos/$(APP_NAME)" ]; then \
		tar -C "$(DIST_DIR)/macos" -czf "release/$(APP_NAME)-macos.tar.gz" "$(APP_NAME)"; \
		echo "Created release/$(APP_NAME)-macos.tar.gz"; \
	else \
		echo "[WARN] macOS artifact not found under $(DIST_DIR)/macos"; \
	fi
	@if [ -f "$(DIST_DIR)/linux/$(APP_NAME)" ]; then \
		tar -C "$(DIST_DIR)/linux" -czf "release/$(APP_NAME)-linux.tar.gz" "$(APP_NAME)"; \
		echo "Created release/$(APP_NAME)-linux.tar.gz"; \
	else \
		echo "[WARN] Linux artifact not found under $(DIST_DIR)/linux"; \
	fi
	@if [ -f "$(DIST_DIR)/windows/$(APP_NAME).exe" ]; then \
		tar -C "$(DIST_DIR)/windows" -czf "release/$(APP_NAME)-windows.tar.gz" "$(APP_NAME).exe"; \
		echo "Created release/$(APP_NAME)-windows.tar.gz"; \
	else \
		echo "[WARN] Windows artifact not found under $(DIST_DIR)/windows"; \
	fi

release:
	@$(MAKE) build-macos || true
	@$(MAKE) build-linux DOCKER_PLATFORM="$(DOCKER_PLATFORM)" || true
	@$(MAKE) build-windows DOCKER_PLATFORM="$(DOCKER_PLATFORM)" || true
	@$(MAKE) package-release

relase: release

run-cli:
	$(PYTHON) "$(ENTRY)" --ui cli --dir "$(DIR)" $(RUN_ARGS)

run-tui:
	$(PYTHON) "$(ENTRY)" --tui --dir "$(DIR)" $(RUN_ARGS)

run-gui:
	$(PYTHON) "$(ENTRY)" --gui --dir "$(DIR)" --app-title "$(APP_TITLE)" $(RUN_ARGS)

clean-build:
	rm -rf "$(BUILD_DIR)"

clean-dist:
	rm -rf "$(DIST_DIR)"

clean: clean-build clean-dist
	rm -f *.spec
	rm -rf release
