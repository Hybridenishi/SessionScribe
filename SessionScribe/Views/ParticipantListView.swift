import SwiftUI

struct ParticipantListView: View {
    let participants: [Participant]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Participants")
                .font(.headline)

            ForEach(participants) { participant in
                ParticipantRow(participant: participant)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ParticipantRow: View {
    let participant: Participant

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(participant.isSpeaking ? Color.green : Color.secondary.opacity(0.35))
                .frame(width: 10, height: 10)
                .overlay {
                    if participant.isSpeaking {
                        Circle()
                            .stroke(Color.green.opacity(0.35), lineWidth: 5)
                    }
                }
                .accessibilityLabel(participant.isSpeaking ? "Speaking" : "Silent")

            VStack(alignment: .leading, spacing: 2) {
                Text(participant.displayName)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                Text(participant.isConnected ? "Connected" : "Disconnected")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()
        }
        .padding(10)
        .background(.background, in: RoundedRectangle(cornerRadius: 8))
    }
}

#Preview {
    ParticipantListView(participants: PreviewFixture.participants)
        .padding()
        .frame(width: 320)
}
