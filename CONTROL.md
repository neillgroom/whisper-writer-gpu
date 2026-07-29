## Helene developer controls

The working local GPU dictation path is unchanged:

- **Pause/Break:** dictate text at the cursor.
- **Ctrl+Pause:** speak a developer command.
- **Pause/Break + “Helene…”:** optional spoken-prefix command.

Commands are parsed locally and sent over length-prefixed JSON to a separate control
broker. The broker owns Windows automation, repo aliases, agent launchers, and Factory
workflows; the Qt/CUDA process stays focused on recording and transcription.

Examples:

```text
Open VS Code
Open project Floodstream
Press control shift P
Switch to Chrome
Type run the focused test
Start Claude Code in Floodstream and ask it to fix the failing test
Run four way review on Floodstream
Run tests in Floodstream
```

Edit [`src/control_config.yaml`](src/control_config.yaml) to match local repo paths and
installed CLI names. The shipped defaults expect repositories under `C:\Projects`,
Claude Code as `claude`, and invoke the existing Shepherd Factory gate as:

```powershell
cd C:\Projects\shepherd-factory
pnpm tsx src/cli.ts gate --repo C:\Projects\floodstream --base main --intent "Voice-requested four-way review"
```

Only named commands from `control_config.yaml` can be launched by voice. Arbitrary
dictated shell text is rejected. Screen-reading and visual click guessing are not part
of this first release. `click_control` uses Windows UI Automation when a caller supplies
an exact window and control name.

### Test the command layer

The parser can be checked without launching Qt or loading Whisper:

```powershell
python src\control_broker.py --parse "Helene, open project Floodstream"
python -m unittest discover -s tests -v
```
