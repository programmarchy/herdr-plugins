# Herdr plugins

Free, open-source plugins for [Herdr](https://herdr.dev/). Each plugin lives in its own directory and can be installed separately.

| Plugin | What it does | Install |
| --- | --- | --- |
| [Ambient Sound](herdr-sound/) | Quiet thinking pulse, tool-call tink, and completion chime for Claude Code and Codex on macOS | `herdr plugin install programmarchy/herdr-plugins/herdr-sound` |
| [Email Bridge](herdr-email/) | Forward authenticated AgentMail messages to a Herdr workspace/tab | `herdr plugin install programmarchy/herdr-plugins/herdr-email` |

After installing Ambient Sound, run `herdr plugin action invoke programmarchy.ambient-sound.configure` to add the agent tool hooks. See its [README](herdr-sound/README.md) for options and uninstall instructions.

Email Bridge also needs one AgentMail inbox and private setup before its listening pane starts. See its [README](herdr-email/README.md).

These plugins are licensed under [MIT](LICENSE).
