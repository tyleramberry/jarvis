# Jarvis

A voice assistant for a Mac Mini. It listens in the room for a wake word, entirely on the machine. After it hears the wake word it plays a short chime and opens a live speech-to-speech session with OpenAI or xAI. When the room goes quiet it hangs up and goes back to listening. Nothing from the microphone is sent over the network before the wake word.

It is meant to replace an Alexa speaker: a USB microphone (or a USB conference speakerphone) and, if you want, an Echo Dot used only as a Bluetooth speaker.

## What you need

- A Mac Mini with Apple silicon, on macOS, plugged into power.
- Python 3.11 or newer. The steps below install it with Homebrew.
- A microphone the Mac can see. Two setups are supported:
  - **USB conference speakerphone** with its own echo cancellation, such as an Anker PowerConf S330. Use it as both the microphone and the speaker. You can interrupt Jarvis while it is talking.
  - **A plain USB microphone plus a separate speaker**, such as an Echo Dot over Bluetooth. This hardware cannot cancel echo. Jarvis mutes the microphone while it is talking (half-duplex). You wait until it finishes, then you talk.
- An API key from OpenAI (default) or xAI. The key stays in a file on the Mac.

## Half-duplex and barge-in

This is the important hardware tradeoff.

| Setup | What the config does | While Jarvis is talking |
| --- | --- | --- |
| PowerConf (or similar) as **both** mic and speaker | Full-duplex, barge-in on | You can interrupt. The speakerphone cancels its own echo. |
| USB mic + Echo Dot, or any mic/speaker pair with no shared echo cancellation | Half-duplex (the default when no such device is recognized) | The mic is ignored while Jarvis speaks, plus a short tail (default 650 ms) so Bluetooth delay and room echo are not heard as a new question. You cannot interrupt. |
| PowerConf mic, but sound coming out of the Echo | Half-duplex | The PowerConf only cancels sound that comes out of the PowerConf. Sound from the Echo still leaks into the mic. |

`audio.half_duplex` accepts `auto`, `on`, or `off`. `auto` is the default: half-duplex unless the input and output names look like the same echo-cancelling speakerphone (PowerConf, Jabra, Poly, and a few others). When it is unsure, it chooses half-duplex. That is the safe choice for a cheap USB mic.

If Jarvis talks over itself, raise `audio.half_duplex_tail_ms` (try 900 or 1200). If it feels slow to listen after it finishes, lower that number. A wired speaker can often use 250–400 ms. An Echo Dot usually wants 500–800 ms because Bluetooth adds delay and the delay is not fixed.

To force barge-in on a speakerphone the name list does not recognize, set `audio.aec: "on"` **and** use that speakerphone as the output. Setting `aec: on` while the speaker is an Echo Dot will make Jarvis hear itself.

There is no software echo canceller in this project. A canceller needs a clean copy of what the speaker is playing, and Bluetooth does not provide one in time. Half-duplex is the reliable behavior for that wiring.

## 1. Install Python and PortAudio

