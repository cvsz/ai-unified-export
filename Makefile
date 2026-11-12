# Unified AI Server + All-Installed-AI-Tools Exporter (native WSL2 Ubuntu)
# Read-only inventory. No installation, upgrades, host mutation, or secret reads.

SHELL := /bin/sh
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

PYTHON             ?= python3
SCRIPTS_DIR        := scripts
TESTS_DIR          := tests
EXPORT_SCRIPT      := $(SCRIPTS_DIR)/ai_unified_export.py
MONITOR_SCRIPT     := $(SCRIPTS_DIR)/ai_export_monitor.py
MODEL_SCRIPT       := $(SCRIPTS_DIR)/ai_model_manager.py
INSTALLER_SCRIPT   := $(SCRIPTS_DIR)/ai_tools_installer.py

INVENTORY_DIR      ?= ./ai-inventory-export
INVENTORY_FILE     ?= $(INVENTORY_DIR)/ai-inventory.json
MODEL_OUTPUT       ?= ./model-export
MODEL_MAX_BYTES    ?= 107374182400
MODEL_FILES        ?=
DOWNLOAD_URL       ?=
DOWNLOAD_OUTPUT    ?=
DOWNLOAD_SHA256    ?=
DOWNLOAD_MAX_BYTES ?= 21474836480
DOWNLOAD_ALLOW_HOST ?=
OLLAMA_MODELS_DIR  ?=
TOOLS              ?=
TOOLS_MANAGER      ?=
TOOLS_SCRIPT_SHA256 ?=
TOOLS_LOG_DIR      ?= ./ai-installer-logs
TOOLS_TIMEOUT      ?= 1800
TOOLS_CATEGORY     ?=
TOOLS_INSTALLED_ONLY ?=
TOOLS_MISSING_ONLY ?=
TOOLS_JSON         ?=
APPLY              ?=

PROFILE            ?= quick
MONITOR            ?= both
MONITOR_HOST       ?= 127.0.0.1
MONITOR_PORT       ?= 8765
MONITOR_KEEPALIVE  ?= 120
MONITOR_TOKEN_FILE ?=
EXCLUDE_ROOTS      ?=
CONFIG_FILE        ?=

# Full scan budgets. Increase explicitly for very large WSL installations.
AI_MAX_ENTRIES          ?= 2000000
AI_MAX_ENTRIES_PER_ROOT ?= 500000
AI_MAX_INVOCATIONS      ?= 5000
AI_PER_TOOL_INVOCATIONS ?= 64
AI_MAX_DEPTH            ?= 4
AI_TIMEOUT              ?= 5
AI_EXTRA_ROOTS          ?=

PACKAGE     ?= ../ai-unified-server-cli-exporter-wsl2-full.zip
PACKAGE_ABS := $(abspath $(PACKAGE))
PROJECT_DIR  := $(notdir $(CURDIR))
EXTRA_ROOT_ARGS := $(foreach root,$(AI_EXTRA_ROOTS),--scan-root '$(root)')
EXCLUDE_ROOT_ARGS := $(foreach root,$(EXCLUDE_ROOTS),--exclude-root '$(root)')
# Conditional argument fragments are precomputed here so recipes contain only
# plain variable references (keeps the expanded shell recipes shellcheck-clean).
MONITOR_TOKEN_ARGS := $(if $(MONITOR_TOKEN_FILE),--monitor-token-file '$(MONITOR_TOKEN_FILE)')
TOOLS_MANAGER_ARGS := $(if $(TOOLS_MANAGER),--manager '$(TOOLS_MANAGER)')
TOOLS_HASH_ARGS := $(if $(TOOLS_SCRIPT_SHA256),--script-sha256 '$(TOOLS_SCRIPT_SHA256)')
TOOLS_TIMEOUT_ARGS := $(if $(TOOLS_TIMEOUT),--timeout '$(TOOLS_TIMEOUT)')
APPLY_ARGS := $(if $(APPLY),--apply)
OLLAMA_DIR_ARGS := $(if $(OLLAMA_MODELS_DIR),--models-dir '$(OLLAMA_MODELS_DIR)')
CONFIG_ARGS := $(if $(CONFIG_FILE),--config '$(CONFIG_FILE)')
DOWNLOAD_ALLOW_HOST_ARGS := $(foreach host,$(DOWNLOAD_ALLOW_HOST),--allow-host '$(host)')

