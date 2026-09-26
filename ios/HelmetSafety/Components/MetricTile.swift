import SwiftUI

struct MetricTile: View {
    let icon: String
    let label: String
    let value: String
    var tint: Color = AppTheme.accent

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.small) {
            Image(systemName: icon).font(.subheadline.weight(.semibold)).foregroundStyle(tint)
            Text(value).font(AppTypography.largeMetric).foregroundStyle(AppTheme.primaryText).minimumScaleFactor(0.72).lineLimit(1)
            Text(label).font(.caption.weight(.medium)).foregroundStyle(AppTheme.secondaryText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .appCard(padding: 14)
    }
}
