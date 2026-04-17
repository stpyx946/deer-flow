import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .parser import parse_skill_file
from .types import Skill

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)


def get_skills_root_path() -> Path:
    """
    Get the root path of the skills directory.

    Returns:
        Path to the skills directory (deer-flow/skills)
    """
    # loader.py lives at packages/harness/deerflow/skills/loader.py — 5 parents up reaches backend/
    backend_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
    # skills directory is sibling to backend directory
    skills_dir = backend_dir.parent / "skills"
    return skills_dir


def load_skills(
    app_config: "AppConfig | None" = None,
    *,
    skills_path: Path | None = None,
    enabled_only: bool = False,
) -> list[Skill]:
    """
    Load all skills from the skills directory.

    Scans both public and custom skill directories, parsing SKILL.md files
    to extract metadata. The enabled state is determined by the skills_state_config.json file.

    Args:
        app_config: Application config used to resolve the configured skills
                    directory. Ignored when ``skills_path`` is supplied.
        skills_path: Explicit override for the skills directory. When both
                     ``skills_path`` and ``app_config`` are omitted the
                     default repository layout is used (``deer-flow/skills``).
        enabled_only: If True, only return enabled skills (default: False)

    Returns:
        List of Skill objects, sorted by name
    """
    if skills_path is None:
        if app_config is not None:
            skills_path = app_config.skills.get_skills_path()
        else:
            skills_path = get_skills_root_path()

    if not skills_path.exists():
        return []

    skills_by_name: dict[str, Skill] = {}

    # Scan public and custom directories
    for category in ["public", "custom"]:
        category_path = skills_path / category
        if not category_path.exists() or not category_path.is_dir():
            continue

        for current_root, dir_names, file_names in os.walk(category_path, followlinks=True):
            # Keep traversal deterministic and skip hidden directories.
            dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
            if "SKILL.md" not in file_names:
                continue

            skill_file = Path(current_root) / "SKILL.md"
            relative_path = skill_file.parent.relative_to(category_path)

            skill = parse_skill_file(skill_file, category=category, relative_path=relative_path)
            if skill:
                skills_by_name[skill.name] = skill

    skills = list(skills_by_name.values())

    # Load skills state configuration and update enabled status
    # NOTE: We use ExtensionsConfig.from_file() instead of get_extensions_config()
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected in the LangGraph Server when loading skills.
    try:
        from deerflow.config.extensions_config import ExtensionsConfig

        extensions_config = ExtensionsConfig.from_file()
        for skill in skills:
            skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
    except Exception as e:
        # If config loading fails, default to all enabled
        logger.warning("Failed to load extensions config: %s", e)

    # Filter by enabled status if requested
    if enabled_only:
        skills = [skill for skill in skills if skill.enabled]

    # Sort by name for consistent ordering
    skills.sort(key=lambda s: s.name)

    return skills
