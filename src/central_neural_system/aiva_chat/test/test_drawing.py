import json
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from aiva_chat.drawing import (
    validate_actions, canvas_context, drawing_history, drawing_summary,
    repair_model_actions,
)
from aiva_chat.node import AivaChat
from aiva_chat.memory import GraceMemory


def _mock_stream(monkeypatch, response):
    request = Mock()

    def open_stream(*args, **kwargs):
        payload = response.json()
        choice = payload['choices'][0]
        message = choice.get('message', {})
        lines = []
        if message.get('content'):
            lines.append('data: ' + json.dumps({'choices': [{'delta': {'content': message['content']}}]}))
        for index, call in enumerate(message.get('tool_calls', [])):
            function = call.get('function', {})
            arguments = function.get('arguments', '')
            fragments = [arguments[offset:offset + 24] for offset in range(0, len(arguments), 24)] or ['']
            for fragment_index, fragment in enumerate(fragments):
                delta = {'tool_calls': [{'index': index, 'function': {
                    'name': function.get('name') if fragment_index == 0 else None,
                    'arguments': fragment,
                }}]}
                lines.append('data: ' + json.dumps({'choices': [{'delta': delta}]}))
        lines.append('data: ' + json.dumps({'choices': [{
            'delta': {}, 'finish_reason': choice.get('finish_reason', 'tool_calls'),
        }]}))
        lines.append('data: [DONE]')
        streamed = Mock(is_error=getattr(response, 'is_error', False))
        streamed.iter_lines.return_value = iter(lines)
        streamed.read.return_value = b''
        return nullcontext(streamed)

    request.side_effect = open_stream
    monkeypatch.setattr('aiva_chat.node.httpx.stream', request)
    return request


def test_drawing_validation_bounds_coordinates_and_preserves_text():
    result = validate_actions([{'kind': 'text', 'x': 2000, 'y': -1, 'text': 'Hello 🌱', 'color': '#123abc', 'size': 30}])
    assert result[0]['x'] == 1000
    assert result[0]['y'] == 0
    assert result[0]['text'] == 'Hello 🌱'


def test_letter_format_survives_validation_for_playback_and_later_editing():
    letter = {'kind': 'letter', 'id': 'note', 'text': 'Dear friend,\n\nHello!\n\nGrace',
              'x': 270, 'y': 20, 'width': 460, 'height': 610, 'size': 16,
              'margin': 40, 'line_height': 1.5, 'align': 'left'}
    assert validate_actions([letter])[0] == {**letter, 'fill': False}
    bounded = validate_actions([{'kind': 'letter', 'margin': 1000, 'line_height': 0}])[0]
    assert bounded['margin'] == 100
    assert bounded['line_height'] == 1


def test_paint_attributes_and_partial_style_survive_round_trip():
    paint = {'kind': 'polygon', 'points': [[10, 10], [70, 30], [40, 80]],
             'color': '#b84a56', 'fill_color': '#ed8060', 'gradient_color': '#f4c56a',
             'gradient': 'radial', 'opacity': 0.3, 'brush': 'soft', 'fill': True, 'stroke': False}
    assert validate_actions([paint])[0] == paint
    assert validate_actions([{'kind': 'style', 'id': 'petal', 'opacity': 0.5}])[0] == {
        'kind': 'style', 'id': 'petal', 'opacity': 0.5}


@pytest.mark.parametrize('actions', [
    [{'kind': 'execute', 'text': 'code'}],
    [{'kind': 'path', 'points': [[float('nan'), 20]]}],
    [{'kind': 'text', 'color': 'url(bad)'}],
    [{'kind': 'text', 'text': 'x' * 2001}],
    [{'kind': 'clear'}] * 101,
    [{'kind': 'letter', 'align': 'justify'}],
    [{'kind': 'letter', 'line_height': float('inf')}],
    [{'kind': 'letter', 'margin': True}],
    [{'kind': 'ellipse', 'gradient_color': 'red'}],
    [{'kind': 'path', 'points': [[1, 2]], 'brush': 'unknown'}],
    [{'kind': 'style', 'opacity': float('nan')}],
])
def test_rejects_invalid_drawing_commands(actions):
    with pytest.raises(ValueError):
        validate_actions(actions)


def test_model_curve_with_invalid_control_point_count_falls_back_to_smooth_path():
    malformed = {'kind': 'curve', 'points': [[x, x] for x in range(5)], 'stroke': True}
    repaired = validate_actions(repair_model_actions([malformed]))
    assert repaired[0]['kind'] == 'path'
    assert repaired[0]['smooth'] is True
    assert len(repaired[0]['points']) == 5


