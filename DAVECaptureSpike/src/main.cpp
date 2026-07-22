#include "session_scribe_spike/recorder.hpp"

#include <atomic>
#include <charconv>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <span>
#include <stdexcept>
#include <string>
#include <thread>

#include <dpp/dpp.h>

namespace {

std::atomic_bool stopRequested = false;

void requestStop(int) {
    stopRequested.store(true);
}

struct Arguments {
    dpp::snowflake guildID;
    dpp::snowflake channelID;
    std::filesystem::path outputDirectory;
};

[[nodiscard]] dpp::snowflake parseSnowflake(const std::string& value, const std::string& name) {
    uint64_t parsed = 0;
    const auto [pointer, error] = std::from_chars(value.data(), value.data() + value.size(), parsed);
    if (error != std::errc {} || pointer != value.data() + value.size() || parsed == 0) {
        throw std::invalid_argument(name + " must be a non-zero Discord snowflake");
    }
    return dpp::snowflake { parsed };
}

[[nodiscard]] Arguments parseArguments(const int argumentCount, char* arguments[]) {
    if (argumentCount != 7 || std::string(arguments[1]) != "--guild-id" ||
        std::string(arguments[3]) != "--channel-id" || std::string(arguments[5]) != "--output") {
        throw std::invalid_argument(
            "Usage: session-scribe-dave-spike --guild-id <id> --channel-id <id> --output <directory>\n"
            "Provide the bot token in SESSION_SCRIBE_SPIKE_TOKEN or as one line on standard input; it is never accepted as an argument or written to disk.");
    }

    return Arguments {
        .guildID = parseSnowflake(arguments[2], "--guild-id"),
        .channelID = parseSnowflake(arguments[4], "--channel-id"),
        .outputDirectory = arguments[6],
    };
}

[[nodiscard]] std::string readToken() {
    if (const char* environmentToken = std::getenv("SESSION_SCRIBE_SPIKE_TOKEN");
        environmentToken != nullptr && environmentToken[0] != '\0') {
        return environmentToken;
    }

    std::string token;
    if (!std::getline(std::cin, token) || token.empty()) {
        throw std::invalid_argument("A bot token is required in SESSION_SCRIBE_SPIKE_TOKEN or on standard input");
    }
    return token;
}

[[nodiscard]] std::string userID(const dpp::snowflake id) {
    return std::to_string(static_cast<uint64_t>(id));
}

[[nodiscard]] std::string redacted(std::string message, const std::string& token) {
    std::string::size_type position = 0;
    while ((position = message.find(token, position)) != std::string::npos) {
        message.replace(position, token.size(), "[redacted]");
        position += 10;
    }
    return message;
}

} // namespace

int main(int argumentCount, char* arguments[]) {
    try {
        const Arguments configuration = parseArguments(argumentCount, arguments);
        const std::string token = readToken();
        session_scribe::spike::Recorder recorder(configuration.outputDirectory);
        recorder.start("dpp-10.1.5");

        dpp::cluster bot(token, dpp::i_guilds | dpp::i_guild_voice_states);
        std::atomic_bool joinRequested = false;
        std::atomic_bool voiceReady = false;
        std::atomic_bool receivedAudio = false;
        std::atomic<dpp::discord_client*> voiceShard = nullptr;

        bot.on_log([&token](const dpp::log_t& event) {
            if (event.severity >= dpp::ll_warning) {
                std::cerr << "DPP warning: " << redacted(event.message, token) << '\n';
            }
        });

        bot.on_ready([&](const dpp::ready_t& event) {
            if (!joinRequested.exchange(true)) {
                if (dpp::discord_client* shard = event.from(); shard != nullptr) {
                    voiceShard.store(shard);
                    std::cerr << "Gateway ready. Joining the voice channel...\n";
                    shard->connect_voice(configuration.guildID, configuration.channelID, false, false, true);
                } else {
                    recorder.disconnected("gateway_shard_unavailable");
                }
            }
        });

        bot.on_voice_ready([&](const dpp::voice_ready_t&) {
            recorder.connected();
            if (voiceReady.exchange(true)) {
                std::cerr << "Voice connection ready again. Recording remains armed.\n";
            } else {
                std::cerr << "Voice connection ready. Recording is armed; you can speak now.\n";
            }
        });

        bot.on_voice_client_disconnect([&](const dpp::voice_client_disconnect_t& event) {
            recorder.speaking(userID(event.user_id), false);
        });

        bot.on_voice_client_speaking([&](const dpp::voice_client_speaking_t& event) {
            recorder.speaking(userID(event.user_id), true);
        });

        bot.on_voice_receive([&](const dpp::voice_receive_t& event) {
            if (event.user_id == 0 || event.audio_data.empty()) {
                return;
            }

            const std::string id = userID(event.user_id);
            recorder.participant(id, id);
            const auto* packetData = reinterpret_cast<const uint8_t*>(event.raw_event.data());
            const std::span<const uint8_t> packet(packetData, event.raw_event.size());
            recorder.record({
                .userID = id,
                .rtp = session_scribe::spike::parseRtpHeader(packet),
                .pcm = event.audio_data,
            });

            if (!receivedAudio.exchange(true)) {
                std::cerr << "Recording started: receiving audio from Discord user " << id << ".\n";
            }
        });

        std::signal(SIGINT, requestStop);
        std::signal(SIGTERM, requestStop);
        bot.start(dpp::st_return);

        std::cerr << "Capture spike is running. Waiting for the voice connection to be ready.\n";
        while (!stopRequested.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        if (!receivedAudio.load()) {
            std::cerr << "No audio frames arrived. Wait for 'Voice connection ready' before speaking.\n";
        }
        std::cerr << "Stopping capture: leaving the voice channel and finalizing evidence tracks...\n";
        if (dpp::discord_client* shard = voiceShard.load(); shard != nullptr) {
            shard->disconnect_voice(configuration.guildID);
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        bot.shutdown();
        recorder.finalize();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Capture spike failed: " << error.what() << '\n';
        return 1;
    }
}
