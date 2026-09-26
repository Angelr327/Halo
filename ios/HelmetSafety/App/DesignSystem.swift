import SwiftUI

enum AppSpacing {
    static let xSmall: CGFloat = 4
    static let small: CGFloat = 8
    static let medium: CGFloat = 12
    static let large: CGFloat = 16
    static let xLarge: CGFloat = 24
    static let section: CGFloat = 28
    static let screen: CGFloat = 20
}

enum AppTypography {
    static let heroMetric = Font.system(size: 68, weight: .bold, design: .rounded)
    static let largeMetric = Font.system(size: 30, weight: .bold, design: .rounded)
    static let sectionTitle = Font.title3.weight(.bold)
    static let cardTitle = Font.caption.weight(.bold)
}

struct AppCardModifier: ViewModifier {
    var padding: CGFloat = AppSpacing.large

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(AppTheme.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(AppTheme.divider, lineWidth: 0.5))
            .shadow(color: .black.opacity(0.045), radius: 12, y: 5)
    }
}

extension View {
    func appCard(padding: CGFloat = AppSpacing.large) -> some View {
        modifier(AppCardModifier(padding: padding))
    }
}

struct AppSectionHeader: View {
    let title: String
    var detail: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(AppTypography.sectionTitle)
            Spacer()
            if let detail { Text(detail).font(.caption.weight(.medium)).foregroundStyle(AppTheme.secondaryText) }
        }
        .foregroundStyle(AppTheme.primaryText)
        .accessibilityAddTraits(.isHeader)
    }
}
