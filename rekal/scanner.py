"""Codebase scanner for project initialization.

Walks a project directory, extracts structural patterns (classes, routes,
models, exceptions, enums), and synthesizes ready-to-store memory candidates.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────

SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".eggs",
        "site-packages",
        ".next",
        ".nuxt",
        "target",
        "vendor",
        ".cargo",
        "coverage",
        "htmlcov",
        ".hatch",
    }
)

SKIP_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".whl",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".min.js",
        ".min.css",
        ".map",
    }
)

SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".rb",
        ".swift",
        ".cs",
    }
)

CONFIG_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "package.json",
        "tsconfig.json",
        "deno.json",
        "Cargo.toml",
        "go.mod",
        "Gemfile",
        "build.gradle",
        "pom.xml",
        "Makefile",
        "Justfile",
        "Taskfile.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Dockerfile",
        ".env.example",
        ".envrc",
    }
)

DOC_NAMES = frozenset(
    {
        "README.md",
        "README.rst",
        "README.txt",
        "CLAUDE.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "ARCHITECTURE.md",
        "DESIGN.md",
    }
)

MAX_FILE_SIZE = 50_000  # characters
MAX_MODULE_DEPTH = 3
MAX_LIST_OVERVIEW = 15
MAX_ROUTE_DISPLAY = 10
MAX_LIST_DETAIL = 20
MIN_MODULE_FILES = 3
MAX_JS_DEPS_DISPLAY = 20

# ── Regex patterns ───────────────────────────────────────────────────

PY_CLASS = re.compile(r"^class\s+(\w+)(?:\(([^)]*)\))?:", re.MULTILINE)

PY_ROUTE = re.compile(
    r"@\w+(?:\.\w+)*\."
    r"(get|post|put|patch|delete|head|options|websocket|route)"
    r"\s*\(\s*[\"']([^\"']*)[\"']",
)

PY_MODEL_BASES = re.compile(
    r"class\s+(\w+)\([^)]*(?:BaseModel|TypedDict|NamedTuple)[^)]*\):",
    re.MULTILINE,
)

PY_DATACLASS = re.compile(
    r"@dataclass[^\n]*\n(?:\s*#[^\n]*\n|\s*\n)*class\s+(\w+)",
    re.MULTILINE,
)

PY_EXCEPTION = re.compile(
    r"class\s+(\w+)\([^)]*(?:Error|Exception)[^)]*\):",
    re.MULTILINE,
)

PY_ENUM = re.compile(
    r"class\s+(\w+)\([^)]*(?:Enum|IntEnum|StrEnum|Flag)[^)]*\):",
    re.MULTILINE,
)

SQL_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?",
    re.IGNORECASE | re.MULTILINE,
)

PROTO_SERVICE = re.compile(r"service\s+(\w+)\s*\{", re.MULTILINE)
PROTO_MESSAGE = re.compile(r"message\s+(\w+)\s*\{", re.MULTILINE)


# ── Data types ───────────────────────────────────────────────────────


@dataclass
class ModuleInfo:
    """Info about a source module/package."""

    name: str
    path: str
    file_count: int = 0
    classes: list[str] = field(default_factory=list)
    routes: list[tuple[str, str]] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    enums: list[str] = field(default_factory=list)
    docstring: str = ""


@dataclass
class MemoryCandidate:
    """A ready-to-store memory extracted from scanning."""

    content: str
    memory_type: str
    tags: list[str]
    category: str


@dataclass
class ScanResult:
    """Complete scan results for a project."""

    project_dir: str
    total_files: int = 0
    total_source_files: int = 0
    top_level_dirs: list[str] = field(default_factory=list)
    modules: list[ModuleInfo] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)
    adr_files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    sql_tables: list[str] = field(default_factory=list)
    proto_services: list[str] = field(default_factory=list)
    proto_messages: list[str] = field(default_factory=list)
    memories: list[MemoryCandidate] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────


def should_skip_dir(name: str) -> bool:
    """Check if a directory should be skipped during scanning."""
    return name in SKIP_DIRS or name.endswith(".egg-info")


def should_skip_file(path: Path) -> bool:
    """Check if a file should be skipped during scanning."""
    return path.suffix in SKIP_SUFFIXES


def is_source_file(path: Path) -> bool:
    """Check if a file is a source code file."""
    return path.suffix in SOURCE_SUFFIXES


def read_file_safe(path: Path, max_size: int = MAX_FILE_SIZE) -> str:
    """Read a file safely, capping at max_size characters."""
    try:
        with path.open(errors="replace") as f:
            return f.read(max_size)
    except OSError:
        return ""


# ── Module discovery ─────────────────────────────────────────────────


def extract_module_docstring(init_path: Path) -> str:
    """Extract the module docstring from __init__.py."""
    content = read_file_safe(init_path, max_size=2000)
    if not content:
        return ""
    stripped = content.lstrip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            start = stripped.index(quote) + 3
            end = stripped.find(quote, start)
            if end != -1:
                return stripped[start:end].strip()[:200]
    return ""


def discover_modules(root: Path) -> list[ModuleInfo]:
    """Find Python packages at up to 3 levels deep."""
    modules: list[ModuleInfo] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip_dir(d))
        dirpath = Path(dirpath_str)
        rel = dirpath.relative_to(root)
        if len(rel.parts) > MAX_MODULE_DEPTH:
            continue
        if "__init__.py" in filenames:
            name = str(rel) if str(rel) != "." else root.name
            modules.append(ModuleInfo(name=name, path=str(rel)))
    return modules


# ── File scanning ────────────────────────────────────────────────────


def scan_python_file(
    path: Path,
) -> tuple[
    list[str],  # classes
    list[tuple[str, str]],  # routes (method, path)
    list[str],  # models
    list[str],  # exceptions
    list[str],  # enums
]:
    """Extract patterns from a single Python file."""
    content = read_file_safe(path)
    if not content:
        return [], [], [], [], []

    classes = [m.group(1) for m in PY_CLASS.finditer(content)]
    routes = [(m.group(1).upper(), m.group(2)) for m in PY_ROUTE.finditer(content)]

    models = [m.group(1) for m in PY_MODEL_BASES.finditer(content)]
    models.extend(m.group(1) for m in PY_DATACLASS.finditer(content))
    models = list(dict.fromkeys(models))  # dedupe, preserve order

    exceptions = [m.group(1) for m in PY_EXCEPTION.finditer(content)]
    enums = [m.group(1) for m in PY_ENUM.finditer(content)]

    return classes, routes, models, exceptions, enums


def scan_module(mod: ModuleInfo, root: Path) -> None:
    """Scan Python files in a module, excluding sub-packages."""
    pkg_dir = root / mod.path if mod.path != "." else root

    init_path = pkg_dir / "__init__.py"
    if init_path.is_file():
        mod.docstring = extract_module_docstring(init_path)

    for dirpath_str, dirnames, filenames in os.walk(pkg_dir):
        dirpath = Path(dirpath_str)
        # Skip sub-packages (they get their own ModuleInfo)
        dirnames[:] = [
            d
            for d in dirnames
            if not should_skip_dir(d) and not (dirpath / d / "__init__.py").is_file()
        ]
        for f in sorted(filenames):
            if f.endswith(".py"):
                mod.file_count += 1
                classes, routes, models, exceptions, enums = scan_python_file(dirpath / f)
                mod.classes.extend(classes)
                mod.routes.extend(routes)
                mod.models.extend(models)
                mod.exceptions.extend(exceptions)
                mod.enums.extend(enums)


# ── Extra patterns ───────────────────────────────────────────────────


def scan_sql_files(root: Path) -> list[str]:
    """Find CREATE TABLE statements in .sql files."""
    tables: list[str] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for f in filenames:
            if f.endswith(".sql"):
                content = read_file_safe(Path(dirpath_str) / f)
                tables.extend(m.group(1) for m in SQL_TABLE.finditer(content))
    return list(dict.fromkeys(tables))


def scan_proto_files(root: Path) -> tuple[list[str], list[str]]:
    """Find service and message definitions in .proto files."""
    services: list[str] = []
    messages: list[str] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for f in filenames:
            if f.endswith(".proto"):
                content = read_file_safe(Path(dirpath_str) / f)
                services.extend(m.group(1) for m in PROTO_SERVICE.finditer(content))
                messages.extend(m.group(1) for m in PROTO_MESSAGE.finditer(content))
    return list(dict.fromkeys(services)), list(dict.fromkeys(messages))


# ── File discovery ───────────────────────────────────────────────────


def find_doc_files(root: Path) -> list[str]:
    """Find documentation files that exist."""
    found: list[str] = []
    for name in sorted(DOC_NAMES):
        if (root / name).is_file():
            found.append(name)
    if (root / ".claude" / "CLAUDE.md").is_file():
        found.append(".claude/CLAUDE.md")
    return found


def find_adr_files(root: Path) -> list[str]:
    """Find ADR markdown files."""
    seen: set[str] = set()
    adrs: list[str] = []
    for pattern in (
        "ADR/*.md",
        "adr/*.md",
        "docs/adr/*.md",
        "docs/ADR/*.md",
        "docs/decisions/*.md",
    ):
        for path in sorted(root.glob(pattern)):
            rel = str(path.relative_to(root))
            if rel not in seen:
                seen.add(rel)
                adrs.append(rel)
    return adrs[:20]


def find_config_files(root: Path) -> list[str]:
    """Find config files that exist."""
    found: list[str] = []
    for name in sorted(CONFIG_NAMES):
        if (root / name).is_file():
            found.append(name)
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for f in sorted(workflows.glob("*.yml"))[:5]:
            found.append(f".github/workflows/{f.name}")
    if (root / ".gitlab-ci.yml").is_file():
        found.append(".gitlab-ci.yml")
    return found


# ── Dependency parsing ───────────────────────────────────────────────


def parse_pyproject_deps(root: Path) -> MemoryCandidate | None:
    """Parse pyproject.toml and synthesize a stack memory."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    content = read_file_safe(path)
    if not content:
        return None
    try:
        data = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, ValueError):
        return None

    project = data.get("project", {})
    name = project.get("name", "")
    python_req = project.get("requires-python", "")
    deps = project.get("dependencies", [])

    if not deps and not python_req:
        return None

    dep_names: list[str] = []
    for dep in deps:
        dep_name = re.split(r"[>=<!\[;]", dep)[0].strip()
        if dep_name:
            dep_names.append(dep_name)

    parts: list[str] = []
    if name:
        parts.append(f"Project: {name}.")
    if python_req:
        parts.append(f"Python {python_req}.")
    if dep_names:
        parts.append(f"Deps: {', '.join(dep_names)}.")

    return MemoryCandidate(
        content=" ".join(parts),
        memory_type="fact",
        tags=["stack", "dependencies", "python"],
        category="stack",
    )