.PHONY: help list-targets check-python check-tools compile test test-verbose smoke validate \
        inventory inventory-quick inventory-full inventory-only inventory-resume \
        ai-all ai-all-quiet ai-discover ai-cli-all ai-tools ai-packages ai-servers ai-report \
        monitor-both monitor-terminal monitor-web monitor-off dashboard \
        model-export model-export-partial model-validate model-download model-validate-ollama \
        tools-list tools-missing tools-check tools-plan tools-install tools-script-hash tools-install-missing \
        clean package show-config

help: ## Show all targets and common variables
	@printf '%s\n' 'Unified WSL2 AI inventory and model lifecycle' '' 'Usage: make <target> [VAR=value]' '' 'AI coverage:'
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\n%s\n' 'Common variables:' \
	  '  INVENTORY_DIR=./ai-inventory-export' \
	  '  MONITOR=both|terminal|web|off' \
	  '  AI_MAX_ENTRIES=2000000 AI_MAX_INVOCATIONS=30000' \
	  '  AI_EXTRA_ROOTS="/home/USER/.local/share /opt/custom-ai"' \
	  '  MODEL_FILES="model.gguf model.safetensors"' \
	  '  CONFIG_FILE=./scan-config.json' \
	  '  DOWNLOAD_ALLOW_HOST="huggingface.co cdn-lfs.huggingface.co"' \
	  '  TOOLS_CATEGORY=coding TOOLS_JSON=1 TOOLS_TIMEOUT=1800'

list-targets: ## List target names only
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / {print $$1}' $(MAKEFILE_LIST)

check-python: ## Require Python 3.10+
	@$(PYTHON) -c 'import sys; v=sys.version_info; print("Python", sys.version.split()[0]); sys.exit(0 if v >= (3, 10) else "Python 3.10+ is required")'

check-tools: check-python ## Verify exporter, monitor, model manager, installer, legacy scripts and tests exist
	@test -f '$(EXPORT_SCRIPT)' -a -f '$(MODEL_SCRIPT)' -a -f '$(MONITOR_SCRIPT)' -a -f '$(INSTALLER_SCRIPT)' || { echo 'ERROR: project scripts missing; extract the complete project ZIP' >&2; exit 1; }
	@test -f '$(SCRIPTS_DIR)/discover_ai_server.py' -a -f '$(SCRIPTS_DIR)/export_ai_cli.py' || { echo 'ERROR: legacy scripts missing; extract the complete project ZIP' >&2; exit 1; }
	@test -d '$(TESTS_DIR)' || { echo 'ERROR: tests directory missing' >&2; exit 1; }
	@command -v zip >/dev/null 2>&1 || { echo 'ERROR: zip is required only for make package' >&2; exit 1; }
	@command -v unzip >/dev/null 2>&1 || { echo 'ERROR: unzip is required only for package verification' >&2; exit 1; }
	@echo 'Project prerequisites: PASS'

compile: check-python ## Compile every Python script and test
	@$(PYTHON) -m compileall -q '$(SCRIPTS_DIR)' '$(TESTS_DIR)'
	@echo 'Compilation: PASS'

test: check-python ## Run all unit tests
	@$(PYTHON) -m unittest discover -s '$(TESTS_DIR)' -p 'test_*.py'

test-verbose: check-python ## Run all unit tests verbosely
	@$(PYTHON) -m unittest discover -s '$(TESTS_DIR)' -p 'test_*.py' -v

