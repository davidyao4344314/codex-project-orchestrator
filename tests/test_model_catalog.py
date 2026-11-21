import sys

import pytest
from typer.testing import CliRunner

from codex_orchestrator import model_catalog
from codex_orchestrator.cli import app
from codex_orchestrator.session import PlanningSession


def offers():
    return model_catalog.parse_offers(
        [
            {"model": "gpt-5.6-luna", "displayName": "Luna"},
            {"model": "gpt-6-luna", "displayName": "New Luna"},
            {"model": "gpt-6.2-sol", "displayName": "Sol", "isDefault": True},
            {"model": "gpt-6.2-astra", "displayName": "Astra"},
            {"model": "gpt-9-astra", "hidden": True},
            {"model": "gpt 9 invalid"},
        ]
    )


def test_model_policy_uses_exact_visible_catalog_ids():
    policy = model_catalog.suggested_policy(offers())
    assert policy.routine == "gpt-6-luna"
    assert policy.standard == "gpt-6.2-sol"
    assert policy.strong_reasoning == "gpt-6.2-astra"
    assert model_catalog.selected_policy(offers(), routine="default").routine is None
    with pytest.raises(ValueError, match="not in the Codex model catalog"):
        model_catalog.selected_policy(offers(), routine="gpt 6.2 sol")
    unknown = model_catalog.suggested_policy([model_catalog.ModelOffer("custom", "Unknown")])
    assert unknown.model_dump() == {"routine": None, "standard": None, "strong_reasoning": None}


def test_app_server_model_list_protocol_uses_no_inference(tmp_path, monkeypatch):
    script = tmp_path / "fake-codex"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "assert sys.argv[1:] == ['app-server','--listen','stdio://']\n"
        "for line in sys.stdin:\n"
        "    m=json.loads(line)\n"
        "    if m['method']=='initialize': result={}\n"
        "    elif m['method']=='initialized': continue\n"
        "    elif m['method']=='model/list':\n"
        "        assert not m['params']['includeHidden']\n"
        "        result={'data':[{'model':'gpt-6.2-sol','displayName':'Sol'}],"
        "'nextCursor':None}\n"
        "    else: raise AssertionError(m['method'])\n"
        "    print(json.dumps({'id':m['id'],'result':result}),flush=True)\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(model_catalog, "find_codex", lambda: str(script))
    found = model_catalog.discover_models(tmp_path)
    assert [m.id for m in found] == ["gpt-6.2-sol"]


def test_palette_configuration_requires_confirmation_and_show_is_local(
    approved_session, monkeypatch
):
    source, original = approved_session
    monkeypatch.setattr(model_catalog, "discover_models", lambda project: offers())
    runner = CliRunner()
    args = ["cycle", "models", str(source)]
    assert runner.invoke(app, args, input="n\n").exit_code == 0
    assert PlanningSession.load(source).model_policy is None
    accepted = runner.invoke(app, args, input="y\n")
    assert accepted.exit_code == 0, accepted.output
    state = PlanningSession.load(source)
    assert state.intake_model == "gpt-6-luna" and state.planning_model == "gpt-6.2-sol"
    assert state.revisions == original.revisions and state.approved_revision == 1
    monkeypatch.setattr(model_catalog, "discover_models", lambda _: pytest.fail("Unexpected call"))
    result = runner.invoke(app, [*args, "--show"])
    assert result.exit_code == 0 and "gpt-6.2-astra" in result.output