def parse_package_json_deps(root: Path) -> MemoryCandidate | None:
    """Parse package.json and synthesize a stack memory."""
    path = root / "package.json"
    if not path.is_file():
        return None
    content = read_file_safe(path)
    if not content:
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None

    name = data.get("name", "")
    deps = list(data.get("dependencies", {}).keys())

    if not deps:
        return None

    parts: list[str] = []
    if name:
        parts.append(f"Project: {name}.")
    parts.append(f"Deps: {', '.join(deps[:MAX_JS_DEPS_DISPLAY])}.")
    if len(deps) > MAX_JS_DEPS_DISPLAY:
        parts.append(f"+ {len(deps) - MAX_JS_DEPS_DISPLAY} more.")

    return MemoryCandidate(
        content=" ".join(parts),
        memory_type="fact",
        tags=["stack", "dependencies", "javascript"],
        category="stack",
    )


# ── Memory synthesis ─────────────────────────────────────────────────


def truncate_list(items: list[str], limit: int) -> str:
    """Join items with comma, adding '+ N more' if truncated."""
    shown = ", ".join(items[:limit])
    if len(items) > limit:
        shown += f" + {len(items) - limit} more"
    return shown


def summarize_single_module(mod: ModuleInfo) -> MemoryCandidate | None:
    """Summarize a single module into a memory candidate."""
    mod_parts = [f"Module '{mod.name}': {mod.file_count} files."]
    if mod.docstring:
        mod_parts.append(mod.docstring + ".")
    if mod.classes:
        mod_parts.append(f"Classes: {truncate_list(mod.classes, MAX_LIST_OVERVIEW)}.")
    if mod.routes:
        route_strs = [f"{m} {p}" for m, p in mod.routes[:MAX_ROUTE_DISPLAY]]
        extra = len(mod.routes) - MAX_ROUTE_DISPLAY
        suffix = f" + {extra} more" if extra > 0 else ""
        mod_parts.append(f"Routes: {', '.join(route_strs)}{suffix}.")
    if mod.models:
        mod_parts.append(f"Models: {truncate_list(mod.models, MAX_ROUTE_DISPLAY)}.")
    if mod.exceptions:
        mod_parts.append(f"Exceptions: {truncate_list(mod.exceptions, MAX_ROUTE_DISPLAY)}.")
    if mod.enums:
        mod_parts.append(f"Enums: {truncate_list(mod.enums, MAX_ROUTE_DISPLAY)}.")

    if len(mod_parts) <= 1 and mod.file_count < MIN_MODULE_FILES:
        return None

    return MemoryCandidate(
        content=" ".join(mod_parts),
        memory_type="fact",
        tags=["module", mod.name.split("/")[-1], "architecture"],
        category="module-detail",
    )


