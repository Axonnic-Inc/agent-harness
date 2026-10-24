# dev-mcp

Three MCP servers for building and checking web work, with no tokens required:

- **playwright**: headless, isolated browser at 1440x900 with vision and devtools capabilities, for screenshots and end-to-end checks.
- **chrome-devtools**: headless Chrome DevTools for performance traces, Lighthouse, network and console inspection.
- **context7**: live, version-specific library documentation over HTTP, so answers don't rely on stale training data.

Install with `claude plugin install dev-mcp@agent-harness`. Servers start on demand through `npx`; Node.js is the only prerequisite.
