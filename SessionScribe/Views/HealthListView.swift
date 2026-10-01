import SwiftUI

struct HealthListView: View {
    let rows: [HealthRow]
    var title = "Health"

    init(rows: [HealthRow], title: String = "Health") {
        self.rows = rows
        self.title = title
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.headline)

            ForEach(rows) { item in
                HStack(spacing: 10) {
                    Image(systemName: symbol(for: item.status))
                        .foregroundStyle(color(for: item.status))
                        .frame(width: 18)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.name)
                            .font(.body.weight(.medium))
                        Text(item.detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }

                    Spacer()
                }
                .padding(10)
                .background(.background, in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func symbol(for status: ServiceHealth.Status) -> String {
        switch status {
        case .inactive:
            "circle"
        case .healthy:
            "checkmark.circle.fill"
        case .degraded:
            "exclamationmark.triangle.fill"
        case .failed:
            "xmark.octagon.fill"
        }
    }

    private func color(for status: ServiceHealth.Status) -> Color {
        switch status {
        case .inactive:
            .secondary
        case .healthy:
            .green
        case .degraded:
            .orange
        case .failed:
            .red
        }
    }
}

#Preview {
    HealthListView(rows: [
        HealthRow(name: "Hub", status: .healthy, detail: "Connected"),
        HealthRow(name: "naota GPU", status: .degraded, detail: "Busy for 40 s, 3 waiting"),
        HealthRow(name: "Mac worker", status: .failed, detail: "Last seen 2 d ago"),
        HealthRow(name: "Foundry", status: .inactive, detail: "Arrives in H4")
    ])
        .padding()
        .frame(width: 320)
}