Install [Homebrew](https://brew.sh) if the Mac does not have it, then:

```bash
brew install python@3.12 portaudio
```

`portaudio` is the library that talks to microphones and speakers. The Python package usually bundles it; installing it with Homebrew is the fallback if device listing fails.

## 2. Install Jarvis

In Terminal, go to this project directory (the folder that contains `config.yaml`):

```bash
cd /path/to/this/project
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Leave the virtualenv activated for the rest of the commands (`source .venv/bin/activate` in each new Terminal window). The prompt usually starts with `(.venv)`.

Check that the program runs:

```bash
python -m jarvis demo
```

That demo uses no microphone and no API key. It prints the half-duplex decision for a USB mic plus an Echo Dot, runs a fake conversation that drops mic audio while the assistant is “speaking”, and then checks that barge-in stops playback when echo cancellation is in use. It should end with `Demo passed.`

## 3. Put the API key in `.env`

```bash
cp .env.example .env
```

Open `.env` in TextEdit or any editor and set one key:

```bash
OPENAI_API_KEY=sk-...
```

Use `XAI_API_KEY` instead if you switch `provider.name` to `grok` in `config.yaml`. Do not put the key in `config.yaml` and do not commit `.env`.

The login service reads `.env` from this same folder. You do not paste the key into the launchd plist.

## 4. Choose the microphone and the speaker

```bash
python -m jarvis devices
```

You will see a table of indexes, channel counts, and names. Note the input (the mic) and the output (the speaker).

Edit `config.yaml`:

```yaml
audio:
  input_device: "PowerConf"     # or "USB" or whatever substring is unique
  output_device: "PowerConf"    # or "Echo Dot" for the Bluetooth speaker
  half_duplex: "auto"
  aec: "auto"
  half_duplex_tail_ms: 650
```

A short unique piece of the name is enough. `null` means “whatever macOS currently calls the default”. Defaults change when you connect a Bluetooth speaker, so naming the devices is more reliable.

See what Jarvis decided:

```bash
python -m jarvis check
```

The line `duplex: half-duplex` or `duplex: full-duplex` is the decision, with the reason next to it. `check` also tells you if the API key is missing. It never prints the key.

## 5. Pair an Echo Dot as the speaker

Skip this if the speakerphone is both the mic and the speaker.

The Echo is only a speaker here. Its own microphones will still answer to “Alexa” unless you mute them.

1. Mute the Echo with the microphone button on top. The ring turns red. Leave it muted.
2. On the Mac, open **System Settings → Bluetooth**.
3. Put the Echo into pairing. If the Alexa app is still set up: **Devices → your Echo → Bluetooth Devices → Pair a New Device**. The light ring turns orange. Saying “Alexa, pair” also works while its mic is unmuted; mute it again after it pairs.
4. Select the Echo in the Mac’s Bluetooth list. The name looks like `Echo Dot-XXX`.
5. In **System Settings → Sound → Output**, select the Echo.
6. Set `output_device` in `config.yaml` to `Echo Dot` (or the exact name from `python -m jarvis devices`).
7. Set `input_device` to the USB microphone, not the Echo. The Echo is a poor microphone for this program, and Bluetooth headsets add even more delay.
8. Play a test:

```bash
python -m jarvis chime
```

You should hear two short notes. The Mac’s volume and the Echo’s volume both matter. If the first note is missing, that is Bluetooth waking up; the chime already starts with a little silence for that reason.

Bluetooth delay is often 200–400 ms and it wanders. That is why the half-duplex tail exists. Music-style Bluetooth (A2DP) is one direction: the Mac sends sound to the Echo. It does not give the Mac a copy of that sound in time to subtract it from the USB mic.

## 6. Allow the microphone

macOS will not let a program listen until you allow it.

1. Run:

```bash
python -m jarvis wake-test
```

2. When macOS asks, click **Allow**.
3. If it never asks, open **System Settings → Privacy & Security → Microphone** and turn on **Terminal** (or iTerm, if that is what you used).
4. Say “hey jarvis” toward the mic. The line should change to `DETECTED hey_jarvis` and a score. Scores are printed about twice a second. Quit with Control-C if you passed `--seconds` and do not want to wait; the default listen is 20 seconds.

The first run downloads the wake-word model (a few megabytes of model weights from the openWakeWord project). That download is not your audio. After it is cached, wake detection works offline.

If the score barely moves while you talk, the wrong input device is selected. If it fires when nobody is talking, raise `wake.threshold` from `0.5` toward `0.65` in `config.yaml`.

Wake detection supports the built-in openWakeWord names `hey_jarvis` (the default), `hey_mycroft`, `alexa`, `hey_rhasspy`, `timer`, and `weather`. A custom phrase is an `.onnx` file trained with openWakeWord and pointed at by `wake.model_path`.

## 7. Allow Calendar and Reminders

These tools use AppleScript. They only work on macOS, and only after you approve them. Run this from Terminal (the approval dialogs do not appear for a background service that has never been approved):

```bash
python -m jarvis tools
```

Allow **Calendar** and, if you add a reminder, **Reminders**. If the dialog does not appear:

**System Settings → Privacy & Security → Automation → Terminal** (or Python) → enable Calendar and Reminders.

Try a reminder only when you mean to create one:

```bash
python -m jarvis tools --reminder "Test from Jarvis"
```

Check the Reminders app. It should contain that item. `python -m jarvis tools` without `--reminder` does not create one.

On a Mac, `calendar_today` reads today’s events. The first call can launch the Calendar app and take a few seconds.

## 8. Talk to it

```bash
python -m jarvis run
```

Leave that Terminal window open.

1. Say “hey jarvis”.
2. Wait for the chime.
3. Ask something short, for example “what time is it?” or “what’s on my calendar today?”
4. Stop talking. After it finishes, and after about 10 seconds of quiet (`session.silence_timeout_sec`), it hangs up and listens for the wake word again. The log line says `session ended (silence)`.

Until the next wake word, the microphone is scored locally and is not sent to the API.

Stop the program with Control-C.

To rehearse the speakers and the clock with no API charges:

```bash
python -m jarvis run --mock
```

`--mock` plays a tone (or the macOS `say` voice, when `say` is available) and still runs the local tools. It does not contact OpenAI or xAI.

Logs while you experiment:

```bash
tail -f ~/Library/Logs/jarvis/jarvis.log
```

On this Linux checkout the same file is under `~/.local/state/jarvis/jarvis.log`.

## 9. Start it at login

Do the microphone and Calendar approvals above **before** this step, using the same `.venv` Python. The login service uses that binary.

The Mac Mini should stay awake. The display can sleep. While Jarvis is running it holds a macOS wake assertion (`caffeinate`) so idle sleep does not drop the microphone. You can turn that off with `system.keep_awake: false`. Plugged-in sleep settings still matter after the process quits; in **System Settings → Energy**, leave the Mini set to avoid sleeping on power adapter.

Install the agent as your normal user. Do not use `sudo`. A root service cannot see your microphone or your Bluetooth devices.

```bash
./scripts/install_launchd.sh
```

That writes `~/Library/LaunchAgents/com.jarvis.assistant.plist`, loads it into your graphical login session, and starts it. If it crashes, launchd starts it again after 5 seconds. It also starts at the next login.

Useful commands:

```bash
launchctl print "gui/$(id -u)/com.jarvis.assistant"
tail -f ~/Library/Logs/jarvis/jarvis.log
tail -f ~/Library/Logs/jarvis/stderr.log
```

Remove it (this does not delete `config.yaml` or `.env`):

```bash
./scripts/uninstall_launchd.sh
```

After you change `config.yaml` or `.env`, kick the service:

```bash
launchctl kickstart -k "gui/$(id -u)/com.jarvis.assistant"
```

## Costs

Prices below are from the OpenAI and xAI docs as of October 2026. Check them again before you leave this running all day; they change.

A conversation is billed only while a session is open. The wake-word loop does not call the API. The session closes after `session.silence_timeout_sec` of quiet (default 10 seconds) or after `session.max_duration_sec` (default 5 minutes), whichever comes first.

**OpenAI Realtime, default model `gpt-realtime-2.1`**

Audio is billed in tokens, not as a flat per-minute fee.

- User audio: $32 per million tokens, about 1 token per 100 ms, which is about **$0.019 per minute** of your speech.
- Assistant audio: $64 per million tokens, about 1 token per 50 ms, which is about **$0.077 per minute** of Jarvis talking.
- Text (instructions, tool calls) is much cheaper per token, but each new reply re-reads the conversation so far. A long session costs more than several short ones. The silence timeout exists partly for this reason.
- `gpt-realtime-2.1-mini` is roughly a third of the audio price (about $0.006 per minute of your speech and $0.024 per minute of the reply). Set `provider.openai.model` to `gpt-realtime-2.1-mini` if you want that.
- Turning on `transcribe_input` adds a separate transcription charge so the log can show what you said. It is off by default.

A typical one-question exchange is a few cents. A session you leave open for many minutes of back-and-forth costs more, because earlier audio is part of the next turn’s input.

**xAI Grok Voice (`provider.name: grok`, model `grok-voice-latest`)**

- About **$0.08 per minute of session time** while server voice-activity detection is on, including the quiet seconds before the hang-up.
- About $0.004 for each text item the client sends. Tool results are not charged that way.
- With the default 10 second silence timeout, a short question is often well under a dime. The published cap is 120 minutes per session; this program’s own cap is 5 minutes unless you raise it.

## Commands

| Command | What it does |
| --- | --- |
| `python -m jarvis demo` | Fake conversation. No mic, no key. |
| `python -m jarvis devices` | List microphones and speakers. |
| `python -m jarvis check` | Show the key (set or missing), devices, and duplex decision. |
| `python -m jarvis wake-test` | Listen locally and print scores. |
| `python -m jarvis chime` | Play the wake chime on the output device. |
| `python -m jarvis tools` | Run the clock tool and read today’s calendar. |
| `python -m jarvis tools --reminder "Buy milk"` | Add a Reminder. |
| `python -m jarvis run` | Real assistant. |
| `python -m jarvis run --mock` | Real mic and speaker, local replies, no API. |

## Tools

Enabled tools are listed under `tools.enabled` in `config.yaml`.

| Tool | What it does |
| --- | --- |
| `get_current_datetime` | Clock in the Mac’s timezone, or a timezone you name in config. |
| `web_search` | DuckDuckGo Instant Answer. This is a short summary service, not a full web index. It often returns nothing for an obscure question, and Jarvis is told to say so. |
| `fetch_url` | Download one `http` or `https` page and read the text. The model chooses the URL. |
| `calendar_today` | Today’s Apple Calendar events, via `osascript`. |
| `add_reminder` | Create an Apple Reminder. The title is passed as an argument, not pasted into the script. |

To add a tool, copy `src/jarvis/tools/datetime_tool.py`, give it a `name`, a `description`, a JSON schema, and a `run` method that returns a string. Register it in `src/jarvis/tools/registry.py` (`_FACTORIES`) and add the name to `tools.enabled`. The next session sends the schema to the model. Keep `run` fast; it is called off the audio thread, but the person is waiting.

**MCP.** Set `mcp.enabled: true` and `mcp.command` to a stdio MCP server, for example a local agent that speaks the Model Context Protocol. Jarvis starts that process, lists its tools, and calls them when the model asks. If the process fails, Jarvis logs the error and keeps going with the built-in tools. A remote HTTP MCP server is not opened by this client. Grok’s own session can attach a server-side `mcp` tool; that would be a small addition in `src/jarvis/providers/realtime.py` if you want the provider to call it instead.

## Voice providers

`provider.name` is `openai` (default), `grok`, or `mock`.

Both network providers use the same adapter: open a WebSocket, send `session.update`, stream 16-bit little-endian PCM, play `response.output_audio.delta`, and return function calls with `conversation.item.create` plus `response.create`. OpenAI is the GA Realtime shape (`session.type: realtime`, nested `audio`, no `OpenAI-Beta` header). Grok is xAI’s speech-to-speech API (`wss://api.x.ai/v1/realtime`, voice `eve` by default).

A new provider is a class with `connect`, `configure`, `send_audio`, `cancel_response`, `truncate`, `send_tool_result`, `request_response`, `events`, and `close`. Wire it up in `src/jarvis/providers/__init__.py`. Normalized events live in `src/jarvis/providers/events.py`.

Voices: OpenAI defaults to `marin`. Grok defaults to `eve`. Change `provider.openai.voice` or `provider.grok.voice`.

## How a turn works

1. The mic is read locally. openWakeWord scores 80 ms frames at 16 kHz. No socket is open.
2. On the wake word, Jarvis plays the chime and mutes the mic long enough that the chime itself is not the first thing the model hears.
3. It opens the provider, sends the tool list and a short instruction that includes the current time, and streams the mic.
4. The provider’s voice-activity detection decides when you have finished a sentence and replies with audio.
5. **Full-duplex:** if you start talking over the reply, playback stops, the unplayed audio is truncated, and the provider is cancelled. There is a local loudness detector as well as the server’s detector. Both are disabled in half-duplex.
6. **Half-duplex:** mic samples are dropped from the moment playback starts until `half_duplex_tail_ms` after it drains. The provider is also told not to treat incoming sound as an interrupt, in case a few samples were already in flight.
7. Tool calls run on the Mac (clock, search, Calendar, Reminders, MCP) and the result is spoken.
8. After the reply, the tail, and `silence_timeout_sec` of quiet, the socket closes. Listening for the wake word resumes. A failed socket or a missing key plays a lower two-note tone and returns to the wake word. A USB or Bluetooth device that disappears is reopened with backoff.

## Limits

- The wake word is not a password. Anyone in the room can start a session.
- openWakeWord’s “hey jarvis” model will sometimes fire on similar sounds, and it will miss a quiet or accented take. Tune `wake.threshold`.
- The default model is trained for that English phrase.
- After the wake word, microphone audio and the reply audio go to OpenAI or xAI, under their terms and pricing.
- Half-duplex cannot be interrupted. That is the cost of a mic with no echo cancellation.
- Bluetooth speakers drop, sleep, and add delay. The reopen loop handles a device that vanishes; it cannot remove the delay.
- Mute the Echo’s own microphones or Alexa and Jarvis will both answer.
- Calendar and Reminders need a graphical login and an Automation approval. They do nothing useful over a plain SSH session.
- DuckDuckGo Instant Answer is intentionally small. Replace `web_search` if you want a real search API.
- This is not an emergency or medical device.
- OpenAI realtime sessions have a platform maximum (about 60 minutes). This program ends them much sooner by default.

## If something is wrong

| What you see | What to do |
| --- | --- |
| `demo` does not say `Demo passed` | Run it from the project directory with the virtualenv activated. Paste the failure lines. |
| `devices` says PortAudio or sounddevice failed | `brew install portaudio`, then `pip install -e ".[dev]"` again. |
| Wake scores stay at 0.00 | Wrong input device, or the mic is not allowed. Check **Privacy & Security → Microphone**. |
| Wake word never fires, scores rise a bit | Lower `wake.threshold` slightly, or speak closer. |
| Wake word fires from the TV | Raise `wake.threshold`, and raise `wake.vad_threshold`. |
| Chime plays, then the low tone, log says the API key is missing or rejected | Fix `.env`, then restart. `python -m jarvis check` reports a missing key without printing it. |
| Jarvis answers its own voice | You are in full-duplex without real echo cancellation. Set `half_duplex: "on"` or `aec: "auto"`, and raise `half_duplex_tail_ms`. |
| Jarvis will not let you interrupt | That is half-duplex. Use the speakerphone as both mic and speaker, confirm `duplex: full-duplex` in `check`, or set `half_duplex: "off"` only if echo is actually cancelled. |
| It feels late to accept the next question | Lower `half_duplex_tail_ms`. If it then replies to itself, the tail was too short. |
| Calendar says not authorized | Run `python -m jarvis tools` in Terminal and approve Automation. |
| The login service is quiet | `launchctl print "gui/$(id -u)/com.jarvis.assistant"` and `tail ~/Library/Logs/jarvis/stderr.log`. The agent only runs in a graphical login (`Aqua`), not over SSH as a different user. |
| Bluetooth speaker is silent | Re-pair, pick it in **Sound → Output**, run `python -m jarvis chime`, and raise both volume knobs. |

## Project layout

```
config.yaml                 settings you edit
.env                        API keys (you create this)
src/jarvis/assistant.py     wake loop, chime, reconnect
src/jarvis/session.py       one conversation: barge-in, half-duplex, silence
src/jarvis/duplex.py        half-duplex decision
src/jarvis/audio/           devices, streams, mic gate, chime
src/jarvis/wake/            openWakeWord
src/jarvis/providers/       OpenAI, Grok, and the mock provider
src/jarvis/tools/           clock, search, Calendar, Reminders, MCP
launchd/                    launchd plist template
scripts/install_launchd.sh  install the login service
```

Third-party pieces: [openWakeWord](https://github.com/dscripka/openWakeWord) (Apache-2.0) for the wake word, PortAudio via `sounddevice` for devices, and the provider’s realtime API for the conversation.

## Development

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m jarvis demo
```

`pytest` does not open a microphone and does not call the network. The stdio MCP test launches a tiny local Python process.

On macOS that install is enough. On Linux, openWakeWord 0.6 also asks for `tflite-runtime`, and that package has no wheel for Python 3.12. Jarvis forces the ONNX runtime and does not import TensorFlow Lite. If pip stops on `tflite-runtime`, install the other dependencies, then `pip install "openwakeword==0.6.0" --no-deps`.