def synthesize_module_memories(result: ScanResult) -> list[MemoryCandidate]:
    """Synthesize module map and per-module memories."""
    memories: list[MemoryCandidate] = []
    if not result.modules:
        return memories

    # Module map overview
    parts: list[str] = []
    for mod in result.modules:
        desc = mod.name
        if mod.file_count:
            desc += f" ({mod.file_count} files)"
        if mod.docstring:
            desc += f" — {mod.docstring}"
        parts.append(desc)

    limit = MAX_LIST_OVERVIEW
    content = f"Module map — {len(result.modules)} packages: " + "; ".join(parts[:limit])
    if len(parts) > limit:
        content += f"; + {len(parts) - limit} more"
    memories.append(
        MemoryCandidate(
            content=content,
            memory_type="fact",
            tags=["architecture", "module-map", "structure"],
            category="module-map",
        )
    )

    # Per-module summaries
    for mod in result.modules:
        candidate = summarize_single_module(mod)
        if candidate:
            memories.append(candidate)

    return memories


def synthesize_cross_cutting_memories(result: ScanResult) -> list[MemoryCandidate]:
    """Synthesize API, model, exception, and enum overview memories."""
    memories: list[MemoryCandidate] = []

    # API surface
    all_routes: list[str] = []
    for mod in result.modules:
        for method, path in mod.routes:
            all_routes.append(f"{method} {path} ({mod.name})")
    if all_routes:
        sample = "; ".join(all_routes[:MAX_LIST_DETAIL])
        extra = len(all_routes) - MAX_LIST_DETAIL
        suffix = f"... + {extra} more" if extra > 0 else ""
        memories.append(
            MemoryCandidate(
                content=f"API surface — {len(all_routes)} endpoints: {sample}{suffix}",
                memory_type="fact",
                tags=["api", "routes", "endpoints"],
                category="api-surface",
            )
        )

    # Domain models
    all_models = [f"{m} ({mod.name})" for mod in result.modules for m in mod.models]
    if all_models:
        listing = truncate_list(all_models, 25)
        memories.append(
            MemoryCandidate(
                content=f"Domain models — {len(all_models)} types: {listing}",
                memory_type="fact",
                tags=["domain-model", "types", "schema"],
                category="domain-model",
            )
        )

    # Exceptions
    all_exc = [f"{e} ({mod.name})" for mod in result.modules for e in mod.exceptions]
    if all_exc:
        memories.append(
            MemoryCandidate(
                content=(
                    f"Exception types — {len(all_exc)}: {truncate_list(all_exc, MAX_LIST_DETAIL)}"
                ),
                memory_type="fact",
                tags=["errors", "exceptions", "error-handling"],
                category="exceptions",
            )
        )

    # Enums
    all_enums = [f"{e} ({mod.name})" for mod in result.modules for e in mod.enums]
    if all_enums:
        memories.append(
            MemoryCandidate(
                content=f"Enums — {len(all_enums)}: {truncate_list(all_enums, MAX_LIST_DETAIL)}",
                memory_type="fact",
                tags=["enums", "domain", "types"],
                category="enums",
            )
        )

    return memories