def test_model_drawing_tool_is_validated_and_published(tmp_path, monkeypatch):
    node = AivaChat.__new__(AivaChat)
    node._base_url = 'http://llm.test/v1'
    node._model = 'test'
    node._system_prompt = 'You are Grace.'
    node._api_key = ''
    node._draw_timeout = 600
    node._memory = GraceMemory(str(tmp_path / 'memory.sqlite'))
    node._memory.add_exchange('My favorite color is coral.', 'I will remember coral.')
    node._publish = Mock()
    node.get_logger = Mock(return_value=Mock())
    response = Mock()
    response.is_error = False
    response.json.return_value = {'choices': [{'message': {'tool_calls': [{'function': {
        'name': 'canvas_draw', 'arguments': json.dumps({'actions': [{'kind': 'text', 'text': 'Hello!', 'x': 20, 'y': 30}], 'response': 'Here is a hello!'})
    }}]}}]}
    request = _mock_stream(monkeypatch, response)
    node._draw('Write hello', 'draw-1', {'objects': []})
    events = [call.args[0] for call in node._publish.call_args_list]
    assert [event['type'] for event in events] == ['canvas_start', 'canvas_actions', 'canvas_reply', 'canvas_done']
    assert events[2]['content'] == 'Here is a hello!'
    assert events[2]['identifier'] == 'draw-1'
    assert events[1]['actions'][0]['text'] == 'Hello!'
    assert request.call_args.kwargs['json']['tools'][0]['function']['name'] == 'canvas_draw'
    messages = request.call_args.kwargs['json']['messages']
    assert [item['role'] for item in messages] == ['system', 'user', 'assistant', 'user']
    assert messages[1]['content'] == 'My favorite color is coral.'
    assert 'Canvas state:' in messages[-1]['content']
    assert 'Drawing request: Write hello' in messages[-1]['content']
    assert node._memory.recent()[-2]['content'] == 'Write hello'
    assert node._memory.recent()[-1]['content'].startswith('Here is a hello!')
    restored = GraceMemory(str(tmp_path / 'memory.sqlite'))
    assert restored.recent()[-1]['content'].startswith('Here is a hello!')
    assert 'playback not confirmed' in restored.recent()[-1]['content']

    response.json.return_value = {'choices': [{'message': {'tool_calls': [{'function': {
        'name': 'canvas_draw', 'arguments': json.dumps({'actions': [], 'response': 'A little note for you.'})
    }}]}}]}
    node._publish.reset_mock()
    node._draw('Write me a note', 'draw-empty', {'objects': []})
    empty_action_events = [call.args[0] for call in node._publish.call_args_list]
    canvas_actions = next(event for event in empty_action_events if event['type'] == 'canvas_actions')
    assert canvas_actions['actions'][0]['kind'] == 'text'
    assert canvas_actions['actions'][0]['text'] == 'A little note for you.'

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


def test_cubic_paint_and_multistop_gradient_survive_validation():
    artwork = {
        'kind': 'curve', 'id': 'leaf',
        'points': [[300, 400], [220, 280], [270, 160], [380, 140],
                   [400, 240], [370, 340], [300, 400]],
        'fill': True, 'stroke': False, 'gradient': 'linear', 'gradient_angle': 90,
        'gradient_stops': [{'offset': 0, 'color': '#c4dda2'},
                           {'offset': 0.4, 'color': '#648c55'},
                           {'offset': 1, 'color': '#234f4a'}],
    }
    assert validate_actions([artwork])[0] == artwork
    assert validate_actions([{'kind': 'style', 'id': 'title', 'font': 'serif'}])[0] == {
        'kind': 'style', 'id': 'title', 'font': 'serif'}


@pytest.mark.parametrize('action', [
    {'kind': 'curve', 'points': [[0, 0]] * 3},
    {'kind': 'curve', 'points': [[0, 0]] * 5},
    {'kind': 'curve', 'points': [[0, 0]] * 64},
    {'kind': 'style', 'gradient_angle': float('nan')},
    {'kind': 'text', 'font': 'url(external)'},
    {'kind': 'style', 'gradient_stops': [{'offset': 0, 'color': '#abcdef'}]},
    {'kind': 'style', 'gradient_stops': [{'offset': True, 'color': '#abcdef'}] * 2},
    {'kind': 'style', 'gradient_stops': [{'offset': float('nan'), 'color': '#abcdef'}] * 2},
    {'kind': 'style', 'gradient_stops': [{'offset': 1.1, 'color': '#abcdef'}] * 2},
    {'kind': 'style', 'gradient_stops': [{'offset': 0, 'color': 'red'}] * 2},
    {'kind': 'style', 'gradient_stops': [{'offset': 1, 'color': '#abcdef'}, {'offset': 0, 'color': '#ffffff'}]},
])
def test_invalid_cubic_and_gradient_data_is_rejected(action):
    with pytest.raises(ValueError):
        validate_actions([action])


