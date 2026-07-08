from hermes.core.commander.interface import Commander, build_commander
from hermes.core.commander.models import IncomingRequest
from hermes.core.commander.tests.fakes import ScriptedTaskDispatcher


async def test_build_commander_returns_a_commander(commander_kwargs, bus):
    kwargs = dict(commander_kwargs, task_dispatcher=ScriptedTaskDispatcher(bus))
    commander = build_commander(**kwargs)

    assert isinstance(commander, Commander)


async def test_build_commander_wiring_handles_a_request_end_to_end(commander_kwargs, bus):
    kwargs = dict(commander_kwargs, task_dispatcher=ScriptedTaskDispatcher(bus))
    commander = build_commander(**kwargs)

    response = await commander.handle_request(IncomingRequest(raw_input="hi", requester="u"))

    assert response.status == "completed"
