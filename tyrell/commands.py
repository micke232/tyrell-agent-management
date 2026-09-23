"""Discoverable slash commands; completion inserts text and never sends a prompt."""
COMMANDS = [
    ('/help', 'Show help'), ('/new', 'Create an agent'), ('/agents', 'Show agents'),
    ('/chat', 'Open chat'), ('/tasks', 'Open plan'), ('/files', 'Open changed files'),
    ('/log', 'Open tools'), ('/processes', 'Open processes'), ('/setup', 'Agent setup'),
    ('/settings', 'Application settings'), ('/connections', 'Provider connections'),
    ("/skills", "List selected provider's skills"), ('/skill', 'Use a skill: /skill NAME TASK'),
    ('/rename', 'Rename selected agent'), ('/archive', 'Archive an agent'),
    ('/archives', 'Browse archive'), ('/restore', 'Restore an agent'),
    ('/remove', 'Hide an agent'), ('/hidden', 'Browse hidden agents'),
    ('/handoff', 'Hand over to another agent'), ('/start', 'Start selected planned task'),
    ('/models', 'List models'), ('/model', 'Set model: /model NAME [EFFORT]'),
    ('/default', 'Default model: /default NAME [EFFORT]'),
    ('/request', 'Review pending requests'), ('/requests', 'Review pending requests'),
    ('/interrupt', 'Interrupt selected agent'), ('/mouse', 'Toggle mouse'),
    ('/quit', 'Quit dashboard'), ('/q', 'Quit dashboard'),
]


def choices(text, skills):
    if text.startswith('/skill '):
        prefix = text[7:]
        if any(c.isspace() for c in prefix):
            return []
        return [('/skill ' + s['name'], s['description'] or 'Use this skill')
                for s in skills if s['name'].casefold().startswith(prefix.casefold())]
    if not text.startswith('/') or any(c.isspace() for c in text):
        return []
    return sorted([(name, description) for name, description in COMMANDS if name.startswith(text.lower())],
                  key=lambda item: item[0] != text.lower())