def test_dense_art_context_stays_bounded_and_preserves_selected_object():
    objects = validate_actions([
        {'kind': 'path', 'id': f'stroke-{index}', 'color': '#648c55',
         'points': [[point / 2, point / 4] for point in range(2000)]}
        for index in range(99)
    ] + [{'kind': 'curve', 'id': 'selected-leaf', 'points': [[20, 20], [30, 0], [60, 30], [40, 40]]}])
    original = json.dumps(objects)
    context = canvas_context(objects, 'selected-leaf')
    assert len(context) <= 3600
    parsed = json.loads(context)
    assert parsed['object_count'] == 100
    assert parsed['omitted_objects'] == 100 - len(parsed['objects'])
    leaf = next(obj for obj in parsed['objects'] if obj['id'] == 'selected-leaf')
    assert leaf['points'] == objects[-1]['points']
    assert json.dumps(objects) == original
    assert canvas_context(objects[:0]) == '{"objects":[],"selected":null}'


def test_old_drawing_history_and_new_summaries_do_not_repeat_geometry():
    actions = [{'kind': 'path', 'color': '#648c55', 'points': [[1, 2]] * 2000},
               {'kind': 'text', 'text': 'For you', 'color': '#df78a8'}]
    old = 'Here is a tree.\n\nPrepared 2 canvas actions (playback not confirmed): ' + json.dumps(actions)
    history = drawing_history([
        {'role': 'user', 'content': 'I like green trees.'},
        {'role': 'assistant', 'content': old},
    ])
    assert 'I like green trees.' == history[0]['content']
    assert 'Here is a tree.' in history[1]['content']
    assert 'points' not in history[1]['content']
    summary = json.loads(drawing_summary(actions))
    assert summary['shapes'] == {'path': 1, 'text': 1}
    assert summary['palette'] == ['#648c55', '#df78a8']
    assert summary['writing'] == ['For you']
    assert len(drawing_summary(actions)) < 250


def test_truncated_model_plan_reports_actionable_error_without_painting(tmp_path, monkeypatch):
    node = AivaChat.__new__(AivaChat)
    node._base_url = 'http://llm.test/v1'
    node._model = 'test'
    node._system_prompt = 'You are Grace.'
    node._api_key = ''
    node._draw_timeout = 600
    node._memory = GraceMemory(str(tmp_path / 'memory.sqlite'))
    node._publish = Mock()
    node.get_logger = Mock(return_value=Mock())
    response = Mock(is_error=False)
    response.json.return_value = {'choices': [{'finish_reason': 'length', 'message': {
        'tool_calls': [{'function': {'name': 'canvas_draw', 'arguments': '{"actions":['}}]
    }}]}
    request = _mock_stream(monkeypatch, response)
    node._draw('Paint a garden', 'draw-truncated', {'objects': []})
    events = [call.args[0] for call in node._publish.call_args_list]
    assert [event['type'] for event in events] == ['canvas_start', 'canvas_progress', 'canvas_progress', 'canvas_error']
    assert 'complete drawing action' in events[-1]['content']
    assert request.call_count == 3
    assert request.call_args.kwargs['json']['max_tokens'] == 3072
    assert node._memory.recent() == []


def test_canvas_summary_prioritizes_long_selected_id_and_counts_omissions():
    ids = [f'{index:04d}' + 'a' * 60 for index in range(100)]
    objects = validate_actions([
        {'kind': 'path', 'id': object_id, 'points': [[point, point / 2] for point in range(100)]}
        for object_id in ids
    ])
    context = canvas_context(objects, ids[0])
    assert len(context) <= 3600
    summary = json.loads(context)
    assert summary['selected'] == ids[0]
    assert summary['objects'][0]['id'] == ids[0]
    assert summary['objects'][0]['bounds'] == [0, 0, 99, 49.5]
    assert summary['objects'][0]['layer'] == 0
    assert len(summary['objects']) + summary['omitted_objects'] == 100
    assert summary['omitted_objects'] > 0
    assert len({item['id'] for item in summary['objects']}) == len(summary['objects'])


