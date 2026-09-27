import SwiftUI

struct DestinationSearchField: View {
    @ObservedObject var service: DestinationSearchService
    let placeholder: String
    let onSelect: (DestinationSuggestion) async -> Void
    let onRecentSelect: (Destination) async -> Void
    @FocusState private var isSearchFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: AppSpacing.medium) {
                Image(systemName: "magnifyingglass").foregroundStyle(AppTheme.secondaryText)
                TextField(placeholder, text: $service.query)
                    .textInputAutocapitalization(.words)
                    .autocorrectionDisabled()
                    .focused($isSearchFocused)
                    .submitLabel(.search)
                if service.isResolving { ProgressView().controlSize(.small) }
                else if !service.query.isEmpty {
                    Button(action: service.clear) { Image(systemName: "xmark.circle.fill").foregroundStyle(AppTheme.secondaryText) }
                        .buttonStyle(.plain)
                }
                if isSearchFocused {
                    Button("Cancel") { isSearchFocused = false }
                        .font(.subheadline.weight(.semibold))
                        .buttonStyle(.plain)
                }
            }

            if isSearchFocused, service.query.isEmpty, !service.recentDestinations.isEmpty {
                Divider()
                HStack {
                    Text("RECENT").font(.caption2.weight(.bold)).tracking(0.8).foregroundStyle(AppTheme.secondaryText)
                    Spacer()
                    Button("Clear") { service.clearRecentHistory() }
                        .font(.caption.weight(.semibold)).foregroundStyle(AppTheme.danger)
                        .buttonStyle(.plain)
                        .accessibilityLabel("Clear recent destination history")
                }
                ForEach(service.recentDestinations.prefix(5)) { destination in
                    Button {
                        isSearchFocused = false
                        Task { await onRecentSelect(destination) }
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: "clock.arrow.circlepath").foregroundStyle(AppTheme.secondaryText)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(destination.name).font(.subheadline.weight(.semibold)).foregroundStyle(AppTheme.primaryText)
                                if let subtitle = destination.subtitle { Text(subtitle).font(.caption).foregroundStyle(AppTheme.secondaryText).lineLimit(1) }
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
            } else if !service.suggestions.isEmpty {
                Divider()
                ForEach(service.suggestions) { suggestion in
                    Button {
                        isSearchFocused = false
                        Task { await onSelect(suggestion) }
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(suggestion.title).font(.subheadline.weight(.semibold)).foregroundStyle(AppTheme.primaryText)
                            if !suggestion.subtitle.isEmpty {
                                Text(suggestion.subtitle).font(.caption).foregroundStyle(AppTheme.secondaryText).lineLimit(2)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if suggestion.id != service.suggestions.last?.id { Divider() }
                }
            } else if !service.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      let error = service.errorMessage {
                Text(error).font(.caption).foregroundStyle(AppTheme.danger)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { isSearchFocused = false }
            }
        }
    }
}