smoke: check-tools ## Verify all CLI entry points and model subcommands
	@$(PYTHON) '$(EXPORT_SCRIPT)' --help >/dev/null
	@$(PYTHON) '$(EXPORT_SCRIPT)' --version
	@$(PYTHON) '$(MODEL_SCRIPT)' --help >/dev/null
	@$(PYTHON) '$(MODEL_SCRIPT)' --version
	@$(PYTHON) '$(MODEL_SCRIPT)' export --help >/dev/null
	@$(PYTHON) '$(MODEL_SCRIPT)' validate --help >/dev/null
	@$(PYTHON) '$(MODEL_SCRIPT)' download --help >/dev/null
	@$(PYTHON) '$(MODEL_SCRIPT)' ollama-validate --help >/dev/null
	@$(PYTHON) '$(INSTALLER_SCRIPT)' --help >/dev/null
	@$(PYTHON) '$(INSTALLER_SCRIPT)' --version
	@$(PYTHON) '$(INSTALLER_SCRIPT)' check-catalog >/dev/null
	@$(PYTHON) '$(SCRIPTS_DIR)/discover_ai_server.py' --help >/dev/null
	@$(PYTHON) '$(SCRIPTS_DIR)/export_ai_cli.py' --help >/dev/null
	@echo 'CLI smoke checks: PASS'

validate: compile test smoke ## Run compile, unit tests and CLI smoke checks
	@echo 'Validation: PASS'

# Inventory profiles
inventory: inventory-quick ## Run default quick inventory

inventory-quick: check-python ## Quick pass over AI tools, packages, services and model paths
	@$(PYTHON) '$(EXPORT_SCRIPT)' --profile quick --output '$(INVENTORY_DIR)' \
		--monitor '$(MONITOR)' --monitor-host '$(MONITOR_HOST)' --monitor-port '$(MONITOR_PORT)' \
		--monitor-keepalive '$(MONITOR_KEEPALIVE)' \
		$(MONITOR_TOKEN_ARGS) \
		$(EXTRA_ROOT_ARGS) $(EXCLUDE_ROOT_ARGS) $(CONFIG_ARGS)

inventory-full: check-python ## Bounded full scan of native WSL2 AI paths and CLI help trees
	@$(PYTHON) '$(EXPORT_SCRIPT)' --profile full --output '$(INVENTORY_DIR)' \
		--max-total-entries '$(AI_MAX_ENTRIES)' \
		--max-entries-per-root '$(AI_MAX_ENTRIES_PER_ROOT)' \
		--max-invocations '$(AI_MAX_INVOCATIONS)' \
		--per-tool-invocations '$(AI_PER_TOOL_INVOCATIONS)' \
		--max-depth '$(AI_MAX_DEPTH)' --timeout '$(AI_TIMEOUT)' \
		--monitor '$(MONITOR)' --monitor-host '$(MONITOR_HOST)' --monitor-port '$(MONITOR_PORT)' \
		--monitor-keepalive '$(MONITOR_KEEPALIVE)' \
		$(MONITOR_TOKEN_ARGS) \
		$(EXTRA_ROOT_ARGS) $(EXCLUDE_ROOT_ARGS) $(CONFIG_ARGS)

inventory-only: check-python ## Inventory without invoking any AI CLI help command
	@$(PYTHON) '$(EXPORT_SCRIPT)' --profile full --skip-cli-help --output '$(INVENTORY_DIR)' \
		--max-total-entries '$(AI_MAX_ENTRIES)' --max-entries-per-root '$(AI_MAX_ENTRIES_PER_ROOT)' \
		--monitor '$(MONITOR)' $(EXTRA_ROOT_ARGS) $(EXCLUDE_ROOT_ARGS) $(CONFIG_ARGS)

inventory-resume: check-python ## Resume matching full/quick inventory checkpoint
	@$(PYTHON) '$(EXPORT_SCRIPT)' --profile '$(PROFILE)' --resume --output '$(INVENTORY_DIR)' \
		--monitor '$(MONITOR)' $(EXTRA_ROOT_ARGS) $(CONFIG_ARGS)

