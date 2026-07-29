# Patched D++: DAVE passthrough fix (local, unmerged)

The Milestone 0 live tests in [LIVE-TEST-FINDINGS.md](LIVE-TEST-FINDINGS.md) showed
order-dependent capture failures: audio with repeated decrypt warnings when the bot
joined before the human, and no audio at all when the human joined first. Reading
D++ 10.1.5's own source (`src/dpp/dave/`, `src/dpp/voice/enabled/`) found the likely
cause and two bugs, both patched here. This patch is local and unmerged upstream.

## Root cause

D++ vendors a fork of Discord's `libdave` C++ decryptor. That decryptor has a
built-in passthrough grace period (`decryptor::transition_to_passthrough_mode`) meant
to tolerate a peer's frames arriving unencrypted for a moment while their own MLS
onboarding (key package / welcome / commit) is still in flight. Searching the entire
D++ codebase for callers of that method turns up none — it is defined but never
wired up anywhere. `allow_pass_through_until` (in `dave/decryptor.h`) defaults to
`time_point::min()` and nothing ever raises it, so:

- Every transitional unencrypted frame from a joining member unconditionally hits
  `decryptor.cpp:104`'s hard failure path and logs exactly the warning the spike
  captured: `"decrypt failed, frame is not encrypted and pass through is disabled"`.
- Separately, in `voice/enabled/courier_loop.cpp`, when `decrypt()` returns 0
  (failure), the code did not skip the frame — it fell through and fed the
  still-DAVE-encrypted bytes into `opus_decode()` anyway, producing garbage instead
  of a clean drop.

This plausibly explains both observed runs: bot-joins-first got PCM with
interleaved decrypt warnings (the human's join triggered an MLS transition
mid-session, hitting the unwired passthrough path for a few packets); human-joins-first
got no audio at all (the bot's own decryptors/encryptor weren't ready before speech
started, and failures were silently decoded as noise/errors rather than logged).

**This has not yet been confirmed against a live Discord session** — that requires
an actual bot token and voice channel, which only a live test can provide. The patch
is believed correct based on static reading of the DAVE protocol's documented
transition/grace-period behavior and D++'s own (unwired) API for it, not by
reproducing the bug outside Discord.

## What changed

`DPP-patched/` (a sibling directory next to this repo, not checked in — see below)
is a clone of `brainboxdotcc/DPP` at tag `v10.1.5` with two patches:

1. **`src/dpp/voice/enabled/handle_frame.cpp`, `update_ratchets()`** — when a
   decryptor is created for a newly-joined MLS member, grant it an indefinite
   passthrough window immediately, then schedule it to close after the same
   `RATCHET_EXPIRY` (10s) already used for key-ratchet rotation in this function.
   This is the wiring `transition_to_passthrough_mode` was designed for but never
   received.
2. **`src/dpp/voice/enabled/courier_loop.cpp`** — when DAVE decryption is expected
   but fails (no decryptor yet, or `decrypt()` returns 0), skip Opus-decoding that
   frame instead of decoding the still-encrypted ciphertext as if it were plaintext
   Opus.

Neither patch changes behavior for already-established members with working key
ratchets — only the join-transition and hard-failure paths.

## Building the patched library

```sh
git clone --branch v10.1.5 --depth 1 https://github.com/brainboxdotcc/DPP.git DPP-patched
# apply the two edits above to DPP-patched/src/dpp/voice/enabled/{handle_frame,courier_loop}.cpp
```

Then configure and build with the same flags Homebrew's `libdpp` formula uses
(`brew cat libdpp`), installing to a local prefix instead of `/opt/homebrew`:

```sh
export PKG_CONFIG_PATH="/opt/homebrew/opt/openssl@4/lib/pkgconfig:/opt/homebrew/opt/opus/lib/pkgconfig:$PKG_CONFIG_PATH"
cmake -S DPP-patched -B DPP-patched/build \
  -DDPP_BUILD_TEST=OFF -DDPP_NO_CONAN=ON -DDPP_NO_VCPKG=ON \
  -DDPP_USE_EXTERNAL_JSON=ON -DRUN_LDCONFIG=OFF \
  -DCMAKE_INSTALL_PREFIX="$(pwd)/DPP-patched/install" \
  -DCMAKE_BUILD_TYPE=Release \
  -DOPENSSL_ROOT_DIR="/opt/homebrew/opt/openssl@4" \
  -DCMAKE_CXX_FLAGS="-I/opt/homebrew/opt/nlohmann-json/include"
cmake --build DPP-patched/build -j"$(sysctl -n hw.ncpu)"
cmake --install DPP-patched/build
```

(`nlohmann-json` must be installed via `brew install nlohmann-json`; it's a
build-only dependency of D++'s vendored `mlspp` submodule that isn't propagated
automatically when `DPP_USE_EXTERNAL_JSON=ON`, hence the explicit include flag.)

## Building the spike against the patched library

Point `pkg-config` at the patched install instead of Homebrew's `libdpp` and
configure into a separate build directory (`build-patched/`, already gitignored)
so the normal `build/` directory tracking the Homebrew release stays untouched:

```sh
export PKG_CONFIG_PATH="/path/to/DPP-patched/install/lib/pkgconfig"
cmake -S DAVECaptureSpike -B DAVECaptureSpike/build-patched -DCMAKE_BUILD_TYPE=Release
cmake --build DAVECaptureSpike/build-patched
ctest --test-dir DAVECaptureSpike/build-patched --output-on-failure
```

Run the live capture gate exactly as in the main [README](README.md), substituting
`build-patched/session-scribe-dave-spike` for `build/session-scribe-dave-spike`.

## New diagnostics for this run

The spike previously discarded all D++ log output below `ll_warning`, which threw
away the DAVE session/epoch/passthrough lifecycle logging D++ already emits at
`ll_debug`. `main.cpp` now also writes everything `ll_debug` and above (redacted,
same as the warning path) to `dpp-dave-diagnostics.log` in the run's output
directory. After the next live test, that file should show, per participant join:
MLS proposal/commit/welcome opcodes, `"Inserting decryptor key ratchet for NEW
user"`, and whether decrypt succeeds through the new passthrough window instead of
warning.

## Repeating the test matrix

Re-run the same join-order matrix from LIVE-TEST-FINDINGS.md (bot-first,
human-first, join/leave during capture, forced reconnect) against
`build-patched/session-scribe-dave-spike`, and compare `dpp-dave-diagnostics.log`
and the resulting WAV tracks against the prior findings. If audio now arrives
cleanly in both join orders, this is a strong candidate to upstream as a PR against
`brainboxdotcc/DPP` rather than carry indefinitely as a local patch.
