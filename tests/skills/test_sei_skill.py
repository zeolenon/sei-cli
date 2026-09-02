from pathlib import Path


SKILL_PATH = Path(__file__).parents[2] / "skills" / "openclaw" / "SKILL.md"


def test_sei_skill_metadata_and_operational_guards() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "\n---\n" in content
    assert "name: sei\n" in content
    assert "version: 0.8.0\n" in content
    assert "license: MIT\n" in content
    assert "platforms: [linux, macos, windows]\n" in content
    assert "description: \"Operar o SEI com leitura contextual e ações canônicas.\"" in content
    description = next(line for line in content.splitlines() if line.startswith("description:"))
    assert len(description.split(": ", 1)[1].strip('"')) <= 60
    assert "sei process-history <processo> --full --json" in content
    assert "sei acompanhamento-search [--palavras" in content
    assert "visual_analysis_required" in content
    assert "visual_artifacts" in content
    assert "sei block <bloco> --json" in content
    assert "read_summary.read_status" in content
    assert "Nenhum valor de credencial deve ser registrado; qualquer credencial eventualmente encontrada deve aparecer somente como `[REDACTED]`." in content
    assert "~/Projects/" not in content
    assert "/Users/" not in content
