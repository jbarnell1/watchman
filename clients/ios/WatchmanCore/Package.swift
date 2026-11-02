// swift-tools-version:5.9
import PackageDescription

// The parts of the iOS client that need no UIKit: the API, the hash, the queue, decisions.
// `swift test` runs on macOS in CI; the app and the extensions depend on this package.
let package = Package(
    name: "WatchmanCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "WatchmanCore", targets: ["WatchmanCore"])],
    targets: [
        .target(name: "WatchmanCore"),
        .testTarget(name: "WatchmanCoreTests", dependencies: ["WatchmanCore"], resources: [.copy("Vectors")]),
    ]
)
