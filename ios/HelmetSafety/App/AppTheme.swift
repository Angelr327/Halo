import SwiftUI
import UIKit

enum AppTheme {
    static let accent = Color(red: 0.96, green: 0.27, blue: 0.08)
    static let background = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark ? UIColor(red: 0.055, green: 0.055, blue: 0.06, alpha: 1) : UIColor(red: 0.965, green: 0.96, blue: 0.95, alpha: 1)
    })
    static let surface = Color(uiColor: .secondarySystemBackground)
    static let elevatedSurface = Color(uiColor: .tertiarySystemBackground)
    static let primaryText = Color(uiColor: .label)
    static let secondaryText = Color(uiColor: .secondaryLabel)
    static let divider = Color(uiColor: .separator).opacity(0.45)
    static let safe = Color(red: 0.10, green: 0.64, blue: 0.34)
    static let caution = Color(red: 0.95, green: 0.61, blue: 0.08)
    static let danger = Color(red: 0.88, green: 0.15, blue: 0.16)
}
