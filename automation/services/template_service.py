import json
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Tuple

import yaml  # type: ignore[import]


@dataclass
class TemplateVariable:
    name: str
    description: str = ""
    default: Optional[str] = None
    required: bool = False


@dataclass
class TemplateEntry:
    name: str
    title: str
    description: str
    file: Path
    categories: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    difficulty: str = "basic"
    estimated_nodes: Optional[int] = None
    variables: List[TemplateVariable] = field(default_factory=list)


class TemplateRegistryError(RuntimeError):
    """Raised when registry metadata is invalid."""


class TemplateService:
    """Manage built-in workflow templates."""

    def __init__(
        self,
        registry_path: Optional[str] = None,
        templates_dir: Optional[str] = None
    ) -> None:
        base_dir = Path(__file__).resolve().parents[1] / "templates"
        self.registry_path = (
            Path(registry_path) if registry_path
            else base_dir / "registry.yml"
        )
        self.templates_dir = (
            Path(templates_dir) if templates_dir
            else base_dir / "library"
        )
        if not self.registry_path.exists():
            raise TemplateRegistryError(
                f"Template registry not found at {self.registry_path}"
            )
        if not self.templates_dir.exists():
            raise TemplateRegistryError(
                f"Template library directory not found at {self.templates_dir}"
            )
        self._templates = self._load_registry()

    def list_templates(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": entry.name,
                "title": entry.title,
                "description": entry.description,
                "categories": entry.categories,
                "tags": entry.tags,
                "difficulty": entry.difficulty,
                "estimated_nodes": entry.estimated_nodes,
                "variables": [
                    {
                        "name": var.name,
                        "description": var.description,
                        "default": var.default,
                        "required": var.required,
                    }
                    for var in entry.variables
                ],
            }
            for entry in self._templates
        ]

    def render_template(
        self,
        name: str,
        overrides: Optional[Dict[str, str]] = None
    ) -> Tuple[str, Dict[str, str]]:
        entry = self._get_entry(name)
        context = self._build_context(entry, overrides or {})
        template_path = entry.file
        if not template_path.exists():
            raise FileNotFoundError(
                "Template file "
                f"'{template_path.name}' missing at {template_path}"
            )
        raw = template_path.read_text(encoding="utf-8")
        rendered = Template(raw).safe_substitute(context)
        return rendered, context

    def apply_template(
        self,
        name: str,
        output_path: str,
        overrides: Optional[Dict[str, str]] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        content, context = self.render_template(name, overrides)
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"File {destination} already exists. "
                "Use overwrite=True to replace it."
            )
        destination.write_text(content, encoding="utf-8")
        bytes_written = len(content.encode("utf-8"))
        return {
            "template": name,
            "output_path": str(destination),
            "bytes_written": bytes_written,
            "variables": context,
        }

    def export_registry(self, output_path: str) -> None:
        """Helper used mainly for debugging or CLI --json dumps."""
        data = self.list_templates()
        Path(output_path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _build_context(
        self,
        entry: TemplateEntry,
        overrides: Dict[str, str]
    ) -> Dict[str, str]:
        context: Dict[str, str] = {
            "template_name": entry.name,
        }
        for variable in entry.variables:
            value = overrides.get(variable.name)
            if value is None:
                if variable.default is not None:
                    value = variable.default
                elif variable.required:
                    raise ValueError(
                        f"Template '{entry.name}' requires variable "
                        f"'{variable.name}'"
                    )
                else:
                    value = ""
            context[variable.name] = str(value)
        # Ensure overrides can add ad-hoc variables as well
        for key, value in overrides.items():
            if key not in context:
                context[key] = str(value)
        return context

    def _get_entry(self, name: str) -> TemplateEntry:
        for entry in self._templates:
            if entry.name == name:
                return entry
        raise ValueError(f"Template '{name}' not found")

    def _load_registry(self) -> List[TemplateEntry]:
        data = yaml.safe_load(self.registry_path.read_text(encoding="utf-8"))
        templates_data = (
            data.get("templates") if isinstance(data, dict) else None
        )
        if not templates_data:
            raise TemplateRegistryError(
                "Registry file does not contain any templates"
            )
        entries: List[TemplateEntry] = []
        for row in templates_data:
            try:
                name = row["name"]
                file_path = self.templates_dir / row["file"]
            except KeyError as exc:
                raise TemplateRegistryError(
                    f"Invalid registry row: missing {exc.args[0]}"
                ) from exc
            variables = [
                TemplateVariable(
                    name=var["name"],
                    description=var.get("description", ""),
                    default=var.get("default"),
                    required=var.get("required", False),
                )
                for var in row.get("variables", [])
            ]
            entry = TemplateEntry(
                name=name,
                title=row.get("title", name.title()),
                description=row.get("description", ""),
                file=file_path,
                categories=row.get("categories", []),
                tags=row.get("tags", []),
                difficulty=row.get("difficulty", "basic"),
                estimated_nodes=row.get("estimated_nodes"),
                variables=variables,
            )
            entries.append(entry)
        return entries
