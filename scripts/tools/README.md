# Runtime Tools

Drop-in Python/Bash scripts that Claude can discover and use at runtime.
No restart needed — just create a file here and Claude can run it.

## Convention
- Each tool is a standalone script (Python or Bash)
- First line of docstring = tool description (Claude reads this)
- Scripts must be executable: `chmod +x`
- Use `argparse` for Python tools so `--help` works

## Example
```python
#!/usr/bin/env python3
"""Check if a domain's SSL certificate is expiring soon."""
import argparse, ssl, socket
...
```

## Discovery
Claude checks this directory via CLAUDE.md rule.
Run `ls scripts/tools/` to see available tools.
