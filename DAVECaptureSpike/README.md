# DAVE capture spike

This is a disposable command-line proof of the Discord receiver boundary. It is deliberately outside the SwiftUI app and does not own a production SessionScribe archive.

It uses D++ 10.1.5, which supports DAVE-enabled voice connections and per-user receive callbacks. D++'s public receive callback exposes decoded PCM, so the spike writes per-user `48 kHz`, stereo, signed-16-bit WAV tracks. The output manifest labels those tracks as noncanonical. Do not treat them as the MVP's future preserved Opus archive.

## Build and test

Install the macOS dependencies:

```sh
brew install cmake libdpp
```

Then configure and test the independent project:

```sh
cmake -S DAVECaptureSpike -B DAVECaptureSpike/build
cmake --build DAVECaptureSpike/build
ctest --test-dir DAVECaptureSpike/build --output-on-failure
```

## Live capture gate

Create a dedicated test bot and voice channel. The bot must be visible in the channel and have permission to connect and speak. Enable the required Guilds and Guild Voice States gateway intents in the Discord developer portal.

Run the binary with the bot token supplied only on standard input, not in shell history, process arguments, or files:

```sh
read -rs SESSION_SCRIBE_SPIKE_TOKEN
printf '%s\n' "$SESSION_SCRIBE_SPIKE_TOKEN" | DAVECaptureSpike/build/session-scribe-dave-spike \
  --guild-id <guild-id> \
  --channel-id <channel-id> \
  --output DAVECaptureSpike/capture-output/run-001
unset SESSION_SCRIBE_SPIKE_TOKEN
```

To pass the capture spike:

1. Have at least three people speak, including one period of overlap.
2. Verify that `audio/<discord-user-id>/epoch-1.wav` exists for each participant who spoke and can be played as a normal WAV file.
3. Interrupt the bot's voice network connection once while leaving the process running. D++ should reconnect; a new `epoch-N.wav` must be created and `sidecar-events.ndjson` must record a disconnected then connected epoch.
4. Verify that a deliberate short network interruption produces a `gap` event when RTP sequence numbers skip.
5. Verify that `sidecar-events.ndjson`, `spike-manifest.json`, and process diagnostics contain no bot token.

The output is evidence for the DAVE receiver gate only. The subsequent native-ingestion increment must use the versioned local protocol and preserve raw decrypted Opus frames as canonical audio; D++'s public PCM callback does not satisfy that archival requirement.