# Explicit all-AI workflows. `ai-all` is the comprehensive bounded default.
ai-all: inventory-full ## Discover all installed AI-named CLIs, packages, runtimes, containers, model artifacts and bounded help trees

ai-all-quiet: ## Full AI discovery without executing CLI help commands
	@$(MAKE) --no-print-directory inventory-only

ai-discover: ## Discover all AI executables and packages quickly, without CLI help
	@$(MAKE) --no-print-directory inventory-only AI_MAX_ENTRIES=500000

ai-cli-all: ## Full inventory with expanded recursive CLI help budget
	@$(MAKE) --no-print-directory inventory-full AI_MAX_INVOCATIONS='$(AI_MAX_INVOCATIONS)' AI_PER_TOOL_INVOCATIONS='$(AI_PER_TOOL_INVOCATIONS)' AI_MAX_DEPTH='$(AI_MAX_DEPTH)'

ai-tools: ## Print every discovered AI executable and its source/path
	@test -f '$(INVENTORY_FILE)' || { echo 'ERROR: run make ai-all first' >&2; exit 2; }
	@$(PYTHON) -c 'import json,pathlib; d=json.loads(pathlib.Path("$(INVENTORY_FILE)").read_text()); xs=d.get("executables", []); print("Discovered AI executables:", len(xs)); [print("{name}\t{source}\t{path}".format(**x)) for x in xs]'

ai-packages: ## Print detected AI packages by package manager
	@test -f '$(INVENTORY_FILE)' || { echo 'ERROR: run make ai-all first' >&2; exit 2; }
	@$(PYTHON) -c 'import json,pathlib; p=json.loads(pathlib.Path("$(INVENTORY_FILE)").read_text()).get("packages", {}); [print("\n[{}]".format(k), *["{} {}".format(x.get("name", ""), x.get("version", "")) for x in v]) for k,v in p.items() if isinstance(v,list)]'

ai-servers: ## Print discovered AI processes, systemd services and containers
	@test -f '$(INVENTORY_FILE)' || { echo 'ERROR: run make ai-all first' >&2; exit 2; }
	@$(PYTHON) -c 'import json,pathlib; d=json.loads(pathlib.Path("$(INVENTORY_FILE)").read_text()); r=d.get("runtime", {}); c=d.get("containers", {}); [(print("\n[{}]".format(k)), *[print(x) for x in v]) for k,v in r.items() if isinstance(v,list)]; [(print("\n[{}]".format(k)), *[print(x) for x in v]) for k,v in c.items() if isinstance(v,list)]'

ai-report: ## Print report and inventory paths
	@test -f '$(INVENTORY_FILE)' || { echo 'ERROR: inventory not found; run make ai-all' >&2; exit 2; }
	@printf 'JSON: %s\nMarkdown: %s/AI-INVENTORY-REPORT.md\nCLI help: %s/cli-help/\n' '$(INVENTORY_FILE)' '$(INVENTORY_DIR)' '$(INVENTORY_DIR)'
	@sed -n '1,240p' '$(INVENTORY_DIR)/AI-INVENTORY-REPORT.md'

# Monitor shortcuts
monitor-both: ## Run quick inventory with terminal and browser monitors
	@$(MAKE) --no-print-directory inventory-quick MONITOR=both

monitor-terminal: ## Run quick inventory with terminal TUI
	@$(MAKE) --no-print-directory inventory-quick MONITOR=terminal

monitor-web: ## Run quick inventory with loopback web dashboard
	@$(MAKE) --no-print-directory inventory-quick MONITOR=web

monitor-off: ## Run quick inventory without monitor
	@$(MAKE) --no-print-directory inventory-quick MONITOR=off

dashboard: monitor-web ## Alias for quick inventory plus local dashboard

