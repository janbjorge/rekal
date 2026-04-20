"""Tests for codebase scanner."""

from __future__ import annotations

import json
from pathlib import Path

from rekal.scanner import (
    MemoryCandidate,
    ScanResult,
    discover_modules,
    extract_module_docstring,
    find_adr_files,
    find_config_files,
    find_doc_files,
    is_source_file,
    parse_package_json_deps,
    parse_pyproject_deps,
    read_file_safe,
    scan_module,
    scan_project,
    scan_proto_files,
    scan_python_file,
    scan_sql_files,
    should_skip_dir,
    should_skip_file,
    synthesize_memories,
    truncate_list,
)

# ── Helpers ──────────────────────────────────────────────────────────


def test_should_skip_dir_known() -> None:
    assert should_skip_dir("__pycache__")
    assert should_skip_dir(".git")
    assert should_skip_dir("node_modules")


def test_should_skip_dir_egg_info() -> None:
    assert should_skip_dir("mypackage.egg-info")


def test_should_skip_dir_normal() -> None:
    assert not should_skip_dir("src")
    assert not should_skip_dir("rekal")


def test_should_skip_file(tmp_path: Path) -> None:
    assert should_skip_file(tmp_path / "image.png")
    assert should_skip_file(tmp_path / "archive.zip")
    assert not should_skip_file(tmp_path / "code.py")
    assert not should_skip_file(tmp_path / "data.json")


def test_is_source_file(tmp_path: Path) -> None:
    assert is_source_file(tmp_path / "main.py")
    assert is_source_file(tmp_path / "app.ts")
    assert is_source_file(tmp_path / "lib.rs")
    assert not is_source_file(tmp_path / "readme.md")
    assert not is_source_file(tmp_path / "data.json")


def test_read_file_safe(tmp_path: Path) -> None:
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    assert read_file_safe(f) == "hello world"


def test_read_file_safe_missing(tmp_path: Path) -> None:
    assert read_file_safe(tmp_path / "nope.txt") == ""


