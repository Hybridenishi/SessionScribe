import SwiftUI

struct TranscriptFeedView: View {
    let segments: [TranscriptSegment]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Transcript")
                .font(.headline)

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(segments) { segment in
                        TranscriptSegmentRow(segment: segment)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct TranscriptSegmentRow: View {
    let segment: TranscriptSegment

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(formatOffset(segment.start))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(width: 54, alignment: .leading)

                Text(segment.displayName)
                    .font(.subheadline.weight(.semibold))

                if segment.status == .partial {
                    Text("Partial")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.orange)
                }
            }

            Text(segment.text)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private func formatOffset(_ duration: Duration) -> String {
        let totalSeconds = max(0, Int(duration.components.seconds))
        let minutes = totalSeconds / 60
        let seconds = totalSeconds % 60
        return String(format: "%02d:%02d", minutes, seconds)
    }
}

#Preview {
    TranscriptFeedView(segments: PreviewFixture.transcriptSegments)
        .padding()
        .frame(width: 620, height: 420)
}
