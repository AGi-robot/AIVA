import json
from unittest.mock import Mock

import pytest

from aiva_chat.drawing import validate_actions
from aiva_chat.node import AivaChat
from aiva_chat.memory import GraceMemory


def test_drawing_validation_bounds_coordinates_and_preserves_text():
    result = validate_actions([{'kind': 'text', 'x': 2000, 'y': -1, 'text': 'Hello 🌱', 'color': '#123abc', 'size': 30}])
    assert result[0]['x'] == 1000
    assert result[0]['y'] == 0
    assert result[0]['text'] == 'Hello 🌱'


@pytest.mark.parametrize('actions', [
    [{'kind': 'execute', 'text': 'code'}],
    [{'kind': 'path', 'points': [[float('nan'), 20]]}],
    [{'kind': 'text', 'color': 'url(bad)'}],
    [{'kind': 'text', 'text': 'x' * 2001}],
    [{'kind': 'clear'}] * 101,
])
def test_rejects_invalid_drawing_commands(actions):
    with pytest.raises(ValueError):
        validate_actions(actions)


def test_model_drawing_tool_is_validated_and_published(tmp_path, monkeypatch):
    node = AivaChat.__new__(AivaChat)
    node._base_url = 'http://llm.test/v1'
    node._model = 'test'
    node._system_prompt = 'You are Grace.'
    node._api_key = ''
    node._memory = GraceMemory(str(tmp_path / 'memory.sqlite'))
    node._publish = Mock()
    node.get_logger = Mock(return_value=Mock())
    response = Mock()
    response.json.return_value = {'choices': [{'message': {'tool_calls': [{'function': {
        'name': 'canvas_draw', 'arguments': json.dumps({'actions': [{'kind': 'text', 'text': 'Hello!', 'x': 20, 'y': 30}]})
    }}]}}]}
    request = Mock(return_value=response)
    monkeypatch.setattr('aiva_chat.node.httpx.post', request)
    node._draw('Write hello', 'draw-1', {'objects': []})
    events = [call.args[0] for call in node._publish.call_args_list]
    assert [event['type'] for event in events] == ['canvas_start', 'canvas_actions', 'canvas_done']
    assert events[1]['identifier'] == 'draw-1'
    assert events[1]['actions'][0]['text'] == 'Hello!'
    assert request.call_args.kwargs['json']['tools'][0]['function']['name'] == 'canvas_draw'
    assert node._memory.recent()[0]['content'] == 'Write hello'

    response.json.return_value = {'choices': [{'message': {'tool_calls': [{'function': {
        'name': 'canvas_draw', 'arguments': '{invalid json'
    }}]}}]}
    node._publish.reset_mock()
    node._draw('bad reply', 'draw-2', {'objects': []})
    assert node._publish.call_args.args[0]['type'] == 'canvas_error'
    assert not any(call.args[0]['type'] == 'canvas_actions' for call in node._publish.call_args_list)


def test_canvas_request_routes_to_drawing_worker():
    node = AivaChat.__new__(AivaChat)
    node._pool = Mock()
    node._draw = Mock()
    node._submit_prompt = Mock()
    message = Mock(data=json.dumps({'text': 'Draw a tree', 'identifier': 'draw-5', 'canvas': {'objects': []}}))
    node._on_webui_input(message)
    node._pool.submit.assert_called_once_with(node._draw, 'Draw a tree', 'draw-5', {'objects': []})
    node._submit_prompt.assert_not_called()