def test_read_file_safe_max_size(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_text("x" * 1000)
    assert len(read_file_safe(f, max_size=100)) == 100


def test_truncate_list_short() -> None:
    assert truncate_list(["a", "b", "c"], 5) == "a, b, c"


def test_truncate_list_truncated() -> None:
    result = truncate_list(["a", "b", "c", "d", "e"], 3)
    assert result == "a, b, c + 2 more"


# ── Module docstrings ────────────────────────────────────────────────


def test_extract_module_docstring(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('"""This is the module docstring."""\n')
    assert extract_module_docstring(init) == "This is the module docstring."


def test_extract_module_docstring_single_quotes(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text("'''Single quote docstring.'''\n")
    assert extract_module_docstring(init) == "Single quote docstring."


def test_extract_module_docstring_none(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text("# no docstring\nimport os\n")
    assert extract_module_docstring(init) == ""


def test_extract_module_docstring_missing_file(tmp_path: Path) -> None:
    assert extract_module_docstring(tmp_path / "nope.py") == ""


def test_extract_module_docstring_truncated(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text(f'"""{"a" * 300}"""')
    result = extract_module_docstring(init)
    assert len(result) == 200


# ── Module discovery ─────────────────────────────────────────────────


def test_discover_modules(tmp_path: Path) -> None:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    sub = pkg / "auth"
    sub.mkdir()
    (sub / "__init__.py").write_text("")

    modules = discover_modules(tmp_path)
    names = [m.name for m in modules]
    assert "myapp" in names
    assert "myapp/auth" in names


def test_discover_modules_skips_pycache(tmp_path: Path) -> None:
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "__init__.py").write_text("")

    modules = discover_modules(tmp_path)
    names = [m.name for m in modules]
    assert "__pycache__" not in names


def test_discover_modules_depth_limit(tmp_path: Path) -> None:
    # Create a package 5 levels deep — should not be found
    d = tmp_path
    for name in ["a", "b", "c", "d", "e"]:
        d = d / name
        d.mkdir()
        (d / "__init__.py").write_text("")

    modules = discover_modules(tmp_path)
    # Only up to 3 levels deep (a, a/b, a/b/c, a/b/c/d = 4 parts from a)
    # Actually: rel for "a" = "a" (1 part), "a/b" (2), "a/b/c" (3), "a/b/c/d" (4 > 3)
    names = [m.name for m in modules]
    assert "a" in names
    assert "a/b" in names
    assert "a/b/c" in names
    assert "a/b/c/d" not in names


# ── Python file scanning ─────────────────────────────────────────────


def test_scan_python_file_classes(tmp_path: Path) -> None:
    f = tmp_path / "models.py"
    f.write_text("class User:\n    pass\n\nclass Order(Base):\n    pass\n")
    classes, routes, _models, _exceptions, _enums = scan_python_file(f)
    assert classes == ["User", "Order"]
    assert routes == []


def test_scan_python_file_routes(tmp_path: Path) -> None:
    f = tmp_path / "routes.py"
    f.write_text(
        '@router.get("/users")\n'
        "async def list_users(): ...\n\n"
        '@app.post("/orders")\n'
        "async def create_order(): ...\n"
    )
    _classes, routes, _models, _exceptions, _enums = scan_python_file(f)
    assert ("GET", "/users") in routes
    assert ("POST", "/orders") in routes


def test_scan_python_file_multiline_route(tmp_path: Path) -> None:
    f = tmp_path / "routes.py"
    f.write_text('@router.get(\n    "/items",\n    response_model=List,\n)\n')
    _, routes, _, _, _ = scan_python_file(f)
    assert ("GET", "/items") in routes


def test_scan_python_file_models(tmp_path: Path) -> None:
    f = tmp_path / "schemas.py"
    f.write_text(
        "from pydantic import BaseModel\n"
        "from dataclasses import dataclass\n\n"
        "class UserSchema(BaseModel):\n    name: str\n\n"
        "@dataclass\nclass Config:\n    debug: bool\n"
    )
    _, _, models, _, _ = scan_python_file(f)
    assert "UserSchema" in models
    assert "Config" in models


def test_scan_python_file_exceptions(tmp_path: Path) -> None:
    f = tmp_path / "errors.py"
    f.write_text(
        "class AuthError(Exception):\n    pass\n\nclass NotFoundError(AppError):\n    pass\n"
    )
    _, _, _, exceptions, _ = scan_python_file(f)
    assert "AuthError" in exceptions
    assert "NotFoundError" in exceptions


def test_scan_python_file_enums(tmp_path: Path) -> None:
    f = tmp_path / "types.py"
    f.write_text(
        "from enum import Enum, IntEnum\n\n"
        "class Status(Enum):\n    ACTIVE = 1\n\n"
        "class Priority(IntEnum):\n    LOW = 1\n"
    )
    _, _, _, _, enums = scan_python_file(f)
    assert "Status" in enums
    assert "Priority" in enums


def test_scan_python_file_missing(tmp_path: Path) -> None:
    result = scan_python_file(tmp_path / "nope.py")
    assert result == ([], [], [], [], [])


def test_scan_python_file_deduplicates_models(tmp_path: Path) -> None:
    f = tmp_path / "dual.py"
    # A class that matches both BaseModel and dataclass patterns should appear once
    f.write_text("class Foo(BaseModel):\n    x: int\n")
    _, _, models, _, _ = scan_python_file(f)
    assert models.count("Foo") == 1


# ── Module scanning ──────────────────────────────────────────────────


def test_scan_module(tmp_path: Path) -> None:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""My app."""\n')
    (pkg / "models.py").write_text("class User(BaseModel):\n    pass\n")
    (pkg / "routes.py").write_text('@router.get("/health")\ndef health(): ...\n')

    from rekal.scanner import ModuleInfo

    mod = ModuleInfo(name="myapp", path="myapp")
    scan_module(mod, tmp_path)

    assert mod.docstring == "My app."
    assert mod.file_count == 3  # __init__.py, models.py, routes.py
    assert "User" in mod.models
    assert ("GET", "/health") in mod.routes


def test_scan_module_excludes_sub_packages(tmp_path: Path) -> None:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("class Core: pass\n")
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "nested.py").write_text("class Nested: pass\n")

    from rekal.scanner import ModuleInfo

    mod = ModuleInfo(name="myapp", path="myapp")
    scan_module(mod, tmp_path)

    assert "Core" in mod.classes
    assert "Nested" not in mod.classes  # sub-package excluded


# ── SQL scanning ─────────────────────────────────────────────────────


def test_scan_sql_files(tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE users (id INT);\nCREATE TABLE IF NOT EXISTS orders (id INT);\n"
    )
    tables = scan_sql_files(tmp_path)
    assert "users" in tables
    assert "orders" in tables


def test_scan_sql_files_deduplicates(tmp_path: Path) -> None:
    (tmp_path / "a.sql").write_text("CREATE TABLE foo (id INT);")
    (tmp_path / "b.sql").write_text("CREATE TABLE foo (id INT);")
    tables = scan_sql_files(tmp_path)
    assert tables.count("foo") == 1


# ── Proto scanning ───────────────────────────────────────────────────


def test_scan_proto_files(tmp_path: Path) -> None:
    (tmp_path / "api.proto").write_text(
        "service UserService {\n  rpc GetUser(Req) returns (Resp);\n}\n"
        "message UserRequest {\n  string id = 1;\n}\n"
    )
    services, messages = scan_proto_files(tmp_path)
    assert "UserService" in services
    assert "UserRequest" in messages


# ── File discovery ───────────────────────────────────────────────────


def test_find_doc_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Hello")
    (tmp_path / "CLAUDE.md").write_text("# Rules")
    found = find_doc_files(tmp_path)
    assert "README.md" in found
    assert "CLAUDE.md" in found


def test_find_doc_files_claude_subdir(tmp_path: Path) -> None:
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "CLAUDE.md").write_text("# Rules")
    found = find_doc_files(tmp_path)
    assert ".claude/CLAUDE.md" in found


def test_find_adr_files(tmp_path: Path) -> None:
    adr = tmp_path / "ADR"
    adr.mkdir()
    (adr / "0001-use-sqlite.md").write_text("# ADR 1")
    (adr / "0002-use-python.md").write_text("# ADR 2")
    found = find_adr_files(tmp_path)
    # At least 2 (may be more on case-insensitive filesystems)
    assert len(found) >= 2
    assert "ADR/0001-use-sqlite.md" in found


def test_find_config_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
    (tmp_path / "Makefile").write_text("all:\n\techo hi")
    found = find_config_files(tmp_path)
    assert "Makefile" in found
    assert "pyproject.toml" in found


def test_find_config_files_github_workflows(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("name: CI")
    found = find_config_files(tmp_path)
    assert ".github/workflows/ci.yml" in found


def test_find_config_files_gitlab(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [build]")
    found = find_config_files(tmp_path)
    assert ".gitlab-ci.yml" in found


# ── Dependency parsing ───────────────────────────────────────────────


def test_parse_pyproject_deps(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nrequires-python = ">=3.11"\n'
        'dependencies = ["fastapi>=0.100", "pydantic>=2.0"]\n'
    )
    mem = parse_pyproject_deps(tmp_path)
    assert mem is not None
    assert "myapp" in mem.content
    assert "fastapi" in mem.content
    assert "pydantic" in mem.content
    assert mem.memory_type == "fact"


def test_parse_pyproject_deps_missing(tmp_path: Path) -> None:
    assert parse_pyproject_deps(tmp_path) is None


def test_parse_pyproject_deps_no_deps(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'empty'\n")
    assert parse_pyproject_deps(tmp_path) is None


def test_parse_pyproject_deps_malformed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("not valid toml {{{")
    assert parse_pyproject_deps(tmp_path) is None


def test_parse_package_json_deps(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "myapp", "dependencies": {"react": "^18", "next": "^14"}}'
    )
    mem = parse_package_json_deps(tmp_path)
    assert mem is not None
    assert "myapp" in mem.content
    assert "react" in mem.content
    assert "next" in mem.content


def test_parse_package_json_deps_missing(tmp_path: Path) -> None:
    assert parse_package_json_deps(tmp_path) is None


def test_parse_package_json_deps_no_deps(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "empty"}')
    assert parse_package_json_deps(tmp_path) is None


def test_parse_package_json_deps_malformed(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("not json")
    assert parse_package_json_deps(tmp_path) is None


def test_parse_pyproject_deps_empty_content(tmp_path: Path) -> None:
    f = tmp_path / "pyproject.toml"
    f.write_text("")
    assert parse_pyproject_deps(tmp_path) is None


def test_parse_package_json_deps_empty_content(tmp_path: Path) -> None:
    f = tmp_path / "package.json"
    f.write_text("")
    assert parse_package_json_deps(tmp_path) is None


def test_parse_package_json_deps_many_deps(tmp_path: Path) -> None:
    deps = {f"dep-{i}": "^1.0" for i in range(25)}
    (tmp_path / "package.json").write_text(json.dumps({"name": "big", "dependencies": deps}))
    mem = parse_package_json_deps(tmp_path)
    assert mem is not None
    assert "+ 5 more" in mem.content


# ── Memory synthesis ─────────────────────────────────────────────────


def test_synthesize_memories_module_map() -> None:
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[
            ModuleInfo(name="auth", path="auth", file_count=5, docstring="Auth module"),
            ModuleInfo(name="billing", path="billing", file_count=3),
        ],
    )
    memories = synthesize_memories(result)
    categories = [m.category for m in memories]
    assert "module-map" in categories
    module_map = next(m for m in memories if m.category == "module-map")
    assert "2 packages" in module_map.content
    assert "auth" in module_map.content


def test_synthesize_memories_per_module() -> None:
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[
            ModuleInfo(
                name="auth",
                path="auth",
                file_count=5,
                classes=["UserService", "AuthMiddleware"],
                routes=[("GET", "/login"), ("POST", "/register")],
                models=["UserSchema"],
            ),
        ],
    )
    memories = synthesize_memories(result)
    detail = next(m for m in memories if m.category == "module-detail")
    assert "auth" in detail.content
    assert "UserService" in detail.content
    assert "GET /login" in detail.content
    assert "UserSchema" in detail.content


def test_synthesize_memories_api_surface() -> None:
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[
            ModuleInfo(name="api", path="api", routes=[("GET", "/users"), ("POST", "/orders")]),
        ],
    )
    memories = synthesize_memories(result)
    api = next(m for m in memories if m.category == "api-surface")
    assert "2 endpoints" in api.content


def test_synthesize_memories_exceptions() -> None:
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[
            ModuleInfo(name="core", path="core", exceptions=["AuthError", "NotFoundError"]),
        ],
    )
    memories = synthesize_memories(result)
    exc = next(m for m in memories if m.category == "exceptions")
    assert "AuthError" in exc.content


def test_synthesize_memories_sql_tables() -> None:
    result = ScanResult(project_dir="/tmp/test", sql_tables=["users", "orders", "products"])
    memories = synthesize_memories(result)
    db = next(m for m in memories if m.category == "database")
    assert "3" in db.content
    assert "users" in db.content


def test_synthesize_memories_proto() -> None:
    result = ScanResult(
        project_dir="/tmp/test",
        proto_services=["UserService"],
        proto_messages=["UserRequest"],
    )
    memories = synthesize_memories(result)
    grpc = next(m for m in memories if m.category == "grpc")
    assert "UserService" in grpc.content
    assert "UserRequest" in grpc.content


def test_synthesize_memories_adrs() -> None:
    result = ScanResult(project_dir="/tmp/test", adr_files=["ADR/0001.md", "ADR/0002.md"])
    memories = synthesize_memories(result)
    adr = next(m for m in memories if m.category == "adr-list")
    assert "2 decision records" in adr.content


def test_synthesize_memories_always_has_structure() -> None:
    result = ScanResult(project_dir="/tmp/test", total_files=10, total_source_files=5)
    memories = synthesize_memories(result)
    structure = next(m for m in memories if m.category == "structure")
    assert "10 files" in structure.content
    assert "5 source" in structure.content


def test_synthesize_memories_small_module_skipped() -> None:
    """Module with 1 file and no content should be skipped."""
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[ModuleInfo(name="tiny", path="tiny", file_count=1)],
    )
    memories = synthesize_memories(result)
    details = [m for m in memories if m.category == "module-detail"]
    assert len(details) == 0


def test_synthesize_memories_enums() -> None:
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[ModuleInfo(name="types", path="types", enums=["Status", "Priority"])],
    )
    memories = synthesize_memories(result)
    enum_mem = next(m for m in memories if m.category == "enums")
    assert "Status" in enum_mem.content


def test_synthesize_memories_domain_models() -> None:
    from rekal.scanner import ModuleInfo

    result = ScanResult(
        project_dir="/tmp/test",
        modules=[ModuleInfo(name="schema", path="schema", models=["User", "Order"])],
    )
    memories = synthesize_memories(result)
    dm = next(m for m in memories if m.category == "domain-model")
    assert "2 types" in dm.content
    assert "User" in dm.content


def test_synthesize_memories_many_modules() -> None:
    """Module map truncates when >15 modules."""
    from rekal.scanner import ModuleInfo

    modules = [ModuleInfo(name=f"mod{i}", path=f"mod{i}", file_count=5) for i in range(20)]
    result = ScanResult(project_dir="/tmp/test", modules=modules)
    memories = synthesize_memories(result)
    module_map = next(m for m in memories if m.category == "module-map")
    assert "+ 5 more" in module_map.content


def test_synthesize_infra_with_package_json(tmp_path: Path) -> None:
    """Integration: synthesize_infra_memories picks up package.json."""
    (tmp_path / "package.json").write_text(
        '{"name": "frontend", "dependencies": {"react": "^18"}}'
    )
    result = ScanResult(project_dir=str(tmp_path))
    from rekal.scanner import synthesize_infra_memories

    memories = synthesize_infra_memories(result)
    categories = [m.category for m in memories]
    assert "stack" in categories


# ── Integration: scan_project ────────────────────────────────────────


def test_scan_project_full(tmp_path: Path) -> None:
    """Full integration test with a realistic project structure."""
    # Create project structure
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""My web app."""\n')
    (pkg / "models.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class User(BaseModel):\n    name: str\n\n"
        "class Order(BaseModel):\n    total: float\n"
    )
    (pkg / "routes.py").write_text(
        '@router.get("/users")\nasync def list_users(): ...\n\n'
        '@router.post("/orders")\nasync def create_order(): ...\n'
    )
    (pkg / "errors.py").write_text("class AppError(Exception): pass\n")
    (pkg / "types.py").write_text("from enum import Enum\nclass Status(Enum):\n    ACTIVE = 1\n")

    auth = pkg / "auth"
    auth.mkdir()
    (auth / "__init__.py").write_text('"""Authentication module."""\n')
    (auth / "service.py").write_text("class AuthService:\n    pass\n")

    # Config files
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nrequires-python = ">=3.11"\n'
        'dependencies = ["fastapi", "pydantic"]\n'
    )
    (tmp_path / "README.md").write_text("# My App\n")

    # SQL
    (tmp_path / "schema.sql").write_text("CREATE TABLE users (id INT);\n")

    result = scan_project(str(tmp_path))

    assert result.total_files > 0
    assert result.total_source_files > 0
    assert len(result.modules) >= 2  # myapp + myapp/auth
    assert "README.md" in result.doc_files
    assert "pyproject.toml" in result.config_files
    assert "users" in result.sql_tables
    assert len(result.memories) >= 5  # module map, modules, structure, stack, etc.

    # Check specific memory categories
    categories = {m.category for m in result.memories}
    assert "module-map" in categories
    assert "structure" in categories
    assert "stack" in categories


def test_scan_project_nonexistent() -> None:
    result = scan_project("/tmp/definitely_does_not_exist_12345")
    assert result.total_files == 0
    assert result.modules == []
    assert result.memories == []


def test_scan_project_empty(tmp_path: Path) -> None:
    result = scan_project(str(tmp_path))
    assert result.total_files == 0
    assert result.modules == []
    # Should still have at least structure memory
    assert any(m.category == "structure" for m in result.memories)


def test_scan_project_skips_pycache(tmp_path: Path) -> None:
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.pyc").write_bytes(b"\x00")
    (tmp_path / "real.py").write_text("x = 1")

    result = scan_project(str(tmp_path))
    # pyc files should be skipped
    assert result.total_source_files == 1


def test_memory_candidate_fields() -> None:
    m = MemoryCandidate(
        content="Test content",
        memory_type="fact",
        tags=["test"],
        category="test",
    )
    assert m.content == "Test content"
    assert m.memory_type == "fact"
    assert m.tags == ["test"]
    assert m.category == "test"