# Model lifecycle
model-export: ## Export only structurally validated models found in inventory
	@test -f '$(INVENTORY_FILE)' || { echo 'ERROR: inventory not found; run make ai-all first' >&2; exit 2; }
	@$(PYTHON) '$(MODEL_SCRIPT)' export --inventory '$(INVENTORY_FILE)' --output '$(MODEL_OUTPUT)' --max-total-bytes '$(MODEL_MAX_BYTES)'

model-export-partial: ## Export PASS and PARTIAL model candidates for manual review
	@test -f '$(INVENTORY_FILE)' || { echo 'ERROR: inventory not found' >&2; exit 2; }
	@$(PYTHON) '$(MODEL_SCRIPT)' export --inventory '$(INVENTORY_FILE)' --output '$(MODEL_OUTPUT)' --max-total-bytes '$(MODEL_MAX_BYTES)' --include-partial

model-validate: ## Validate model files without loading or deserializing weights
	@test -n '$(strip $(MODEL_FILES))' || { echo 'ERROR: set MODEL_FILES="path1 path2"' >&2; exit 2; }
	@$(PYTHON) '$(MODEL_SCRIPT)' validate $(MODEL_FILES)

model-download: ## Download one public HTTPS model with a required SHA-256
	@test -n '$(DOWNLOAD_URL)' -a -n '$(DOWNLOAD_OUTPUT)' -a -n '$(DOWNLOAD_SHA256)' || { echo 'ERROR: set DOWNLOAD_URL, DOWNLOAD_OUTPUT and DOWNLOAD_SHA256' >&2; exit 2; }
	@$(PYTHON) '$(MODEL_SCRIPT)' download --url '$(DOWNLOAD_URL)' --output '$(DOWNLOAD_OUTPUT)' --sha256 '$(DOWNLOAD_SHA256)' --max-bytes '$(DOWNLOAD_MAX_BYTES)' $(DOWNLOAD_ALLOW_HOST_ARGS)

model-validate-ollama: ## Verify Ollama manifests/blobs (size + SHA-256); OLLAMA_MODELS_DIR optional
	@$(PYTHON) '$(MODEL_SCRIPT)' ollama-validate $(OLLAMA_DIR_ARGS)

# Free AI tool installer (explicit and dry-run by default)
tools-list: ## List the free AI tool catalog and install status
	@$(PYTHON) '$(INSTALLER_SCRIPT)' list $(if $(TOOLS_CATEGORY),--category '$(TOOLS_CATEGORY)') $(if $(TOOLS_INSTALLED_ONLY),--installed) $(if $(TOOLS_MISSING_ONLY),--missing) $(if $(TOOLS_JSON),--json)

tools-missing: ## List catalog tools that are not installed
	@$(PYTHON) '$(INSTALLER_SCRIPT)' list --missing

tools-check: ## Validate installer catalog structure and argument safety
	@$(PYTHON) '$(INSTALLER_SCRIPT)' check-catalog

tools-plan: ## Show the exact install command for TOOLS="a b" (never installs)
	@test -n '$(strip $(TOOLS))' || { echo 'ERROR: set TOOLS="opencode aider"' >&2; exit 2; }
	@$(PYTHON) '$(INSTALLER_SCRIPT)' plan $(TOOLS) $(TOOLS_MANAGER_ARGS) --log-dir '$(TOOLS_LOG_DIR)' $(TOOLS_TIMEOUT_ARGS)

tools-install: ## Install TOOLS="a b" (dry-run unless APPLY=1)
	@test -n '$(strip $(TOOLS))' || { echo 'ERROR: set TOOLS="opencode aider"' >&2; exit 2; }
	@$(PYTHON) '$(INSTALLER_SCRIPT)' install $(TOOLS) $(TOOLS_MANAGER_ARGS) \
		$(TOOLS_HASH_ARGS) --log-dir '$(TOOLS_LOG_DIR)' $(TOOLS_TIMEOUT_ARGS) $(APPLY_ARGS)