def synthesize_infra_memories(result: ScanResult) -> list[MemoryCandidate]:
    """Synthesize SQL, proto, structure, stack, and ADR memories."""
    memories: list[MemoryCandidate] = []

    if result.sql_tables:
        memories.append(
            MemoryCandidate(
                content=(
                    f"DB tables — {len(result.sql_tables)}: {truncate_list(result.sql_tables, 30)}"
                ),
                memory_type="fact",
                tags=["database", "tables", "schema"],
                category="database",
            )
        )

    if result.proto_services:
        svc = truncate_list(result.proto_services, MAX_LIST_OVERVIEW)
        msg = truncate_list(result.proto_messages, MAX_LIST_OVERVIEW)
        memories.append(
            MemoryCandidate(
                content=f"gRPC services: {svc}. Messages: {msg}",
                memory_type="fact",
                tags=["grpc", "proto", "api"],
                category="grpc",
            )
        )

    memories.append(
        MemoryCandidate(
            content=(
                f"Project structure — {result.total_files} files total, "
                f"{result.total_source_files} source files. "
                f"Top dirs: {', '.join(result.top_level_dirs[:MAX_LIST_OVERVIEW])}"
            ),
            memory_type="fact",
            tags=["structure", "layout", "overview"],
            category="structure",
        )
    )

    pyproject_mem = parse_pyproject_deps(Path(result.project_dir))
    if pyproject_mem:
        memories.append(pyproject_mem)

    pkg_json_mem = parse_package_json_deps(Path(result.project_dir))
    if pkg_json_mem:
        memories.append(pkg_json_mem)

    if result.adr_files:
        memories.append(
            MemoryCandidate(
                content=(
                    f"ADRs — {len(result.adr_files)} decision records: "
                    f"{', '.join(result.adr_files)}"
                ),
                memory_type="fact",
                tags=["adr", "decisions", "architecture"],
                category="adr-list",
            )
        )

    return memories


