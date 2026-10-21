"""Neutral chat messages → vendor wire format.

Neutral shapes:
- {'role': 'system' | 'user' | 'assistant', 'content': str}
- {'role': 'assistant', 'content': str | None, 'tool_calls': [{'id', 'name', 'arguments': str}]}
- {'role': 'tool', 'tool_call_id': str, 'name': str, 'content': str}

Plain system/user/assistant messages pass through unchanged, so requests that
never use tools keep their exact bytes (and request hashes).
"""
from __future__ import annotations

import json


def has_tool_turns(messages) -> bool:
    return any(message.get('role') == 'tool' or message.get('tool_calls') for message in messages)


def to_openai(messages):
    if not has_tool_turns(messages):
        return messages
    converted = []
    for message in messages:
        if message.get('role') == 'tool':
            converted.append({'role': 'tool', 'tool_call_id': message['tool_call_id'], 'content': message['content']})
        elif message.get('tool_calls'):
            converted.append({'role': 'assistant', 'content': message.get('content'),
                              'tool_calls': [{'id': call['id'], 'type': 'function',
                                              'function': {'name': call['name'], 'arguments': call['arguments']}}
                                             for call in message['tool_calls']]})
        else:
            converted.append(message)
    return converted


def to_anthropic(messages):
    """Returns (system_text, messages) with tool results grouped into user turns."""
    system = '\n\n'.join(message['content'] for message in messages if message.get('role') == 'system')
    converted = []
    for message in messages:
        role = message.get('role')
        if role == 'system':
            continue
        if role == 'tool':
            block = {'type': 'tool_result', 'tool_use_id': message['tool_call_id'], 'content': message['content']}
            # Consecutive tool results belong to one user turn.
            if converted and converted[-1]['role'] == 'user' and isinstance(converted[-1]['content'], list) \
                    and all(item.get('type') == 'tool_result' for item in converted[-1]['content']):
                converted[-1]['content'].append(block)
            else:
                converted.append({'role': 'user', 'content': [block]})
        elif message.get('tool_calls'):
            blocks = [{'type': 'text', 'text': message['content']}] if message.get('content') else []
            for call in message['tool_calls']:
                try:
                    arguments = json.loads(call['arguments'] or '{}')
                except ValueError:
                    arguments = {}
                blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['name'], 'input': arguments})
            converted.append({'role': 'assistant', 'content': blocks})
        else:
            converted.append({'role': role, 'content': message['content']})
    return system, converted