def test_oversized_pass_stops_at_budget_and_remembers_already_streamed_actions(tmp_path, monkeypatch):
    node = AivaChat.__new__(AivaChat)
    node._base_url = 'http://llm.test/v1'
    node._model = 'test'
    node._system_prompt = 'You are Grace.'
    node._api_key = ''
    node._draw_timeout = 600
    node._memory = GraceMemory(str(tmp_path / 'memory.sqlite'))
    node._publish = Mock()
    node.get_logger = Mock(return_value=Mock())
    response = Mock(is_error=False)
    arguments = json.dumps({'actions': [{'kind': 'ellipse', 'x': 10, 'y': 10,
                                        'width': 20, 'height': 20}] * 101,
                            'response': 'Painting flowers.'})
    response.json.return_value = {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'tool_calls': [{'function': {'name': 'canvas_draw', 'arguments': arguments}}]
    }}]}
    _mock_stream(monkeypatch, response)
    node._draw('Paint a garden', 'draw-limit', {'objects': []})
    events = [call.args[0] for call in node._publish.call_args_list]
    actions = [action for event in events if event['type'] == 'canvas_actions' for action in event['actions']]
    assert len(actions) == 16
    assert events[-1]['type'] == 'canvas_error'
    assert '16-action pass budget' in events[-1]['content']
    assert 'Drawing interrupted' in node._memory.recent()[-1]['content']


def drawing_node(tmp_path):
    node = AivaChat.__new__(AivaChat)
    node._base_url, node._model, node._system_prompt = 'http://llm.test/v1', 'test', 'Grace'
    node._api_key, node._draw_timeout = '', 600
    node._memory = GraceMemory(str(tmp_path / 'memory.sqlite'))
    node._publish = Mock()
    node.get_logger = Mock(return_value=Mock())
    return node


def tool_response(actions, more=False):
    return {'choices': [{'message': {'tool_calls': [{'function': {
        'name': 'canvas_draw', 'arguments': json.dumps({'actions': actions, 'response': 'A garden for you.', 'more': more})
    }}]}}]}


def test_truncation_continues_from_complete_objects_without_duplicates(tmp_path, monkeypatch):
    node = drawing_node(tmp_path)
    first = {'kind': 'ellipse', 'x': 20, 'y': 20, 'width': 50, 'height': 60}
    truncated = {'choices': [{'finish_reason': 'length', 'message': {'tool_calls': [{'function': {
        'name': 'canvas_draw', 'arguments': '{"actions":[' + json.dumps(first) + ',{"kind":"curve","points":['
    }}]}}]}
    response = Mock(is_error=False)
    response.json.side_effect = [truncated, tool_response([{'kind': 'text', 'text': 'Garden'}])]
    request = _mock_stream(monkeypatch, response)
    node._draw('Draw a garden', 'garden', {'objects': []})
    events = [call.args[0] for call in node._publish.call_args_list]
    actions = [a for event in events if event['type'] == 'canvas_actions' for a in event['actions']]
    assert [a['kind'] for a in actions] == ['ellipse', 'text']
    assert actions[0]['id'] in request.call_args_list[1].kwargs['json']['messages'][-1]['content']
    assert request.call_count == 2
    assert request.call_args_list[1].kwargs['json']['tools'][0]['function']['parameters']['properties']['actions']['maxItems'] == 8
    assert events[-1] == {'type': 'canvas_done', 'identifier': 'garden', 'partial': False}
    assert 'Prepared 2 canvas actions' in node._memory.recent()[-1]['content']


def test_more_passes_are_bounded_and_finish_as_an_editable_draft(tmp_path, monkeypatch):
    node = drawing_node(tmp_path)
    response = Mock(is_error=False)
    response.json.side_effect = [tool_response([{'kind': 'text', 'text': f'Detail {i}'}], True) for i in range(3)]
    request = _mock_stream(monkeypatch, response)
    node._draw('Paint', 'draft', {'objects': []})
    events = [call.args[0] for call in node._publish.call_args_list]
    assert request.call_count == 3
    assert events[-1]['type'] == 'canvas_done' and events[-1]['partial'] is True
    assert 'drawing budget' in events[-2]['content']


def test_wrong_tool_never_paints_even_valid_looking_actions(tmp_path, monkeypatch):
    node = drawing_node(tmp_path)
    payload = tool_response([{'kind': 'clear'}])
    payload['choices'][0]['message']['tool_calls'][0]['function']['name'] = 'unknown'
    response = Mock(is_error=False)
    response.json.return_value = payload
    _mock_stream(monkeypatch, response)
    node._draw('Paint', 'wrong', {'objects': []})
    assert [call.args[0]['type'] for call in node._publish.call_args_list] == ['canvas_start', 'canvas_error']


def test_expired_time_budget_does_not_start_a_model_request(tmp_path, monkeypatch):
    node = drawing_node(tmp_path)
    node._draw_max_seconds = -1
    request = Mock()
    monkeypatch.setattr('aiva_chat.node.httpx.stream', request)
    node._draw('Paint', 'timeout', {'objects': []})
    request.assert_not_called()
    assert node._publish.call_args.args[0]['type'] == 'canvas_error'
