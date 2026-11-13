// swift-tools-version: 6.0
// labb's design system for SwiftUI: the tokens (generated from the same JSON as the web's CSS) and
// a few styles and views on them. The iOS app (mobile/ios) adds it as a local package.
import PackageDescription

let package = Package(
    name: "LabbDesign",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "LabbDesign", targets: ["LabbDesign"]),
    ],
    targets: [
        .target(name: "LabbDesign", resources: [.process("Resources")]),
    ]
)
