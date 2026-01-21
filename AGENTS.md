# AGENTS.md

## Project Overview

Luminary is an AI-powered code reviewer for GitLab Merge Requests. It uses LLM providers to analyze code changes and generate inline comments with validation.

**Architecture:** Clean Architecture with 4 layers (dependencies flow inward only):
- **Presentation** (`cli.py`) - CLI interface using Click
- **Application** (`application/`) - Orchestration services (ReviewService, MRReviewService)
- **Domain** (`domain/`) - Business logic, models, validators, prompts (no external dependencies)
- **Infrastructure** (`infrastructure/`) - External integrations (GitLab, LLM providers, config, HTTP)

**Current Status:** MVP completed (v0.1.0). Fully functional and ready for use.

## Setup Commands

### Install dependencies

```bash
# Development setup (includes pytest, black, ruff)
python -m pip install -e ".[dev]"

# Production setup
python -m pip install -e .
```

### Environment Variables

**Required for LLM providers:**
- `OPENROUTER_API_KEY` - for OpenRouter provider
- `OPENAI_API_KEY` - for OpenAI provider
- `DEEPSEEK_API_KEY` - for DeepSeek provider
- `VLLM_API_URL` - for VLLM provider (optional, defaults to `http://localhost:8000/v1/chat/completions`)
- `VLLM_API_KEY` - for VLLM provider (optional)

**Required for GitLab integration:**
- `GITLAB_TOKEN` - GitLab API token (required for MR reviews)
- `GITLAB_URL` - GitLab instance URL (optional, defaults to `gitlab.com`)

**Optional configuration overrides:**
- `LUMINARY_LLM_PROVIDER` - override provider from config
- `LUMINARY_LLM_MODEL` - override model from config

**Note:** Mock provider requires no API keys and is always available for testing.

## Development Commands

### Run tests

```bash
# All tests
python -m pytest

# With verbose output
python -m pytest -v

# Specific test file
python -m pytest tests/test_review_service.py

# Specific test
python -m pytest tests/test_mock_provider.py::test_mock_provider_basic

# With coverage
python -m pytest --cov=src/luminary
```

### Code formatting and linting

```bash
# Format code (Black)
uv run black src/
# Or with pip
black src/

# Lint code (Ruff)
uv run ruff check src/
# Or with pip
ruff check src/

# Auto-fix linting issues
ruff check --fix src/
```

### Run CLI commands

```bash
# Review a file (uses mock provider by default)
luminary file examples/sample_code.py

# With specific provider
luminary file examples/sample_code.py --provider mock

# With verbose logging
luminary file examples/sample_code.py --verbose

# Disable validation
luminary file examples/sample_code.py --no-validate

# Review MR (dry run - doesn't post comments)
luminary mr group/project 123 --no-post

# Review MR with posting (requires GITLAB_TOKEN)
luminary mr group/project 123 --provider openrouter
```

## Code Style

- **Python version:** >= 3.10
- **Line length:** 100 characters
- **Formatter:** Black (line-length=100, target-version=py310)
- **Linter:** Ruff (selects E, F, I, N, W rules, ignores E501)
- **Type hints:** Use type hints for function parameters and return values
- **Docstrings:** Add docstrings for public methods and classes (Google style)
- **Imports:** Use absolute imports from `luminary.*`

## Architecture Guidelines

### Layer Dependencies (CRITICAL)

**Important:** Dependencies flow inward only. This is the core principle of Clean Architecture:

- ✅ **Infrastructure → Domain** (allowed)
- ✅ **Application → Domain** (allowed)
- ✅ **Presentation → Application** (allowed)
- ❌ **Domain → Infrastructure** (forbidden)
- ❌ **Domain → Application** (forbidden)

**Example:**
- Domain models (`domain/models/`) should NOT import from infrastructure
- Domain validators (`domain/validators/`) should NOT import from infrastructure
- Application services can import from both Domain and Infrastructure

### File Structure

