import textwrap
import pytest

from automation.services.template_service import TemplateService


def create_service(tmp_path, registry_text, template_files):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    for filename, content in template_files.items():
        (library_dir / filename).write_text(content, encoding="utf-8")
    registry_path = tmp_path / "registry.yml"
    registry_path.write_text(registry_text, encoding="utf-8")
    return TemplateService(
        registry_path=str(registry_path),
        templates_dir=str(library_dir)
    )


def test_list_templates_returns_metadata(tmp_path):
    service = create_service(
        tmp_path,
        registry_text=textwrap.dedent(
            """
            templates:
              - name: demo
                title: Demo Template
                description: Sample description
                file: demo.yml
                categories: ["starter"]
                tags: ["llm"]
                estimated_nodes: 3
                variables:
                  - name: app_name
                    description: Name
                    default: Demo App
            """
        ).strip(),
        template_files={
            "demo.yml": "app:\n  name: \"${app_name}\"\n"
        }
    )

    templates = service.list_templates()
    assert len(templates) == 1
    entry = templates[0]
    assert entry["name"] == "demo"
    assert entry["categories"] == ["starter"]
    assert entry["estimated_nodes"] == 3
    assert entry["variables"][0]["default"] == "Demo App"


def test_render_template_applies_overrides(tmp_path):
    service = create_service(
        tmp_path,
        registry_text=textwrap.dedent(
            """
            templates:
              - name: demo
                title: Demo
                description: Desc
                file: demo.yml
                variables:
                  - name: app_name
                    description: Name
                    default: Demo App
                  - name: system_prompt
                    description: Prompt
                    default: Default prompt
            """
        ).strip(),
        template_files={
            "demo.yml": (
                "app:\n"
                "  name: \"${app_name}\"\n"
                "workflow:\n"
                "  graph:\n"
                "    nodes:\n"
                "      - data:\n"
                "          prompt_template:\n"
                "            - role: system\n"
                "              text: \"${system_prompt}\"\n"
            )
        }
    )

    rendered, context = service.render_template(
        "demo", overrides={"system_prompt": "Custom"}
    )
    assert "Custom" in rendered
    assert context["app_name"] == "Demo App"
    assert context["system_prompt"] == "Custom"


def test_apply_template_respects_overwrite_flag(tmp_path):
    service = create_service(
        tmp_path,
        registry_text=textwrap.dedent(
            """
            templates:
              - name: demo
                title: Demo
                description: Desc
                file: demo.yml
                variables:
                  - name: app_name
                    default: Demo
            """
        ).strip(),
        template_files={
            "demo.yml": "app:\n  name: \"${app_name}\"\n"
        }
    )

    output_path = tmp_path / "output.yml"
    output_path.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        service.apply_template("demo", str(output_path))

    result = service.apply_template(
        "demo", str(output_path), overwrite=True
    )
    assert result["bytes_written"] > 0
    assert "Demo" in output_path.read_text(encoding="utf-8")


def test_missing_required_variable_raises(tmp_path):
    service = create_service(
        tmp_path,
        registry_text=textwrap.dedent(
            """
            templates:
              - name: strict
                title: Strict
                description: Desc
                file: strict.yml
                variables:
                  - name: required_value
                    required: true
            """
        ).strip(),
        template_files={
            "strict.yml": "value: \"${required_value}\"\n"
        }
    )

    with pytest.raises(ValueError):
        service.render_template("strict")