tools-script-hash: ## Download (not run) a tool's installer script and print its SHA-256; TOOLS=ollama
	@test -n '$(strip $(TOOLS))' || { echo 'ERROR: set TOOLS=ollama' >&2; exit 2; }
	@$(PYTHON) '$(INSTALLER_SCRIPT)' script-hash $(firstword $(TOOLS))

tools-install-missing: ## Install all catalog tools that are not currently installed AND have an available package manager (dry-run unless APPLY=1)
	@printf 'import sys, json, subprocess, shutil\nmissing_json = subprocess.check_output([sys.executable, "$(INSTALLER_SCRIPT)", "list", "--missing", "--json"], text=True)\nmissing = json.loads(missing_json)\navailable_managers = {m for m in ["npm", "pipx", "brew", "cargo", "go", "script"] if shutil.which(m)}\ninstallable = [t["id"] for t in missing if any(mgr in available_managers for mgr in t["managers"])]\nskipped = [t["id"] for t in missing if t["id"] not in installable]\nif skipped:\n    sys.stderr.write("Skipping (no available package manager): " + " ".join(skipped) + "\\n")\nif not installable:\n    print("No installable missing tools (all require unavailable package managers)")\n    sys.exit(0)\nprint(" ".join(installable))\n' > /tmp/filter_tools.py; \
	$(PYTHON) /tmp/filter_tools.py > /tmp/installable_tools.txt; \
	installable=$$(cat /tmp/installable_tools.txt); \
	if [ -z "$$installable" ]; then exit 0; fi; \
	echo "Installing: $$installable"; \
	$(PYTHON) '$(INSTALLER_SCRIPT)' install $$installable $(TOOLS_MANAGER_ARGS) \
		$(TOOLS_HASH_ARGS) --log-dir '$(TOOLS_LOG_DIR)' $(TOOLS_TIMEOUT_ARGS) $(APPLY_ARGS)

show-config: ## Show resolved configuration (no secrets)
	@printf '%-28s %s\n' \
		'PYTHON' '$(PYTHON)' 'INVENTORY_DIR' '$(INVENTORY_DIR)' \
		'INVENTORY_FILE' '$(INVENTORY_FILE)' 'PROFILE' '$(PROFILE)' \
		'MONITOR' '$(MONITOR)' 'AI_MAX_ENTRIES' '$(AI_MAX_ENTRIES)' \
		'AI_MAX_INVOCATIONS' '$(AI_MAX_INVOCATIONS)' 'AI_MAX_DEPTH' '$(AI_MAX_DEPTH)' \
		'AI_EXTRA_ROOTS' '$(AI_EXTRA_ROOTS)' 'CONFIG_FILE' '$(CONFIG_FILE)' \
		'TOOLS_TIMEOUT' '$(TOOLS_TIMEOUT)' 'DOWNLOAD_ALLOW_HOST' '$(DOWNLOAD_ALLOW_HOST)'

clean: ## Remove only generated Python/test caches; preserve inventory and models
	@find '$(SCRIPTS_DIR)' '$(TESTS_DIR)' -type d -name __pycache__ -prune -exec rm -rf {} +
	@find '$(SCRIPTS_DIR)' '$(TESTS_DIR)' -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo 'Caches removed; inventory and model artifacts preserved.'

package: validate check-tools ## Build ZIP, then verify archive integrity
	@rm -f '$(PACKAGE_ABS)'
	@cd .. && zip -qr '$(PACKAGE_ABS)' '$(PROJECT_DIR)' \
		-x '$(PROJECT_DIR)/.git/*' '$(PROJECT_DIR)/**/__pycache__/*' \
		   '$(PROJECT_DIR)/**/*.pyc' '$(PROJECT_DIR)/ai-inventory-export/*' \
		   '$(PROJECT_DIR)/model-export/*'
	@unzip -t '$(PACKAGE_ABS)' >/dev/null
	@echo 'Package created and verified: $(PACKAGE_ABS)'