```
src/luminary/
├── domain/              # Business logic (no external dependencies)
│   ├── models/          # FileChange, Comment, ReviewResult
│   ├── prompts/         # ReviewPromptBuilder, ValidationPromptBuilder
│   └── validators/      # CommentValidator
├── application/         # Orchestration services
│   ├── review_service.py      # ReviewService (single file)
│   └── mr_review_service.py   # MRReviewService (multiple files)
├── infrastructure/      # External integrations
│   ├── llm/            # LLM providers (mock, openrouter, openai, deepseek, vllm)
│   ├── gitlab/         # GitLabClient
│   ├── config/         # ConfigManager
│   ├── http_client.py  # HTTP client with retry
│   ├── diff_parser.py  # Parse unified diff
│   └── file_filter.py  # Filter binary/ignored files
├── presentation/        # Presentation layer (currently empty, CLI is in cli.py)
└── cli.py              # CLI interface (Click)
```

### Adding a New LLM Provider

1. Create provider class in `src/luminary/infrastructure/llm/`
2. Inherit from `LLMProvider` (ABC) or `OpenAICompatibleChatProvider` (for OpenAI-compatible APIs)
3. Implement required methods: `generate_review()`, `validate_comment()`
4. Register in `LLMProviderFactory.PROVIDERS` dictionary
5. Add tests in `tests/test_llm_provider_factory.py`
6. Update documentation with required environment variables

**Example providers:**
- `MockLLMProvider` - for testing (no API key needed)
- `OpenRouterProvider` - OpenRouter API
- `OpenAIProvider` - OpenAI API
- `DeepSeekProvider` - DeepSeek API
- `VLLMProvider` - Local OpenAI-compatible server

### Configuration Priority

Configuration is merged in this order (later overrides earlier):

1. **Default values** (hardcoded in `ConfigManager.DEFAULT_CONFIG`)
2. **`.ai-reviewer.yml` file** (searched from current directory up to root)
3. **Environment variables** (`LUMINARY_*`, `GITLAB_*`, provider-specific keys)
4. **CLI arguments** (highest priority)

### Configuration File

Create `.ai-reviewer.yml` in project root. See `examples/ai-reviewer-config-java.yml` for reference.

**Key sections:**
- `llm` - LLM provider settings (provider, model, temperature, max_tokens, top_p)
- `validator` - Comment validation settings (enabled, provider, model, threshold)
- `comments` - Comment mode (inline/summary/both), severity_levels, markdown
- `limits` - Processing limits (max_files, max_lines, max_context_tokens, chunk_overlap_size)
- `retry` - Retry strategy (max_attempts, backoff_multiplier, initial_delay)
- `ignore` - File filtering patterns (patterns list, binary_files boolean)
- `prompts` - Custom prompt templates (review, validation)
- `gitlab` - GitLab settings (url, token)

## Testing Instructions

### Mock Provider

Always use `mock` provider for testing without API keys:

```bash
luminary file examples/sample_code.py --provider mock
```

Mock provider returns predefined responses and doesn't require any API keys.

### Test Structure

- **Unit tests:** `tests/test_*.py`
- **Integration tests:** `tests/test_*_integration.py`
- **Test naming:** `test_<functionality>`
- **Test classes:** `Test*` (pytest convention)

### Running Tests Before Commits

Always run these commands before committing:

```bash
python -m pytest
black src/
ruff check src/
```

### Test Coverage

Current test files:
- `test_cli.py` - CLI interface tests
- `test_gitlab_client.py` - GitLab client tests
- `test_http_client_retry.py` - HTTP retry logic tests
- `test_llm_provider_factory.py` - LLM provider factory tests
- `test_mock_provider.py` - Mock provider tests
- `test_mr_review_service_integration.py` - MR review integration tests
- `test_review_service.py` - Review service tests
- `test_review_service_features.py` - Review service feature tests

## Docker

### Build

```bash
docker build -t luminary:local .
```

**Note:** Uses multi-stage build (Python 3.11-slim). Final image size is optimized.

### Run

```bash
# Review file (mount current directory)
docker run --rm -v "%cd%:/work" -w /work luminary:local file examples/sample_code.py --provider mock

# Review MR (requires environment variables)
docker run --rm \
  -e GITLAB_TOKEN=... \
  -e OPENROUTER_API_KEY=... \
  luminary:local mr group/project 123
```

**Windows PowerShell:**
```powershell
docker run --rm -v "${PWD}:/work" -w /work luminary:local file examples/sample_code.py --provider mock
```

## GitLab CI/CD

The project includes `.gitlab-ci.yml` with three stages:

1. **`build`** - Builds Docker image and pushes to GitLab Container Registry
2. **`test`** - Runs pytest tests
3. **`ai_review`** - Runs Luminary to review the MR (uses built image)

**Required CI/CD variables:**
- `GITLAB_TOKEN` - GitLab API token
- `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` - LLM provider key

**Note:** The `ai_review` stage only runs for merge request pipelines (`CI_MERGE_REQUEST_IID`).

## Important Notes

### Mock Provider
- Always available, no API key needed
- Use for development and testing
- Returns predefined responses

### File Filtering
- Binary files are automatically filtered (configurable via `ignore.binary_files`)
- Patterns in `ignore.patterns` are filtered using glob matching
- Default patterns: `*.lock`, `*.min.js`, `*.min.css`, `*.map`, `node_modules/**`, `.git/**`

### Comment Validation
- Disabled by default (can be enabled with `validator.enabled: true` in config)
- Can be disabled via `--no-validate` CLI flag (overrides config)
- Uses LLM to validate comment relevance, usefulness, and non-redundancy
- Invalid comments are rejected and logged
- Validation threshold is configurable (default: 0.7)

### Chunking
- Large files are automatically chunked when exceeding `max_context_tokens`
- Chunks overlap by `chunk_overlap_size` lines (default: 200) to preserve context
- Comments from chunks are aggregated

### Retry Logic
- Both LLM and GitLab API calls have retry with exponential backoff
- Configurable via `retry` section in config
- Default: 3 attempts, initial delay 1s, backoff multiplier 2

### Error Handling
- Use `_die()` helper in CLI for user-friendly error messages
- Errors are logged with context
- Use `--verbose` flag for detailed error information

### Comment Modes
- `inline` - Only inline comments to specific lines
- `summary` - Only summary comments per file
- `both` - Both inline and summary comments (default)

## Common Tasks

### Add a new feature

1. **Start from Domain layer** - Define models and business logic first
2. **Add to Application layer** - Create orchestration services
3. **Wire up in Infrastructure layer** - Add external integrations if needed
4. **Expose via CLI** - Add CLI commands if needed
5. **Add tests** - Write tests for new functionality

### Debug issues

- Use `--verbose` flag for detailed logging
- Check `.ai-reviewer.yml` configuration
- Verify environment variables are set correctly
- Use `--no-post` for MR reviews to test without posting comments
- Check logs for validation rejections

### Update dependencies

1. Edit `pyproject.toml`
2. Run `python -m pip install -e ".[dev]"` to update
3. Test that everything still works
4. Run `pytest` to ensure no regressions

### Add a new LLM provider

1. Create provider class in `src/luminary/infrastructure/llm/<provider_name>.py`
2. Inherit from `LLMProvider` or `OpenAICompatibleChatProvider`
3. Implement `generate_review()` and `validate_comment()` methods
4. Register in `LLMProviderFactory.PROVIDERS`
5. Add environment variable documentation
6. Add tests
7. Update README.md with provider information

### Modify prompts

- Default prompts are in `src/luminary/domain/prompts/`
- Custom prompts can be provided via `prompts.review` and `prompts.validation` in config
- Prompts use f-string templating (consider Jinja2 for future improvements)

## Known Issues and Future Improvements

See `docs/ARCHITECTURE_SUMMARY.md` for detailed recommendations:

1. **Retry logic duplication** - Consider using `tenacity` or `backoff` library
2. **Configuration validation** - Consider using `pydantic` for schema validation
3. **Prompt templating** - Consider migrating to Jinja2 for complex templates
4. **Async support** - Consider migrating to `httpx` or `aiohttp` for async HTTP
5. **Parallel processing** - Currently sequential, could be parallelized

## Documentation

- **README.md** - Main project documentation
- **QUICK_START.md** - Quick start guide
- **ROADMAP.md** - Development roadmap
- **docs/ARCHITECTURE_SUMMARY.md** - Architecture overview and recommendations
- **docs/ADR/** - Architecture Decision Records
- **examples/** - Example configurations and code samples

## Getting Help

- Check `luminary --help` for CLI usage
- Review example configs in `examples/`
- See `docs/ARCHITECTURE_SUMMARY.md` for architecture details
- Check test files for usage examples