def synthesize_memories(result: ScanResult) -> list[MemoryCandidate]:
    """Generate ready-to-store memory candidates from scan data."""
    memories: list[MemoryCandidate] = []
    memories.extend(synthesize_module_memories(result))
    memories.extend(synthesize_cross_cutting_memories(result))
    memories.extend(synthesize_infra_memories(result))
    return memories


# ── Main scan function ───────────────────────────────────────────────


def scan_project(directory: str) -> ScanResult:
    """Scan a project directory and return structured findings with memory candidates.

    Walks the directory tree, discovers Python packages, extracts classes/routes/models/
    exceptions/enums, finds config and doc files, parses dependencies, and synthesizes
    ready-to-store memory candidates.
    """
    root = Path(directory).resolve()

    if not root.is_dir():
        return ScanResult(project_dir=str(root))

    # Walk the tree once for file counts and top-level dirs
    total_files = 0
    total_source = 0
    top_level_dirs: list[str] = []

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip_dir(d))
        dirpath = Path(dirpath_str)
        if dirpath == root:
            top_level_dirs = list(dirnames)
        for f in filenames:
            fpath = dirpath / f
            if not should_skip_file(fpath):
                total_files += 1
                if is_source_file(fpath):
                    total_source += 1

    # Discover and scan modules
    modules = discover_modules(root)
    for mod in modules:
        scan_module(mod, root)

    # Extra patterns
    sql_tables = scan_sql_files(root)
    proto_services, proto_messages = scan_proto_files(root)

    # File discovery
    doc_files = find_doc_files(root)
    adr_files = find_adr_files(root)
    config_files = find_config_files(root)

    result = ScanResult(
        project_dir=str(root),
        total_files=total_files,
        total_source_files=total_source,
        top_level_dirs=top_level_dirs,
        modules=modules,
        doc_files=doc_files,
        adr_files=adr_files,
        config_files=config_files,
        sql_tables=sql_tables,
        proto_services=proto_services,
        proto_messages=proto_messages,
    )

    result.memories = synthesize_memories(result)

    return result
