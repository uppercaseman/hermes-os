import json

from hermes.demos.research_brief.cli import main


def test_main_prints_a_json_structured_brief(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["research-brief", "cli smoke test topic"])

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert output["topic"] == "cli smoke test topic"
